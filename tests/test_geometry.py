"""building.json loading, surface generation and takeoff QA."""

import math

import pytest
from conftest import box_building

from pdfgeom import geometry, model, qa
from pdfgeom.pdfkit import parse_length


def build(raw):
    b = model.load(raw)
    return b, geometry.build(b)


def area(geo, zone, cat):
    return sum(s.area for s in geo["surfaces"] if s.zone == zone and s.category == cat)


# ---------- parsing ----------

@pytest.mark.parametrize("s,unit,expected", [
    ("48'-0\"", "ft", 48.0), ("15'-10\"", "ft", 15 + 10 / 12), ("12'", "ft", 12.0), ("3'6\"", "ft", 3.5),
    ("3.6m", "m", 3.6), ("3600mm", "m", 3.6), ("48ft", "ft", 48.0), ("10ft", "m", 3.048),
])
def test_parse_length(s, unit, expected):
    assert math.isclose(parse_length(s, unit), expected, rel_tol=1e-6)


def test_fractional_inches_and_parse_errors_become_qa_errors(box):
    assert parse_length("10'-6 1/2\"", "ft") == pytest.approx(10 + 6.5 / 12)
    assert parse_length("10'-6½\"", "ft") == pytest.approx(10 + 6.5 / 12)
    box["source"] = {"dimension_chains": [{"label": "bad", "parts": ["ten feet"], "total": "10'"}]}
    b, g = build(box)
    rep = qa.check(b, g)
    assert any("bad" in e for e in rep["errors"])


# ---------- model validation ----------

def test_overlapping_rooms_rejected():
    raw = box_building(rooms=[
        {"id": "a", "level": "L1", "polygon": [[0, 0], [6, 0], [6, 8], [0, 8]]},
        {"id": "b", "level": "L1", "polygon": [[5, 0], [10, 0], [10, 8], [5, 8]]},
    ])
    with pytest.raises(model.ModelError):
        build(raw)


def test_reserved_room_ids_rejected():
    raw = box_building(rooms=[{"id": "attic", "level": "L1", "polygon": [[0, 0], [10, 0], [10, 8], [0, 8]]}])
    with pytest.raises(model.ModelError):
        model.load(raw)


def test_roof_pitch_and_foundation_are_required(box):
    box["attic"] = {}
    box["foundation"] = {}
    with pytest.raises(model.ModelError) as e:
        model.load(box)
    msg = str(e.value)
    assert "roof_pitch" in msg and "foundation.type" in msg


# ---------- geometry ----------

def test_box_surfaces_and_areas(box):
    b, g = build(box)
    assert math.isclose(area(g, "a", "wall_exterior"), 36 * 2.5, rel_tol=1e-6)
    assert math.isclose(area(g, "a", "slab"), 80, rel_tol=1e-6)
    slab = next(s for s in g["surfaces"] if s.category == "slab")
    assert math.isclose(slab.exposed_perimeter, 36, rel_tol=1e-6)
    assert math.isclose(area(g, "attic", "attic_floor"), 80, rel_tol=1e-6)
    # uniform-pitch roof area = footprint / cos(pitch)
    assert math.isclose(area(g, "attic", "roof_attic"), 80 * math.sqrt(1 + 0.5 ** 2), rel_tol=1e-6)


def test_normals_point_outward(box):
    _, g = build(box)
    for s in g["surfaces"]:
        if s.kind == "roof":
            assert s.tilt < 90, s.id
        if s.kind in ("floor",):
            assert s.tilt > 90, s.id
    az = sorted(round(s.azimuth) for s in g["surfaces"] if s.category == "wall_exterior")
    assert az == [0, 90, 180, 270]


def test_north_arrow_rotates_azimuths():
    raw = box_building(project={"name": "rot", "north_arrow_deg": 30})
    _, g = build(raw)
    south = next(s for s in g["surfaces"] if s.category == "wall_exterior" and s.edge == 0)  # sheet-bottom wall
    assert math.isclose(south.azimuth, 150, abs_tol=1e-6)  # arrow 30 deg clockwise -> sheet-down faces 150


def test_adjacency_interior_and_garage():
    raw = box_building(rooms=[
        {"id": "a", "level": "L1", "type": "living", "polygon": [[0, 0], [6, 0], [6, 8], [0, 8]]},
        {"id": "b", "level": "L1", "type": "bedroom", "polygon": [[6, 0], [10, 0], [10, 8], [6, 8]]},
        {"id": "gar", "level": "L1", "type": "garage", "floor_elevation": 0.0, "polygon": [[10, 0], [14, 0], [14, 5], [10, 5]]},
    ])
    b, g = build(raw)
    assert math.isclose(area(g, "a", "wall_interior"), 8 * 2.5, rel_tol=1e-6)
    assert math.isclose(area(g, "b", "wall_to_garage"), 5 * 2.5, rel_tol=1e-6)
    assert math.isclose(area(g, "b", "wall_exterior"), (4 + 3 + 4) * 2.5, rel_tol=1e-6)
    to_gar = [s for s in g["surfaces"] if s.category == "wall_to_garage"]
    assert len(to_gar) == 2
    s1, s2 = to_gar
    assert s1.other_side == s2.id and s2.other_side == s1.id
    assert math.isclose(s1.area, s2.area, rel_tol=1e-9)  # garage side mirrors the conditioned wall


