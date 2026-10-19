# building.json reference (geometry)

One file per project describes the building's geometry. `pdf2epjson.py` turns it into typed 3D surfaces and writes them as epJSON. Keep every value traceable: give rooms and openings a `src` string that says where the value came from, for example `"p1 plan, dims 12'-0\" x 13'-0\""` or `"user answer 2026-09-30"`.

## Coordinates and units

- `units`: `ft` | `in` | `m` | `mm`. This applies to every length in the file: polygons, heights, elevations, opening sizes and `at` points. The epJSON is always in metres.
- Plan coordinates: **x points to the right of the sheet, y to the top of the sheet**, as drawn. Choose an origin at a building corner, usually the lower-left exterior corner. The epJSON keeps these coordinates (`GlobalGeometryRules` `Relative`) and puts the rotation in `Building` `north_axis`.
- `project.north_arrow_deg` is the clockwise angle of the drawing's north arrow from the top of the sheet. Use 0 if the arrow points straight up, 90 if it points right, and −30 (or 330) if it points up-left.
- z = 0 is finished grade. `level.elevation` is the height of the finished floor above grade. It is negative for basements.

## Top level

```jsonc
{
  "schema": "pdf2epjson.building/1",
  "units": "ft",
  "project": {"name": "...", "north_arrow_deg": 0},
  "source": {...}, "levels": [...], "rooms": [...], "openings": [...],
  "foundation": {...}, "attic": {...},
  "assumptions": [{"item": "...", "value": "...", "basis": "..."}]
}
```

## source (for QA)

```jsonc
"source": {
  "pdf": "plans/house.pdf", "kind": "raster",         // from pdf-info: vector | raster | mixed
  "stated": {"conditioned_area": 1136, "garage_area": 280, "glazing_area": 158},   // plan units², from the plans
  "dimension_chains": [
    {"label": "north overall", "page": 1, "parts": ["8'-0\"", "12'-0\"", "28'-0\""], "total": "48'-0\"",
     "axis": "x", "level": "L1"}                     // axis+level: the model extent must equal the total
  ],
  "calibrations": [{"page": 1, "level": "L1", "origin_pt": [158.4, 420.0], "units_per_pt": 0.158}],
  "stated_notes": {"conditioned_area": "stated area includes the 6'x4' recessed porch"},   // explains a reconciliation gap
  "stated_rooms": {"salon": 26.9, "cuisine": 23.0},    // printed room areas (plan units²), each checked within 2 %
  "stated_levels": {"L1": 83.2},                        // printed conditioned area per level
  "geometry_confirmed": "2026-09-30"          // set only after the user confirms the geometry
}
```

- Add `"resolution": "used the 48'-0\" overall"` to any chain whose parts don't add up, once you and the user have decided.
- Chains without `level` are checked against the lowest above-grade level.
- Chain parts and totals may be numbers (plan units) or strings such as `12'-6"` or `3.6m`.
- `calibrations` drive `pdf2epjson.py overlay`, which draws the model on the page.

## levels

```jsonc
{"id": "L1", "name": "Main floor", "elevation": 2.0, "ceiling_height": 8.0, "floor_to_floor": 9.0}
```

- `floor_to_floor` defaults to the next level's elevation minus this one. On the top level it defaults to the ceiling height.
- Walls of a room with a room above run up to the next floor (the floor framing is included). In the epJSON, the ceiling under that room is raised to the same height so the zone is closed.

## rooms

Each room becomes one EnergyPlus `Zone`, named by its `id`.

```jsonc
{"id": "bed1", "name": "Bedroom #1", "level": "L1", "type": "bedroom",   // ids "attic", "atticN", "crawlspace" are reserved
 "polygon": [[0,0],[12,0],[12,13],[0,13]],
 "ceiling": "attic",          // attic | cathedral | flat_roof  (only where no room is above)
 "floor": "auto",             // auto | slab | crawlspace | exposed (override for this room)
 "ceiling_height": 8.0,       // optional override of the level value
 "floor_elevation": 0.0,      // optional: this room's floor height ABOVE GRADE (plan units), e.g. a garage slab
 "src": "p1 plan"}
```

- **Tile the footprint.** Rooms on a level must not overlap and should leave no gaps.
  - Draw exterior edges on the **outside face of the exterior walls**, which matches the plan's overall dimensions and the stated gross areas.
  - Draw shared edges on partition centrelines.
  - Coincident edges are detected automatically and become interzone surface pairs.
