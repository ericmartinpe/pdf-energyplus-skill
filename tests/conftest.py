import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "pdf-energyplus"
sys.path.insert(0, str(SKILL / "scripts"))


def box_building(**over):
    """A 10 m x 8 m single-storey slab-on-grade house with an attic, north up."""
    b = {
        "schema": "pdf2epjson.building/1", "units": "m",
        "project": {"name": "box", "north_arrow_deg": 0},
        "levels": [{"id": "L1", "elevation": 0.2, "ceiling_height": 2.5}],
        "foundation": {"type": "slab"}, "attic": {"roof_pitch": 6},
        "rooms": [{"id": "a", "level": "L1", "type": "living", "polygon": [[0, 0], [10, 0], [10, 8], [0, 8]]}],
        "openings": [],
    }
    b.update(copy.deepcopy(over))
    return b


@pytest.fixture
def box():
    return box_building()


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))
