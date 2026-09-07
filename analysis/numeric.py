"""IEEE-754 single-precision numeric helpers shared across stock analysis.

OHLC values are normalized to single precision before calculations; these
helpers keep rounding and JSON representation stable across the API and UI.
"""

from __future__ import annotations

import math

import numpy as np

__all__ = ["f32", "float32_json", "round_half_up", "round_float32_half_up"]


def f32(x: float) -> float:
    """Return the nearest IEEE-754 single-precision value as a Python float."""
    return float(np.float32(x))


def float32_json(x) -> float:
    """Return a concise JSON-safe representation of a single-precision value."""
    return float(str(np.float32(x)))


def round_half_up(x: float) -> int:
    """Round to the nearest integer, with half values rounded upward."""
    return int(math.floor(x + 0.5))


def round_float32_half_up(x: float) -> int:
    """Round a single-precision value to the nearest integer, half upward."""
    return int(math.floor(f32(f32(x) + np.float32(0.5))))