- `type` values:
  - conditioned: `bedroom`, `bath`, `kitchen`, `living`, `family`, `dining`, `hall`, `entry`, `closet`, `laundry`, `utility`, `office`, `stair`, `room`;
  - unconditioned: `garage`, `basement_unconditioned` (these default to `conditioned: false`, which sets the zone's `part_of_total_floor_area` to No).
- At least one room must be conditioned.
- Open-plan spaces can be one room or several.
- Stairs: model the stair footprint as a room on each level it occupies, or merge it into the hall.

## openings

```jsonc
{"id": "W3", "room": "living", "kind": "window", "at": [23.2, 0], "width": 3, "height": 4, "sill": 3,
 "label": "3040", "src": "p1 plan tag 3040; p2 south elevation"}
```

- `kind`: `window` | `glass_door` | `door` | `skylight`. These become `FenestrationSurface:Detailed` of type Window, GlassDoor, Door and Window (on the roof).
- `at` is a plan point near the opening's centre on the wall. It snaps to the nearest exterior or garage wall of that room within 1 m. Alternatively give `edge`: the index of the room polygon edge **as written** (edge i runs from vertex i to vertex i+1).
- A door between the house and the garage belongs to the house room. If you enter it on the garage, it is moved to the house side automatically. The epJSON gets the door on both sides of the interzone wall.
- An opening can't straddle two rooms. Give it to the room that holds most of it.
- `sill` is measured from the room floor. It defaults to 3 ft for windows and 0 for doors.
- US window tags such as `4030` mean **width first**: 4'-0" wide × 3'-0" high.
- Skylights need a room with a `cathedral` or `flat_roof` ceiling.
- `product` is not needed. Every opening gets the placeholder construction of its kind.

## foundation and attic

```jsonc
"foundation": {"type": "crawlspace", "depth_below_grade": 0.5},   // slab | crawlspace | exposed | basement
"attic": {"roof_pitch": 4,
          "roof_sections": [{"polygon": [[0,0],[48,0],[48,24],[0,24]], "type": "gable", "ridge": "x"}]}
```

- The foundation applies under any floor that has no room below it and is within 1.5 m of grade. Higher uncovered floors are exposed floors (cantilevers), and `check` warns about them.
- `foundation.type` is required: there is no default. Read it from the foundation plan. Use `basement` when the lowest level is a full basement; its floors are slabs either way.
- **Basements are levels**, not a foundation type. Put their rooms on a level with a negative elevation. Their exterior walls are split at grade in the epJSON: Outdoors above, Ground below.
- A crawlspace becomes its own zone (`crawlspace`), running from `-depth_below_grade` up to the lowest level's elevation.
- `roof_pitch` is the rise per 12 run. It is required: there is no default.
- `roof_sections` is optional:
  - if it is omitted, the attic footprint is split into rectangles, each with a hip roof;
  - `type` can be `hip`, `gable`, `shed` or `flat`;
  - `ridge` is `x` or `y` (for a shed, the slope rises toward +y or +x).
- Sections are rectangles; valleys aren't modelled. For L-shaped houses, hips give the cleanest closed attic. With gables, a gable end shared with another section is left open.
- Ceilings under the attic at different heights get separate attic zones (`attic`, `attic2`, …), each with its own roof.

## How it maps to epJSON

| building.json | epJSON |
|---|---|
| `project.name`, `north_arrow_deg` | `Building` name and `north_axis` |
| room | `Zone` (volume and floor area calculated by EnergyPlus) |
| generated attic and crawlspace | `Zone` `attic`, `attic2`, …, `crawlspace` |
| exterior wall, roof, exposed floor | `BuildingSurface:Detailed`, Outdoors |
| slab, below-grade part of a foundation wall | `BuildingSurface:Detailed`, Ground |
| shared wall, floor/ceiling between rooms, walls and ceilings to garage, attic or crawlspace | `BuildingSurface:Detailed` pairs, Surface (each names the other) |
| opening | `FenestrationSurface:Detailed`, mirrored onto the partner surface when its wall is interzone |
| surface category | placeholder `Construction` `PH <category>` |

Surface names are the generated ids (`living_wall1`, `bed_slab1`, `attic_roof2`, …). Foundation walls get `_ag` and `_bg` suffixes for the parts above and below grade.

Placeholder constructions are symmetric (gypsum, a no-mass core, gypsum; doors and glazing a single layer), so both sides of an interzone pair can use the same one. Their values only let the file simulate. Replace each `PH <category>` with a real construction, for example `PH wall_exterior`, `PH slab`, `PH ceiling_attic` or `PH window`.

The epJSON has no `SimulationControl`, run periods, weather, ground temperatures, schedules, loads or HVAC. Add the ones your simulation needs.
