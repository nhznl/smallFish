"""Synthetic coverage for the RSI/SuperTrend Pine replication.

No test opens a socket. No test uses 2022-2025 strategy results as expected
values. A missing TradingView export fails closed rather than skipping.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from studies.rsi_supertrend import emulator as emulator_mod
from studies.rsi_supertrend.emulator import emulate_symbol, percent_equity_qty
from studies.rsi_supertrend.pine import (
    PINE_SHA256,
    pine_atr,
    pine_rma,
    pine_rsi,
    pine_sma,
    pine_supertrend,
    pine_true_range,
    special_buy_signals,
)
from studies.rsi_supertrend.study import (
    CONFIG_PATH,
    FROZEN_CONFIG,
    SOURCE_PATH,
    TV_FIXTURE_PATH,
    compare_tradingview_export,
    enforce_holdout_guard,
    load_config,
    moving_block_summary,
    run_cohort,
    sha256_file,
    verify_source_hash,
    write_run,
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
    monkeypatch.setattr(
        emulator_mod, "special_buy_signals", lambda *_args, **_kwargs: special)
    monkeypatch.setattr(
        emulator_mod, "pine_supertrend",
        lambda *_args, **_kwargs: (np.zeros(n), direction))
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


def _approved_parity(tmp_path: Path) -> tuple[dict, Path]:
    report = {
        "ok": True,
        "fixture_sha256": "abc123",
        "study_id": FROZEN_CONFIG["study_id"],
    }
    path = tmp_path / "tradingview_parity.json"
    path.write_text(json.dumps(report) + "\n", encoding="utf-8")
    return report, path


def test_holdout_guard_requires_confirm_flag(tmp_path):
    report, path = _approved_parity(tmp_path)
    try:
        enforce_holdout_guard(
            _cfg(), tmp_path, confirm=False, claim_root=tmp_path,
            parity_report=report, parity_report_path=path)
    except ValueError as exc:
        assert "--confirm-holdout" in str(exc)
    else:
        raise AssertionError("holdout without --confirm-holdout must fail")


def test_holdout_guard_requires_clean_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "studies.rsi_supertrend.study.git_is_dirty", lambda: True)
    report, path = _approved_parity(tmp_path)
    try:
        enforce_holdout_guard(
            _cfg(), tmp_path, confirm=True, claim_root=tmp_path,
            parity_report=report, parity_report_path=path)
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


def test_holdout_guard_claims_atomically_and_ignores_output_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "studies.rsi_supertrend.study.git_is_dirty", lambda: False)
    report, path = _approved_parity(tmp_path)
    first = enforce_holdout_guard(
        _cfg(), tmp_path / "out-a", confirm=True, claim_root=tmp_path,
        parity_report=report, parity_report_path=path)
    assert (first / "claim.json").is_file()
    claim = json.loads((first / "claim.json").read_text(encoding="utf-8"))
    assert claim["parity_report_sha256"]
    assert claim["fixture_sha256"] == "abc123"
    try:
        enforce_holdout_guard(
            _cfg(), tmp_path / "out-b", confirm=True, claim_root=tmp_path,
            parity_report=report, parity_report_path=path)
    except ValueError as exc:
        assert "already claimed" in str(exc)
    else:
        raise AssertionError("a second holdout caller must not pass the claim")


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
    write_run(run_dir, primary, _cfg(), {"include_stocks": True}, universe, "digest",
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
    report = compare_tradingview_export(path)
    assert report["ok"]
    assert report["comparisons"]["st_direction_mismatches"] == 0
    assert report["comparisons"]["special_buy_mismatches"] == 0
    assert report["fixture_sha256"]


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
        compare_tradingview_export(path, require_fills=True)
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
    report = compare_tradingview_export(path, require_fills=True)
    assert report["ok"]
    assert report["fills_compared"] is True
