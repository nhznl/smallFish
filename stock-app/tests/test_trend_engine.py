"""Deterministic tests for trend analysis and numeric helper behavior."""

from __future__ import annotations

from datetime import datetime, timedelta

from app import serializers
from app import trend_engine as te
from app.stock_model import (
    BEARISH_REVERSAL,
    BEARISH_CONTINUATION,
    BULLISH_CONTINUATION,
    BULLISH_REVERSAL,
    Stock,
)


def _rising_series(n: int) -> list[te.Daily]:
    """n consecutive weekdays, close rising 1.00/day from 100, tight bars."""
    out = []
    d = datetime(2025, 1, 1)
    close = 100.0
    while len(out) < n:
        if d.weekday() < 5:  # weekday
            c = te.f32(close)
            out.append(te.Daily(d, te.f32(close - 0.2), te.f32(close + 0.3),
                                 te.f32(close - 0.3), c, 1_000_000 + len(out) * 1000))
            close += 1.0
        d += timedelta(days=1)
    return out


def _confirmed_reversal_candidate(source_direction: str, target_direction: int) -> Stock:
    """Build a deterministic scanner candidate and control only the final turn."""
    stock = Stock.build("TURN", _rising_series(80))
    trend = stock.advanced_trend_with_volume
    assert trend is not None
    trend.direction = source_direction
    trend.reversal_signal = True
    trend.no_of_reversal_signals = 2
    stock.freshness_status = "FRESH"
    stock.volume_ratio = 1.2
    stock.rsi_change_five_day = 5.0 * target_direction
    stock.macd_histogram_change = 0.5 * target_direction

    latest = stock.dailies[-1]
    prior = stock.dailies[-2]
    prior_five_average = sum(bar.close for bar in stock.dailies[-6:-1]) / 5
    if target_direction > 0:
        latest.close = max(prior.high, prior_five_average) + 2.0
        latest.open = latest.close - 1.0
        latest.high = latest.close + 0.5
        latest.low = latest.close - 1.5
    else:
        latest.close = min(prior.low, prior_five_average) - 2.0
        latest.open = latest.close + 1.0
        latest.high = latest.close + 1.5
        latest.low = latest.close - 0.5
    return stock


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #


def test_round_half_up():
    assert te.round_half_up(0.5) == 1
    assert te.round_half_up(1.4) == 1
    assert te.round_half_up(-0.5) == 0
    assert te.round_float32_half_up(2.675 * 100) in {267, 268}


def test_float32_json_uses_concise_representation():
    assert serializers.float32_json(te.f32(328.01)) == 328.01
    assert serializers.float32_json(te.f32(293.91)) == 293.91


def test_enums_thresholds():
    assert te.trend_strength_from_value(0.39) == "WEAK"
    assert te.trend_strength_from_value(0.4) == "MODERATE"
    assert te.trend_strength_from_value(0.7) == "STRONG"


# --------------------------------------------------------------------------- #
# engine + Stock                                                              #
# --------------------------------------------------------------------------- #


def test_rising_series_classifies_up_and_overbought():
    data = _rising_series(80)
    atv = te.analyze_trend_with_volume(data)
    assert atv is not None
    assert atv.direction == te.UP
    # a monotonic rise pins RSI at the top of the range
    assert atv.rsi == 100.0
    assert atv.no_of_reversal_signals >= 0


def test_short_history_has_no_trend():
    data = _rising_series(10)  # < 20 bars
    s = Stock.build("TINY", data)
    assert s.advanced_trend_with_volume is None
    assert s.is_bullish() is False
    assert s.signal() == "NEUTRAL"


def test_stock_detail_dict_contract_is_focused():
    s = Stock.build("RISE", _rising_series(80))
    d = serializers.stock_detail_dict(s)
    expected_keys = {
        "code", "type", "lastTradeStats", "yearToDate", "midPointToDate", "fiveWeeksToDate",
        "fiveDaysToDate", "yearlySlopes", "recentWeeks", "dailyBars", "atrPct", "volumeRatio",
        "relativeStrengthSpyOneMonth", "setup", "setupScore", "trendRsi",
    }
    assert set(d) == expected_keys
    assert len(d["recentWeeks"]) == 5
    assert set(d["lastTradeStats"]) == {"tradeDate", "open", "high", "low", "close", "volume"}
    assert all(set(week) == {"startDate", "endDate", "avgClose"} for week in d["recentWeeks"])
    # Daily closes back the detail price chart; it scopes its own range client-side.
    assert all(set(bar) == {"tradeDate", "close", "volume"} for bar in d["dailyBars"])
    assert len(d["dailyBars"]) == 80


