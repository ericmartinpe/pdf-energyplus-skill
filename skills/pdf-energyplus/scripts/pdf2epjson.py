# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy>=1.26",
#   "shapely>=2.0",
#   "pdfplumber>=0.11",
#   "pypdfium2>=4.30",
#   "pillow>=10",
# ]
# ///
"""pdf2epjson: command-line helpers for the pdf-energyplus skill.

Run with uv (dependencies install automatically):
    uv run scripts/pdf2epjson.py <command> [options]
Run any command with -h for its options.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
for _stream in (sys.stdout, sys.stderr):  # accents in names and help text on Windows consoles
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _pts(s: str):
    return [tuple(float(v) for v in p.split(",")) for p in s.split(";") if p.strip()]


def _cmd_pdf_info(a):
    from pdfgeom import pdfkit

    print(pdfkit.dump(pdfkit.info(a.pdf)))


def _cmd_pdf_render(a):
    from pdfgeom import pdfkit

    crop = tuple(float(v) for v in a.crop.split(",")) if a.crop else None
    print(pdfkit.dump(pdfkit.render(a.pdf, a.page, a.out, dpi=a.dpi, crop=crop, grid=a.grid)))


def _cmd_pdf_vectors(a):
    from pdfgeom import pdfkit

    res = pdfkit.vectors(a.pdf, a.page, min_len=a.min_len)
    Path(a.out).write_text(pdfkit.dump(res), encoding="utf-8")
    print(pdfkit.dump({k: v for k, v in res.items() if k not in ("lines", "words", "curves")} | {"written": a.out}))


def _cmd_pdf_profile(a):
    from pdfgeom import pdfkit

    crop = tuple(float(v) for v in a.crop.split(","))
    print(pdfkit.dump(pdfkit.profile(a.pdf, a.page, crop, a.axis, dpi=a.dpi, min_fraction=a.min_fraction)))


def _cmd_pdf_measure(a):
    from pdfgeom import pdfkit

    (p1, p2) = _pts(a.calib_points)
    s = pdfkit.calibrate(p1, p2, a.calib_length, a.units)
    metres_per_pt = s * (0.3048 if a.units == "ft" else 1.0)
    out = {"units_per_pt": s, "units": a.units,
           "implied_scale": f"1:{metres_per_pt / (0.0254 / 72):.0f} on this page (compare with any printed scale note)",
           "check": "calibrate on the LONGEST dimension string; verify with a second one in the other direction"}
    if a.origin:
        origin = _pts(a.origin)[0]
        out["origin_pt"] = origin
        out["points"] = [{"pt": p, "plan": [round(c, 3) for c in pdfkit.to_plan(p, origin, s)]}
                         for p in _pts(a.points or "")]
    if a.lengths:
        segs = _pts(a.lengths)
        out["lengths"] = [round(math.dist(segs[i], segs[i + 1]) * s, 3) for i in range(0, len(segs) - 1, 2)]
    ok = True
    if a.verify_points and a.verify_length:
        v1, v2 = _pts(a.verify_points)
        s2 = pdfkit.calibrate(v1, v2, a.verify_length, a.units)
        diff = 100 * (s2 - s) / s
        ok = abs(diff) <= 1.0
        out["verify"] = {"units_per_pt": s2, "diff_pct": round(diff, 2), "ok": ok,
                         "note": "within 1 % is expected; more means a distorted scan or a misread dimension"}
    else:
        out["verify"] = "not checked: pass --verify-points/--verify-length (a dimension in the other axis)"
    print(json.dumps(out, indent=2))
    sys.exit(0 if ok else 1)


def _cmd_overlay(a):
    from pdfgeom import pdfkit

    bpath = _building_path(a.project)
    raw = json.loads(bpath.read_text(encoding="utf-8"))
    cals = raw.get("source", {}).get("calibrations") or []
    if not cals:
        raise SystemExit("building.json source.calibrations is empty: add {page, origin_pt, units_per_pt, level}")
    pdf = a.pdf or raw.get("source", {}).get("pdf")
    pdf = str((bpath.parent / pdf).resolve()) if pdf and not Path(pdf).is_absolute() else pdf
    outs = []
    for cal in cals:
        out = bpath.parent / "work" / f"overlay_p{cal['page']}_{cal.get('level', 'all')}.png"
        crop = tuple(float(v) for v in a.crop.split(",")) if a.crop else None
        outs.append(pdfkit.overlay(pdf, cal["page"], raw, cal, str(out), dpi=a.dpi, crop=crop))
    print(json.dumps(outs, indent=2))


def _cmd_preview_png(a):
    from pdfgeom import shots

    import re as _re

    html = Path(a.html) if a.html.endswith(".html") else _building_path(a.html).parent / "work" / "model3d_preview.html"
    m = _re.search(r'"levels":(\[.*?\])', html.read_text(encoding="utf-8"))
    level_ids = [lv["id"] for lv in json.loads(m.group(1))] if m else []
    if a.level:
        level_ids = [a.level]
    mode = f"&mode={a.mode}" if a.mode else ""
    views = [("iso", "view=iso" + mode)] + [(f"top_{lid}", f"view=top&roof=0&level={lid}" + mode) for lid in level_ids]
    pngs = []
    for name, q in views:
        pngs.append(shots.screenshot(html, html.parent / f"{html.stem}_{name}.png", q))
    print(json.dumps({"png": pngs, "note": "look at these, then show them to the user (or have them open the HTML)"
                      if all(pngs) else "no headless Edge/Chrome found, or rendering failed"}, indent=2))


def _building_path(p: str) -> Path:
    path = Path(p)
    return path / "building.json" if path.is_dir() else path


def _cmd_init(a):
    from pdfgeom import project

    print(json.dumps(project.init(Path(a.project), Path(a.pdf) if a.pdf else None, a.name or ""), indent=2))


def _cmd_check(a):
    from pdfgeom import project, viewer
    from pdfgeom.model import ModelError

    try:
        b, geo, rep = project.check(a.project)
    except ModelError as e:
        print(json.dumps({"ok": False, "stage": "model", "errors": e.errors}, indent=2))
        sys.exit(2)
    work = _building_path(a.project).parent / "work"
    html = viewer.build(b, geo, work / "model3d_preview.html",
                        subtitle="PREVIEW before export - confirm the geometry with the user")
    rep["preview_3d"] = html
    rep.pop("info", None) if a.brief else None
    print(json.dumps(rep, indent=2, default=str))
    sys.exit(0 if rep["ok"] else 1)


def _cmd_export(a):
    from pdfgeom import project, viewer
    from pdfgeom.model import ModelError

    try:
        res = project.export(a.project, Path(a.out) if a.out else None, version=a.version, force=a.force)
    except ModelError as e:
        res = {"ok": False, "stage": "model", "errors": e.errors}
    if res.get("ok"):
        b, geo, _ = project.check(a.project)
        res["model3d"] = viewer.build(b, geo, Path(res["epjson"]).parent / "model3d.html",
                                      subtitle="same surfaces as the exported epJSON")
        if a.validate:
            res["validation"] = project.validate(Path(res["epjson"]))
    print(json.dumps(res, indent=2, default=str))
    sys.exit(0 if res.get("ok") and (res.get("validation") or {}).get("ok") is not False else 1)


def _cmd_validate(a):
    from pdfgeom import project

    res = project.validate(Path(a.epjson))
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["ok"] else 1)


def main(argv=None):
    p = argparse.ArgumentParser(prog="pdf2epjson", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project folder, copy + classify the PDF, render pages, write a starter building.json")
    s.add_argument("project")
    s.add_argument("--pdf")
    s.add_argument("--name")
    s.set_defaults(fn=_cmd_init)

    s = sub.add_parser("pdf-info", help="classify pages (vector/raster/mixed), list scale notes and dimension text")
    s.add_argument("pdf")
    s.set_defaults(fn=_cmd_pdf_info)

    s = sub.add_parser("pdf-render", help="render a page or crop to PNG, optionally with a labelled point grid")
    s.add_argument("pdf")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--out", required=True)
    s.add_argument("--dpi", type=int, default=150)
    s.add_argument("--crop", help="x0,y0,x1,y1 in page points (top-left origin)")
    s.add_argument("--grid", type=float, help="grid spacing in page points, labels are page coordinates")
    s.set_defaults(fn=_cmd_pdf_render)

    s = sub.add_parser("pdf-vectors", help="extract line work + positioned words from a vector page to JSON")
    s.add_argument("pdf")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--out", required=True)
    s.add_argument("--min-len", type=float, default=8.0, help="ignore lines shorter than this (points)")
    s.set_defaults(fn=_cmd_pdf_vectors)

    s = sub.add_parser("pdf-profile", help="locate straight dark lines (extension lines, wall faces) in a page crop")
    s.add_argument("pdf")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--crop", required=True, help="x0,y0,x1,y1 in page points: a narrow band across the feature")
    s.add_argument("--axis", default="x", choices=["x", "y"], help="x: vertical lines, y: horizontal lines")
    s.add_argument("--dpi", type=int, default=300)
    s.add_argument("--min-fraction", type=float, default=0.35, help="dark share of the band needed to count as a line")
    s.set_defaults(fn=_cmd_pdf_profile)

    s = sub.add_parser("pdf-measure", help="two-point scale calibration; convert page points to plan coordinates")
    s.add_argument("--calib-points", required=True, help='"x1,y1;x2,y2" page points at the ends of a dimension')
    s.add_argument("--calib-length", required=True, help="real length of that dimension, e.g. 48ft, 12'-6\", 14.6m")
    s.add_argument("--units", default="ft", choices=["ft", "m"])
    s.add_argument("--origin", help='"x,y" page point that becomes plan (0,0)')
    s.add_argument("--points", help='"x,y;x,y;..." page points to convert to plan coordinates')
    s.add_argument("--lengths", help='"x1,y1;x2,y2;x3,y3;x4,y4" pairs of page points to measure')
    s.add_argument("--verify-points", help='"x1,y1;x2,y2" ends of a dimension in the OTHER axis')
    s.add_argument("--verify-length", help="its printed length; exit 1 if the two scales differ by more than 1 %%")
    s.set_defaults(fn=_cmd_pdf_measure)

    s = sub.add_parser("overlay", help="draw the model's rooms/openings over the plan page(s) for visual QA")
    s.add_argument("project", help="project folder or building.json")
    s.add_argument("--pdf", help="override source.pdf")
    s.add_argument("--dpi", type=int, default=200)
    s.add_argument("--crop", help="x0,y0,x1,y1 in page points (default: model extent + margin)")
    s.set_defaults(fn=_cmd_overlay)

    s = sub.add_parser("preview-png", help="render the 3D viewer to PNG (3D + top view) with headless Edge/Chrome")
    s.add_argument("html", help="project folder (uses work/model3d_preview.html) or an .html file (e.g. out/model3d.html)")
    s.add_argument("--level", help="only this level's top view (default: one top view per level)")
    s.add_argument("--mode", choices=["type", "boundary"])
    s.set_defaults(fn=_cmd_preview_png)

    s = sub.add_parser("check", help="validate building.json, run takeoff QA gates, write a 3D preview")
    s.add_argument("project")
    s.add_argument("--brief", action="store_true", help="omit the detailed info block")
    s.set_defaults(fn=_cmd_check)

    s = sub.add_parser("export", help="write the geometry as a minimal EnergyPlus model (.epJSON) + 3D viewer")
    s.add_argument("project")
    s.add_argument("--out", help="output .epJSON path (default: <project>/out/<project>.epJSON)")
    s.add_argument("--version", default="25.2", help="EnergyPlus version written in the Version object")
    s.add_argument("--validate", action="store_true", help="then run EnergyPlus on the file and report errors")
    s.add_argument("--force", action="store_true", help="export despite QA errors (only after the user accepts them)")
    s.set_defaults(fn=_cmd_export)

    s = sub.add_parser("validate", help="run EnergyPlus on an .epJSON and report severe errors and warnings")
    s.add_argument("epjson")
    s.set_defaults(fn=_cmd_validate)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
