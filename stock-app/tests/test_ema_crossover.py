"""Synthetic daily bars only; crossover evidence never changes setup scores."""

from datetime import datetime, timedelta, timezone

import pytest

from app.ema_crossover import EmaCrossover, ema14_over_20_crossover
from app.serializers import momentum_stock_dict
from app.stock_model import Stock
from app.trend_engine import Daily

NOW = datetime(2026, 8, 25, 21, tzinfo=timezone.utc)


def bars(closes, *, end=None):
    dates = []
    day = end or datetime(2025, 12, 31)
    while len(dates) < len(closes):
        if day.weekday() < 5:
            dates.append(day)
        day -= timedelta(days=1)
    return [Daily(day, price, price + 1, price - 1, price, 1_000_000)
            for day, price in zip(reversed(dates), closes)]


def cross(closes):
    return ema14_over_20_crossover(bars(closes), now=NOW)


def confirmed_closes(age):
    # Keep the gap above $1 even near the 60-session age limit.
    return [100] * 40 + [130 + 2 * index for index in range(age + 1)]


@pytest.mark.parametrize("age", [0, 1, 2, 59, 60])
def test_upward_cross_age_counts_completed_sessions(age):
    result = cross(confirmed_closes(age))
    assert result.status == "ACTIVE"
    assert result.sessions_ago == age
    assert result.as_of_date == datetime(2025, 12, 31).date()


def test_61_sessions_old_is_no_even_if_still_above():
    result = cross(confirmed_closes(61))
    assert result.status == "NONE"
    assert result.sessions_ago is None


def test_reversal_clears_age_and_new_upward_cross_restarts_it():
    assert cross([100] * 40 + [130, 50]).status == "NONE"
    result = cross([100] * 40 + [130, 50, 200])
    assert (result.status, result.sessions_ago) == ("ACTIVE", 0)


def test_flat_and_below_are_no():
    assert cross([100] * 40).status == "NONE"
    assert cross(list(range(140, 100, -1))).status == "NONE"


def test_initial_seed_is_not_a_cross_and_short_history_is_unknown():
    assert cross([]).status == "UNAVAILABLE"
    assert cross([100] * 19 + [110]).status == "UNAVAILABLE"
    # An upward alignment starting before warmup has no eligible crossing.
    assert cross(list(range(100, 180))).status == "UNAVAILABLE"
    assert cross(list(range(100, 181))).status == "NONE"
    assert cross([100] * 20 + [130]).sessions_ago == 0


@pytest.mark.parametrize("price, status", [(126.24, "NONE"), (126.25, "NONE"), (126.26, "ACTIVE")])
def test_gap_must_be_strictly_greater_than_one_dollar(price, status):
    # Flat $100 then $126.25 gives EMA14 = $103.50 and EMA20 = $102.50.
    # All three prices exceed both EMAs; only the gap decides eligibility.
    result = cross([100] * 40 + [price])
    assert result.status == status
    assert result.sessions_ago == (0 if status == "ACTIVE" else None)


def test_small_cross_waits_for_gap_without_resetting_original_age():
    pending = [100] * 40 + [110, 110]
    assert cross(pending).status == "NONE"
    confirmed = cross(pending + [130])
    assert (confirmed.status, confirmed.sessions_ago) == ("ACTIVE", 2)


def test_price_below_both_hides_age_but_does_not_reset_cross():
    rising = [100] * 40 + [140] * 5
    assert cross(rising).sessions_ago == 4
    # EMA14 ~119.05, EMA20 ~114.66: gap still > $1, but close below both.
    assert cross(rising + [110]).status == "NONE"
    restored = cross(rising + [110, 140])
    assert (restored.status, restored.sessions_ago) == ("ACTIVE", 6)


def test_price_between_emas_is_not_above_both():
    # EMA14 ~120.91, EMA20 ~116.00; $124 earlier had qualified.
    rising = [100] * 40 + [140] * 5
    assert cross(rising + [124]).status == "ACTIVE"
    assert cross(rising + [124, 118]).status == "NONE"


def test_price_equal_to_fast_ema_is_not_greater():
    # After the jump, EMA14 = 114 and EMA20 = 110. A close of 114
    # leaves EMA14 exactly 114; the gap still comfortably exceeds $1.
    assert cross([100] * 40 + [205, 114]).status == "NONE"


def test_gap_confirmation_is_not_latched_forever():
    rising = [100] * 40 + [140] * 5
    assert cross(rising).status == "ACTIVE"
    # Both EMAs approach $140 from below. Gap shrinks below $1 without
    # an EMA reversal, so suppress the age until it qualifies again.
    narrowed = rising + [140] * 40
    assert cross(narrowed).status == "NONE"
    restored = cross(narrowed + [180])
    assert (restored.status, restored.sessions_ago) == ("ACTIVE", 45)


