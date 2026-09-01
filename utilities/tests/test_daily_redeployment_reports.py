"""Artifact, CLI guard, and synthetic reconciliation tests."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
import yaml

import studies.pre_earnings_momentum.daily_redeployment as daily_redeployment_cli
from studies.pre_earnings_momentum.daily_redeployment import (
    CASH_STAGING_CONFIG,
    DEFAULT_CONFIG,
    _fail_closed_for_2021,
    _validate_continuation_config,
    main,
    parse_args,
)
from studies.pre_earnings_momentum.daily_redeployment_engine import (
    checkpoint_payload,
    checkpoint_from_payload,
    load_study_config,
    run_simulation,
)
from studies.pre_earnings_momentum.daily_redeployment_report import write_run
from studies.pre_earnings_momentum.daily_redeployment_series_report import (
    SeriesValidationError,
    build_series_summary,
    write_series_summary,
)
from studies.pre_earnings_momentum.momentum_v3_replay import SETUP_SCORE_VERSION

from utilities.tests.test_daily_redeployment_engine import _market


def test_2021_guard_fails_closed_without_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path / "real-data-should-not-be-touched"))
    assert main(["--year", "2021"]) == 2
    assert main(["--year", "1800"]) == 2


def test_2021_continuation_from_earlier_authorized_origin_is_not_pilot_guarded():
    _fail_closed_for_2021(2021, 2010, False)


def test_help_labels_guarded_development_tooling():
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with pytest.raises(SystemExit) as exited:
            parse_args(["--help"])
    assert exited.value.code == 0
    text = buf.getvalue()
    assert "Development tooling" in text
    assert "2021" in text


def test_price_cap_sensitivity_configs_change_only_price_max():
    baseline = load_study_config()
    for ceiling in (500, 1000):
        variant = load_study_config(Path(
            f"studies/pre_earnings_momentum/config/daily_redeployment_price_{ceiling}.yaml"
        ))
        expected = dict(baseline.raw)
        expected["price_max"] = float(ceiling)
        assert variant.raw == expected


def test_entry_cadence_configs_change_only_the_scan_schedule():
    baseline = load_study_config()
    for name, schedule in (
        ("daily_redeployment_monday_thursday.yaml", "monday_thursday"),
        ("daily_redeployment_monday.yaml", "monday"),
    ):
        variant = load_study_config(
            Path("studies/pre_earnings_momentum/config") / name
        )
        expected = dict(baseline.raw)
        expected["entry_scan_schedule"] = schedule
        assert variant.raw == expected


def test_invalid_config_and_output_collision(tmp_path):
    cfg_path = tmp_path / "bad.yaml"
    raw = yaml.safe_load(Path("studies/pre_earnings_momentum/config/daily_redeployment.yaml").read_text())
    raw["unexpected"] = True
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown configuration key"):
        load_study_config(cfg_path)
    raw.pop("unexpected")
    raw["entry_scan_schedule"] = "friday"
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported entry_scan_schedule"):
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
    run_manifest = json.loads((left / "run_manifest.json").read_text(encoding="utf-8"))
    assert isinstance(run_manifest["git_commit"], str)
    assert isinstance(run_manifest["git_dirty"], bool)
    report = (left / "report.md").read_text(encoding="utf-8")
    assert "Each historical run requires owner authorization" in report
    assert "not calendar-year returns" in report


def test_series_report_validates_checkpoint_chain_and_calculates_calendar_years(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "utilities.manifest._git",
        lambda *args: "test-commit" if args == ("rev-parse", "HEAD") else "",
    )
    cfg = load_study_config()
    bundle, _ = _market(tickers=("AAA", "BBB"), n=400)
    artifact_root = tmp_path / "daily"
    first = run_simulation(cfg=cfg, market=bundle, year=2000)
    first_dir = artifact_root / "2000" / "series-2000"
    write_run(first, first_dir, command="test", args={
        "year": 2000, "origin_year": 2000, "state_in": None,
    })
    second = run_simulation(
        cfg=cfg,
        market=bundle,
        year=2001,
        initial_checkpoint=first.checkpoint,
    )
    second.summary["input_hashes"] = {
        **second.summary["input_hashes"],
        "state_checkpoint": __import__("hashlib").sha256(
            (first_dir / "state_checkpoint.json").read_bytes()
        ).hexdigest(),
    }
    second_dir = artifact_root / "2001" / "series-2001"
    write_run(second, second_dir, command="test", args={
        "year": 2001,
        "origin_year": 2000,
        "state_in": str(first_dir / "state_checkpoint.json"),
    })

    rows, evidence = build_series_summary(artifact_root, "series", [2000, 2001])
    assert evidence["status"] == "PASS"
    assert len(rows) == 4
    assert [row["Year"] for row in rows] == ["2000", "2000", "2001", "2001"]
    for arm in ("equal", "proportional"):
        arm_rows = [row for row in rows if row["Arm"] == arm]
        assert float(arm_rows[0]["Beginning Equity"]) == 50_000.0
        assert float(arm_rows[1]["Beginning Equity"]) == pytest.approx(
            float(arm_rows[0]["Ending Equity"]), abs=0.01,
        )
        assert int(arm_rows[0]["No Of Transactions"]) == (
            int(arm_rows[0]["Stock Transactions"]) + int(arm_rows[0]["SPY Transactions"])
        )
    output = tmp_path / "annual_summary.csv"
    write_series_summary(output, rows, evidence, artifact_root=artifact_root)
    assert output.is_file()
    assert output.with_name("annual_summary.validation.json").is_file()
    assert output.with_name("annual_summary.csv.meta.json").is_file()


def test_series_report_rejects_broken_checkpoint_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "utilities.manifest._git",
        lambda *args: "test-commit" if args == ("rev-parse", "HEAD") else "",
    )
    cfg = load_study_config()
    bundle, _ = _market(tickers=(), n=400)
    artifact_root = tmp_path / "daily"
    first = run_simulation(cfg=cfg, market=bundle, year=2000)
    first_dir = artifact_root / "2000" / "series-2000"
    write_run(first, first_dir, command="test", args={
        "year": 2000, "origin_year": 2000, "state_in": None,
    })
    second = run_simulation(cfg=cfg, market=bundle, year=2001, initial_checkpoint=first.checkpoint)
    second_dir = artifact_root / "2001" / "series-2001"
    write_run(second, second_dir, command="test", args={
        "year": 2001,
        "origin_year": 2000,
        "state_in": str(first_dir / "state_checkpoint.json"),
    })
    with pytest.raises(SeriesValidationError, match="checkpoint hash"):
        build_series_summary(artifact_root, "series", [2000, 2001])


def test_series_report_supports_one_arm_study(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "utilities.manifest._git",
        lambda *args: "test-commit" if args == ("rev-parse", "HEAD") else "",
    )
    cfg = load_study_config(CASH_STAGING_CONFIG)
    bundle, _ = _market(tickers=("AAA",), n=400)
    artifact_root = tmp_path / "cash-staging"
    first = run_simulation(cfg=cfg, market=bundle, year=2000)
    first_dir = artifact_root / "2000" / "series-2000"
    write_run(first, first_dir, command="test", args={
        "year": 2000, "origin_year": 2000, "state_in": None,
    })
    second = run_simulation(
        cfg=cfg, market=bundle, year=2001, initial_checkpoint=first.checkpoint,
    )
    second.summary["input_hashes"] = {
        **second.summary["input_hashes"],
        "state_checkpoint": __import__("hashlib").sha256(
            (first_dir / "state_checkpoint.json").read_bytes()
        ).hexdigest(),
    }
    second_dir = artifact_root / "2001" / "series-2001"
    write_run(second, second_dir, command="test", args={
        "year": 2001, "origin_year": 2000,
        "state_in": str(first_dir / "state_checkpoint.json"),
    })
    rows, evidence = build_series_summary(artifact_root, "series", [2000, 2001])
    assert evidence["arms"] == ["equal"]
    assert [row["Arm"] for row in rows] == ["equal", "equal"]


def test_progress_callback_reports_every_completed_session():
    bundle, _ = _market(tickers=("AAA",), n=85)
    observed = []
    result = run_simulation(
        cfg=load_study_config(),
        market=bundle,
        year=2000,
        progress_callback=lambda completed, total, session: observed.append(
            (completed, total, session)
        ),
    )
    assert len(observed) == len(result.sessions)
    assert observed[0] == (1, len(result.sessions), result.sessions[0])
    assert observed[-1] == (
        len(result.sessions), len(result.sessions), result.sessions[-1],
    )


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


def test_continuation_accepts_only_frozen_selected_config():
    cfg = load_study_config(DEFAULT_CONFIG)
    _validate_continuation_config(DEFAULT_CONFIG, cfg)
    staging = load_study_config(CASH_STAGING_CONFIG)
    _validate_continuation_config(CASH_STAGING_CONFIG, staging)
    assert staging.arms == ("equal",)
    assert staging.cash_staging_enabled is True
    drifted_raw = dict(cfg.raw)
    drifted_raw["cost_bps_per_side"] = 9
    with pytest.raises(ValueError, match="does not match the frozen"):
        _validate_continuation_config(
            DEFAULT_CONFIG, replace(cfg, raw=drifted_raw),
        )
    for name in (
        "daily_redeployment_price_500.yaml",
        "daily_redeployment_price_1000.yaml",
        "daily_redeployment_monday_thursday.yaml",
        "daily_redeployment_monday.yaml",
    ):
        path = DEFAULT_CONFIG.parent / name
        with pytest.raises(
            ValueError, match="sensitivity configurations are not accepted",
        ):
            _validate_continuation_config(path, load_study_config(path))


def test_schema_v1_checkpoint_with_continuation_fields_restores():
    cfg = load_study_config(DEFAULT_CONFIG)
    bundle, _ = _market(tickers=(), n=400)
    first = run_simulation(cfg=cfg, market=bundle, year=2000)
    payload = checkpoint_payload(first.checkpoint, cfg)
    assert payload["schema_version"] == 1
    for arm in cfg.arms:
        payload["arms"][arm]["next_order_seq"] = 426
        payload["arms"][arm]["scheduled_sweep_session"] = "2001-01-02"
        payload["zero_cost_shadows"][arm]["next_order_seq"] = 426
        payload["zero_cost_shadows"][arm]["scheduled_sweep_session"] = "2001-01-02"
    restored = checkpoint_from_payload(payload, cfg, expected_source_year=2000)
    for arm in cfg.arms:
        assert restored.states[arm].next_order_seq == 426
        assert (
            restored.states[arm].scheduled_sweep_session.isoformat()
            == "2001-01-02"
        )
        assert restored.shadows[arm].next_order_seq == 426
        assert (
            restored.shadows[arm].scheduled_sweep_session.isoformat()
            == "2001-01-02"
        )


def test_continuation_runner_accepts_default_and_rejects_sensitivities(
    tmp_path, monkeypatch,
):
    cfg = load_study_config(DEFAULT_CONFIG)
    bundle, _ = _market(tickers=(), n=400)
    first = run_simulation(cfg=cfg, market=bundle, year=2000)
    state_path = tmp_path / "state_checkpoint.json"
    state_path.write_text(
        json.dumps(checkpoint_payload(first.checkpoint, cfg)), encoding="utf-8",
    )
    monkeypatch.setattr(
        daily_redeployment_cli, "load_market",
        lambda _cfg, _year, _args: bundle,
    )
    assert main([
        "--year", "2001", "--origin-year", "2000", "--state-in", str(state_path),
        "--output-root", str(tmp_path / "accepted"), "--run-id", "default",
    ]) == 0
    staging_cfg = load_study_config(CASH_STAGING_CONFIG)
    staging_first = run_simulation(cfg=staging_cfg, market=bundle, year=2000)
    staging_state_path = tmp_path / "cash-staging-state_checkpoint.json"
    staging_state_path.write_text(
        json.dumps(checkpoint_payload(staging_first.checkpoint, staging_cfg)), encoding="utf-8",
    )
    assert main([
        "--year", "2001", "--origin-year", "2000", "--state-in", str(staging_state_path),
        "--config", str(CASH_STAGING_CONFIG),
        "--output-root", str(tmp_path / "cash-staging-accepted"), "--run-id", "cash-staging",
    ]) == 0
    for name in (
        "daily_redeployment_price_500.yaml",
        "daily_redeployment_price_1000.yaml",
        "daily_redeployment_monday_thursday.yaml",
        "daily_redeployment_monday.yaml",
    ):
        assert main([
            "--year", "2001", "--origin-year", "2000",
            "--state-in", str(state_path),
            "--config", str(DEFAULT_CONFIG.parent / name),
            "--output-root", str(tmp_path / "rejected"), "--run-id", name,
        ]) == 2


def test_standalone_2021_cli_rejects_state_input_before_confirmation_guard(tmp_path, capsys):
    state_path = tmp_path / "state.json"
    state_path.write_text("{}\n", encoding="utf-8")
    assert main(["--year", "2021", "--state-in", str(state_path)]) == 2
    assert "origin year 2021 cannot accept a prior checkpoint" in capsys.readouterr().err


def test_earlier_origin_requires_selected_config_and_prior_checkpoint(tmp_path, capsys):
    sensitivity = DEFAULT_CONFIG.parent / "daily_redeployment_monday.yaml"
    assert main([
        "--year", "2010", "--origin-year", "2010",
        "--config", str(sensitivity),
    ]) == 2
    assert "sensitivity configurations are not accepted" in capsys.readouterr().err
    assert main(["--year", "2011", "--origin-year", "2010"]) == 2
    assert "require --state-in" in capsys.readouterr().err


def test_later_year_cli_rejects_checkpoint_from_wrong_year(tmp_path, capsys):
    cfg = load_study_config()
    bundle, _ = _market(tickers=(), n=400)
    first = run_simulation(cfg=cfg, market=bundle, year=2000)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(checkpoint_payload(first.checkpoint, cfg)), encoding="utf-8"
    )
    assert main([
        "--year", "2002", "--origin-year", "2000", "--state-in", str(state_path),
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
