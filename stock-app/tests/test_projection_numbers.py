"""Tests for shared brokerage projection number conversion."""

from __future__ import annotations

from decimal import Decimal

from app.brokerages.projections.numbers import number


def test_number_none_returns_none():
    assert number(None) is None


def test_number_decimal_converts_to_float():
    assert number(Decimal("12.34")) == 12.34


def test_number_zero_decimal():
    assert number(Decimal("0")) == 0.0


def test_number_high_precision_matches_float():
    value = Decimal("123.4567890123456789")
    assert number(value) == float(value)