def test_two_storey_basement_categories():
    raw = box_building(
        levels=[{"id": "B", "elevation": -2.0, "ceiling_height": 2.3, "floor_to_floor": 2.6},
                {"id": "L1", "elevation": 0.6, "ceiling_height": 2.5}],
        rooms=[{"id": "bs", "level": "B", "type": "family", "polygon": [[0, 0], [10, 0], [10, 8], [0, 8]]},
               {"id": "a", "level": "L1", "type": "living", "polygon": [[0, 0], [10, 0], [10, 8], [0, 8]]}])
    _, g = build(raw)
    fw = [s for s in g["surfaces"] if s.category == "wall_basement"]
    assert len(fw) == 4 and all(math.isclose(s.depth_below_grade, 2.0) for s in fw)
    assert math.isclose(area(g, "bs", "ceiling_interior"), 80, rel_tol=1e-6)
    assert math.isclose(area(g, "a", "floor_interior"), 80, rel_tol=1e-6)


def test_one_storey_wing_gets_its_own_attic_and_roof():
    """A two-storey block with a one-storey wing: the wing's roof must sit on the wing's ceiling,
    not float at the upper storey's ceiling height."""
    raw = box_building(
        levels=[{"id": "L1", "elevation": 0.3, "ceiling_height": 2.5, "floor_to_floor": 2.8},
                {"id": "L2", "elevation": 3.1, "ceiling_height": 2.4}],
        rooms=[{"id": "main1", "level": "L1", "type": "living", "polygon": [[0, 0], [8, 0], [8, 8], [0, 8]]},
               {"id": "wing", "level": "L1", "type": "family", "polygon": [[8, 0], [14, 0], [14, 6], [8, 6]]},
               {"id": "main2", "level": "L2", "type": "bedroom", "polygon": [[0, 0], [8, 0], [8, 8], [0, 8]]}])
    _, g = build(raw)
    attics = sorted(z for z, v in g["zones"].items() if v["kind"] == "attic")
    assert len(attics) == 2
    wing_ceiling = next(s for s in g["surfaces"] if s.zone == "wing" and s.category == "ceiling_attic")
    wing_attic = wing_ceiling.adjacent_zone
    roof_z = [v[2] for s in g["surfaces"] if s.zone == wing_attic and s.kind == "roof" for v in s.vertices]
    assert min(roof_z) == pytest.approx(0.3 + 2.5)          # eaves at the wing's ceiling
    assert max(roof_z) < 3.1 + 2.4                          # ridge well below the upper storey's ceiling
    for zid in attics:  # each attic's floor area equals the ceilings below it
        floor = sum(s.area for s in g["surfaces"] if s.zone == zid and s.category == "attic_floor")
        assert floor == pytest.approx(g["zones"][zid]["floor_area"])


def test_partial_basement_rest_of_house_on_crawlspace():
    raw = box_building(
        levels=[{"id": "B", "elevation": -2.0, "ceiling_height": 2.2, "floor_to_floor": 2.6},
                {"id": "L1", "elevation": 0.6, "ceiling_height": 2.4}],
        foundation={"type": "crawlspace", "depth_below_grade": 0.3},
        rooms=[{"id": "bs", "level": "B", "type": "family", "polygon": [[0, 0], [6, 0], [6, 8], [0, 8]]},
               {"id": "liv", "level": "L1", "type": "living", "polygon": [[0, 0], [6, 0], [6, 8], [0, 8]]},
               {"id": "bed", "level": "L1", "type": "bedroom", "polygon": [[6, 0], [10, 0], [10, 8], [6, 8]]}])
    _, g = build(raw)
    assert area(g, "bed", "floor_crawlspace") == pytest.approx(32.0)   # not "exposed" to outdoor air
    assert area(g, "bed", "floor_exposed") == 0
    assert g["zones"]["crawlspace"]["volume"] > 0
    crawl_top = max(v[2] for s in g["surfaces"] if s.category == "wall_crawlspace" for v in s.vertices)
    assert crawl_top == pytest.approx(0.6)                            # up to the floor it supports
    # no crawl wall along the basement: only the 3 outer sides (4 + 8 + 4 m) remain
    assert g["zones"]["crawlspace"]["perimeter"] == pytest.approx(16.0)


