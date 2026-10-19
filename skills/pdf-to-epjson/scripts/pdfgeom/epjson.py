"""Write the typed surfaces as a minimal EnergyPlus model in epJSON.

Contents: Version, Building (north axis), GlobalGeometryRules, one Zone per room (plus generated
attic / crawlspace zones), BuildingSurface:Detailed, FenestrationSurface:Detailed and one placeholder
construction per surface category. No SimulationControl, weather, schedules, loads or HVAC: add the
run periods you need (`validate` checks the geometry with a temporary design day).

Boundary conditions
- outside -> Outdoors; ground -> Ground.
- foundation walls are split at grade: the above-grade part is Outdoors, the below-grade part Ground.
- zone (garage, attic, crawlspace, unconditioned basement) -> Surface, paired with the matching surface.
- adiabatic room-to-room partitions -> Surface when the matching surface of the other room is found
  (same polygon, reversed), otherwise Adiabatic (counted in the notes).
- ceilings under another room are raised to that room's floor, so stacked zones close: the load model
  keeps the floor framing in the wall area instead.

Placeholder constructions are symmetric (gypsum | no-mass core | gypsum; doors and glazing a single
layer), so interzone pairs can share one construction. Names are "PH <category>": replace them by
category. Their values only make the file simulate; they are not design values.
"""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import Polygon

from .geometry import Surface
from .model import Building

SURFACE_TYPE = {"wall": "Wall", "floor": "Floor", "ceiling": "Ceiling", "roof": "Roof"}
OPENINGS = ("window", "door", "skylight")
# nominal RSI (m2K/W) of the placeholder layers, only so the file simulates; not design values
PLACEHOLDER_RSI = {"wall": 2.5, "roof": 4.0, "ceiling": 4.0, "floor": 2.0, "ground": 1.0, "interior": 0.3}
PLACEHOLDER_GLAZING = {"u_factor": 2.0, "solar_heat_gain_coefficient": 0.4}
PLACEHOLDER_DOOR_RSI = 0.5
PLACEHOLDER_GYPSUM = ("PH gypsum 13mm", {"roughness": "Smooth", "thickness": 0.0127, "conductivity": 0.16,
                                         "density": 800.0, "specific_heat": 1090.0, "thermal_absorptance": 0.9,
                                         "solar_absorptance": 0.5, "visible_absorptance": 0.5})


def _nomass(rsi: float) -> dict:
    return {"roughness": "MediumRough", "thermal_resistance": rsi, "thermal_absorptance": 0.9,
            "solar_absorptance": 0.7, "visible_absorptance": 0.7}


def _name(s: str) -> str:
    return str(s).replace(",", "_").replace(";", "_").replace("!", "_")


def _vkey(v) -> tuple:
    return tuple(sorted((round(x, 3), round(y, 3), round(z, 3)) for x, y, z in v))


def _vertices(v) -> list[dict]:
    return [{"vertex_x_coordinate": round(x, 4), "vertex_y_coordinate": round(y, 4),
             "vertex_z_coordinate": round(z, 4)} for x, y, z in v]


def _drop_collinear(v, tol=1e-4) -> list:
    """Remove repeated and collinear vertices (EnergyPlus warns about them and removes them itself)."""
    pts = list(v)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            ab = [b[k] - a[k] for k in range(3)]
            bc = [c[k] - b[k] for k in range(3)]
            cross = (ab[1] * bc[2] - ab[2] * bc[1], ab[2] * bc[0] - ab[0] * bc[2], ab[0] * bc[1] - ab[1] * bc[0])
            if sum(x * x for x in cross) ** 0.5 < tol * max(sum(x * x for x in ab) ** 0.5, 1e-9):
                pts.pop(i)
                changed = True
                break
    return pts


def _flat_vertices(v) -> dict:
    out = {}
    for i, (x, y, z) in enumerate(v, 1):
        out.update({f"vertex_{i}_x_coordinate": round(x, 4), f"vertex_{i}_y_coordinate": round(y, 4),
                    f"vertex_{i}_z_coordinate": round(z, 4)})
    return out


def _lift_ceilings(b: Building, surfaces: list[Surface], verts: dict[str, list]) -> int:
    """Raise ceilings under another room to that room's floor.

    The load model keeps the floor framing in the wall area (walls run floor-to-floor, the ceiling sits at
    ceiling height). An EnergyPlus zone must be closed, so the framing depth goes into the lower zone.
    """
    n = 0
    for s in surfaces:
        if s.kind != "ceiling" or s.category not in ("ceiling_interior", "ceiling_to_garage"):
            continue
        above = b.room(s.adjacent_zone)
        z_top = above.floor_elevation if above.floor_elevation is not None else b.level(above.level).elevation
        z = s.vertices[0][2]
        if not 0.001 < z_top - z <= 1.0:
            continue
        verts[s.id] = [(x, y, z_top) for x, y, _ in verts[s.id]]
        if s.other_side:  # generated mirror floor of the unconditioned room above
            verts[s.other_side] = [(x, y, z_top) for x, y, _ in verts[s.other_side]]
        n += 1
    return n


