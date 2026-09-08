"""Synthetic-only acceptance tests for Study 7 defensive ETF staging."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from studies.pre_earnings_momentum import post_earnings_defensive_regime_staging as cli
from studies.pre_earnings_momentum import post_earnings_regime_staging as study6
from studies.pre_earnings_momentum.daily_redeployment_engine import (
    REGIME_NEUTRAL,
    REGIME_RISK_OFF,
    REGIME_RISK_ON,
    REGIME_UNKNOWN,
    load_study_config,
    market_regime_at_close,
)
from studies.pre_earnings_momentum.post_earnings_defensive_regime_staging_comparison import (
    VARIANT_DIRS,
    Study7ValidationError,
    build_comparison,
    classify_d_versus_c,
    session_path_attribution,
)
from studies.pre_earnings_momentum.post_earnings_defensive_regime_staging_engine import (
    STUDY_FAMILY,
    RegimeStagingMarket,
    checkpoint_from_payload,
    checkpoint_payload,
    run_regime_staging_simulation,
    target_symbol,
)
from studies.pre_earnings_momentum.post_earnings_defensive_regime_staging_report import write_run
from utilities.tests.test_daily_redeployment_engine import _market


def _cfg(variant: str):
    return load_study_config(cli.VARIANT_CONFIGS[variant])


def _price_frame(symbol: str, dates: pd.DatetimeIndex, close: float = 100.0):
    return pd.DataFrame({
        "date": dates, "ticker": symbol, "open": close, "high": close + 1,
        "low": close - 1, "close": close, "adj_close": close, "volume": 5_000_000,
    })


def _bundle(*, n: int = 130, tickers=("AAA",), days_ahead: int = 21, gld_close: float = 80.0):
    legacy, sessions = _market(n=n, tickers=tickers, days_ahead=days_ahead)
    gld = legacy.spy.copy(deep=True)
    gld["ticker"] = "GLD"
    for column in ("open", "high", "low", "close", "adj_close"):
        if column in gld:
            gld[column] = gld_close
    return RegimeStagingMarket(
        spy=legacy.spy,
        staging_assets={
            "SPY": legacy.spy,
            "SPXL": legacy.spy.copy(deep=True),
            "GLD": gld,
        },
        stocks=legacy.stocks,
        earnings=legacy.earnings,
        sectors=legacy.sectors,
        quarantines=legacy.quarantines,
        input_hashes={"snapshot": "synthetic", "SPY": "spy", "SPXL": "spxl", "GLD": "gld"},
    ), sessions


def test_configs_are_frozen_and_change_only_declared_variant_fields():
    configs = {key: _cfg(key) for key in cli.VARIANT_CONFIGS}
    for variant, cfg in configs.items():
        cli._validate_config(variant, cfg)
    assert configs["passive-spy"].output_relative_root.endswith("passive_spy")
    assert configs["stocks-spy-control"].output_relative_root.endswith("stocks_spy_control")
    assert configs["stocks-spxl-spy-control"].output_relative_root.endswith("stocks_spxl_spy_control")
    assert configs["stocks-spxl-gld-treatment"].output_relative_root.endswith("stocks_spxl_gld_treatment")
    assert configs["etf-only-spxl-gld"].output_relative_root.endswith("etf_only_spxl_gld")

    b = deepcopy(configs["stocks-spy-control"].raw)
    c = deepcopy(configs["stocks-spxl-spy-control"].raw)
    d = deepcopy(configs["stocks-spxl-gld-treatment"].raw)
    for payload in (b, c, d):
        payload.pop("study_id")
        payload.pop("staging_policy")
        payload.pop("output")
    assert b == c == d
    e = deepcopy(configs["etf-only-spxl-gld"].raw)
    d_policy = deepcopy(configs["stocks-spxl-gld-treatment"].raw)
    e.pop("study_id")
    e.pop("stock_entries_enabled")
    e.pop("output")
    d_policy.pop("study_id")
    d_policy.pop("stock_entries_enabled")
    d_policy.pop("output")
    assert e == d_policy
    assert not configs["passive-spy"].stock_entries_enabled
    assert not configs["etf-only-spxl-gld"].stock_entries_enabled
    assert configs["stocks-spy-control"].stock_entries_enabled


@pytest.mark.parametrize(
    ("variant", "regime", "expected"),
    [
        ("passive-spy", REGIME_RISK_ON, "SPY"),
        ("passive-spy", REGIME_RISK_OFF, "SPY"),
        ("stocks-spy-control", REGIME_RISK_ON, "SPY"),
        ("stocks-spy-control", REGIME_RISK_OFF, "SPY"),
        ("stocks-spxl-spy-control", REGIME_RISK_ON, "SPXL"),
        ("stocks-spxl-spy-control", REGIME_NEUTRAL, "SPY"),
        ("stocks-spxl-spy-control", REGIME_RISK_OFF, "SPY"),
        ("stocks-spxl-spy-control", REGIME_UNKNOWN, "SPY"),
        ("stocks-spxl-gld-treatment", REGIME_RISK_ON, "SPXL"),
        ("stocks-spxl-gld-treatment", REGIME_NEUTRAL, "SPY"),
        ("stocks-spxl-gld-treatment", REGIME_RISK_OFF, "GLD"),
        ("stocks-spxl-gld-treatment", REGIME_UNKNOWN, "SPY"),
        ("etf-only-spxl-gld", REGIME_RISK_ON, "SPXL"),
        ("etf-only-spxl-gld", REGIME_RISK_OFF, "GLD"),
        ("etf-only-spxl-gld", REGIME_NEUTRAL, "SPY"),
    ],
)
def test_exact_a_e_regime_destinations(variant: str, regime: str, expected: str):
    assert target_symbol(_cfg(variant), regime) == expected


def test_regime_classification_ignores_spxl_and_gld():
    market, _ = _bundle()
    baseline = run_regime_staging_simulation(
        cfg=_cfg("etf-only-spxl-gld"), variant="etf-only-spxl-gld",
        market=deepcopy(market), year=2000,
    )
    mutated = deepcopy(market)
    mutated.staging_assets["SPXL"]["close"] = 1.0
    mutated.staging_assets["GLD"]["close"] = 1000.0
    mutated.staging_assets["SPXL"]["open"] = 1.0
    mutated.staging_assets["GLD"]["open"] = 1000.0
    replay = run_regime_staging_simulation(
        cfg=_cfg("etf-only-spxl-gld"), variant="etf-only-spxl-gld",
        market=mutated, year=2000,
    )
    assert [item.daily_market_regime for item in baseline.marks] == [
        item.daily_market_regime for item in replay.marks
    ]
    assert [
        item.payload["market_regime"] for item in baseline.decisions
        if item.payload["state"] == "staging_target"
    ] == [
        item.payload["market_regime"] for item in replay.decisions
        if item.payload["state"] == "staging_target"
    ]


def test_equal_price_and_flat_sma_boundaries():
    dates = pd.bdate_range("1999-10-01", periods=80)
    spy = _price_frame("SPY", dates, 100.0)
    spy["close"] = 100.0
    session = dates[60].date()
    assert market_regime_at_close(spy, session) == REGIME_RISK_OFF
    spy_up = _price_frame("SPY", dates)
    spy_up["close"] = list(range(80, 160))
    assert market_regime_at_close(spy_up, session) == REGIME_RISK_ON
    spy_flat = _price_frame("SPY", dates, 100.0)
    spy_flat["close"] = 100.0
    spy_flat.loc[spy_flat.index[-5:], "close"] = [90.0, 90.0, 90.0, 90.0, 140.0]
    assert market_regime_at_close(spy_flat, dates[-1].date()) == REGIME_NEUTRAL


def test_holiday_week_uses_final_available_session():
    dates = pd.to_datetime([
        date(2000, 1, 3), date(2000, 1, 4), date(2000, 1, 5), date(2000, 1, 6),
        date(2000, 1, 10), date(2000, 1, 11), date(2000, 1, 12), date(2000, 1, 13),
    ])
    spy = _price_frame("SPY", dates)
    market = RegimeStagingMarket(
        spy=spy,
        staging_assets={
            "SPY": spy, "SPXL": _price_frame("SPXL", dates, 50.0),
            "GLD": _price_frame("GLD", dates, 80.0),
        },
        stocks={}, earnings=pd.DataFrame(), sectors={},
        input_hashes={"SPY": "s", "SPXL": "x", "GLD": "g"},
    )
    result = run_regime_staging_simulation(
        cfg=_cfg("etf-only-spxl-gld"), variant="etf-only-spxl-gld",
        market=market, year=2000,
    )
    executions = {
        item.payload["execution_session"]
        for item in result.decisions if item.payload["state"] == "staging_target"
    }
    assert executions == {"2000-01-06", "2000-01-13"}


def test_one_decision_per_final_session_open():
    market, _ = _bundle()
    result = run_regime_staging_simulation(
        cfg=_cfg("etf-only-spxl-gld"), variant="etf-only-spxl-gld",
        market=market, year=2000,
    )
    executions = [
        item.payload["execution_session"] for item in result.decisions
        if item.payload["state"] == "staging_target"
    ]
    assert executions
    assert len(executions) == len(set(executions))


def test_terminal_2026_execution_is_excluded_without_pending_batch():
    dates = pd.bdate_range("2024-10-01", "2026-01-09")
    dates = dates[dates.date != date(2026, 1, 1)]
    spy = _price_frame("SPY", dates)
    market = RegimeStagingMarket(
        spy=spy,
        staging_assets={
            "SPY": spy, "SPXL": _price_frame("SPXL", dates, 50.0),
            "GLD": _price_frame("GLD", dates, 80.0),
        },
        stocks={}, earnings=pd.DataFrame(), sectors={},
    )
    result = run_regime_staging_simulation(
        cfg=_cfg("etf-only-spxl-gld"), variant="etf-only-spxl-gld",
        market=market, year=2025,
    )
    assert result.checkpoint.state.pending_target is None
    assert "terminal_boundary_excluded:2025-12-31->2026-01-02" in result.notes
    staging = [
        item.payload for item in result.decisions
        if item.payload["state"] == "staging_target"
    ]
    assert all(date.fromisoformat(item["execution_session"]).year <= 2025 for item in staging)


def test_execution_day_mutations_cannot_change_frozen_decisions():
    market, _ = _bundle()
    cfg = _cfg("stocks-spxl-gld-treatment")
    baseline = run_regime_staging_simulation(
        cfg=cfg, variant="stocks-spxl-gld-treatment", market=deepcopy(market), year=2000,
    )
    mutated = deepcopy(market)
    execution_dates = sorted({
        date.fromisoformat(item.payload["execution_session"])
        for item in baseline.decisions if item.payload["state"] == "staging_target"
    })
    first_execution = execution_dates[0]
    first_cutoff = next(
        date.fromisoformat(item.payload["decision_date"])
        for item in baseline.decisions if item.payload["state"] == "staging_target"
    )
    for frame in list(mutated.stocks.values()) + list(mutated.staging_assets.values()):
        mask = frame["date"].dt.date == first_execution
        frame.loc[mask, "open"] *= 1.25
    replay = run_regime_staging_simulation(
        cfg=cfg, variant="stocks-spxl-gld-treatment", market=mutated, year=2000,
    )

    def frozen(result):
        return [
            {
                key: record.payload.get(key)
                for key in (
                    "ticker", "state", "target_etf", "market_regime", "shares",
                    "limit_price", "reserved_cash", "rank",
                )
            }
            for record in result.decisions
            if date.fromisoformat(record.payload["decision_date"]) == first_cutoff
        ]

    assert frozen(baseline) == frozen(replay)


def test_d_transitions_among_spxl_gld_spy_and_retains_unchanged_etf(monkeypatch):
    import studies.pre_earnings_momentum.post_earnings_defensive_regime_staging_engine as engine

    market, sessions = _bundle()
    year_sessions = [item for item in sessions if item.year == 2000]
    labels = {
        year_sessions[3]: REGIME_RISK_ON,
        year_sessions[8]: REGIME_RISK_ON,
        year_sessions[13]: REGIME_NEUTRAL,
        year_sessions[18]: REGIME_RISK_OFF,
        year_sessions[23]: REGIME_RISK_ON,
    }

    def sequenced(spy, session, **kwargs):
        closest = max((key for key in labels if key <= session), default=None)
        return REGIME_UNKNOWN if closest is None else labels[closest]

    monkeypatch.setattr(engine, "market_regime_at_close", sequenced)
    result = run_regime_staging_simulation(
        cfg=_cfg("etf-only-spxl-gld"), variant="etf-only-spxl-gld",
        market=market, year=2000,
    )
    destinations = [
        item.payload["target_etf"] for item in result.decisions
        if item.payload["state"] == "staging_target"
    ]
    assert {"SPXL", "SPY", "GLD"} <= set(destinations)
    switches = [item for item in result.orders if item.kind == "etf_switch_exit"]
    consecutive = list(zip(destinations, destinations[1:]))
    unchanged = [left for left, right in consecutive if left == right]
    if unchanged:
        assert not any(
            item.ticker == symbol and f"switch_to_{symbol}" == item.reason
            for item in switches for symbol in unchanged
        )


def test_passive_a_buys_spy_once_and_never_trades_again():
    market, _ = _bundle()
    result = run_regime_staging_simulation(
        cfg=_cfg("passive-spy"), variant="passive-spy", market=market, year=2000,
    )
    buys = [item for item in result.orders if item.side == "buy"]
    sells = [item for item in result.orders if item.side == "sell"]
    assert len(buys) == 1
    assert buys[0].ticker == "SPY"
    assert buys[0].reason == "passive_origin_purchase"
    assert not sells
    assert not result.decisions
    assert result.checkpoint.state.etf_symbol == "SPY"
    assert result.checkpoint.state.origin_consumed


def test_etf_only_never_creates_stock_orders_or_positions():
    market, _ = _bundle(tickers=("AAA", "BBB"))
    result = run_regime_staging_simulation(
        cfg=_cfg("etf-only-spxl-gld"), variant="etf-only-spxl-gld",
        market=market, year=2000,
    )
    assert not [item for item in result.orders if item.kind.startswith("stock_")]
    assert not result.trades
    assert not result.checkpoint.state.positions
    assert all(item.payload["state"] == "staging_target" for item in result.decisions)


def test_b_c_d_share_stock_decisions_when_etf_prices_are_identical():
    market, _ = _bundle(gld_close=100.0)
    market.staging_assets["GLD"] = market.spy.copy(deep=True)
    market.staging_assets["GLD"]["ticker"] = "GLD"
    results = {
        variant: run_regime_staging_simulation(
            cfg=_cfg(variant), variant=variant, market=deepcopy(market), year=2000,
        )
        for variant in (
            "stocks-spy-control", "stocks-spxl-spy-control", "stocks-spxl-gld-treatment",
        )
    }

    def stock_decisions(result):
        return [
            {
                key: value for key, value in record.payload.items()
                if key not in {"staging_policy", "target_etf"}
            }
            for record in result.decisions
            if record.payload.get("ticker") not in {"SPY", "SPXL", "GLD"}
        ]

    shared = stock_decisions(results["stocks-spy-control"])
    assert shared == stock_decisions(results["stocks-spxl-spy-control"])
    assert shared == stock_decisions(results["stocks-spxl-gld-treatment"])


def test_independent_arms_diverge_when_risk_off_prices_differ(monkeypatch):
    import studies.pre_earnings_momentum.post_earnings_defensive_regime_staging_engine as engine

    market, sessions = _bundle(gld_close=40.0)
    origin = next(item for item in sessions if item.year == 2000)

    def later_off(spy, session, **kwargs):
        return REGIME_RISK_ON if session <= origin else REGIME_RISK_OFF

    monkeypatch.setattr(engine, "market_regime_at_close", later_off)
    control = run_regime_staging_simulation(
        cfg=_cfg("stocks-spxl-spy-control"), variant="stocks-spxl-spy-control",
        market=deepcopy(market), year=2000,
    )
    treatment = run_regime_staging_simulation(
        cfg=_cfg("stocks-spxl-gld-treatment"), variant="stocks-spxl-gld-treatment",
        market=deepcopy(market), year=2000,
    )
    assert control.checkpoint.state.etf_symbol == "SPY"
    assert treatment.checkpoint.state.etf_symbol == "GLD"
    assert (
        control.checkpoint.state.cash != treatment.checkpoint.state.cash
        or control.checkpoint.state.etf_shares != treatment.checkpoint.state.etf_shares
    )


def test_missing_required_gld_session_aborts_before_any_result():
    market, sessions = _bundle()
    missing = next(item for item in sessions if item.year == 2000)
    gld = market.staging_assets["GLD"]
    broken = replace(
        market,
        staging_assets={
            **market.staging_assets,
            "GLD": gld.loc[gld["date"].dt.date != missing].reset_index(drop=True),
        },
    )
    with pytest.raises(ValueError, match="incomplete"):
        run_regime_staging_simulation(
            cfg=_cfg("etf-only-spxl-gld"), variant="etf-only-spxl-gld",
            market=broken, year=2000,
        )


def test_checkpoint_round_trip_persists_executable_regime():
    first_market, _ = _bundle(n=390)
    cfg = _cfg("etf-only-spxl-gld")
    first = run_regime_staging_simulation(
        cfg=cfg, variant="etf-only-spxl-gld", market=first_market, year=2000,
    )
    assert first.checkpoint.state.last_executable_weekly_regime != REGIME_UNKNOWN
    payload = checkpoint_payload(first.checkpoint, cfg)
    restored = checkpoint_from_payload(
        json.loads(json.dumps(payload)), cfg, "etf-only-spxl-gld",
        expected_source_year=2000,
    )
    assert restored.state.last_executable_weekly_regime == (
        first.checkpoint.state.last_executable_weekly_regime
    )
    direct = run_regime_staging_simulation(
        cfg=cfg, variant="etf-only-spxl-gld", market=deepcopy(first_market),
        year=2001, initial_checkpoint=first.checkpoint,
    )
    replay = run_regime_staging_simulation(
        cfg=cfg, variant="etf-only-spxl-gld", market=deepcopy(first_market),
        year=2001, initial_checkpoint=restored,
    )
    assert checkpoint_payload(direct.checkpoint, cfg) == checkpoint_payload(
        replay.checkpoint, cfg
    )
    assert direct.marks[0].last_executable_weekly_regime == (
        first.checkpoint.state.last_executable_weekly_regime
    )
    assert direct.marks[0].last_executable_weekly_regime != REGIME_UNKNOWN


def test_costs_shadow_and_liquidation_reconcile():
    market, _ = _bundle()
    result = run_regime_staging_simulation(
        cfg=_cfg("stocks-spxl-gld-treatment"), variant="stocks-spxl-gld-treatment",
        market=market, year=2000,
    )
    final = result.marks[-1]
    shadow = result.checkpoint.shadow
    assert result.summary["zero_cost_shadow_reconciled"] is True
    assert shadow.etf_shares == result.checkpoint.state.etf_shares
    assert shadow.etf_symbol == result.checkpoint.state.etf_symbol
    assert {key: value.shares for key, value in shadow.positions.items()} == {
        key: value.shares for key, value in result.checkpoint.state.positions.items()
    }
    assert final.net_liquidation_value <= final.total_equity
    filled_cost = sum(item.cost for item in result.orders if item.status == "filled")
    assert pytest.approx(filled_cost, abs=1e-6) == (
        result.checkpoint.state.cumulative_stock_costs
        + result.checkpoint.state.cumulative_etf_costs
    )


def test_d_minus_c_path_attribution_splits_overnight_and_open_to_close(monkeypatch):
    import studies.pre_earnings_momentum.post_earnings_defensive_regime_staging_engine as engine

    market, sessions = _bundle()
    origin = next(item for item in sessions if item.year == 2000)

    def on_then_off(spy, session, **kwargs):
        return REGIME_RISK_ON if session <= origin else REGIME_RISK_OFF

    monkeypatch.setattr(engine, "market_regime_at_close", on_then_off)
    result = run_regime_staging_simulation(
        cfg=_cfg("etf-only-spxl-gld"), variant="etf-only-spxl-gld",
        market=market, year=2000,
    )
    orders = pd.DataFrame([item.__dict__ for item in result.orders])
    daily = pd.DataFrame([item.__dict__ for item in result.marks])
    daily["date"] = pd.to_datetime(daily["date"])
    path = session_path_attribution(daily, orders)
    switch_days = [
        item for item in path if item["phase"] in {"overnight", "open_to_close"}
    ]
    assert switch_days
    overnight = next(item for item in switch_days if item["phase"] == "overnight")
    incoming = next(item for item in switch_days if item["phase"] == "open_to_close")
    assert overnight["regime"] != incoming["regime"]


def test_writer_uses_study7_family_and_refuses_overwrite(tmp_path: Path):
    market, _ = _bundle()
    cfg = _cfg("etf-only-spxl-gld")
    result = run_regime_staging_simulation(
        cfg=cfg, variant="etf-only-spxl-gld", market=market, year=2000,
    )
    output = tmp_path / "run"
    write_run(result, output, command="test", args={"synthetic": True})
    payload = json.loads((output / "state_checkpoint.json").read_text())
    assert payload["study_family"] == STUDY_FAMILY
    assert "last_executable_weekly_regime" in payload["state"]
    assert "spy_shares" not in payload["state"]
    with pytest.raises(FileExistsError):
        write_run(result, output, command="test", args={"synthetic": True})


def _write_variant_year(tmp_path: Path, variant: str, year: int, tag: str, market, commit: str):
    cfg = _cfg(variant)
    result = run_regime_staging_simulation(
        cfg=cfg, variant=variant, market=deepcopy(market), year=year,
    )
    output = tmp_path / VARIANT_DIRS[variant] / str(year) / tag
    write_run(result, output, command="test", args={"synthetic": True})
    manifest_path = output / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["git_dirty"] = False
    manifest["git_commit"] = commit
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return output, result


def test_comparison_validates_shared_snapshot_and_independent_origin(tmp_path: Path):
    market, _ = _bundle()
    tag = "synthetic"
    for variant in cli.VARIANT_CONFIGS:
        _write_variant_year(tmp_path, variant, 2000, tag, market, "synthetic-commit")
    tags = {variant: tag for variant in cli.VARIANT_CONFIGS}
    daily, annual, monthly, summary = build_comparison(tmp_path, tags, [2000])
    assert set(daily["variant"]) == set(cli.VARIANT_CONFIGS)
    assert len(annual) == 5
    assert summary["evidence_status"] == "NO_VERDICT / EXPLORATORY"
    labels = classify_d_versus_c(
        summary["variants"]["stocks-spxl-gld-treatment"],
        summary["variants"]["stocks-spxl-spy-control"],
    )
    assert set(labels) >= {
        "return_dominance", "drawdown_dominance", "strict_portfolio_dominance",
        "risk_adjusted_improvement", "tradeoff",
    }


def test_comparison_rejects_modified_artifact_hash(tmp_path: Path):
    market, _ = _bundle()
    tag = "synthetic"
    for variant in cli.VARIANT_CONFIGS:
        _write_variant_year(tmp_path, variant, 2000, tag, market, "synthetic-commit")
    broken = tmp_path / VARIANT_DIRS["passive-spy"] / "2000" / tag / "summary.json"
    payload = json.loads(broken.read_text())
    payload["tampered"] = True
    broken.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    with pytest.raises(Study7ValidationError, match="hash mismatch"):
        build_comparison(tmp_path, {variant: tag for variant in cli.VARIANT_CONFIGS}, [2000])


def test_cli_guards_fail_before_market_loading(monkeypatch, tmp_path: Path):
    loaded = False

    def forbidden(*args, **kwargs):
        nonlocal loaded
        loaded = True
        raise AssertionError("market loading must not run")

    monkeypatch.setattr(cli, "load_market", forbidden)
    common = [
        "--variant", "etf-only-spxl-gld", "--year", "2010", "--origin-year", "2010",
        "--cache-root", str(tmp_path), "--output-root", str(tmp_path / "out"),
    ]
    assert cli.main(common) == 2
    assert not loaded

    monkeypatch.setattr(cli, "pinned_implementation_revision", lambda: ("abc", True))
    assert cli.main([
        *common, "--confirm-weekly-defensive-regime-staging-exploratory-run",
    ]) == 2
    assert not loaded

    monkeypatch.setattr(cli, "pinned_implementation_revision", lambda: ("abc", False))
    protected = tmp_path / "weekly_regime_staging" / "out"
    assert cli.main([
        "--variant", "passive-spy", "--year", "2010", "--origin-year", "2010",
        "--cache-root", str(tmp_path), "--output-root", str(protected),
        "--confirm-weekly-defensive-regime-staging-exploratory-run",
    ]) == 2
    assert not loaded


def test_legacy_runner_rejects_study7_config(capsys):
    import studies.pre_earnings_momentum.daily_redeployment as legacy_cli

    config = cli.VARIANT_CONFIGS["etf-only-spxl-gld"]
    rc = legacy_cli.main([
        "--year", "2021", "--origin-year", "2021", "--confirm-2021-pilot",
        "--config", str(config),
    ])
    assert rc == 2
    assert "dedicated Study 7 runner" in capsys.readouterr().err


def test_study6_frozen_config_hashes_remain_unchanged():
    for variant, expected in study6.VARIANT_CONFIG_SHA256.items():
        cfg = load_study_config(study6.VARIANT_CONFIGS[variant])
        assert study6._effective_config_hash(cfg.raw) == expected
        study6._validate_config(variant, cfg)
