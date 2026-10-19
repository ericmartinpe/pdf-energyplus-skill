# pdf-epjson-skill

A [Claude Code](https://claude.com/claude-code) skill that turns building plans (vector or scanned PDF) into **EnergyPlus geometry** (`.epJSON`), checked against the plans and confirmed by you before it is written. It also produces an **interactive 3D viewer of the same surfaces**.

## What it does

```
plans.pdf ──► takeoff (Claude reads the plans; tools calibrate, crop, measure) ──► building.json
                                                                                  │  QA gates + your confirmation
                                                                                  ▼
                               typed surfaces (walls, windows, slabs, attic, crawlspace, garage…)
                                         ┌────────────────┴────────────────┐
                                         ▼                                 ▼
                              model.epJSON (EnergyPlus)          model3d.html (same surfaces)
```

- **Plan reading.**
  - Vector PDFs: lines, curves and dimension text, including metric and vertical text.
  - Scanned PDFs: gridded crops and line finding. A scanned PDF triggers an up-front accuracy warning.
  - Scale is calibrated on dimension strings, with a second-axis check; printed scale notes are never trusted.
- **QA gates.**
  - Dimension chains must add up.
  - Modelled areas are reconciled with the whole-building, per-level and per-room areas printed on the plans.
  - The model is drawn over the plan pages, and 3D/top-view previews are produced.
  - **You confirm the geometry before the epJSON is written.**
- **The epJSON.**
  - one `Zone` per room, plus generated attic and crawlspace zones;
  - `BuildingSurface:Detailed` walls, floors, ceilings and roofs, set to outdoors, ground, or paired with the matching surface of the neighbouring zone; basement walls are split at grade;
  - `FenestrationSurface:Detailed` windows, glass doors, doors and skylights;
  - the building's north axis from the plan's north arrow;
  - placeholder constructions named `PH <category>` (e.g. `PH wall_exterior`, `PH slab`, `PH window`), for you to replace.

  It has no simulation settings, run periods, weather, ground temperatures, schedules, loads or HVAC. Add the ones your simulation needs.
- **Validation.** `export --validate` runs EnergyPlus on a temporary copy with a placeholder design day, so EnergyPlus checks enclosure, convexity, interzone pairs and window placement. The exported file is unchanged.

## Install

Requirements:
- [uv](https://docs.astral.sh/uv/);
- optional: [EnergyPlus](https://energyplus.net/downloads), to validate the output;
- optional: Edge or Chrome, for PNG previews.

Developed and tested on Windows 11.

As a Claude Code plugin:

```
/plugin marketplace add ericmartinpe/pdf-epjson-skill
/plugin install pdf-to-epjson@pdf-epjson-skill
```

Or copy `skills/pdf-to-epjson` into `~/.claude/skills/`.

Then ask, for example: *"Build EnergyPlus geometry from C:\…\house.pdf. Use C:\…\house-geom as the project folder."*

The skill's command-line tools (`uv run scripts/pdf2epjson.py <command>`, `-h` for options) can also be used directly:

| Command | Purpose |
|---|---|
| `init` | New project from a PDF: classify pages, render them, write a starter `building.json` |
| `pdf-info`, `pdf-render`, `pdf-profile`, `pdf-vectors`, `pdf-measure` | Classify pages; gridded crops; find lines on scans; vector geometry and text; calibration with a second-axis check |
| `check`, `overlay`, `preview-png` | QA gates, model drawn over the plans, 3D and top-view PNGs |
| `export [--validate] [--version 25.2]` | Write the `.epJSON` and `model3d.html` |
| `validate` | Run EnergyPlus on an `.epJSON` and report severe errors and warnings |

`validate` finds EnergyPlus in `PDF2EPJSON_ENERGYPLUS`, on the PATH, or in the installer's default folder (`C:\EnergyPlusV*`, `/usr/local/EnergyPlus-*`, `/Applications/EnergyPlus-*`), newest version first.

## Limitations

- Roof sections are rectangles; valleys aren't modelled.
- When a garage slab is lower than the house floor, the strip of wall between the two levels isn't generated. EnergyPlus reports the garage zone as not fully enclosed and computes its volume from floor area and height.
- Cathedral and flat roofs are written flat at ceiling height.
- Opening the 3D viewer needs internet access (three.js from a CDN).

## Development

```bash
uv run --group dev pytest        # unit tests; EnergyPlus validation tests run when EnergyPlus is found
uv run skills/pdf-to-epjson/scripts/pdf2epjson.py export <project-folder> --validate
uv run tests/fixtures/laval/make_plans.py plans.pdf     # regenerate the synthetic vector plan set
```

## Origin and licensing

This project is a fork of [vinnividivicci/hvac-loads-skill](https://github.com/vinnividivicci/hvac-loads-skill) by Vincent Cadoret, which calculates room-by-room heating and cooling loads from house plans. The plan reading, takeoff QA, surface generation and 3D viewer come from that project; the load calculation was removed and an epJSON writer added.

- Python dependencies are all permissive (MIT, BSD or Apache-2.0): numpy, shapely, pdfplumber and pdfminer.six, pypdfium2, pillow. The 3D viewer loads three.js (MIT).
- EnergyPlus is not included or downloaded; `validate` uses your own installation.
- This repository is released under the [MIT License](LICENSE).