def _pair_partitions(surfaces: list[Surface], verts: dict[str, list]) -> dict[str, str]:
    """Match each adiabatic room-to-room surface with its mirror image in the adjacent room."""
    pool: dict[tuple, list[Surface]] = {}
    for s in surfaces:
        if s.boundary == "adiabatic" and s.kind in SURFACE_TYPE and s.adjacent_zone:
            pool.setdefault((s.zone, s.adjacent_zone), []).append(s)
    mate = {"wall": "wall", "floor": "ceiling", "ceiling": "floor"}

    def same(a: Surface, c: Surface) -> bool:
        va, vc = verts[a.id], verts[c.id]
        if a.kind == "wall":
            return _vkey(va) == _vkey(vc)
        # floors/ceilings: same height and same plan polygon (vertex count may differ by collinear points)
        if abs(va[0][2] - vc[0][2]) > 0.001:
            return False
        pa, pc = Polygon([p[:2] for p in va]), Polygon([p[:2] for p in vc])
        return pa.symmetric_difference(pc).area < 1e-4 * max(pa.area, 1.0)

    pairs: dict[str, str] = {}
    for (z, adj), group in pool.items():
        for s in group:
            if s.id in pairs:
                continue
            other = next((o for o in pool.get((adj, z), []) if o.id not in pairs and o.kind == mate.get(s.kind)
                          and same(s, o)), None)
            if other is not None:
                pairs[s.id], pairs[other.id] = other.id, s.id
    return pairs