def test_strategy_stock_dict_uses_a_rolling_252_session_range():
    s = Stock.build("RANGE", _rising_series(260))
    d = serializers.strategy_stock_dict(s)

    assert d["fiftyTwoWeekLow"] is not None
    assert d["fiftyTwoWeekHigh"] is not None
    assert d["fiftyTwoWeekLow"] < d["fiftyTwoWeekHigh"]
    assert 99 < d["fiftyTwoWeekPosition"] < 100


def test_strategy_stock_dict_contract_is_focused():
    s = Stock.build("RISE", _rising_series(60))
    d = serializers.strategy_stock_dict(s)
    assert set(d) == {
        "code", "type", "lastTradeStats", "fiftyTwoWeekLow", "fiftyTwoWeekHigh",
        "fiftyTwoWeekPosition", "signal", "strategyReport",
    }
    assert set(d["lastTradeStats"]) == {"close"}


def test_stock_type_defaults_and_normalizes_supported_types():
    assert Stock.build("DEFAULT", []).type == "STOCK"
    assert Stock.build("FUND", [], stock_type="etf").type == "ETF"
    assert Stock.build("MUTUAL", [], stock_type="MF").type == "MF"
    assert Stock.build("UNKNOWN", [], stock_type="crypto").type == "STOCK"


def test_scanner_metrics_and_setup_are_explicit():
    s = Stock.build("RISE", _rising_series(80))
    s.apply_scanner_context(s, s.last_trade.date)
    payload = serializers.momentum_stock_dict(s)

    assert payload["setup"] == "BULLISH_CONTINUATION"
    assert payload["freshnessStatus"] == "FRESH"
    assert payload["relativeStrengthSpyOneMonth"] == 0.0
    assert payload["fiveDaysToDate"]["gainLoss"] > 0
    assert payload["atrPct"] > 0
    assert payload["averageDollarVolume20"] > 0
    assert sum(payload["setupScoreComponents"].values()) == payload["setupScore"]
    assert 0 <= payload["setupScore"] <= 100


def test_reversal_setup_names_the_source_trend_and_is_mutually_exclusive():
    bearish_turn = _confirmed_reversal_candidate(te.DOWN, 1)
    bullish_turn = _confirmed_reversal_candidate(te.UP, -1)

    assert bearish_turn.scanner_setup() == BEARISH_REVERSAL
    assert bearish_turn.is_bearish() is True
    assert bearish_turn.is_bullish() is False
    assert bullish_turn.scanner_setup() == BULLISH_REVERSAL
    assert bullish_turn.is_bullish() is True
    assert bullish_turn.is_bearish() is False


def test_unconfirmed_reversal_remains_in_source_trend_with_score_penalty():
    candidate = _confirmed_reversal_candidate(te.DOWN, 1)
    candidate.volume_ratio = 0.99
    assert candidate.scanner_setup() == BEARISH_CONTINUATION
    assert candidate.has_preliminary_reversal_evidence() is True
    assert candidate.preliminary_reversal_label() == "Possible Bearish Reversal"
    assert candidate.setup_score_components()["preliminaryReversalPenalty"] == -16.0

    candidate.volume_ratio = 1.2
    candidate.freshness_status = "STALE"
    assert candidate.scanner_setup() == BEARISH_CONTINUATION
    assert candidate.has_preliminary_reversal_evidence() is True


def test_preliminary_warning_clears_when_reversal_clues_clear():
    candidate = _confirmed_reversal_candidate(te.UP, -1)
    candidate.macd_histogram_change = 0.1
    assert candidate.scanner_setup() == BULLISH_CONTINUATION
    assert candidate.has_preliminary_reversal_evidence() is True
    assert candidate.setup_score_components()["preliminaryReversalPenalty"] < 0

    candidate.advanced_trend_with_volume.reversal_signal = False
    assert candidate.scanner_setup() == BULLISH_CONTINUATION
    assert candidate.has_preliminary_reversal_evidence() is False
    assert "preliminaryReversalPenalty" not in candidate.setup_score_components()


def test_signal_does_not_prefix_sideways_with_hyphen():
    s = Stock.build("FLAT", [
        te.Daily(bar.date, 100.0, 100.2, 99.8, 100.0, 1_000_000)
        for bar in _rising_series(80)
    ])
    assert s.signal() in {"SIDEWAYS", "SIDEWAYS-REVERSAL"}


def test_non_overlapping_volume_ratio():
    data = _rising_series(23)
    for bar in data[:-3]:
        bar.volume = 100
    for bar in data[-3:]:
        bar.volume = 200
    assert te.calc_volume_ratio(data, 3, 20) == 2.0


def test_wilder_atr_constant_true_range():
    data = [
        te.Daily(bar.date, 100.0, 101.0, 99.0, 100.0, 1_000_000)
        for bar in _rising_series(20)
    ]
    assert te.calc_atr(data, 14) == 2.0