def test_pending_cross_that_reverses_is_not_later_reported():
    history = [100] * 40 + [110, 80]
    assert cross(history).status == "NONE"
    assert cross(history + [100]).status == "NONE"
    result = cross(history + [100, 140])
    assert (result.status, result.sessions_ago) == ("ACTIVE", 0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -1])
def test_invalid_prices_are_unavailable_not_skipped(bad):
    data = bars([100] * 40 + [110])
    data[10].close = bad
    assert ema14_over_20_crossover(data, now=NOW).status == "UNAVAILABLE"


def test_duplicate_or_unsorted_sessions_are_unavailable():
    data = bars([100] * 40 + [110])
    data[10].date = data[11].date
    assert ema14_over_20_crossover(data, now=NOW).status == "UNAVAILABLE"
    assert ema14_over_20_crossover(list(reversed(data)), now=NOW).status == "UNAVAILABLE"


@pytest.mark.parametrize("today, before_close, after_close", [
    (datetime(2026, 8, 25), (19, 59), (20, 0)),  # EDT
    (datetime(2026, 1, 6), (20, 59), (21, 0)),   # EST
])
def test_current_day_excluded_until_new_york_close(today, before_close, after_close):
    data = bars([100] * 40 + [130], end=today)
    before = today.replace(hour=before_close[0], minute=before_close[1], tzinfo=timezone.utc)
    after = today.replace(hour=after_close[0], minute=after_close[1], tzinfo=timezone.utc)
    pending = ema14_over_20_crossover(data, now=before)
    assert pending.status == "NONE"
    assert pending.as_of_date == data[-2].date.date()
    completed = ema14_over_20_crossover(data, now=after)
    assert (completed.status, completed.sessions_ago) == ("ACTIVE", 0)
    # A future-dated row cannot change today's result.
    assert ema14_over_20_crossover(
        data + [Daily(today + timedelta(days=1), 50, 51, 49, 50, 1)], now=after,
    ) == completed


def test_weekend_and_holiday_are_not_extra_sessions():
    data = bars([100] * 40 + [130, 130], end=datetime(2026, 7, 6))
    # July 3 was a holiday: last two cached sessions are Thursday and Monday.
    data[-2].date = datetime(2026, 7, 2)
    data.pop(-3)  # remove the now-duplicate Thursday
    result = ema14_over_20_crossover(data, now=NOW)
    assert result.sessions_ago == 1


def test_stock_caches_metric_and_serializer_keeps_scoring_unchanged():
    stock = Stock.build("DEMO", bars([100] * 80 + [130]))
    stock.apply_scanner_context(stock, stock.last_trade.date)
    score_before = stock.setup_score_components()
    payload = momentum_stock_dict(stock)
    assert payload["ema14Over20Cross"] == {
        "status": "ACTIVE", "sessionsAgo": 0, "asOfDate": "2025-12-31",
    }
    stock.ema14_over_20_cross = EmaCrossover("NONE", as_of_date=stock.last_trade.date.date())
    assert stock.setup_score_components() == score_before
    assert momentum_stock_dict(stock)["setupScore"] == payload["setupScore"]


@pytest.mark.parametrize("freshness", ["UNKNOWN", "STALE", "DATE_MISMATCH", "INCOMPLETE"])
def test_stale_or_unknown_rows_do_not_claim_a_recent_cross(freshness):
    stock = Stock.build("DEMO", bars([100] * 40 + [130]))
    stock.freshness_status = freshness
    assert momentum_stock_dict(stock)["ema14Over20Cross"] == {
        "status": "UNAVAILABLE", "sessionsAgo": None, "asOfDate": "2025-12-31",
    }


def test_missing_benchmark_sessions_make_age_unavailable():
    history = bars([100] * 100 + [130, 130])
    benchmark = Stock.build("SPY", history)
    missing = Stock.build("GAP", history[:80] + history[81:])
    assert missing.ema14_over_20_cross.status == "ACTIVE"
    missing.apply_scanner_context(benchmark, benchmark.last_trade.date)
    assert missing.freshness_status == "FRESH"
    assert momentum_stock_dict(missing)["ema14Over20Cross"]["status"] == "UNAVAILABLE"


def test_without_benchmark_age_is_unavailable():
    stock = Stock.build("DEMO", bars([100] * 40 + [130]))
    stock.apply_scanner_context(None, stock.last_trade.date)
    assert stock.ema14_over_20_cross.status == "UNAVAILABLE"