def test_walls_step_where_only_part_of_the_room_has_a_floor_above():
    raw = box_building(
        levels=[{"id": "L1", "elevation": 0.3, "ceiling_height": 2.4, "floor_to_floor": 2.8},
                {"id": "L2", "elevation": 3.1, "ceiling_height": 2.4}],
        rooms=[{"id": "big", "level": "L1", "type": "living", "polygon": [[0, 0], [10, 0], [10, 8], [0, 8]]},
               {"id": "up", "level": "L2", "type": "bedroom", "polygon": [[0, 0], [3, 0], [3, 8], [0, 8]]}])
    _, g = build(raw)
    # walls under the upper room run to 3.1 m, the rest to the 2.7 m ceiling
    expected = (3 * 2.8 + 7 * 2.4) * 2 + 8 * 2.8 + 8 * 2.4
    assert area(g, "big", "wall_exterior") == pytest.approx(expected, rel=1e-6)


# ---------- openings ----------

def test_opening_placement_and_errors(box):
    box["openings"] = [{"id": "w1", "room": "a", "at": [5, 0], "width": 2, "height": 1.5}]
    _, g = build(box)
    w = next(s for s in g["surfaces"] if s.id == "w1")
    assert math.isclose(w.area, 3.0, rel_tol=1e-6) and round(w.azimuth) == 180
    host = next(s for s in g["surfaces"] if s.id == w.parent)
    assert math.isclose(host.net_area, 10 * 2.5 - 3.0, rel_tol=1e-6)
    box["openings"] = [{"id": "w1", "room": "a", "at": [5, 4], "width": 2, "height": 1.5}]
    with pytest.raises(model.ModelError):
        build(box)  # 4 m from any wall
    box["openings"] = [{"id": "w1", "room": "a", "at": [5, 0], "width": 11, "height": 1.5}]
    with pytest.raises(model.ModelError):
        build(box)  # wider than the wall


def test_edge_index_refers_to_polygon_as_written():
    raw = box_building(rooms=[{"id": "a", "level": "L1", "type": "living",
                               "polygon": [[0, 0], [0, 8], [10, 8], [10, 0]]}])  # clockwise; edge 0 = west wall
    raw["openings"] = [{"id": "w", "room": "a", "edge": 0, "width": 1, "height": 1}]
    _, g = build(raw)
    assert round(next(s for s in g["surfaces"] if s.id == "w").azimuth) == 270
    raw["rooms"][0]["polygon"] = [[0, 0], [5, 0], [10, 0], [10, 8], [0, 8]]     # collinear point kept in numbering
    raw["openings"][0]["edge"] = 2                                               # (10,0)->(10,8): east
    _, g = build(raw)
    assert round(next(s for s in g["surfaces"] if s.id == "w").azimuth) == 90


def test_garage_side_door_is_moved_to_the_house():
    raw = box_building(rooms=[
        {"id": "k", "level": "L1", "type": "kitchen", "polygon": [[0, 0], [6, 0], [6, 8], [0, 8]]},
        {"id": "gar", "level": "L1", "type": "garage", "floor_elevation": 0.0, "polygon": [[6, 0], [12, 0], [12, 8], [6, 8]]}])
    raw["openings"] = [{"id": "d", "room": "gar", "kind": "door", "at": [6, 4], "width": 0.9, "height": 2.0}]
    _, g = build(raw)
    d = next(s for s in g["surfaces"] if s.id == "d")
    assert d.room == "k" and d.category == "door"
    assert next(s for s in g["surfaces"] if s.id == d.parent).room == "k"


# ---------- QA ----------

def test_qa_reconciliation_and_chains(box):
    box["source"] = {"stated": {"conditioned_area": 90.0},
                     "dimension_chains": [{"label": "south", "parts": [6.0, 4.1], "total": 10.0, "axis": "x", "level": "L1"},
                                          {"label": "west", "parts": [8.0], "total": 9.0, "axis": "y", "level": "L1"}]}
    b, g = build(box)
    rep = qa.check(b, g)
    assert any("conditioned_area" in e for e in rep["errors"])  # 80 vs 90 m2 is > 5 %
    assert any("south" in w and "inconsistent" in w for w in rep["warnings"])
    assert any("west" in e and "model spans" in e for e in rep["errors"])


def test_per_room_stated_areas_and_below_grade_window():
    raw = box_building(
        levels=[{"id": "B", "elevation": -2.0, "ceiling_height": 2.3, "floor_to_floor": 2.6},
                {"id": "L1", "elevation": 0.6, "ceiling_height": 2.4}],
        foundation={"type": "basement"},
        rooms=[{"id": "bs", "level": "B", "type": "family", "polygon": [[0, 0], [10, 0], [10, 8], [0, 8]]},
               {"id": "liv", "level": "L1", "type": "living", "polygon": [[0, 0], [10, 0], [10, 8], [0, 8]]}])
    raw["openings"] = [{"id": "well", "room": "bs", "at": [5, 0], "width": 0.8, "height": 0.4, "sill": 1.5}]
    raw["source"] = {"stated_rooms": {"liv": 80.0, "bs": 70.0}, "stated_levels": {"L1": 80.0}}
    b, g = build(raw)
    rep = qa.check(b, g)
    assert any("stated_rooms bs" in w for w in rep["warnings"])
    assert not any("stated_rooms liv" in w for w in rep["warnings"])
    assert any("well" in w and "below grade" in w for w in rep["warnings"])
