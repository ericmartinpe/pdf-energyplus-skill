"""Length units. Internally everything is metres."""

from __future__ import annotations

FT = 0.3048
IN = 0.0254
LENGTH = {"m": 1.0, "mm": 0.001, "cm": 0.01, "ft": FT, "in": IN}
M2_PER_FT2 = FT * FT


def m2_to_ft2(a: float) -> float:
    return a / M2_PER_FT2
