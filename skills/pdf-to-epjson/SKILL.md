---
name: pdf-to-epjson
description: Use when a user provides house or building plans (PDF, vector or scanned) and wants the building geometry as an EnergyPlus model — zones per room, walls, floors, roofs, windows and doors in .epJSON — as the starting point of an energy model.
---

# Building geometry from plans to epJSON

## Overview

Turn a set of plans into an EnergyPlus geometry model (`.epJSON`):
- one `Zone` per room, plus generated attic and crawlspace zones;
- `BuildingSurface:Detailed` for every wall, floor, ceiling and roof, with boundary conditions (outdoors, ground, or a paired surface in the next zone);
- `FenestrationSurface:Detailed` for every window, glass door, door and skylight;
- placeholder constructions named `PH <category>`, ready to be replaced.

The file has no weather, schedules, loads or HVAC.

**Core principle:** every length must trace to a plan dimension or a user answer, and the user must confirm the geometry before you export it. Scale comes from dimension strings, never from the printed scale note.

All tools: `uv run scripts/pdf2epjson.py <command>` (run from this skill's folder; `-h` on any command).

## Workflow

Each step has a gate. Don't pass a gate without meeting it.

1. **Intake**: `init <project> --pdf plans.pdf`. Use a new, empty project folder. Read `pdf_kind`.
   - If it's **raster or mixed**, tell the user now: the geometry is read visually from images and is less accurate than from a vector/CAD PDF.
   - Ask for what the plans may not show: the north arrow if there is none, the foundation type, ceiling heights and floor elevations if no section gives them.
2. **Takeoff**: follow `reference/plan-takeoff.md`. Write `building.json` (`reference/building-schema.md`):
   - calibrate on dimension strings with a second-axis check (never the printed scale);
   - tile rooms;
   - record dimension chains, stated areas and a `src` for every room and opening.
3. **Geometry QA**:
   - Run `check` and fix every error.
   - Run `overlay` and look at the PNGs yourself.
   - Run `preview-png P` (a 3D view plus one top view per level) and look at the images yourself.
   - Show the user the 3D preview (the PNGs, or `work/model3d_preview.html` opened in their browser), the room list with areas, the area reconciliation and each drawing inconsistency with your proposed resolution.
   - **HARD STOP**: get explicit confirmation of the geometry, then record `source.geometry_confirmed` (date) and each `resolution` / `stated_notes`.
4. **Export**: `export P --validate`.
   - Writes `out/<project>.epJSON` and `out/model3d.html` (the same surfaces).
   - `--validate` runs EnergyPlus on a temporary copy with a placeholder design day, if EnergyPlus is installed. Any `severe` error must be fixed. Explain each remaining warning to the user.
   - Read `notes`: openings raised above grade, partitions left adiabatic, ceilings raised to the floor above.
5. **Deliver**: give the user:
   - the `.epJSON` path and `model3d.html`;
   - the zone list with floor areas;
   - the placeholder constructions to replace (`constructions` in the export output);
   - what the file deliberately leaves out (SimulationControl, run periods, weather, ground temperatures, schedules, loads, HVAC);
   - the raster warning, if it applies.

## Non-negotiables

| Never | Instead |
|---|---|
| Trust a printed scale note ("1/4" = 1'-0"") | Two-axis calibration on overall dimension strings (`pdf-measure --verify-*`) |
| Assume north is up | Read the north arrow, or ask |
| Export before the user confirms the geometry | Hard stop at step 3 |
| Present placeholder constructions as real ones | Say they are `PH <category>` placeholders, only there so the file simulates |
| Hide an EnergyPlus severe error or geometry warning | Fix it, or tell the user what it means |
| Use `examples/` as the project folder | `init` a new folder; the example `building.json` would be kept |

## Quick reference

| Command | Purpose |
|---|---|
| `init P --pdf F` | New project (PDF classified, pages rendered, starter `building.json`) |
| `pdf-info F`, `pdf-render F --page N --crop x0,y0,x1,y1 --grid 10 --dpi 200 --out png` | Classify pages; readable crops with a point grid |
| `pdf-profile F --page N --crop ... --axis x` | Exact positions of extension lines and wall faces on raster pages |
| `pdf-vectors F --page N --out json` | Vector lines and positioned words (vector PDFs) |
| `pdf-measure --calib-points ... --calib-length 48ft --verify-points ... --verify-length 36ft --origin ... --points ...` | Calibration with a second-axis check; page points → plan coordinates |
| `check P`, `overlay P`, `preview-png P` | QA gates, model drawn over the plans, 3D/top-view PNGs |
| `export P [--out F] [--version 25.2] [--validate] [--force]` | Write the `.epJSON` and 3D viewer |
| `validate F.epJSON` | Run EnergyPlus on a copy and report severe errors and warnings |

`validate` finds EnergyPlus in `PDF2EPJSON_ENERGYPLUS` (the path to `energyplus` or `energyplus.exe`), on the PATH, or in the installer's default folder (`C:\EnergyPlusV*`, `/usr/local/EnergyPlus-*`, `/Applications/EnergyPlus-*`). Without it, export still works; say the file wasn't validated.

## Example

`examples/two-room/building.json` is a minimal complete input (two rooms, slab, attic). Copy it into a new folder and run `export` to see the output.
