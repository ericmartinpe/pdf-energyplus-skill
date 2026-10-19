"""Load, validate and normalize a building.json into SI data structures.

The file format is documented in reference/building-schema.md. Validation errors are
collected and reported together so the agent can fix them in one pass.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .units import LENGTH

SCHEMA = "pdf2epjson.building/1"
RESERVED_ID = re.compile(r"^(attic\d*|crawlspace)$", re.I)  # generated zone names

UNCONDITIONED_TYPES = {"garage", "basement_unconditioned"}
CEILING_TYPES = {"attic", "cathedral", "flat_roof"}
FLOOR_TYPES = {"slab", "crawlspace", "exposed", "auto"}
FOUNDATION_TYPES = {"slab", "crawlspace", "exposed", "basement"}
OPENING_KINDS = {"window", "door", "glass_door", "skylight"}
DEFAULT_SILL = {"window": 3.0 * 0.3048, "door": 0.0, "glass_door": 0.0, "skylight": 0.0}


class ModelError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass
class Level:
    id: str
    name: str
    elevation: float  # m, finished floor relative to grade (negative = below grade)
    ceiling_height: float  # m
    floor_to_floor: float  # m
    index: int = 0


@dataclass
class Room:
    id: str
    name: str
    level: str
    type: str
    conditioned: bool
    polygon: list[tuple[float, float]]  # m, CCW, plan coordinates
    ceiling: str  # attic | cathedral | flat_roof
    floor: str  # auto | slab | crawlspace | exposed
    ceiling_height: float  # m
    raw: dict = field(default_factory=dict)
    floor_elevation: float | None = None  # m, overrides the level elevation (e.g. garage slab)
    raw_polygon: list = field(default_factory=list)  # m, vertex order as written in building.json


@dataclass
class Opening:
    id: str
    room: str
    kind: str
    width: float
    height: float
    sill: float
    at: tuple[float, float] | None
    edge: int | None
    label: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Building:
    path: Path | None
    raw: dict
    units: str
    name: str
    plan_north_deg: float
    levels: list[Level]
    rooms: list[Room]
    openings: list[Opening]
    foundation: dict
    attic: dict
    warnings: list[str] = field(default_factory=list)

    def level(self, lid: str) -> Level:
        return next(lv for lv in self.levels if lv.id == lid)

    def room(self, rid: str) -> Room:
        return next(r for r in self.rooms if r.id == rid)


def _ccw(poly):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        a += x1 * y2 - x2 * y1
    return poly if a > 0 else list(reversed(poly))


def _clean(poly, tol=1e-4):
    """Drop repeated and collinear vertices."""
    pts = []
    for p in poly:
        if not pts or math.dist(p, pts[-1]) > tol:
            pts.append(p)
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) <= tol:
        pts.pop()
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if abs(cross) < tol * max(math.dist(a, b), math.dist(b, c), 1e-9):
                pts.pop(i)
                changed = True
                break
    return pts


def _snap(v: float, grid=0.0001) -> float:
    return round(v / grid) * grid


def load(path_or_dict) -> Building:
    if isinstance(path_or_dict, (str, Path)):
        path = Path(path_or_dict)
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        path, raw = None, path_or_dict
    errs: list[str] = []
    warns: list[str] = []

    if raw.get("schema") != SCHEMA:
        errs.append(f'"schema" must be "{SCHEMA}"')
    units = raw.get("units", "ft")
    if units not in LENGTH:
        errs.append(f'"units" must be one of {sorted(LENGTH)}')
        units = "ft"
    k = LENGTH[units]
    proj = raw.get("project", {})
    # north_arrow_deg: clockwise angle of the plan's north arrow from the top of the sheet.
    # The top of the sheet therefore faces compass bearing (-north_arrow_deg) mod 360.
    try:
        north = (-float(proj.get("north_arrow_deg", 0.0))) % 360.0
    except (TypeError, ValueError):
        errs.append("project.north_arrow_deg must be a number (clockwise angle of the north arrow from sheet-up)")
        north = 0.0
    if "north_arrow_deg" not in proj:
        warns.append("project.north_arrow_deg missing; assuming the north arrow points to the top of the sheet")

    # levels
    levels: list[Level] = []
    for i, lv in enumerate(raw.get("levels", [])):
        try:
            ch = float(lv["ceiling_height"]) * k
            f2f = float(lv.get("floor_to_floor", 0) or 0) * k
            levels.append(Level(lv["id"], lv.get("name", lv["id"]), float(lv.get("elevation", 0.0)) * k, ch, f2f))
        except (KeyError, TypeError, ValueError) as e:
            errs.append(f"levels[{i}]: needs id and numeric ceiling_height ({e})")
    if not levels:
        errs.append("at least one level is required")
    levels.sort(key=lambda lv: lv.elevation)
    for i, lv in enumerate(levels):
        lv.index = i
        if lv.floor_to_floor <= 0:
            if i + 1 < len(levels):
                lv.floor_to_floor = levels[i + 1].elevation - lv.elevation
            else:
                lv.floor_to_floor = lv.ceiling_height
        if lv.floor_to_floor + 1e-6 < lv.ceiling_height:
            errs.append(f"level {lv.id}: floor_to_floor ({lv.floor_to_floor:.2f} m) < ceiling_height ({lv.ceiling_height:.2f} m)")
    level_ids = {lv.id for lv in levels}

    # rooms
    rooms: list[Room] = []
    seen = set()
    for i, r in enumerate(raw.get("rooms", [])):
        rid = r.get("id")
        where = f"rooms[{i}] ({rid})"
        if not rid:
            errs.append(f"rooms[{i}]: missing id")
            continue
        if rid in seen:
            errs.append(f"{where}: duplicate id")
        seen.add(rid)
        if RESERVED_ID.match(str(rid)):
            errs.append(f"{where}: id '{rid}' is reserved for generated zones (attic, attic2, crawlspace); rename it")
        if r.get("level") not in level_ids:
            errs.append(f"{where}: level '{r.get('level')}' not defined")
            continue
        try:
            raw_poly = [(_snap(float(x) * k), _snap(float(y) * k)) for x, y in r["polygon"]]
        except (KeyError, TypeError, ValueError):
            errs.append(f"{where}: polygon must be a list of [x, y] numbers")
            continue
        poly = _ccw(_clean(raw_poly))
        if len(poly) < 3:
            errs.append(f"{where}: polygon needs at least 3 distinct, non-collinear vertices")
            continue
        rtype = r.get("type", "room")
        cond = bool(r.get("conditioned", rtype not in UNCONDITIONED_TYPES))
        if not cond and rtype not in UNCONDITIONED_TYPES:
            errs.append(f"{where}: unconditioned rooms must have type in {sorted(UNCONDITIONED_TYPES)}")
        ceiling = r.get("ceiling", "attic")
        if ceiling not in CEILING_TYPES:
            errs.append(f"{where}: ceiling must be one of {sorted(CEILING_TYPES)}")
        floor = r.get("floor", "auto")
        if floor not in FLOOR_TYPES:
            errs.append(f"{where}: floor must be one of {sorted(FLOOR_TYPES)}")
        lv = next(x for x in levels if x.id == r["level"])
        ch = float(r["ceiling_height"]) * k if r.get("ceiling_height") else lv.ceiling_height
        fe = float(r["floor_elevation"]) * k if r.get("floor_elevation") is not None else None
        room = Room(rid, r.get("name", rid), r["level"], rtype, cond, poly, ceiling, floor, ch, r, fe)
        room.raw_polygon = raw_poly  # as written (m): opening `edge` indices refer to this
        rooms.append(room)
    if not any(r.conditioned for r in rooms):
        errs.append("no conditioned rooms defined")
    room_ids = {r.id for r in rooms}

    # openings
    openings: list[Opening] = []
    oseen = set()
    for i, o in enumerate(raw.get("openings", [])):
        oid = o.get("id", f"opening_{i + 1}")
        where = f"openings[{i}] ({oid})"
        if oid in oseen:
            errs.append(f"{where}: duplicate id")
        oseen.add(oid)
        kind = o.get("kind", "window")
        if kind not in OPENING_KINDS:
            errs.append(f"{where}: kind must be one of {sorted(OPENING_KINDS)}")
            continue
        if o.get("room") not in room_ids:
            errs.append(f"{where}: room '{o.get('room')}' not defined")
            continue
        try:
            w, h = float(o["width"]) * k, float(o["height"]) * k
        except (KeyError, TypeError, ValueError):
            errs.append(f"{where}: width and height are required numbers")
            continue
        at = tuple(float(v) * k for v in o["at"]) if o.get("at") is not None else None
        edge = o.get("edge")
        if kind != "skylight" and at is None and edge is None:
            errs.append(f"{where}: give 'at' ([x, y] near the wall, plan units) or 'edge' (room polygon edge index)")
        sill = float(o["sill"]) * k if o.get("sill") is not None else DEFAULT_SILL[kind]
        openings.append(Opening(oid, o["room"], kind, w, h, sill, at, edge, o.get("label", ""), o))

    foundation = dict(raw.get("foundation") or {})
    if foundation.get("type") not in FOUNDATION_TYPES:
        errs.append(f"foundation.type must be one of {sorted(FOUNDATION_TYPES)} (read it from the foundation plan / "
                    "sections; basements are levels with a negative elevation)")
    for key in ("depth_below_grade",):
        if key in foundation:
            foundation[key] = float(foundation[key]) * k
    attic = dict(raw.get("attic") or {})
    try:
        attic["roof_pitch"] = float(attic.get("roof_pitch"))
        if not 0 <= attic["roof_pitch"] <= 24:
            raise ValueError
    except (TypeError, ValueError):
        errs.append("attic.roof_pitch is required: rise per 12 of run, from the elevations (e.g. 4 for 4:12)")
    if "roof_sections" in attic:
        secs = []
        for s in attic["roof_sections"]:
            s = dict(s)
            s["polygon"] = [(float(x) * k, float(y) * k) for x, y in s["polygon"]]
            secs.append(s)
        attic["roof_sections"] = secs

    if errs:
        raise ModelError(errs)
    b = Building(path, raw, units, proj.get("name", "project"), north, levels, rooms, openings, foundation, attic,
                 warns)
    return b