def build(b: Building, geo: dict, version: str = "25.2") -> tuple[dict, list[str]]:
    surfaces: list[Surface] = geo["surfaces"]
    zones = geo["zones"]
    by_id = {s.id: s for s in surfaces}
    notes: list[str] = []
    ep: dict = {
        "Version": {"Version 1": {"version_identifier": version}},
        "Building": {_name(b.name): {"north_axis": round(float(b.plan_north_deg), 3), "terrain": "Suburbs",
                                     "solar_distribution": "FullExterior"}},
        "GlobalGeometryRules": {"GlobalGeometryRules 1": {
            "starting_vertex_position": "UpperLeftCorner", "vertex_entry_direction": "Counterclockwise",
            "coordinate_system": "Relative"}},  # Relative: Building north_axis applies
        "Material": {}, "Material:NoMass": {}, "WindowMaterial:SimpleGlazingSystem": {}, "Construction": {},
        "Zone": {}, "BuildingSurface:Detailed": {}, "FenestrationSurface:Detailed": {},
    }

    def construction(category: str, kind: str) -> str:
        name = f"PH {category}"
        if name in ep["Construction"]:
            return name
        if kind in ("window", "skylight", "glass_door"):
            ep["WindowMaterial:SimpleGlazingSystem"][name + " glazing"] = dict(PLACEHOLDER_GLAZING)
            ep["Construction"][name] = {"outside_layer": name + " glazing"}
        elif kind == "door":
            ep["Material:NoMass"][name + " layer"] = _nomass(PLACEHOLDER_DOOR_RSI)
            ep["Construction"][name] = {"outside_layer": name + " layer"}
        else:
            # gypsum | no-mass core | gypsum: some thermal mass, and the same layers read from either side
            ep["Material"].setdefault(PLACEHOLDER_GYPSUM[0], dict(PLACEHOLDER_GYPSUM[1]))
            ep["Material:NoMass"][name + " core"] = _nomass(PLACEHOLDER_RSI[kind])
            ep["Construction"][name] = {"outside_layer": PLACEHOLDER_GYPSUM[0], "layer_2": name + " core",
                                        "layer_3": PLACEHOLDER_GYPSUM[0]}
        return name

    def opaque_construction(s: Surface) -> str:
        if s.boundary == "ground":
            return construction(s.category, "ground")
        if s.boundary == "adiabatic":
            return construction(s.category, "interior")
        return construction(s.category, s.kind)

    def pair_construction(s: Surface, partner: Surface) -> str:
        # both sides of a pair share one construction, named after the side in a conditioned zone
        cands = [x for x in (s, partner) if zones.get(x.zone, {}).get("conditioned")] or [s, partner]
        prim = min(cands, key=lambda x: x.id)
        kind = "interior" if prim.boundary == "adiabatic" else prim.kind
        return construction(prim.category, kind)

    for z, v in zones.items():
        # volume and floor area are left for EnergyPlus to calculate from the surfaces
        ep["Zone"][_name(z)] = {"part_of_total_floor_area": "Yes" if v.get("conditioned") else "No"}

    verts = {s.id: s.vertices if s.kind in OPENINGS else _drop_collinear(s.vertices) for s in surfaces}
    lifted = _lift_ceilings(b, surfaces, verts)
    if lifted:
        notes.append(f"{lifted} ceilings under another room raised to that room's floor (floor framing in the lower zone)")
    pairs = _pair_partitions(surfaces, verts)
    unpaired = 0
    host_names: dict[str, str] = {}
    for s in surfaces:
        if s.kind in OPENINGS:
            continue
        common = {"surface_type": SURFACE_TYPE[s.kind], "zone_name": _name(s.zone)}
        if s.boundary == "foundation":
            # split at grade: above-grade part to outdoors, below-grade part to ground
            (x1, y1, ztop), (_, _, zbot), (x2, y2, _), _ = s.vertices
            zg = max(zbot, min(ztop, 0.0))
            for tag, z0, z1, bc in (("_ag", zg, ztop, "Outdoors"), ("_bg", zbot, zg, "Ground")):
                if z1 - z0 <= 0.01:
                    continue
                v = [(x1, y1, z1), (x1, y1, z0), (x2, y2, z0), (x2, y2, z1)]
                ep["BuildingSurface:Detailed"][_name(s.id) + tag] = {
                    **common, "construction_name": construction(s.category, "ground" if bc == "Ground" else "wall"),
                    "outside_boundary_condition": bc,
                    "sun_exposure": "SunExposed" if bc == "Outdoors" else "NoSun",
                    "wind_exposure": "WindExposed" if bc == "Outdoors" else "NoWind",
                    "vertices": _vertices(v)}
                if tag == "_ag":
                    host_names[s.id] = _name(s.id) + tag
            continue
        rec = {**common, "vertices": _vertices(verts[s.id])}
        partner_id = s.other_side if s.boundary == "zone" else pairs.get(s.id)
        if partner_id:
            rec.update(construction_name=pair_construction(s, by_id[partner_id]), outside_boundary_condition="Surface",
                       outside_boundary_condition_object=_name(partner_id), sun_exposure="NoSun", wind_exposure="NoWind")
        elif s.boundary == "adiabatic":
            unpaired += 1
            rec.update(construction_name=opaque_construction(s), outside_boundary_condition="Adiabatic",
                       sun_exposure="NoSun", wind_exposure="NoWind")
        elif s.boundary == "ground":
            rec.update(construction_name=opaque_construction(s), outside_boundary_condition="Ground",
                       sun_exposure="NoSun", wind_exposure="NoWind")
        else:
            rec.update(construction_name=opaque_construction(s), outside_boundary_condition="Outdoors",
                       sun_exposure="SunExposed", wind_exposure="WindExposed")
        ep["BuildingSurface:Detailed"][_name(s.id)] = rec
        host_names[s.id] = _name(s.id)
    if pairs:
        notes.append(f"{len(pairs) // 2} room-to-room partition pairs written as interzone surfaces")
    if unpaired:
        notes.append(f"{unpaired} room-to-room partitions without a matching surface in the other room: Adiabatic")

    for s in surfaces:
        if s.kind not in OPENINGS:
            continue
        host = by_id[s.parent]
        if host.id not in host_names:
            notes.append(f"opening {s.id}: host wall {host.id} has no above-grade part; omitted")
            continue
        ov = s.vertices
        if host.boundary == "foundation":
            ag_top = max(v[2] for v in host.vertices)
            z_bot, z_top = min(v[2] for v in ov), max(v[2] for v in ov)
            if z_top - z_bot > ag_top - 0.02:
                notes.append(f"opening {s.id} is taller than the above-grade part of its wall (window well?): omitted")
                continue
            if z_bot < 0.01:
                dz = 0.01 - z_bot
                ov = [(x, y, z + dz) for x, y, z in ov]
                notes.append(f"opening {s.id} extends below grade: raised {dz:.2f} m onto the above-grade wall")
        if s.category == "glass_door":
            ftype = "GlassDoor"
        elif s.kind == "door":
            ftype = "Door"
        else:
            ftype = "Window"
        cname = construction(s.category, s.category if s.category in ("glass_door", "door") else s.kind)
        rec = {"surface_type": ftype, "construction_name": cname, "building_surface_name": host_names[host.id],
               "multiplier": 1, "number_of_vertices": len(ov), **_flat_vertices(ov)}
        partner_id = host.other_side if host.boundary == "zone" else pairs.get(host.id)
        if partner_id:
            # interzone host: mirror the opening onto the partner surface
            mirror = _name(s.id) + "_mirror"
            rec["outside_boundary_condition_object"] = mirror
            ep["FenestrationSurface:Detailed"][mirror] = {
                **rec, "building_surface_name": _name(partner_id), "outside_boundary_condition_object": _name(s.id),
                **_flat_vertices(list(reversed(ov)))}
        ep["FenestrationSurface:Detailed"][_name(s.id)] = rec

    ep = {k: v for k, v in ep.items() if v}
    return ep, notes


def write(b: Building, geo: dict, out_path: Path, version: str = "25.2") -> dict:
    ep, notes = build(b, geo, version)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(ep, indent=2), encoding="utf-8")
    counts = {k: len(v) for k, v in ep.items()}
    return {"epjson": str(out_path), "objects": counts, "notes": notes,
            "constructions": sorted(ep.get("Construction", {})),
            "next": "replace the 'PH <category>' constructions with real ones; add weather, schedules, loads and HVAC "
                    "as needed for your simulation"}
