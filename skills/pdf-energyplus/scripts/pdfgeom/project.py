"""Project-level commands: init, check, export, validate."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import epjson, geometry, model, pdfkit, qa

STARTER = {
    "schema": model.SCHEMA,
    "units": "ft",
    "project": {"name": "", "north_arrow_deg": 0},
    "source": {"pdf": "", "kind": "", "stated": {}, "dimension_chains": [], "calibrations": []},
    "levels": [],
    "foundation": {"type": None},
    "attic": {"roof_pitch": None},
    "rooms": [],
    "openings": [],
    "assumptions": [],
}


def bpath(project: str | Path) -> Path:
    p = Path(project).resolve()
    return p / "building.json" if p.is_dir() else p


def init(project: Path, pdf: Path | None, name: str) -> dict:
    project.mkdir(parents=True, exist_ok=True)
    (project / "work").mkdir(exist_ok=True)
    out = {"project": str(project)}
    starter = json.loads(json.dumps(STARTER))
    starter["project"]["name"] = name or project.name
    if pdf:
        (project / "plans").mkdir(exist_ok=True)
        dest = project / "plans" / pdf.name
        if pdf.resolve() != dest.resolve():
            shutil.copy2(pdf, dest)
        inf = pdfkit.info(str(dest))
        (project / "work" / "pdf_info.json").write_text(json.dumps(inf, indent=2), encoding="utf-8")
        pages = []
        for pg in inf["pages"]:
            png = project / "work" / "pages" / f"p{pg['page']}.png"
            pdfkit.render(str(dest), pg["page"], str(png), dpi=100)
            pages.append({"page": pg["page"], "kind": pg["kind"], "png": str(png),
                          "main_image_dpi": pg.get("main_image_dpi"), "scale_notes": pg["scale_notes_in_text_layer"]})
        starter["source"]["pdf"] = f"plans/{pdf.name}"
        starter["source"]["kind"] = inf["overall"]
        imperial = sum(len(p["dimension_strings_in_text_layer"]) for p in inf["pages"])
        metric = sum(len(p.get("metric_mm_like_numbers", [])) for p in inf["pages"])
        if metric > 3 * max(imperial, 1):
            starter["units"] = "mm"
        out["units_guess"] = f"{starter['units']} (imperial-looking strings: {imperial}, metric-looking numbers: " \
                             f"{metric}); check on the drawings"
        out.update(pdf_kind=inf["overall"], pages=pages, warning=inf.get("warning"))
    b = project / "building.json"
    if not b.exists():
        b.write_text(json.dumps(starter, indent=2), encoding="utf-8")
        out["building_json"] = str(b) + " (starter written)"
    else:
        out["building_json"] = str(b) + " (exists, not overwritten)"
    return out


def check(project) -> tuple[model.Building, dict, dict]:
    b = model.load(bpath(project))
    geo = geometry.build(b)
    rep = qa.check(b, geo)
    return b, geo, rep


def export(project, out: Path | None = None, version: str = "25.2", force: bool = False) -> dict:
    bp = bpath(project)
    b, geo, rep = check(bp)
    if rep["errors"] and not force:
        return {"ok": False, "stage": "qa", "errors": rep["errors"], "warnings": rep["warnings"],
                "hint": "fix the takeoff (or rerun with --force after the user accepts the errors)"}
    out = out or bp.parent / "out" / f"{bp.parent.name}.epJSON"
    res = {"ok": True, **epjson.write(b, geo, out, version)}
    confirmed = b.raw.get("source", {}).get("geometry_confirmed")
    if not confirmed:
        res["warning"] = "source.geometry_confirmed is not set: the user has not confirmed this geometry"
    res["qa_warnings"] = rep["warnings"]
    res["zones"] = {z: {"name": b.room(z).name if v["kind"] == "room" else z, "floor_area_m2": round(v["floor_area"], 2),
                        "conditioned": bool(v.get("conditioned"))} for z, v in geo["zones"].items()}
    return res


def energyplus_exe() -> Path | None:
    if os.environ.get("PDF2EPJSON_ENERGYPLUS"):
        return Path(os.environ["PDF2EPJSON_ENERGYPLUS"])
    for name in ("energyplus", "energyplus.exe"):
        if shutil.which(name):
            return Path(shutil.which(name))
    # default install folders of the EnergyPlus installers, newest first
    roots = glob.glob(r"C:\EnergyPlusV*") + glob.glob("/usr/local/EnergyPlus-*") + \
        glob.glob("/Applications/EnergyPlus-*")
    for root in sorted(roots, key=_version_key, reverse=True):
        for name in ("energyplus.exe", "energyplus"):
            if (Path(root) / name).exists():
                return Path(root) / name
    return None


def _version_key(path: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", Path(path).name))


# Added to a temporary copy only, so EnergyPlus processes the geometry (enclosure, convexity, interzone
# pairs, subsurface placement) during one short design day. Not written to the exported file.
VALIDATION_OBJECTS = {
    "SimulationControl": {"SimulationControl 1": {
        "do_zone_sizing_calculation": "No", "do_system_sizing_calculation": "No", "do_plant_sizing_calculation": "No",
        "run_simulation_for_sizing_periods": "Yes", "run_simulation_for_weather_file_run_periods": "No"}},
    "Timestep": {"Timestep 1": {"number_of_timesteps_per_hour": 4}},
    "Site:Location": {"Validation site": {"latitude": 40.0, "longitude": -100.0, "time_zone": -7.0, "elevation": 0.0}},
    "SizingPeriod:DesignDay": {"Validation day": {
        "month": 7, "day_of_month": 21, "day_type": "SummerDesignDay", "maximum_dry_bulb_temperature": 30.0,
        "daily_dry_bulb_temperature_range": 10.0, "humidity_condition_type": "WetBulb",
        "wetbulb_or_dewpoint_at_maximum_dry_bulb": 20.0, "barometric_pressure": 101325.0, "wind_speed": 3.0,
        "wind_direction": 180.0, "solar_model_indicator": "ASHRAEClearSky", "sky_clearness": 1.0}},
    "Output:Diagnostics": {"Output:Diagnostics 1": {"diagnostics": [{"key": "DisplayExtraWarnings"}]}},
}


# warnings caused by what the export deliberately leaves out (outputs, ground temperatures, HVAC)
IGNORED_WARNINGS = ("No reporting elements have been requested",
                    "Surfaces with interface to Ground found but no \"Ground Temperatures\" were input",
                    "No Timestep object found")


def _messages(err: str) -> list[tuple[str, str]]:
    """eplusout.err as (level, message) with continuation lines joined."""
    out: list[tuple[str, str]] = []
    for ln in err.splitlines():
        s = ln.strip()
        for tag, level in (("** Severe  **", "severe"), ("**  Fatal  **", "fatal"), ("** Warning **", "warning")):
            if s.startswith(tag):
                out.append((level, s[len(tag):].strip()))
                break
        else:
            if s.startswith("**   ~~~   **") and out:
                out[-1] = (out[-1][0], out[-1][1] + " | " + s[len("**   ~~~   **"):].strip())
    return out


def validate(path: Path) -> dict:
    """Run EnergyPlus on a copy of the file with a temporary design day and report its errors."""
    exe = energyplus_exe()
    if not exe:
        return {"ok": None, "error": "EnergyPlus not found: install it, or set PDF2EPJSON_ENERGYPLUS to energyplus(.exe)"}
    ep = json.loads(path.read_text(encoding="utf-8"))
    for key in ("SimulationControl", "Timestep", "Site:Location", "SizingPeriod:DesignDay", "RunPeriod"):
        ep.pop(key, None)
    ep.update(json.loads(json.dumps(VALIDATION_OBJECTS)))
    with tempfile.TemporaryDirectory() as tmp:
        tmp_in = Path(tmp) / "validate.epJSON"
        tmp_in.write_text(json.dumps(ep), encoding="utf-8")
        t0 = time.time()
        r = subprocess.run([str(exe), "--output-directory", tmp, str(tmp_in)], capture_output=True, text=True,
                           timeout=600)
        err_file = Path(tmp) / "eplusout.err"
        err = err_file.read_text(encoding="utf-8", errors="replace") if err_file.exists() else ""
    msgs = _messages(err)
    severe = [m for lv, m in msgs if lv in ("severe", "fatal")]
    warn = list(dict.fromkeys(m for lv, m in msgs if lv == "warning" and not any(i in m for i in IGNORED_WARNINGS)))
    version = next((m for m in warn if m.startswith("Version: in IDF")), None)
    done = next((ln.strip() for ln in err.splitlines() if "EnergyPlus Completed Successfully" in ln
                 or "EnergyPlus Terminated" in ln), "")
    return {"ok": r.returncode == 0 and not severe, "energyplus": str(exe), "seconds": round(time.time() - t0, 1),
            "summary": done, "severe": severe[:20], "warnings": warn[:40], "n_warnings": len(warn),
            "note": "run on a temporary copy with a placeholder design day; the exported file is unchanged. Ignored: "
                    "missing output requests and ground temperatures (not part of a geometry export)",
            "stdout_tail": "" if r.returncode == 0 else r.stdout[-1500:],
            **({"version_mismatch": f"{version}: re-export with `export --version <this EnergyPlus version>`, "
                                    "or validate with the EnergyPlus version you will simulate with"}
               if version else {})}
