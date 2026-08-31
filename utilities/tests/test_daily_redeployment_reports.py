"""Artifact, CLI guard, and synthetic reconciliation tests."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
import yaml

from studies.pre_earnings_momentum.daily_redeployment import main, parse_args
from studies.pre_earnings_momentum.daily_redeployment_engine import (
    checkpoint_payload,
    checkpoint_from_payload,
    load_study_config,
    run_simulation,
)
from studies.pre_earnings_momentum.daily_redeployment_report import write_run
from studies.pre_earnings_momentum.momentum_v3_replay import SETUP_SCORE_VERSION

from utilities.tests.test_daily_redeployment_engine import _market


def test_2021_guard_fails_closed_without_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path / "real-data-should-not-be-touched"))
    assert main(["--year", "2021"]) == 2
    assert main(["--year", "1800"]) == 2


def test_help_labels_unrun_development_tooling():
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with pytest.raises(SystemExit) as exited:
            parse_args(["--help"])
    assert exited.value.code == 0
    text = buf.getvalue()
    assert "UNRUN" in text
    assert "2021" in text


def test_invalid_config_and_output_collision(tmp_path):
    cfg_path = tmp_path / "bad.yaml"
    raw = yaml.safe_load(Path("studies/pre_earnings_momentum/config/daily_redeployment.yaml").read_text())
    raw["unexpected"] = True
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown configuration key"):
        load_study_config(cfg_path)
    assert main(["--year", "2000", "--config", str(cfg_path)]) == 2
    bundle, _ = _market(tickers=("AAA",), n=80)
    result = run_simulation(cfg=load_study_config(), market=bundle, year=2000)
    out = tmp_path / "run-a"
    write_run(result, out, command="test", args={"year": 2000})
    with pytest.raises(FileExistsError):
        write_run(result, out, command="test", args={"year": 2000})


def test_commands_sh_documents_the_guarded_entry_point():
    body = Path("commands.sh").read_text(encoding="utf-8")
    assert "pre-earnings-daily-study)" in body
    assert "pre-earnings-daily-study" in body.split("set -e", 1)[0]


def test_synthetic_run_reconciles_and_is_byte_for_byte(tmp_path):
    bundle, _ = _market(tickers=("AAA", "BBB"), n=85)
    bundle = replace(bundle, input_hashes={"SPY": "synthetic-spy", "earnings": "synthetic-events"})
    cfg = load_study_config()
    first = run_simulation(cfg=cfg, market=bundle, year=2000)
    second = run_simulation(cfg=cfg, market=bundle, year=2000)
    assert [order.shares for order in first.orders] == [order.shares for order in second.orders]
    assert [order.fill_price for order in first.orders] == [order.fill_price for order in second.orders]
    left = tmp_path / "r1"
    right = tmp_path / "r2"
    write_run(first, left, command="test", args={"year": 2000})
    write_run(second, right, command="test", args={"year": 2000})
    for name in (
        "daily_equity.csv", "orders.csv", "trades.csv", "positions_year_end.csv",
        "positions_year_end.json", "state_checkpoint.json", "summary.json", "report.md",
    ):
        assert (left / name).read_bytes() == (right / name).read_bytes()
    equity = pd.read_csv(left / "daily_equity.csv")
    orders = pd.read_csv(left / "orders.csv")
    decisions = pd.read_csv(left / "decisions.csv")
    summary = (left / "summary.json").read_text(encoding="utf-8")
    assert SETUP_SCORE_VERSION in summary
    assert set(["date", "arm", "cash", "spy_shares", "total_equity"]).issubset(equity.columns)
    assert equity["cash"].min() >= -1e-8
    if not orders.empty:
        assert (orders["shares"] == orders["shares"].round()).all()
    assert {
        "sector_open_plus_pending_count", "sector_open_plus_pending_counts", "order_id",
    } <= set(decisions.columns)
    assert decisions["sector_open_plus_pending_count"].notna().all()
    selected = decisions.loc[decisions["state"] == "selected"]
    assert not selected.empty
    assert selected["order_id"].notna().all()
    assert set(selected["order_id"]) <= set(orders["order_id"])
    assert selected["sector_open_plus_pending_counts"].map(json.loads).map(bool).all()
    year_end = pd.read_csv(left / "positions_year_end.csv")
    assert "residual_cash" in set(year_end["detail"].astype(str))
    assert not any(year_end["detail"].astype(str).str.contains("liquidat"))
    # Unavailable values are blank, never coerced from missing SPY to zero shares.
    assert "spy_market_value" in equity.columns
    checkpoint = json.loads((left / "state_checkpoint.json").read_text(encoding="utf-8"))
    restored = checkpoint_from_payload(checkpoint, cfg, expected_source_year=2000)
    assert restored.states["equal"].cash == pytest.approx(first.checkpoint.states["equal"].cash)
    assert restored.shadows["equal"].spy_shares == first.checkpoint.shadows["equal"].spy_shares
    manifest = json.loads((left / "daily_equity.csv.meta.json").read_text(encoding="utf-8"))
    assert manifest["arms"] == list(cfg.arms)
    assert manifest["input_hashes"] == bundle.input_hashes


def test_summary_contains_required_turnover_exposure_and_review_metrics():
    bundle, _ = _market(tickers=("AAA", "BBB"), n=85)
    result = run_simulation(cfg=load_study_config(), market=bundle, year=2000)
    for arm in load_study_config().arms:
        summary = result.summary["arms"][arm]
        required = {
            "average_stock_positions", "average_largest_sector_fraction",
            "stock_exposure_distribution", "spy_exposure_distribution",
            "cash_exposure_distribution", "gross_stock_turnover_dollars",
            "gross_spy_turnover_dollars", "annual_transaction_cost_drag",
            "zero_cost_return_drag", "attempted_pinned_reentries",
            "days_with_no_candidates", "days_fully_allocated_to_spy",
            "stale_holding_observations", "delayed_exits",
        }
        assert required <= set(summary)
        expected_average = sum(
            mark.stock_position_count for mark in result.marks if mark.arm == arm
        ) / len([mark for mark in result.marks if mark.arm == arm])
        assert summary["average_stock_positions"] == pytest.approx(expected_average)


def test_later_year_cli_requires_prior_checkpoint(tmp_path):
    assert main(["--year", "2022", "--cache-root", str(tmp_path)]) == 2


def test_2021_cli_rejects_state_input_before_confirmation_guard(tmp_path, capsys):
    state_path = tmp_path / "state.json"
    state_path.write_text("{}\n", encoding="utf-8")
    assert main(["--year", "2021", "--state-in", str(state_path)]) == 2
    assert "cannot accept a prior checkpoint" in capsys.readouterr().err


def test_later_year_cli_rejects_checkpoint_from_wrong_year(tmp_path, capsys):
    cfg = load_study_config()
    bundle, _ = _market(tickers=(), n=400)
    first = run_simulation(cfg=cfg, market=bundle, year=2000)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(checkpoint_payload(first.checkpoint, cfg)), encoding="utf-8"
    )
    assert main([
        "--year", "2002", "--state-in", str(state_path),
        "--cache-root", str(tmp_path / "must-not-be-read"),
    ]) == 2
    assert "source year 2000 does not match required 2001" in capsys.readouterr().err


def test_daily_ledger_matches_cash_and_positions():
    bundle, _ = _market(tickers=("AAA",), n=80)
    result = run_simulation(cfg=load_study_config(), market=bundle, year=2000)
    for mark in result.marks:
        if mark.total_equity is None or mark.spy_market_value is None or mark.stock_market_value is None:
            continue
        reconstructed = mark.cash + mark.stock_market_value + mark.spy_market_value
        assert reconstructed == pytest.approx(mark.total_equity, abs=1e-6)


def test_report_and_cli_modules_do_not_import_stock_app():
    for path in (
        Path("studies/pre_earnings_momentum/daily_redeployment.py"),
        Path("studies/pre_earnings_momentum/daily_redeployment_report.py"),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module.split(".", 1)[0])
        assert "app" not in imported
        assert "fastapi" not in imported
