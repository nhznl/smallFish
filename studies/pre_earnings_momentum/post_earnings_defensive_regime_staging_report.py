"""Atomic Study 7 artifact writer with an ETF-aware schema."""

from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from utilities.manifest import sha256_file, write_manifest

from studies.pre_earnings_momentum.daily_redeployment_report import (
    ORDER_COLUMNS,
    TRADE_COLUMNS,
    _decision_row,
    _order_row,
    _trade_row,
)
from studies.pre_earnings_momentum.post_earnings_defensive_regime_staging_engine import (
    STUDY_FAMILY,
    RegimeStagingResult,
    checkpoint_payload,
)

DAILY_COLUMNS = [
    "date", "cash", "stock_open_market_value_before_trades", "stock_market_value", "spy_market_value",
    "spxl_market_value", "gld_market_value", "total_equity",
    "net_liquidation_value", "stock_exposure_pct", "spy_exposure_pct",
    "spxl_exposure_pct", "gld_exposure_pct", "cash_exposure_pct",
    "etf_exposure_proxy", "stock_position_count", "etf_symbol", "etf_shares",
    "spy_open", "spy_close", "spxl_open", "spxl_close", "gld_open", "gld_close",
    "daily_market_regime", "last_executable_weekly_regime",
    "cumulative_stock_costs", "cumulative_etf_costs", "realized_stock_pl",
    "realized_etf_pl", "drawdown",
]


def _value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.10f}"
    return str(value)


def _write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _value(row.get(key)) for key in columns})
    os.replace(tmp, path)


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _report(result: RegimeStagingResult) -> str:
    stats = result.summary
    return "\n".join([
        f"# {result.cfg.study_id} — {result.year}", "",
        "NO_VERDICT / EXPLORATORY retrospective research evidence.",
        "This report is not a prediction, recommendation, or validated edge.", "",
        f"- Variant: `{result.variant}`",
        f"- ETF staging policy: `{result.cfg.staging_policy}`",
        f"- Risk-On stock entries enabled: `{result.cfg.stock_entries_enabled}`",
        f"- Ending close equity: {stats['ending_equity']}",
        f"- Ending hypothetical net-liquidation value: {stats['ending_net_liquidation_value']}",
        f"- Since-origin close return: {stats['total_return']}",
        f"- Since-origin net-liquidation return: {stats['net_liquidation_return']}",
        f"- Last executable weekly regime: {stats['last_executable_weekly_regime']}",
        f"- Maximum close-marked drawdown in this annual artifact: {stats['max_drawdown']}",
        f"- Filled stock round trips: {stats['completed_stock_trades']}",
        f"- Weekly in-window staging decisions: {stats['weekly_decisions']}",
        f"- Stock costs since origin: {stats['total_stock_costs']}",
        f"- ETF costs since origin: {stats['total_etf_costs']}", "",
        "Close equity leaves holdings open. Net-liquidation value subtracts a",
        "hypothetical exit fee without mutating the portfolio ledger.", "",
        "## Notes", "",
        *([f"- {note}" for note in result.notes] or ["- None"]), "",
    ])


def write_run(
    result: RegimeStagingResult,
    output_dir: Path,
    *,
    command: str,
    args: dict[str, Any],
) -> Path:
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing run directory: {output_dir}")
    tmp = output_dir.parent / f".{output_dir.name}.tmp"
    if tmp.exists():
        raise FileExistsError(f"incomplete run directory already exists: {tmp}")
    tmp.mkdir(parents=True)

    _write_csv(tmp / "daily_equity.csv", DAILY_COLUMNS, [asdict(item) for item in result.marks])
    decision_columns = sorted(
        {key for item in result.decisions for key in item.payload}
        | {"decision_date", "ticker", "state", "rejection_reasons"}
    )
    _write_csv(
        tmp / "decisions.csv", decision_columns,
        [_decision_row(item) for item in result.decisions],
    )
    _write_csv(tmp / "orders.csv", ORDER_COLUMNS, [_order_row(item) for item in result.orders])
    _write_csv(tmp / "trades.csv", TRADE_COLUMNS, [_trade_row(item) for item in result.trades])

    state = result.checkpoint.state
    positions = [{
        "kind": "cash", "ticker": "", "shares": "", "value": state.cash,
        "detail": "residual_cash",
    }, {
        "kind": "staging_etf", "ticker": state.etf_symbol or "",
        "shares": state.etf_shares, "value": "", "detail": "open_not_liquidated",
    }]
    positions.extend({
        "kind": "stock", "ticker": position.ticker, "shares": position.shares,
        "value": position.last_valid_close, "detail": "open_not_liquidated",
    } for position in state.positions.values())
    positions.extend({
        "kind": "pending", "ticker": order.ticker, "shares": order.shares,
        "value": "", "detail": order.kind,
    } for order in state.pending)
    if state.pending_target is not None:
        positions.append({
            "kind": "pending_etf_target", "ticker": state.pending_target.target_symbol,
            "shares": "", "value": state.pending_target.execution_date,
            "detail": state.pending_target.regime,
        })
    _write_csv(
        tmp / "positions_year_end.csv",
        ["kind", "ticker", "shares", "value", "detail"], positions,
    )
    _write_json(tmp / "state_checkpoint.json", checkpoint_payload(result.checkpoint, result.cfg))
    _write_json(tmp / "summary.json", result.summary)
    (tmp / "report.md").write_text(_report(result), encoding="utf-8")

    artifact_names = (
        "daily_equity.csv", "decisions.csv", "orders.csv", "trades.csv",
        "positions_year_end.csv", "state_checkpoint.json", "summary.json", "report.md",
    )
    run_manifest: dict[str, Any] = {
        "study_family": STUDY_FAMILY,
        "study_id": result.cfg.study_id, "variant": result.variant,
        "phase": result.cfg.phase, "evidence_status": "NO_VERDICT / EXPLORATORY",
        "year": result.year, "args": args, "config": result.cfg.raw,
        "input_hashes": result.summary["input_hashes"], "output_hashes": {},
        "terminal_boundary_exclusions": result.notes,
        "exchange_calendar_source": result.summary["exchange_calendar_source"],
    }
    for name in artifact_names:
        path = tmp / name
        manifest_path = write_manifest(
            path, command=command, args=args, config=result.cfg.raw,
            extra={
                "study_family": run_manifest["study_family"],
                "study_id": result.cfg.study_id, "variant": result.variant,
                "year": result.year, "input_hashes": result.summary["input_hashes"],
                "evidence_status": run_manifest["evidence_status"],
                "exchange_calendar_source": run_manifest["exchange_calendar_source"],
            },
        )
        if "git_commit" not in run_manifest:
            artifact_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key in (
                "command", "generated_at_utc", "git_commit", "git_dirty",
                "dependencies", "local_timezone",
            ):
                run_manifest[key] = artifact_manifest.get(key)
        run_manifest["output_hashes"][name] = sha256_file(path)
    _write_json(tmp / "run_manifest.json", run_manifest)
    os.replace(tmp, output_dir)
    return output_dir
