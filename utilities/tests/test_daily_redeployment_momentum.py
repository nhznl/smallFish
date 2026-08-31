"""Offline parity tests for the frozen momentum-v3 replay."""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta
from pathlib import Path

from studies.pre_earnings_momentum.momentum_v3_replay import (
    BULLISH_CONTINUATION,
    BULLISH_REVERSAL,
    DOWN,
    SETUP_SCORE_VERSION,
    UP,
    ReplayStock,
    apply_confirmed_reversal_mutation,
    evaluate_as_of,
    weekday_bars,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "momentum_v3_golden.json"


def _load_golden() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _bars(recipe: dict) -> list:
    return weekday_bars(
        int(recipe["n"]),
        start=datetime.fromisoformat(recipe["start"]),
        close0=float(recipe["close0"]),
        step=float(recipe["step"]),
        volume0=int(recipe["volume0"]),
        kind=str(recipe["kind"]),
    )


def _evaluate_case(case: dict, recipes: dict):
    bars = _bars(recipes[case["recipe"]])
    context = case["context"]
    if context == "self":
        as_of = bars[-1].date
        return evaluate_as_of(bars, as_of=as_of, spy_bars=bars, symbol="CASE")
    if context == "stale":
        as_of = bars[-1].date + timedelta(days=3)
        return evaluate_as_of(bars, as_of=as_of, spy_bars=bars, symbol="CASE")
    if context == "missing_spy":
        return evaluate_as_of(bars, as_of=bars[-1].date, spy_bars=None, symbol="CASE")
    if context == "mutation_bullish_reversal":
        stock = ReplayStock.build("TURN", bars)
        apply_confirmed_reversal_mutation(stock, UP, -1)
        return stock.snapshot(bars[-1].date)
    if context == "mutation_preliminary_bearish":
        stock = ReplayStock.build("TURN", bars)
        apply_confirmed_reversal_mutation(stock, DOWN, 1)
        stock.volume_ratio = 0.99
        return stock.snapshot(bars[-1].date)
    if context == "as_of_index_69":
        as_of = bars[69].date
        return evaluate_as_of(bars, as_of=as_of, spy_bars=bars, symbol="CASE")
    raise AssertionError(f"unknown golden context {context}")


def _assert_case(snapshot, expected: dict) -> None:
    assert snapshot.setup_score_version == SETUP_SCORE_VERSION
    assert snapshot.setup == expected["setup"]
    assert snapshot.setup_score == expected["setup_score"]
    assert snapshot.setup_score_components == expected["setup_score_components"]
    if expected["setup_score_components"]:
        assert snapshot.setup_score == round(sum(snapshot.setup_score_components.values()), 1)
    if "raw_trend_direction" in expected:
        assert snapshot.raw_trend_direction == expected["raw_trend_direction"]
    if "fully_aligned" in expected:
        assert snapshot.fully_aligned == expected["fully_aligned"]
    if "strength" in expected:
        assert snapshot.strength == expected["strength"]
    if "reversal_signal" in expected:
        assert snapshot.reversal_signal == expected["reversal_signal"]
    if "no_of_reversal_signals" in expected:
        assert snapshot.no_of_reversal_signals == expected["no_of_reversal_signals"]
    if "preliminary_reversal" in expected:
        assert snapshot.preliminary_reversal == expected["preliminary_reversal"]
    if "freshness_status" in expected:
        assert snapshot.freshness_status == expected["freshness_status"]
    if "relative_strength_spy_one_month" in expected:
        assert snapshot.relative_strength_spy_one_month == expected["relative_strength_spy_one_month"]
    if "five_day_gain_loss" in expected:
        assert snapshot.five_day_gain_loss == expected["five_day_gain_loss"]
    if "five_week_gain_loss" in expected:
        assert snapshot.five_week_gain_loss == expected["five_week_gain_loss"]


def test_golden_cases_match_canonical_momentum_v3():
    golden = _load_golden()
    assert golden["setup_score_version"] == SETUP_SCORE_VERSION
    recipes = golden["recipes"]
    for name, expected in golden["cases"].items():
        snapshot = _evaluate_case(expected, recipes)
        _assert_case(snapshot, expected)


def test_setup_score_equals_component_sum_where_evaluated():
    golden = _load_golden()
    recipes = golden["recipes"]
    for expected in golden["cases"].values():
        snapshot = _evaluate_case(expected, recipes)
        if snapshot.setup_score_components:
            assert snapshot.setup_score == round(sum(snapshot.setup_score_components.values()), 1)


def test_future_bars_do_not_change_earlier_as_of_result():
    bars = weekday_bars(80, start=datetime(2021, 1, 4))
    as_of = bars[69].date
    truncated = evaluate_as_of(bars[:70], as_of=as_of, spy_bars=bars[:70], symbol="RISE")
    with_future = evaluate_as_of(bars, as_of=as_of, spy_bars=bars, symbol="RISE")
    assert truncated.setup == with_future.setup == BULLISH_CONTINUATION
    assert truncated.setup_score == with_future.setup_score
    assert truncated.setup_score_components == with_future.setup_score_components
    assert truncated.raw_trend_direction == with_future.raw_trend_direction
    assert truncated.as_of.isoformat() == "2021-04-09"


def test_confirmed_bullish_reversal_is_not_a_bearish_trend():
    bars = weekday_bars(80, start=datetime(2021, 1, 4))
    stock = ReplayStock.build("TURN", bars)
    apply_confirmed_reversal_mutation(stock, UP, -1)
    assert stock.scanner_setup() == BULLISH_REVERSAL
    assert stock.is_bullish() is True
    assert stock.is_bearish() is False
    assert stock.advanced_trend_with_volume is not None
    assert stock.advanced_trend_with_volume.direction == UP


def test_replay_module_does_not_import_stock_app():
    source = Path("studies/pre_earnings_momentum/momentum_v3_replay.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".", 1)[0])
    assert "stock-app" not in imported
    assert "app" not in imported
    assert "fastapi" not in imported
