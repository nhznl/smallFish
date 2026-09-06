"""Synthetic-only acceptance tests for Study 6 ETF staging."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from studies.pre_earnings_momentum import post_earnings_regime_staging as cli
from studies.pre_earnings_momentum.daily_redeployment_engine import (
    REGIME_NEUTRAL,
    REGIME_RISK_OFF,
    REGIME_RISK_ON,
    REGIME_UNKNOWN,
    load_study_config,
)
from studies.pre_earnings_momentum.post_earnings_regime_staging_engine import (
    RegimeStagingMarket,
    checkpoint_from_payload,
    checkpoint_payload,
    run_regime_staging_simulation,
    target_symbol,
)
from studies.pre_earnings_momentum.post_earnings_regime_staging_report import write_run
from studies.pre_earnings_momentum.post_earnings_regime_staging_comparison import (
    build_comparison,
)
from utilities.tests.test_daily_redeployment_engine import _market


def _cfg(variant: str):
    return load_study_config(cli.VARIANT_CONFIGS[variant])


def _bundle(*, n: int = 130, tickers=("AAA",), days_ahead: int = 21):
    legacy, sessions = _market(n=n, tickers=tickers, days_ahead=days_ahead)
    return RegimeStagingMarket(
        spy=legacy.spy,
        staging_assets={"SPY": legacy.spy, "SPXL": legacy.spy.copy(deep=True)},
        stocks=legacy.stocks,
        earnings=legacy.earnings,
        sectors=legacy.sectors,
        quarantines=legacy.quarantines,
        input_hashes={"snapshot": "synthetic"},
    ), sessions


def _price_frame(symbol: str, dates: pd.DatetimeIndex, close: float = 100.0):
    return pd.DataFrame({
        "date": dates, "ticker": symbol, "open": close, "high": close + 1,
        "low": close - 1, "close": close, "adj_close": close, "volume": 5_000_000,
    })


def test_configs_are_frozen_and_change_only_declared_variant_fields():
    configs = {key: _cfg(key) for key in cli.VARIANT_CONFIGS}
    for variant, cfg in configs.items():
        cli._validate_config(variant, cfg)
    control = deepcopy(configs["stocks-spy-control"].raw)
    treatment = deepcopy(configs["stocks-regime-staging"].raw)
    for payload in (control, treatment):
        payload.pop("study_id")
        payload.pop("staging_policy")
        payload.pop("output")
    assert control == treatment
    assert configs["stocks-spy-control"].stock_entries_enabled
    assert configs["stocks-regime-staging"].stock_entries_enabled
    assert not configs["etf-only"].stock_entries_enabled


@pytest.mark.parametrize(
    ("regime", "expected"),
    [
        (REGIME_RISK_ON, "SPXL"),
        (REGIME_NEUTRAL, "SPY"),
        (REGIME_RISK_OFF, "SPY"),
        (REGIME_UNKNOWN, "SPY"),
    ],
)
def test_regime_policy_destinations_and_spy_control(regime: str, expected: str):
    assert target_symbol(_cfg("stocks-regime-staging"), regime) == expected
    assert target_symbol(_cfg("etf-only"), regime) == expected
    assert target_symbol(_cfg("stocks-spy-control"), regime) == "SPY"


def test_a_and_b_share_stock_decisions_when_etf_prices_are_identical():
    market, _ = _bundle()
    results = {
        variant: run_regime_staging_simulation(
            cfg=_cfg(variant), variant=variant, market=deepcopy(market), year=2000,
        )
        for variant in ("stocks-spy-control", "stocks-regime-staging")
    }

    def stock_decisions(result):
        return [
            {
                key: value for key, value in record.payload.items()
                if key not in {"staging_policy", "target_etf"}
            }
            for record in result.decisions
            if record.payload.get("ticker") not in {"SPY", "SPXL"}
        ]

    assert stock_decisions(results["stocks-spy-control"]) == stock_decisions(
        results["stocks-regime-staging"]
    )
    stock_orders = lambda result: [
        replace(order, order_id="") for order in result.orders
        if order.kind.startswith("stock_")
    ]
    assert stock_orders(results["stocks-spy-control"]) == stock_orders(
        results["stocks-regime-staging"]
    )


def test_etf_only_never_creates_stock_orders_or_positions():
    market, _ = _bundle(tickers=("AAA", "BBB"))
    result = run_regime_staging_simulation(
        cfg=_cfg("etf-only"), variant="etf-only", market=market, year=2000,
    )
    assert not [item for item in result.orders if item.kind.startswith("stock_")]
    assert not result.trades
    assert not result.checkpoint.state.positions
    assert all(
        item.payload["state"] == "staging_target" for item in result.decisions
    )


def test_etf_switches_only_at_weekly_execution(monkeypatch):
    import studies.pre_earnings_momentum.post_earnings_regime_staging_engine as engine

    market, sessions = _bundle()
    origin = next(item for item in sessions if item.year == 2000)
    first_cutoff = origin + pd.offsets.BDay(3)

    def changing_regime(spy, session, **kwargs):
        return REGIME_RISK_ON if session <= first_cutoff.date() else REGIME_RISK_OFF

    monkeypatch.setattr(engine, "market_regime_at_close", changing_regime)
    result = run_regime_staging_simulation(
        cfg=_cfg("etf-only"), variant="etf-only", market=market, year=2000,
    )
    switches = [item for item in result.orders if item.kind == "etf_switch_exit"]
    assert len(switches) == 1
    assert switches[0].ticker == "SPXL"
    assert result.checkpoint.state.etf_symbol == "SPY"
    execution_dates = {
        date.fromisoformat(item.payload["execution_session"])
        for item in result.decisions if item.payload["state"] == "staging_target"
    }
    assert all(item.execution_date in execution_dates for item in switches)


def test_missing_required_etf_session_aborts_before_any_result():
    market, sessions = _bundle()
    missing = next(item for item in sessions if item.year == 2000)
    spxl = market.staging_assets["SPXL"]
    broken = replace(
        market,
        staging_assets={
            **market.staging_assets,
            "SPXL": spxl.loc[spxl["date"].dt.date != missing].reset_index(drop=True),
        },
    )
    with pytest.raises(ValueError, match="incomplete"):
        run_regime_staging_simulation(
            cfg=_cfg("etf-only"), variant="etf-only", market=broken, year=2000,
        )


def test_terminal_2026_execution_is_excluded_without_pending_batch():
    dates = pd.bdate_range("2024-10-01", "2026-01-09")
    dates = dates[dates.date != date(2026, 1, 1)]
    spy = _price_frame("SPY", dates)
    spxl = _price_frame("SPXL", dates, 50.0)
    market = RegimeStagingMarket(
        spy=spy, staging_assets={"SPY": spy, "SPXL": spxl}, stocks={},
        earnings=pd.DataFrame(), sectors={},
    )
    result = run_regime_staging_simulation(
        cfg=_cfg("etf-only"), variant="etf-only", market=market, year=2025,
    )
    assert result.checkpoint.state.pending_target is None
    assert "terminal_boundary_excluded:2025-12-31->2026-01-02" in result.notes
    staging = [
        item.payload for item in result.decisions
        if item.payload["state"] == "staging_target"
    ]
    assert all(date.fromisoformat(item["execution_session"]).year <= 2025 for item in staging)


def test_checkpoint_round_trip_matches_direct_continuation():
    first_market, _ = _bundle(n=390)
    cfg = _cfg("etf-only")
    first = run_regime_staging_simulation(
        cfg=cfg, variant="etf-only", market=first_market, year=2000,
    )
    payload = checkpoint_payload(first.checkpoint, cfg)
    restored = checkpoint_from_payload(
        json.loads(json.dumps(payload)), cfg, "etf-only", expected_source_year=2000,
    )
    direct = run_regime_staging_simulation(
        cfg=cfg, variant="etf-only", market=deepcopy(first_market), year=2001,
        initial_checkpoint=first.checkpoint,
    )
    replay = run_regime_staging_simulation(
        cfg=cfg, variant="etf-only", market=deepcopy(first_market), year=2001,
        initial_checkpoint=restored,
    )
    assert checkpoint_payload(direct.checkpoint, cfg) == checkpoint_payload(
        replay.checkpoint, cfg
    )
    assert direct.summary == replay.summary


def test_writer_uses_separate_checkpoint_schema_and_refuses_overwrite(tmp_path: Path):
    market, _ = _bundle()
    cfg = _cfg("etf-only")
    result = run_regime_staging_simulation(
        cfg=cfg, variant="etf-only", market=market, year=2000,
    )
    output = tmp_path / "run"
    write_run(result, output, command="test", args={"synthetic": True})
    payload = json.loads((output / "state_checkpoint.json").read_text())
    assert payload["study_family"] == "pre-earnings-weekly-regime-staging-v1"
    assert "spy_shares" not in payload["state"]
    assert (output / "daily_equity.csv").is_file()
    with pytest.raises(FileExistsError):
        write_run(result, output, command="test", args={"synthetic": True})


def test_comparison_validates_shared_snapshot_and_passive_ledger(tmp_path: Path):
    market, _ = _bundle()
    tag = "synthetic"
    tags = {variant: tag for variant in cli.VARIANT_CONFIGS}
    for variant in cli.VARIANT_CONFIGS:
        cfg = _cfg(variant)
        result = run_regime_staging_simulation(
            cfg=cfg, variant=variant, market=deepcopy(market), year=2000,
        )
        output = (
            tmp_path
            / {
                "stocks-spy-control": "stocks_spy_staging_control",
                "stocks-regime-staging": "stocks_regime_staging",
                "etf-only": "etf_only",
            }[variant]
            / "2000" / tag
        )
        write_run(result, output, command="test", args={"synthetic": True})
        manifest_path = output / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["git_dirty"] = False
        manifest["git_commit"] = "synthetic-commit"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    daily, annual, monthly, summary, drift = build_comparison(tmp_path, tags, [2000])
    assert set(daily["variant"]) == set(cli.VARIANT_CONFIGS)
    assert len(annual) == 3
    assert len(monthly) > 3
    assert summary["implementation_commit"] == "synthetic-commit"
    assert summary["evidence_status"] == "NO_VERDICT / EXPLORATORY"
    assert drift is None


def test_cli_guards_fail_before_market_loading(monkeypatch, tmp_path: Path):
    loaded = False

    def forbidden(*args, **kwargs):
        nonlocal loaded
        loaded = True
        raise AssertionError("market loading must not run")

    monkeypatch.setattr(cli, "load_market", forbidden)
    common = [
        "--variant", "etf-only", "--year", "2010", "--origin-year", "2010",
        "--cache-root", str(tmp_path), "--output-root", str(tmp_path / "out"),
    ]
    assert cli.main(common) == 2
    assert not loaded

    monkeypatch.setattr(cli, "pinned_implementation_revision", lambda: ("abc", True))
    assert cli.main([
        *common, "--confirm-weekly-regime-staging-exploratory-run",
    ]) == 2
    assert not loaded


def test_legacy_runner_rejects_study6_config(monkeypatch, capsys):
    import studies.pre_earnings_momentum.daily_redeployment as legacy_cli

    config = cli.VARIANT_CONFIGS["etf-only"]
    rc = legacy_cli.main([
        "--year", "2021", "--origin-year", "2021", "--confirm-2021-pilot",
        "--config", str(config),
    ])
    assert rc == 2
    assert "dedicated Study 6 runner" in capsys.readouterr().err
