"""Synthetic coverage for the RSI/SuperTrend Pine replication.

No test opens a socket. No test uses 2022-2025 strategy results as expected
values. TradingView export comparison is skipped when the fixture is absent.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from studies.rsi_supertrend import emulator as emulator_mod
from studies.rsi_supertrend.emulator import emulate_symbol
from studies.rsi_supertrend.pine import (
    PINE_SHA256,
    pine_atr,
    pine_rma,
    pine_rsi,
    pine_true_range,
    special_buy_signals,
)
from studies.rsi_supertrend.study import (
    CONFIG_PATH,
    FROZEN_CONFIG,
    SOURCE_PATH,
    TV_FIXTURE_PATH,
    enforce_holdout_guard,
    load_config,
    run_cohort,
    sha256_file,
    verify_source_hash,
)
from utilities.indicators.ta import compute_atr


def _cfg(**overrides) -> dict:
    cfg = {**FROZEN_CONFIG, "inference": dict(FROZEN_CONFIG["inference"])}
    cfg.update(overrides)
    return cfg


def _bars(n: int, start: str = "2010-01-04") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n)
    close = np.linspace(100.0, 110.0, n)
    return pd.DataFrame({
        "date": dates,
        "open": close + 0.25,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "adj_close": close,
        "volume": np.full(n, 1_000_000.0),
    })


def _write_cache(root: Path, symbol: str, dates: pd.DatetimeIndex,
                 closes: np.ndarray) -> None:
    for year in sorted(set(dates.year)):
        mask = dates.year == year
        lines = []
        for date, close in zip(dates[mask], closes[mask]):
            lines.append(
                f"{date.strftime('%m-%d-%Y')},{close},{close},{close},{close},{close},1000000")
        year_dir = root / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        (year_dir / f"{symbol}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_pasted_source_sha256_matches_runtime_digest():
    assert SOURCE_PATH.is_file()
    digest = sha256_file(SOURCE_PATH)
    assert digest == PINE_SHA256
    assert verify_source_hash() == digest


def test_frozen_config_file_matches_in_code_protocol():
    assert load_config() == FROZEN_CONFIG


def test_config_drift_fails_closed(tmp_path):
    text = CONFIG_PATH.read_text(encoding="utf-8")
    path = tmp_path / "changed.yaml"
    path.write_text(text.replace("st_factor: 2.5", "st_factor: 3.0"), encoding="utf-8")
    try:
        load_config(path)
    except ValueError as exc:
        assert "drifted" in str(exc)
    else:
        raise AssertionError("a changed frozen config must be rejected")


def test_rma_seeds_with_sma_then_wilder():
    values = np.arange(1.0, 21.0)
    length = 10
    out = pine_rma(values, length)
    seed = float(values[:length].mean())
    assert out[length - 1] == pytest.approx(seed)
    prev = seed
    for j in range(length, len(values)):
        prev = (values[j] + (length - 1) * prev) / length
        assert out[j] == pytest.approx(prev)
    assert np.isnan(out[: length - 1]).all()


def test_atr_bar0_is_high_minus_low_unlike_shared_atr():
    frame = pd.DataFrame({
        "high": [12.0, 13.0, 11.5],
        "low": [10.0, 10.5, 9.0],
        "close": [11.0, 12.5, 10.0],
    })
    tr = pine_true_range(
        frame["high"].to_numpy(), frame["low"].to_numpy(), frame["close"].to_numpy())
    assert tr[0] == pytest.approx(2.0)
    shared = compute_atr(frame, window=2)
    assert np.isnan(shared.iloc[0])
    pine = pine_atr(
        frame["high"].to_numpy(), frame["low"].to_numpy(),
        frame["close"].to_numpy(), length=2)
    assert np.isfinite(pine[1])


def test_rsi_uses_rma_gains_and_losses():
    close = np.array(
        [1.0, 2.0, 3.0, 2.5, 4.0, 3.5, 5.0, 6.0, 5.5, 7.0, 8.0, 7.5], dtype=float)
    rsi = pine_rsi(close, length=5)
    assert np.isnan(rsi[:5]).all()
    assert np.isfinite(rsi[5:]).all()
    assert ((rsi[5:] >= 0.0) & (rsi[5:] <= 100.0)).all()


def test_two_cross_state_and_reset_after_special_buy():
    rsi = np.array([40.0, 45.0, 40.0, 45.0, 40.0, 45.0], dtype=float)
    signal = np.array([42.0, 42.0, 42.0, 42.0, 42.0, 42.0], dtype=float)
    out = special_buy_signals(rsi, signal, trigger=50.0, target=2)
    assert list(out) == [False, False, False, True, False, False]
    # A later pair of crosses can fire again after the reset.
    assert out[3] and not out[5]


def test_rsi_equals_50_neither_resets_nor_increments():
    rsi = np.array([40.0, 45.0, 50.0, 40.0, 45.0], dtype=float)
    signal = np.array([42.0, 42.0, 48.0, 42.0, 42.0], dtype=float)
    out = special_buy_signals(rsi, signal, trigger=50.0, target=2)
    assert list(out) == [False, False, False, False, True]


def test_stale_first_cross_remains_until_second():
    rsi = np.array([40.0, 45.0, 44.0, 41.0, 45.0], dtype=float)
    signal = np.array([42.0, 42.0, 50.0, 50.0, 42.0], dtype=float)
    out = special_buy_signals(rsi, signal, trigger=50.0, target=2)
    assert list(out) == [False, False, False, False, True]


def test_rsi_above_50_clears_a_stale_first_cross():
    rsi = np.array([40.0, 45.0, 55.0, 40.0, 45.0], dtype=float)
    signal = np.array([42.0, 42.0, 50.0, 42.0, 42.0], dtype=float)
    out = special_buy_signals(rsi, signal, trigger=50.0, target=2)
    assert not out.any()


def _emulate(monkeypatch, frame, special, direction, start=None, end=None):
    n = len(frame)
    special = np.asarray(special, dtype=bool)
    direction = np.asarray(direction, dtype=float)
    monkeypatch.setattr(
        emulator_mod, "special_buy_signals", lambda *_args, **_kwargs: special)
    monkeypatch.setattr(
        emulator_mod, "pine_supertrend",
        lambda *_args, **_kwargs: (np.zeros(n), direction))
    start = start or frame["date"].iloc[0]
    end = end or frame["date"].iloc[-1]
    return emulate_symbol(frame, _cfg(), start, end, "TEST")


def test_next_open_fill_uses_signal_close_for_quantity(monkeypatch):
    frame = _bars(8)
    special = np.zeros(8, dtype=bool)
    special[2] = True
    result = _emulate(monkeypatch, frame, special, np.full(8, -1.0))
    trade = result.trades[0]
    assert trade["signal_date"] == str(pd.Timestamp(frame["date"].iloc[2]).date())
    assert trade["entry_date"] == str(pd.Timestamp(frame["date"].iloc[3]).date())
    assert trade["entry_price"] == pytest.approx(float(frame["open"].iloc[3]))
    assert trade["shares"] == pytest.approx(10000.0 / float(frame["close"].iloc[2]))


def test_bearish_supertrend_at_entry_does_not_auto_exit(monkeypatch):
    frame = _bars(8)
    special = np.zeros(8, dtype=bool)
    special[2] = True
    result = _emulate(monkeypatch, frame, special, np.full(8, 1.0))
    assert len(result.trades) == 1
    assert result.trades[0]["open_at_cutoff"]
    assert result.trades[0]["direction_at_entry"] == pytest.approx(1.0)


def test_repeated_entries_ignored_while_long(monkeypatch):
    frame = _bars(10)
    special = np.zeros(10, dtype=bool)
    special[2] = True
    special[5] = True
    result = _emulate(monkeypatch, frame, special, np.full(10, -1.0))
    assert result.special_buy_count == 2
    assert result.ignored_repeat_entries == 1
    assert len(result.trades) == 1
    assert result.trades[0]["open_at_cutoff"]


def test_same_bar_buy_and_st_sell_while_flat_still_enters(monkeypatch):
    frame = _bars(8)
    special = np.zeros(8, dtype=bool)
    special[2] = True
    direction = np.full(8, -1.0)
    direction[2] = 1.0
    result = _emulate(monkeypatch, frame, special, direction)
    assert result.trades[0]["entry_date"] == str(pd.Timestamp(frame["date"].iloc[3]).date())
    assert result.trades[0]["open_at_cutoff"]


def test_same_bar_buy_and_st_sell_while_long_closes_and_ignores_add(monkeypatch):
    frame = _bars(10)
    special = np.zeros(10, dtype=bool)
    special[2] = True
    special[6] = True
    direction = np.full(10, -1.0)
    direction[6] = 1.0
    result = _emulate(monkeypatch, frame, special, direction)
    assert result.ignored_repeat_entries == 1
    closed = [row for row in result.trades if not row["open_at_cutoff"]]
    assert len(closed) == 1
    assert closed[0]["exit_date"] == str(pd.Timestamp(frame["date"].iloc[7]).date())
    assert closed[0]["exit_reason"] == "supertrend_flip"


def test_cutoff_marks_open_position_at_final_close(monkeypatch):
    frame = _bars(6)
    special = np.zeros(6, dtype=bool)
    special[3] = True
    result = _emulate(monkeypatch, frame, special, np.full(6, -1.0))
    assert result.trades[0]["open_at_cutoff"]
    assert result.trades[0]["exit_reason"] == "open_at_cutoff"
    assert result.trades[0]["exit_price"] == pytest.approx(float(frame["close"].iloc[-1]))


def test_missing_primary_data_fails_closed(tmp_path):
    dates = pd.bdate_range("2010-01-04", periods=80)
    closes = np.linspace(100.0, 120.0, len(dates))
    _write_cache(tmp_path, "SPY", dates, closes)
    cfg = _cfg(development_start="2010-01-04", development_end="2010-04-23")
    try:
        run_cohort(tmp_path, cfg, ["SPY", "XLK"], "development", fail_closed=True)
    except ValueError as exc:
        assert "XLK" in str(exc)
    else:
        raise AssertionError("missing primary ETF history must fail closed")


def test_corrupt_primary_data_fails_closed(tmp_path):
    dates = pd.bdate_range("2010-01-04", periods=40)
    closes = np.linspace(100.0, 110.0, len(dates))
    _write_cache(tmp_path, "SPY", dates, closes)
    _write_cache(tmp_path, "XLK", dates, closes)
    bad = tmp_path / "2010" / "XLK.txt"
    bad.write_text("01-04-2010,10,9,11,10,10,1000\n", encoding="utf-8")
    cfg = _cfg(development_start="2010-01-04", development_end="2010-02-26")
    try:
        run_cohort(tmp_path, cfg, ["SPY", "XLK"], "development", fail_closed=True)
    except ValueError as exc:
        assert "XLK" in str(exc)
    else:
        raise AssertionError("corrupt primary ETF history must fail closed")


def test_holdout_guard_requires_confirm_flag(tmp_path):
    try:
        enforce_holdout_guard(_cfg(), tmp_path, confirm=False)
    except ValueError as exc:
        assert "--confirm-holdout" in str(exc)
    else:
        raise AssertionError("holdout without --confirm-holdout must fail")


def test_holdout_guard_requires_clean_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "studies.rsi_supertrend.study.git_is_dirty", lambda: True)
    try:
        enforce_holdout_guard(_cfg(), tmp_path, confirm=True)
    except ValueError as exc:
        assert "clean committed worktree" in str(exc)
    else:
        raise AssertionError("dirty worktree must block holdout")


def test_holdout_guard_rejects_prior_holdout_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "studies.rsi_supertrend.study.git_is_dirty", lambda: False)
    prior = tmp_path / "holdout" / "already-ran"
    prior.mkdir(parents=True)
    try:
        enforce_holdout_guard(_cfg(), tmp_path, confirm=True)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("a prior holdout directory must block a second run")


def test_tradingview_parity_skips_when_fixture_missing():
    if TV_FIXTURE_PATH.is_file():
        pytest.fail("unexpected TradingView fixture; wire a real comparison")
    pytest.skip(
        "missing external fixture: TradingView development export is not in the repository")
