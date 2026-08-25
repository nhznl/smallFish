"""Synthetic coverage for the RSI/SuperTrend Pine replication.

No test opens a socket. No test uses 2022-2025 strategy results as expected
values. A missing TradingView export fails closed rather than skipping.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from studies.rsi_supertrend import emulator as emulator_mod
from studies.rsi_supertrend.emulator import (
    PINE_IMPLEMENTATION,
    SHARED_TA_IMPLEMENTATION,
    SleeveResult,
    StrategyIndicators,
    compute_strategy_indicators,
    emulate_symbol,
    percent_equity_qty,
)
from studies.rsi_supertrend.pine import (
    pine_atr,
    pine_rma,
    pine_rsi,
    pine_sma,
    pine_supertrend,
    pine_true_range,
    special_buy_signals,
    supertrend_from_atr,
)
from studies.rsi_supertrend.comparison import (
    IMPLEMENTATION_SENSITIVITY_LABEL,
    build_cohort_comparison,
    compare_indicator_series,
    compare_symbol_outcomes,
    label_shared_ta_summary,
    require_complete_paired_result,
)
from studies.rsi_supertrend.study import (
    COMPARISON_TABLES,
    CONFIG_PATH,
    FROZEN_CONFIG,
    OUTPUT_TABLES,
    PINE_INSTRUMENT_COLUMNS,
    SHARED_TA_STOCK_TABLES,
    SHARED_TA_TABLES,
    STOCK_COMPARISON_TABLES,
    TV_FIXTURE_PATH,
    assert_holdout_unclaimed,
    compare_tradingview_export,
    content_addressed_parity_path,
    enforce_holdout_guard,
    load_config,
    main,
    moving_block_summary,
    resolve_export_identity,
    run_cohort,
    run_paired_cohort,
    sha256_file,
    write_parity_report,
    write_run,
)
from utilities.indicators.ta import compute_atr, compute_rsi, compute_sma


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


def test_frozen_config_file_matches_in_code_protocol():
    cfg = load_config()
    assert cfg == FROZEN_CONFIG
    assert cfg["implementation_sensitivity"]["primary"] == PINE_IMPLEMENTATION
    assert cfg["implementation_sensitivity"]["variant"] == SHARED_TA_IMPLEMENTATION


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


def test_pine_supertrend_wrapper_matches_supplied_atr_recurrence():
    frame = _bars(40)
    high = frame["high"].to_numpy(dtype="float64")
    low = frame["low"].to_numpy(dtype="float64")
    close = frame["close"].to_numpy(dtype="float64")
    atr = pine_atr(high, low, close, length=10)
    expected_st, expected_direction = supertrend_from_atr(
        high, low, close, atr, factor=2.5)
    actual_st, actual_direction = pine_supertrend(
        high, low, close, factor=2.5, atr_period=10)
    np.testing.assert_allclose(actual_st, expected_st, equal_nan=True)
    np.testing.assert_allclose(actual_direction, expected_direction, equal_nan=True)


def test_shared_ta_indicator_provider_calls_shared_statistics_directly():
    frame = _bars(60)
    frame["close"] = 100.0 + np.sin(np.arange(len(frame)) / 2.0) * 4.0
    frame["open"] = frame["close"] + 0.1
    frame["high"] = frame["close"] + 1.0
    frame["low"] = frame["close"] - 1.0
    cfg = _cfg()
    actual = compute_strategy_indicators(frame, cfg, SHARED_TA_IMPLEMENTATION)

    expected_rsi = compute_rsi(frame["close"], 10)
    expected_signal = compute_sma(expected_rsi, 10)
    expected_atr = compute_atr(frame[["high", "low", "close"]], 10)
    expected_st, expected_direction = supertrend_from_atr(
        frame["high"].to_numpy(), frame["low"].to_numpy(),
        frame["close"].to_numpy(), expected_atr.to_numpy(), 2.5)

    assert actual.implementation == SHARED_TA_IMPLEMENTATION
    np.testing.assert_allclose(actual.rsi, expected_rsi.to_numpy(), equal_nan=True)
    np.testing.assert_allclose(actual.signal, expected_signal.to_numpy(), equal_nan=True)
    np.testing.assert_allclose(actual.atr, expected_atr.to_numpy(), equal_nan=True)
    np.testing.assert_allclose(actual.supertrend, expected_st, equal_nan=True)
    np.testing.assert_allclose(actual.direction, expected_direction, equal_nan=True)


def test_pine_and_shared_ta_use_same_rsi_sma_but_distinct_atr_seed():
    frame = _bars(60)
    frame["close"] = 100.0 + np.sin(np.arange(len(frame)) / 2.0) * 4.0
    frame["high"] = frame["close"] + 1.0
    frame["low"] = frame["close"] - 1.0
    pine = compute_strategy_indicators(frame, _cfg(), PINE_IMPLEMENTATION)
    shared = compute_strategy_indicators(frame, _cfg(), SHARED_TA_IMPLEMENTATION)

    np.testing.assert_allclose(pine.rsi, shared.rsi, equal_nan=True)
    np.testing.assert_allclose(pine.signal, shared.signal, equal_nan=True)
    assert np.flatnonzero(np.isfinite(pine.atr))[0] == 9
    assert np.flatnonzero(np.isfinite(shared.atr))[0] == 10


def test_unknown_indicator_provider_fails_closed():
    with pytest.raises(ValueError, match="unknown indicator implementation"):
        compute_strategy_indicators(_bars(20), _cfg(), "other")


def test_rsi_uses_rma_gains_and_losses():
    close = np.array(
        [1.0, 2.0, 3.0, 2.5, 4.0, 3.5, 5.0, 6.0, 5.5, 7.0, 8.0, 7.5], dtype=float)
    rsi = pine_rsi(close, length=5)
    assert np.isnan(rsi[:5]).all()
    assert np.isfinite(rsi[5:]).all()
    assert ((rsi[5:] >= 0.0) & (rsi[5:] <= 100.0)).all()


def test_rsi_flat_prices_follow_pine_down_zero_first():
    close = np.full(20, 10.0)
    rsi = pine_rsi(close, length=5)
    defined = rsi[np.isfinite(rsi)]
    assert len(defined)
    assert (defined == 100.0).all()


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
    def fake_indicators(_frame, _cfg, implementation=PINE_IMPLEMENTATION):
        return StrategyIndicators(
            implementation=implementation,
            rsi=np.zeros(n),
            signal=np.zeros(n),
            atr=np.zeros(n),
            supertrend=np.zeros(n),
            direction=direction,
            special_buy=special,
        )

    monkeypatch.setattr(emulator_mod, "compute_strategy_indicators", fake_indicators)
    start = start or frame["date"].iloc[0]
    end = end or frame["date"].iloc[-1]
    return emulate_symbol(frame, _cfg(), start, end, "TEST")


def test_next_open_fill_sizes_from_fill_price(monkeypatch):
    frame = _bars(8)
    frame["close"] = 100.0
    frame["open"] = 100.0
    frame["high"] = 101.0
    frame["low"] = 99.0
    frame.loc[frame.index[3], "open"] = 120.0
    special = np.zeros(8, dtype=bool)
    special[2] = True
    result = _emulate(monkeypatch, frame, special, np.full(8, -1.0))
    trade = result.trades[0]
    assert trade["signal_date"] == str(pd.Timestamp(frame["date"].iloc[2]).date())
    assert trade["entry_date"] == str(pd.Timestamp(frame["date"].iloc[3]).date())
    assert trade["entry_price"] == pytest.approx(120.0)
    assert trade["shares"] == pytest.approx(percent_equity_qty(10000.0, 120.0))
    assert trade["shares"] == 83.0
    assert trade["shares"] * trade["entry_price"] <= 10000.0


def test_percent_equity_qty_floors_to_whole_shares():
    assert percent_equity_qty(10000.0, 120.0) == 83.0
    assert percent_equity_qty(100.0, 120.0) == 0.0
    assert percent_equity_qty(10000.0, 100.0) == 100.0


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


def test_holdout_is_blocked_until_paired_sensitivity_outcomes_exist(tmp_path):
    with pytest.raises(SystemExit, match="paired shared-ta sensitivity outcome runner"):
        main(["--window", "holdout", "--cache-root", str(tmp_path)])


def _tv_identity(**overrides) -> dict:
    identity = {
        "symbol": "SPY",
        "timeframe": "1D",
        "adjustment": "adjusted",
        "session": "NYSE",
    }
    identity.update(overrides)
    return identity


def _approved_parity(tmp_path: Path) -> dict:
    return {
        "ok": True,
        "fixture_sha256": "abc123",
        "study_id": FROZEN_CONFIG["study_id"],
        "export_identity": _tv_identity(),
    }


def test_holdout_guard_requires_confirm_flag(tmp_path):
    report = _approved_parity(tmp_path)
    try:
        enforce_holdout_guard(
            _cfg(), tmp_path, confirm=False, claim_root=tmp_path,
            parity_report=report)
    except ValueError as exc:
        assert "--confirm-holdout" in str(exc)
    else:
        raise AssertionError("holdout without --confirm-holdout must fail")


def test_holdout_guard_requires_clean_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "studies.rsi_supertrend.study.git_is_dirty", lambda: True)
    report = _approved_parity(tmp_path)
    try:
        enforce_holdout_guard(
            _cfg(), tmp_path, confirm=True, claim_root=tmp_path,
            parity_report=report)
    except ValueError as exc:
        assert "clean committed worktree" in str(exc)
    else:
        raise AssertionError("dirty worktree must block holdout")


def test_holdout_guard_requires_parity_report(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "studies.rsi_supertrend.study.git_is_dirty", lambda: False)
    try:
        enforce_holdout_guard(_cfg(), tmp_path, confirm=True, claim_root=tmp_path)
    except ValueError as exc:
        assert "parity report" in str(exc)
    else:
        raise AssertionError("holdout without parity evidence must fail")


def test_holdout_guard_seals_parity_inside_claim_and_is_creation_only(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        "studies.rsi_supertrend.study.git_is_dirty", lambda: False)
    report_a = _approved_parity(tmp_path)
    report_a["fixture_sha256"] = "export-a"
    claim_dir, parity_path = enforce_holdout_guard(
        _cfg(), tmp_path / "out-a", confirm=True, claim_root=tmp_path,
        parity_report=report_a)
    assert parity_path == claim_dir / "tradingview_parity.json"
    sealed = json.loads(parity_path.read_text(encoding="utf-8"))
    assert sealed["fixture_sha256"] == "export-a"
    claim = json.loads((claim_dir / "claim.json").read_text(encoding="utf-8"))
    assert claim["parity_report_sha256"] == sha256_file(parity_path)
    assert claim["export_identity"]["symbol"] == "SPY"

    report_b = _approved_parity(tmp_path)
    report_b["fixture_sha256"] = "export-b"
    try:
        enforce_holdout_guard(
            _cfg(), tmp_path / "out-b", confirm=True, claim_root=tmp_path,
            parity_report=report_b)
    except ValueError as exc:
        assert "already claimed" in str(exc)
    else:
        raise AssertionError("a second holdout caller must not pass the claim")
    # Authoritative claim evidence must still be report A.
    assert json.loads(parity_path.read_text(encoding="utf-8"))["fixture_sha256"] == "export-a"


def test_assert_holdout_unclaimed_before_evidence_write(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "studies.rsi_supertrend.study.git_is_dirty", lambda: False)
    enforce_holdout_guard(
        _cfg(), tmp_path / "out", confirm=True, claim_root=tmp_path,
        parity_report=_approved_parity(tmp_path))
    try:
        assert_holdout_unclaimed(tmp_path)
    except ValueError as exc:
        assert "already claimed" in str(exc)
    else:
        raise AssertionError("existing claim must be detected before evidence writes")


def test_parity_report_is_creation_only(tmp_path):
    path = tmp_path / "parity.json"
    write_parity_report({"ok": True, "fixture_sha256": "a"}, path, exist_ok=False)
    try:
        write_parity_report({"ok": True, "fixture_sha256": "b"}, path, exist_ok=False)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("parity report overwrite must fail closed")
    assert json.loads(path.read_text(encoding="utf-8"))["fixture_sha256"] == "a"


def test_content_addressed_parity_path_uses_fixture_hash(tmp_path):
    path = content_addressed_parity_path(tmp_path, "deadbeef")
    assert path.name == "deadbeef.json"
    assert "parity" in str(path)


def test_moving_block_starts_do_not_wrap(monkeypatch):
    recorded = []
    real_rng = np.random.default_rng

    class Recorder:
        def __init__(self, seed):
            self._rng = real_rng(seed)

        def integers(self, low, high, size=None):
            recorded.append((low, high, size))
            return self._rng.integers(low, high, size=size)

    monkeypatch.setattr(np.random, "default_rng", lambda seed: Recorder(seed))
    cfg = _cfg()
    cfg["inference"]["block_length_sessions"] = 6
    cfg["inference"]["bootstrap_draws"] = 5
    moving_block_summary(list(range(20)), cfg)
    assert recorded
    for low, high, size in recorded:
        assert low == 0
        assert high == 20 - 6 + 1
        assert size == 4


def test_stock_outputs_are_written_separately(tmp_path):
    instruments = pd.DataFrame([{"symbol": "AAA", "special_buy_signals": 0}])
    daily = pd.DataFrame({"date": ["2010-01-04"], "AAA_strategy": [10000.0]})
    trades = pd.DataFrame([{"symbol": "AAA", "entry_date": "2010-01-05"}])
    exclusions = pd.DataFrame([{"symbol": "BBB", "reason": "no bars"}])
    primary = {
        "instruments": pd.DataFrame([{"symbol": "SPY", "special_buy_signals": 1}]),
        "daily": pd.DataFrame({"date": ["2010-01-04"], "SPY_strategy": [10000.0]}),
        "trades": pd.DataFrame([{"symbol": "SPY", "entry_date": "2010-01-05"}]),
        "exclusions": exclusions,
        "summary": {"window": "development", "verdict": None},
        "price_hashes": {"2010/SPY.txt": "abc"},
    }
    stock = {
        "instruments": instruments,
        "daily": daily,
        "trades": trades,
        "exclusions": exclusions,
        "summary": {"window": "development", "verdict": None, "evidence_label": "EXPLORATORY"},
        "price_hashes": {"2010/AAA.txt": "def"},
        "sleeves": {"AAA": object()},
    }
    universe = {
        "primary": ["SPY"],
        "window": "development",
        "stocks": {
            "symbols": ["AAA"],
            "stock_count": 1,
            "evidence_label": "EXPLORATORY",
            "survivorship_bias": True,
        },
    }
    run_dir = tmp_path / "run"
    write_run(run_dir, primary, _cfg(), {"include_stocks": True}, universe,
              stock_result=stock)
    resolved = json.loads((run_dir / "resolved_universe.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert resolved["stocks"]["symbols"] == ["AAA"]
    assert (run_dir / "stock_instrument_summary.csv").is_file()
    assert (run_dir / "stock_daily_equity.csv").is_file()
    assert (run_dir / "stock_trades.csv").is_file()
    assert "AAA" in (run_dir / "stock_instrument_summary.csv").read_text(encoding="utf-8")
    assert "SPY" in (run_dir / "instrument_summary.csv").read_text(encoding="utf-8")
    assert "AAA" not in (run_dir / "instrument_summary.csv").read_text(encoding="utf-8")
    assert manifest["stock_price_sha256"] == {"2010/AAA.txt": "def"}
    assert manifest["stock_evidence_label"] == "EXPLORATORY"
    assert manifest["stock_survivorship_bias"] is True


def _tv_frame(n: int = 60, with_fills: bool = False) -> pd.DataFrame:
    frame = _bars(n)
    close = frame["close"].to_numpy(dtype="float64")
    high = frame["high"].to_numpy(dtype="float64")
    low = frame["low"].to_numpy(dtype="float64")
    rsi = pine_rsi(close, 10)
    signal = pine_sma(rsi, 10)
    _st, direction = pine_supertrend(high, low, close, 2.5, 10)
    special = special_buy_signals(rsi, signal, 50.0, 2)
    out = pd.DataFrame({
        "date": pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d"),
        "open": frame["open"],
        "high": frame["high"],
        "low": frame["low"],
        "close": frame["close"],
        "rsi": rsi,
        "rsi_signal": signal,
        "st_direction": direction,
        "special_buy": special,
    })
    if with_fills:
        entry_day = str(pd.Timestamp(frame["date"].iloc[25]).date())
        exit_day = str(pd.Timestamp(frame["date"].iloc[40]).date())
        out["entry_fill"] = np.nan
        out["exit_fill"] = np.nan
        out.loc[out["date"] == entry_day, "entry_fill"] = float(frame["open"].iloc[25])
        out.loc[out["date"] == exit_day, "exit_fill"] = float(frame["open"].iloc[40])
    return out


def _patch_emulate_with_fills(monkeypatch, frame: pd.DataFrame) -> None:
    from studies.rsi_supertrend import study as study_mod
    from studies.rsi_supertrend.emulator import SleeveResult

    entry_day = str(pd.Timestamp(frame["date"].iloc[25]).date())
    exit_day = str(pd.Timestamp(frame["date"].iloc[40]).date())
    entry_price = float(frame["open"].iloc[25])
    exit_price = float(frame["open"].iloc[40])
    idx = pd.DatetimeIndex(pd.to_datetime(frame["date"]))

    def fake_emulate(_frame, cfg, start, end, symbol):
        return SleeveResult(
            equity=pd.Series(np.full(len(idx), 10000.0), index=idx),
            buy_hold=pd.Series(np.full(len(idx), 10000.0), index=idx),
            trades=[{
                "symbol": symbol,
                "signal_date": str(pd.Timestamp(frame["date"].iloc[24]).date()),
                "entry_date": entry_day,
                "entry_price": entry_price,
                "exit_signal_date": str(pd.Timestamp(frame["date"].iloc[39]).date()),
                "exit_date": exit_day,
                "exit_price": exit_price,
                "direction_at_entry": -1.0,
                "shares": 100.0,
                "return": exit_price / entry_price - 1.0,
                "duration_days": 15,
                "exit_reason": "supertrend_flip",
                "open_at_cutoff": False,
            }],
        )

    monkeypatch.setattr(study_mod, "emulate_symbol", fake_emulate)


def test_tradingview_comparison_fails_closed_when_fixture_missing():
    if TV_FIXTURE_PATH.is_file():
        report = compare_tradingview_export(TV_FIXTURE_PATH)
        assert report["ok"]
        return
    try:
        compare_tradingview_export(TV_FIXTURE_PATH)
    except FileNotFoundError as exc:
        assert "missing" in str(exc).lower()
        assert "rsi" in str(exc).lower()
    else:
        raise AssertionError("a missing TradingView export must fail closed")


def test_tradingview_comparison_matches_recomputed_indicators(tmp_path):
    path = tmp_path / "tradingview_export.csv"
    _tv_frame().to_csv(path, index=False)
    report = compare_tradingview_export(
        path, identity=_tv_identity(), require_identity=True)
    assert report["ok"]
    assert report["comparisons"]["st_direction_mismatches"] == 0
    assert report["comparisons"]["special_buy_mismatches"] == 0
    assert report["fixture_sha256"]
    assert report["export_identity"] == _tv_identity()


def test_tradingview_comparison_requires_export_identity(tmp_path):
    path = tmp_path / "tradingview_export.csv"
    _tv_frame().to_csv(path, index=False)
    try:
        compare_tradingview_export(path, require_identity=True)
    except ValueError as exc:
        assert "identity" in str(exc).lower()
    else:
        raise AssertionError("missing TradingView identity must fail")


def test_tradingview_identity_from_sidecar(tmp_path):
    path = tmp_path / "tradingview_export.csv"
    _tv_frame().to_csv(path, index=False)
    sidecar = tmp_path / "tradingview_export.meta.json"
    sidecar.write_text(json.dumps(_tv_identity()) + "\n", encoding="utf-8")
    identity = resolve_export_identity(path, require=True)
    assert identity["timeframe"] == "1D"
    assert identity["symbol"] == "SPY"


def test_tradingview_identity_rejects_non_daily_timeframe():
    try:
        resolve_export_identity(
            Path("/tmp/missing.csv"),
            symbol="SPY", timeframe="60", adjustment="adjusted", session="NYSE",
            require=True)
    except ValueError as exc:
        assert "daily" in str(exc).lower()
    else:
        raise AssertionError("non-daily timeframe must fail")


def test_tradingview_identity_rejects_nonconstant_csv_column(tmp_path):
    frame = _tv_frame(n=8)
    frame["symbol"] = "SPY"
    frame.loc[frame.index[5], "symbol"] = "QQQ"
    path = tmp_path / "tradingview_export.csv"
    frame.to_csv(path, index=False)
    try:
        resolve_export_identity(
            path, timeframe="1D", adjustment="adjusted", session="NYSE", require=True)
    except ValueError as exc:
        assert "not constant" in str(exc)
        assert "QQQ" in str(exc)
    else:
        raise AssertionError("non-constant CSV identity columns must fail")


def test_tradingview_identity_rejects_cli_csv_conflict(tmp_path):
    frame = _tv_frame(n=8)
    frame["symbol"] = "QQQ"
    path = tmp_path / "tradingview_export.csv"
    frame.to_csv(path, index=False)
    try:
        resolve_export_identity(
            path, symbol="SPY", timeframe="1D", adjustment="adjusted", session="NYSE",
            require=True)
    except ValueError as exc:
        assert "conflicts" in str(exc)
    else:
        raise AssertionError("CLI/CSV identity conflicts must fail")


def test_tradingview_identity_rejects_sidecar_cli_conflict(tmp_path):
    path = tmp_path / "tradingview_export.csv"
    _tv_frame().to_csv(path, index=False)
    sidecar = tmp_path / "tradingview_export.meta.json"
    sidecar.write_text(json.dumps(_tv_identity(symbol="QQQ")) + "\n", encoding="utf-8")
    try:
        resolve_export_identity(
            path, symbol="SPY", timeframe="1D", adjustment="adjusted", session="NYSE",
            require=True)
    except ValueError as exc:
        assert "conflicts" in str(exc)
    else:
        raise AssertionError("sidecar/CLI identity conflicts must fail")


def test_tradingview_comparison_detects_indicator_mismatch(tmp_path):
    frame = _tv_frame()
    frame.loc[frame.index[-1], "rsi"] = 0.0
    path = tmp_path / "tradingview_export.csv"
    frame.to_csv(path, index=False)
    try:
        compare_tradingview_export(path)
    except ValueError as exc:
        assert "disagrees" in str(exc)
    else:
        raise AssertionError("a mismatched TradingView export must fail")


def test_tradingview_comparison_detects_missing_tv_rsi_on_defined_bar(tmp_path):
    frame = _tv_frame()
    defined = frame["rsi"].notna()
    assert defined.any()
    frame.loc[frame.index[defined.to_numpy().nonzero()[0][-1]], "rsi"] = float("nan")
    path = tmp_path / "tradingview_export.csv"
    frame.to_csv(path, index=False)
    try:
        compare_tradingview_export(path)
    except ValueError as exc:
        assert "rsi_defined_mask_mismatches" in str(exc)
    else:
        raise AssertionError("a blank TradingView RSI on a defined bar must fail")


def test_tradingview_comparison_detects_extra_local_fills(tmp_path, monkeypatch):
    frame = _tv_frame(with_fills=True)
    _patch_emulate_with_fills(monkeypatch, _bars(60))
    # Empty fill columns while the local emulator still produces entries.
    frame["entry_fill"] = float("nan")
    frame["exit_fill"] = float("nan")
    path = tmp_path / "tradingview_export.csv"
    frame.to_csv(path, index=False)
    try:
        compare_tradingview_export(path, require_fills=True, identity=_tv_identity(),
                                   require_identity=True)
    except ValueError as exc:
        assert "extra_local_entry_dates" in str(exc) or "entry_fill_mismatches" in str(exc)
    else:
        raise AssertionError("extra local fills against empty TV fills must fail")


def test_tradingview_holdout_require_fills_rejects_missing_columns(tmp_path):
    path = tmp_path / "tradingview_export.csv"
    _tv_frame(with_fills=False).to_csv(path, index=False)
    try:
        compare_tradingview_export(path, require_fills=True)
    except ValueError as exc:
        assert "fill columns" in str(exc)
    else:
        raise AssertionError("holdout parity must require fill columns")


def test_tradingview_comparison_with_fills_passes(tmp_path, monkeypatch):
    frame = _tv_frame(with_fills=True)
    _patch_emulate_with_fills(monkeypatch, _bars(60))
    path = tmp_path / "tradingview_export.csv"
    frame.to_csv(path, index=False)
    report = compare_tradingview_export(
        path, require_fills=True, identity=_tv_identity(), require_identity=True)
    assert report["ok"]
    assert report["fills_compared"] is True
    assert report["export_identity"]["session"] == "NYSE"


def _oscillating_closes(n: int) -> np.ndarray:
    return 100.0 + np.sin(np.arange(n) / 2.0) * 4.0


def _write_ohlc_cache(root: Path, symbol: str, dates: pd.DatetimeIndex,
                      closes: np.ndarray) -> None:
    for year in sorted(set(dates.year)):
        mask = dates.year == year
        lines = []
        for date, close in zip(dates[mask], closes[mask]):
            high = close + 1.0
            low = close - 1.0
            open_ = close + 0.25
            lines.append(
                f"{date.strftime('%m-%d-%Y')},{open_},{high},{low},{close},{close},1000000")
        year_dir = root / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        (year_dir / f"{symbol}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _paired_cfg(**overrides) -> dict:
    cfg = _cfg(
        development_start="2010-01-04",
        development_end="2010-04-23",
        **overrides,
    )
    cfg["inference"] = dict(cfg["inference"])
    cfg["inference"]["bootstrap_draws"] = 8
    cfg["inference"]["block_length_sessions"] = 5
    cfg["inference"]["minimum_benchmark_sessions"] = 1
    return cfg


def _seed_primary_cache(root: Path, symbols: list[str] = ("SPY", "XLK"),
                        n: int = 80) -> tuple[pd.DatetimeIndex, dict]:
    dates = pd.bdate_range("2010-01-04", periods=n)
    closes = _oscillating_closes(n)
    for symbol in symbols:
        _write_ohlc_cache(root, symbol, dates, closes)
    return dates, _paired_cfg()


def test_emulate_symbol_defaults_to_pine():
    parameter = inspect.signature(emulate_symbol).parameters["indicator_implementation"]
    assert parameter.default == PINE_IMPLEMENTATION
    assert inspect.signature(run_cohort).parameters["indicator_implementation"].default == (
        PINE_IMPLEMENTATION)


def test_run_cohort_default_path_is_pine(tmp_path, monkeypatch):
    from studies.rsi_supertrend import study as study_mod
    _seed_primary_cache(tmp_path)
    calls = []
    real = study_mod.emulate_symbol

    def spy(frame, cfg, start, end, symbol, *, indicator_implementation=PINE_IMPLEMENTATION):
        calls.append(indicator_implementation)
        return real(frame, cfg, start, end, symbol,
                    indicator_implementation=indicator_implementation)

    monkeypatch.setattr(study_mod, "emulate_symbol", spy)
    run_cohort(tmp_path, _paired_cfg(), ["SPY", "XLK"], "development", True)
    assert calls
    assert all(implementation == PINE_IMPLEMENTATION for implementation in calls)


def test_paired_pine_outcomes_match_pine_only_contract(tmp_path):
    _seed_primary_cache(tmp_path)
    cfg = _paired_cfg()
    pine_only = run_cohort(tmp_path, cfg, ["SPY", "XLK"], "development", True)
    paired_pine, paired_shared, comparison = run_paired_cohort(
        tmp_path, cfg, ["SPY", "XLK"], "development", True, cohort="primary")
    pd.testing.assert_frame_equal(pine_only["instruments"], paired_pine["instruments"])
    pd.testing.assert_frame_equal(pine_only["daily"], paired_pine["daily"])
    pd.testing.assert_frame_equal(pine_only["trades"], paired_pine["trades"])
    assert list(paired_pine["instruments"].columns) == list(PINE_INSTRUMENT_COLUMNS)
    assert paired_pine["summary"]["verdict"] == pine_only["summary"]["verdict"]
    assert paired_pine["summary"]["evidence_label"] == "DEVELOPMENT"
    assert paired_shared["summary"]["evidence_label"] == IMPLEMENTATION_SENSITIVITY_LABEL
    assert paired_shared["summary"]["primary_verdict_eligible"] is False
    assert paired_shared["summary"]["verdict"] is None
    assert comparison["primary_verdict_eligible"] is False
    assert "AAA" not in paired_pine["instruments"]["symbol"].tolist()


def test_paired_runner_passes_identical_bars_and_frozen_parameters(tmp_path, monkeypatch):
    from studies.rsi_supertrend import study as study_mod
    _seed_primary_cache(tmp_path)
    cfg = _paired_cfg()
    calls = []
    real = study_mod.emulate_symbol

    def spy(frame, cfg, start, end, symbol, *, indicator_implementation=PINE_IMPLEMENTATION):
        calls.append({
            "frame_id": id(frame),
            "cfg_id": id(cfg),
            "start": pd.Timestamp(start),
            "end": pd.Timestamp(end),
            "symbol": symbol,
            "implementation": indicator_implementation,
            "rsi_length": cfg["rsi_length"],
            "signal_length": cfg["signal_length"],
            "atr_period": cfg["atr_period"],
            "trigger_level": cfg["trigger_level"],
            "target_cross_count": cfg["target_cross_count"],
            "st_factor": cfg["st_factor"],
            "closes": frame["close"].to_numpy().copy(),
        })
        return real(frame, cfg, start, end, symbol,
                    indicator_implementation=indicator_implementation)

    monkeypatch.setattr(study_mod, "emulate_symbol", spy)
    run_paired_cohort(tmp_path, cfg, ["SPY"], "development", True, cohort="primary")
    pine_calls = [row for row in calls if row["implementation"] == PINE_IMPLEMENTATION]
    shared_calls = [row for row in calls if row["implementation"] == SHARED_TA_IMPLEMENTATION]
    assert len(pine_calls) == 1
    assert len(shared_calls) == 1
    pine, shared = pine_calls[0], shared_calls[0]
    assert pine["frame_id"] == shared["frame_id"]
    assert pine["cfg_id"] == shared["cfg_id"]
    assert pine["start"] == shared["start"]
    assert pine["end"] == shared["end"]
    assert pine["symbol"] == shared["symbol"] == "SPY"
    np.testing.assert_array_equal(pine["closes"], shared["closes"])
    for key in ("rsi_length", "signal_length", "atr_period", "trigger_level",
                "target_cross_count", "st_factor"):
        assert pine[key] == shared[key] == cfg[key]
    assert {row["implementation"] for row in calls} == {
        PINE_IMPLEMENTATION, SHARED_TA_IMPLEMENTATION}


def test_paired_runner_changes_only_the_indicator_provider(tmp_path, monkeypatch):
    from studies.rsi_supertrend import study as study_mod
    _seed_primary_cache(tmp_path)
    calls = []
    real = study_mod.emulate_symbol

    def spy(frame, cfg, start, end, symbol, *, indicator_implementation=PINE_IMPLEMENTATION):
        calls.append((symbol, indicator_implementation, id(frame), id(cfg),
                      pd.Timestamp(start), pd.Timestamp(end)))
        return real(frame, cfg, start, end, symbol,
                    indicator_implementation=indicator_implementation)

    monkeypatch.setattr(study_mod, "emulate_symbol", spy)
    run_paired_cohort(tmp_path, _paired_cfg(), ["SPY", "XLK"], "development", True,
                      cohort="primary")
    assert [(symbol, impl) for symbol, impl, *_ in calls] == [
        ("SPY", PINE_IMPLEMENTATION), ("SPY", SHARED_TA_IMPLEMENTATION),
        ("XLK", PINE_IMPLEMENTATION), ("XLK", SHARED_TA_IMPLEMENTATION),
    ]
    by_symbol = {}
    for symbol, impl, frame_id, cfg_id, start, end in calls:
        by_symbol.setdefault(symbol, []).append((impl, frame_id, cfg_id, start, end))
    for pair in by_symbol.values():
        assert pair[0][1:] == pair[1][1:]
        assert pair[0][0] != pair[1][0]


def test_indicator_comparison_captures_synthetic_atr_seed_difference():
    frame = _bars(60)
    frame["close"] = _oscillating_closes(len(frame))
    frame["high"] = frame["close"] + 1.0
    frame["low"] = frame["close"] - 1.0
    pine = compute_strategy_indicators(frame, _cfg(), PINE_IMPLEMENTATION)
    shared = compute_strategy_indicators(frame, _cfg(), SHARED_TA_IMPLEMENTATION)
    dates = pd.DatetimeIndex(pd.to_datetime(frame["date"]))
    row = compare_indicator_series(pine, shared, dates)
    assert row["atr_defined_mask_mismatches"] >= 1
    assert np.flatnonzero(np.isfinite(pine.atr))[0] == 9
    assert np.flatnonzero(np.isfinite(shared.atr))[0] == 10


def _trade(symbol: str, entry: str, exit_: str | None, price: float,
           open_at_cutoff: bool = False) -> dict:
    return {
        "symbol": symbol,
        "signal_date": entry,
        "entry_date": entry,
        "entry_price": price,
        "exit_signal_date": exit_,
        "exit_date": exit_,
        "exit_price": price if exit_ is None else price + 1.0,
        "direction_at_entry": -1.0,
        "shares": 10.0,
        "return": 0.01,
        "duration_days": 2,
        "exit_reason": "open_at_cutoff" if open_at_cutoff else "supertrend_flip",
        "open_at_cutoff": open_at_cutoff,
    }


def _sleeve_from_trades(trades: list[dict], exposure: float = 0.4) -> SleeveResult:
    idx = pd.bdate_range("2010-01-04", periods=8)
    zeros = np.zeros(8)
    return SleeveResult(
        equity=pd.Series(np.full(8, 10000.0), index=idx),
        buy_hold=pd.Series(np.full(8, 10000.0), index=idx),
        trades=trades,
        exposure=exposure,
        indicators=StrategyIndicators(
            implementation=PINE_IMPLEMENTATION,
            rsi=zeros, signal=zeros, atr=zeros, supertrend=zeros,
            direction=np.full(8, -1.0), special_buy=np.zeros(8, dtype=bool),
        ),
    )


def test_identical_fills_have_empty_bidirectional_difference_sets():
    trades = [
        _trade("SPY", "2010-01-06", "2010-01-08", 100.0),
        _trade("SPY", "2010-01-12", "2010-01-14", 101.0),
    ]
    pine = _sleeve_from_trades(trades)
    shared = _sleeve_from_trades(list(trades))
    row = compare_symbol_outcomes(
        "SPY", pine, shared,
        {"strategy_return": 0.1, "max_drawdown": -0.05},
        {"strategy_return": 0.1, "max_drawdown": -0.05},
    )
    assert row["pine_only_entry_dates"] == []
    assert row["shared_ta_only_entry_dates"] == []
    assert row["pine_only_exit_dates"] == []
    assert row["shared_ta_only_exit_dates"] == []
    assert row["fill_price_differences"] == []


def test_deliberate_exit_mismatch_lands_in_the_correct_only_sets():
    pine = _sleeve_from_trades([_trade("SPY", "2010-01-06", "2010-01-08", 100.0)])
    shared = _sleeve_from_trades([_trade("SPY", "2010-01-06", "2010-01-13", 100.0)])
    row = compare_symbol_outcomes(
        "SPY", pine, shared,
        {"strategy_return": 0.2, "max_drawdown": -0.04},
        {"strategy_return": 0.1, "max_drawdown": -0.05},
    )
    assert row["pine_only_entry_dates"] == []
    assert row["shared_ta_only_entry_dates"] == []
    assert row["pine_only_exit_dates"] == ["2010-01-08"]
    assert row["shared_ta_only_exit_dates"] == ["2010-01-13"]
    assert row["strategy_return_difference"] == pytest.approx(0.1)


def test_shared_ta_cannot_change_the_primary_verdict(tmp_path):
    _seed_primary_cache(tmp_path)
    cfg = _paired_cfg()
    pine, shared, comparison = run_paired_cohort(
        tmp_path, cfg, ["SPY", "XLK"], "development", True, cohort="primary")
    pine_verdict = pine["summary"]["verdict"]
    shared["summary"] = label_shared_ta_summary(
        {**shared["summary"], "verdict": "PASSED"})
    pine["summary"]["verdict"] = "FAILED"
    rebuilt = build_cohort_comparison(
        pine, shared, comparison["symbols"], cfg,
        cohort="primary", window="development")
    assert pine["summary"]["verdict"] == "FAILED"
    assert shared["summary"]["verdict"] is None
    assert shared["summary"]["diagnostic_verdict"] == "PASSED"
    assert rebuilt["shared_ta_diagnostic_verdict"] == "PASSED"
    assert rebuilt["shared_ta_verdict_differs"] is True
    assert rebuilt["primary_verdict_eligible"] is False
    run_dir = tmp_path / "paired-verdict"
    write_run(
        run_dir, pine, cfg, {"include_stocks": False},
        {"primary": ["SPY", "XLK"], "window": "development", "stocks": None},
        shared_ta_result=shared, comparison=rebuilt, require_paired=True)
    written = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    shared_written = json.loads(
        (run_dir / "shared_ta_summary.json").read_text(encoding="utf-8"))
    assert written["verdict"] == "FAILED"
    assert written["verdict"] != shared_written["diagnostic_verdict"]
    assert shared_written["verdict"] is None
    assert shared_written["primary_verdict_eligible"] is False
    assert pine_verdict is None


def test_shared_ta_stock_artifacts_keep_sensitivity_and_exploratory_labels(tmp_path):
    _seed_primary_cache(tmp_path, ["SPY", "XLK", "AAA"])
    cfg = _paired_cfg()
    pine, shared, comparison = run_paired_cohort(
        tmp_path, cfg, ["SPY"], "development", True, cohort="primary")
    stock_pine, stock_shared, stock_comparison = run_paired_cohort(
        tmp_path, cfg, ["AAA"], "development", False, cohort="stocks")
    universe = {
        "primary": ["SPY"],
        "window": "development",
        "stocks": {
            "symbols": ["AAA"],
            "stock_count": 1,
            "evidence_label": "EXPLORATORY",
            "survivorship_bias": True,
        },
    }
    run_dir = tmp_path / "paired-stocks"
    write_run(
        run_dir, pine, cfg, {"include_stocks": True}, universe,
        stock_result=stock_pine, shared_ta_result=shared, comparison=comparison,
        shared_ta_stock_result=stock_shared, stock_comparison=stock_comparison,
        require_paired=True)
    stock_csv = (run_dir / "shared_ta_stock_instrument_summary.csv").read_text(encoding="utf-8")
    assert "IMPLEMENTATION_SENSITIVITY" in stock_csv
    assert "EXPLORATORY" in stock_csv
    assert "AAA" in stock_csv
    assert "SPY" not in stock_csv
    stock_summary = json.loads(
        (run_dir / "shared_ta_stock_summary.json").read_text(encoding="utf-8"))
    assert stock_summary["evidence_label"] == IMPLEMENTATION_SENSITIVITY_LABEL
    assert stock_summary["stock_evidence_label"] == "EXPLORATORY"
    assert stock_summary["survivorship_bias"] is True
    assert stock_summary["primary_verdict_eligible"] is False
    assert stock_summary["verdict"] is None
    assert stock_comparison["cohort"] == "stocks"
    assert "any_primary_fill_mismatch" not in stock_comparison
    assert "any_fill_mismatch" in stock_comparison
    pine_summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert pine_summary["evidence_label"] == "DEVELOPMENT"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stock_evidence_label"] == "EXPLORATORY"
    assert manifest["stock_survivorship_bias"] is True
    assert manifest["implementation_sensitivity"]["primary_verdict_eligible"] is False


def test_new_paired_artifacts_are_hashed_in_the_manifest(tmp_path):
    _seed_primary_cache(tmp_path, ["SPY", "XLK", "AAA"])
    cfg = _paired_cfg()
    pine, shared, comparison = run_paired_cohort(
        tmp_path, cfg, ["SPY", "XLK"], "development", True, cohort="primary")
    stock_pine, stock_shared, stock_comparison = run_paired_cohort(
        tmp_path, cfg, ["AAA"], "development", False, cohort="stocks")
    run_dir = tmp_path / "paired-manifest"
    write_run(
        run_dir, pine, cfg, {"include_stocks": True},
        {
            "primary": ["SPY", "XLK"],
            "window": "development",
            "stocks": {
                "symbols": ["AAA"],
                "stock_count": 1,
                "evidence_label": "EXPLORATORY",
                "survivorship_bias": True,
            },
        },
        stock_result=stock_pine, shared_ta_result=shared, comparison=comparison,
        shared_ta_stock_result=stock_shared, stock_comparison=stock_comparison,
        require_paired=True)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    expected = (
        list(OUTPUT_TABLES) + list(SHARED_TA_TABLES) + list(COMPARISON_TABLES)
        + ["stock_instrument_summary.csv", "stock_daily_equity.csv", "stock_trades.csv"]
        + list(SHARED_TA_STOCK_TABLES) + list(STOCK_COMPARISON_TABLES)
    )
    for name in expected:
        assert name in manifest["output_sha256"]
        assert manifest["output_sha256"][name] == sha256_file(run_dir / name)
        assert (run_dir / name).is_file()
    assert list(pd.read_csv(run_dir / "instrument_summary.csv").columns) == list(
        PINE_INSTRUMENT_COLUMNS)
    assert manifest["indicator_implementations"] == [
        PINE_IMPLEMENTATION, SHARED_TA_IMPLEMENTATION]


def test_missing_or_incomplete_paired_results_fail_closed(tmp_path):
    _seed_primary_cache(tmp_path)
    cfg = _paired_cfg()
    pine, shared, comparison = run_paired_cohort(
        tmp_path, cfg, ["SPY", "XLK"], "development", True, cohort="primary")
    run_dir = tmp_path / "missing-paired"
    try:
        write_run(
            run_dir, pine, cfg, {},
            {"primary": ["SPY", "XLK"], "window": "development", "stocks": None},
            require_paired=True)
    except ValueError as exc:
        assert "shared_ta result is missing" in str(exc)
    else:
        raise AssertionError("missing shared_ta result must fail closed")
    assert not run_dir.exists()

    incomplete = dict(comparison)
    incomplete.pop("deltas")
    try:
        require_complete_paired_result(
            pine, shared, incomplete, symbols=["SPY", "XLK"],
            fail_closed=True, cohort="primary")
    except ValueError as extra:
        assert "incomplete" in str(extra)
    else:
        raise AssertionError("incomplete comparison must fail closed")

    shared["summary"]["verdict"] = "PASSED"
    try:
        require_complete_paired_result(
            pine, shared, comparison, symbols=["SPY", "XLK"],
            fail_closed=True, cohort="primary")
    except ValueError as extra:
        assert "primary verdict" in str(extra)
    else:
        raise AssertionError("shared_ta carrying a primary verdict must fail closed")


def test_holdout_remains_blocked_before_claim_or_parity_evidence(tmp_path):
    export = tmp_path / "tradingview_export.csv"
    export.write_text("date,open,high,low,close\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="paired shared-ta sensitivity outcome runner"):
        main([
            "--window", "holdout",
            "--confirm-holdout",
            "--include-stocks",
            "--tradingview-export", str(export),
            "--tv-symbol", "SPY",
            "--tv-timeframe", "1D",
            "--tv-adjustment", "adjusted",
            "--tv-session", "NYSE",
            "--cache-root", str(tmp_path),
            "--output-root", str(tmp_path / "out"),
        ])
    claim = tmp_path / "studies" / "rsi-supertrend-pine-v1" / "holdout" / ".authoritative-claim"
    parity = tmp_path / "studies" / "rsi-supertrend-pine-v1" / "parity"
    assert not claim.exists()
    assert not parity.exists()
    assert not (tmp_path / "out").exists()
