"""Decimal-to-JSON float conversion for brokerage projections."""

from __future__ import annotations

from decimal import Decimal


def number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
