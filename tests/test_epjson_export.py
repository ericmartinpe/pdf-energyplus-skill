"""epJSON export and EnergyPlus validation."""

from pathlib import Path

import pytest
from conftest import SKILL, box_building, load_json

from pdfgeom import epjson, geometry, model, project

EXAMPLE = SKILL / "examples" / "two-room" / "building.json"
FIXTURES = SKILL.parents[1] / "tests" / "fixtures"
TWO_STOREY = FIXTURES / "qc_two_storey" / "building.json"
THREE_LEVEL = FIXTURES / "laval" / "building_truth.json"


def box(**over):
    return box_building(**over)


def export(raw):
    b = model.load(raw)
    return epjson.build(b, geometry.build(b))


def assert_consistent(ep):
    surfs = ep["BuildingSurface:Detailed"]
    fens = ep.get("FenestrationSurface:Detailed", {})
    for name, s in {**surfs, **fens}.items():
        assert s["construction_name"] in ep["Construction"], name
    for name, s in surfs.items():
        assert s["zone_name"] in ep["Zone"], name
        if s["outside_boundary_condition"] == "Surface":
            other = surfs[s["outside_boundary_condition_object"]]
            assert other["outside_boundary_condition_object"] == name
            assert other["zone_name"] != s["zone_name"]
            assert other["construction_name"] == s["construction_name"]
    for name, f in fens.items():
        assert f["building_surface_name"] in surfs, name
        if "outside_boundary_condition_object" in f:
            assert fens[f["outside_boundary_condition_object"]]["outside_boundary_condition_object"] == name


def test_example_exports_geometry_only():
    ep, notes = export(load_json(EXAMPLE))
    assert "SimulationControl" not in ep and "SizingPeriod:DesignDay" not in ep
    assert set(ep["Zone"]) == {"living", "bed", "attic"}
    assert_consistent(ep)
    surfs = ep["BuildingSurface:Detailed"]
    partitions = [s for s in surfs.values() if s["outside_boundary_condition"] == "Surface"
                  and s["construction_name"] == "PH wall_interior"]
    assert len(partitions) == 2
    assert any(s["outside_boundary_condition"] == "Ground" for s in surfs.values())
    assert {f["surface_type"] for f in ep["FenestrationSurface:Detailed"].values()} == {"Window", "Door"}


def test_window_needs_no_product():
    ep, _ = export(box(openings=[{"id": "W1", "room": "a", "at": [5, 0], "width": 1.5, "height": 1.2}]))
    assert_consistent(ep)
    assert ep["Construction"]["PH window"]["outside_layer"] in ep["WindowMaterial:SimpleGlazingSystem"]


def test_wrong_schema_is_rejected():
    with pytest.raises(model.ModelError):
        model.load(box(schema="hvacload.building/1"))


@pytest.mark.parametrize("path", [TWO_STOREY, THREE_LEVEL])
def test_stacked_ceilings_close_and_pair(path):
    ep, notes = export(load_json(path))
    assert_consistent(ep)
    surfs = ep["BuildingSurface:Detailed"]
    # every room-to-room ceiling meets the floor above and is paired with it
    adiabatic = [n for n, s in surfs.items() if s["outside_boundary_condition"] == "Adiabatic"]
    assert not adiabatic
    assert any("ceilings under another room raised" in n for n in notes)
    for n, s in surfs.items():
        if s["surface_type"] == "Ceiling" and s["outside_boundary_condition"] == "Surface":
            other = surfs[s["outside_boundary_condition_object"]]
            if other["surface_type"] == "Floor":
                assert {v["vertex_z_coordinate"] for v in s["vertices"]} == \
                       {v["vertex_z_coordinate"] for v in other["vertices"]}


def test_no_collinear_vertices():
    ep, _ = export(load_json(THREE_LEVEL))
    for name, s in ep["BuildingSurface:Detailed"].items():
        v = [(p["vertex_x_coordinate"], p["vertex_y_coordinate"], p["vertex_z_coordinate"]) for p in s["vertices"]]
        assert epjson._drop_collinear(v) == v, name


@pytest.mark.engines
@pytest.mark.skipif(project.energyplus_exe() is None, reason="EnergyPlus not found (set PDF2EPJSON_ENERGYPLUS)")
@pytest.mark.parametrize("path", [EXAMPLE, THREE_LEVEL])
def test_energyplus_accepts_export(tmp_path, path):
    (tmp_path / "building.json").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    res = project.export(tmp_path, force=True)
    assert res["ok"]
    v = project.validate(tmp_path / "out" / f"{tmp_path.name}.epJSON")
    assert v["ok"], v["severe"]
    assert not v["warnings"], v["warnings"]


@pytest.mark.engines
@pytest.mark.skipif(project.energyplus_exe() is None, reason="EnergyPlus not found (set PDF2EPJSON_ENERGYPLUS)")
def test_idf_is_a_lossless_copy_of_the_epjson(tmp_path):
    import json
    import subprocess

    exe = project.energyplus_exe()
    version = project.energyplus_version(exe)
    (tmp_path / "building.json").write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    ep_path = Path(project.export(tmp_path, version=version)["epjson"])
    res = project.to_idf(ep_path)
    assert res["ok"], res
    idf = Path(res["idf"])
    assert idf == ep_path.with_suffix(".idf") and "BuildingSurface:Detailed," in idf.read_text(encoding="utf-8")
    assert sorted(p.name for p in ep_path.parent.iterdir()) == sorted([ep_path.name, idf.name])  # no eplusout.*
    # converting the IDF back gives the same objects
    back = tmp_path / "back"
    subprocess.run([str(exe), "--convert-only", "--output-directory", str(back), str(idf)], capture_output=True)

    def norm(x):
        if isinstance(x, dict):
            return {k.lower(): norm(v) for k, v in x.items()}
        if isinstance(x, list):
            return [norm(v) for v in x]
        return int(x) if isinstance(x, float) and x.is_integer() else x

    assert norm(json.loads((back / ep_path.name).read_text())) == norm(json.loads(ep_path.read_text()))


@pytest.mark.engines
@pytest.mark.skipif(project.energyplus_exe() is None, reason="EnergyPlus not found (set PDF2EPJSON_ENERGYPLUS)")
def test_idf_refuses_a_version_mismatch(tmp_path):
    (tmp_path / "building.json").write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    ep_path = Path(project.export(tmp_path, version="8.9")["epjson"])
    res = project.to_idf(ep_path)
    assert res["ok"] is False and "8.9" in res["error"] and not ep_path.with_suffix(".idf").exists()


def test_idf_without_energyplus(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF2EPJSON_ENERGYPLUS", str(tmp_path / "missing" / "energyplus.exe"))
    (tmp_path / "building.json").write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    ep_path = Path(project.export(tmp_path)["epjson"])
    monkeypatch.setattr(project.shutil, "which", lambda name: None)
    monkeypatch.setattr(project.glob, "glob", lambda pattern: [])
    res = project.to_idf(ep_path)
    assert res["ok"] is None and "not found" in res["error"] and ep_path.exists()
