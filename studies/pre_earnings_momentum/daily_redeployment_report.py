"""Atomic artifact writers for pre-earnings-daily-redeployment-v1."""

from __future__ import annotations

import csv
import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from utilities.manifest import sha256_file, write_manifest

from studies.pre_earnings_momentum.daily_redeployment_engine import (
    DailyMark,
    DecisionRecord,
    OrderRecord,
    SimulationResult,
    TradeRecord,
    checkpoint_payload,
)

DAILY_EQUITY_COLUMNS = [
    "date", "arm", "cash", "stock_market_value", "spy_shares", "spy_market_value",
    "total_equity", "realized_stock_pl", "unrealized_stock_pl", "realized_spy_pl",
    "unrealized_spy_pl", "cumulative_stock_costs", "cumulative_spy_costs",
    "strategy_return", "benchmark_value", "benchmark_return", "excess_return",
    "drawdown", "stock_exposure_pct", "spy_exposure_pct", "cash_exposure_pct",
    "stock_position_count", "sector_position_counts", "notes",
]
ORDER_COLUMNS = [
    "order_id", "arm", "ticker", "side", "kind", "shares", "decision_date",
    "execution_date", "reference_price", "limit_price", "fill_price", "principal",
    "cost", "status", "reason", "rank",
]
TRADE_COLUMNS = [
    "arm", "ticker", "shares", "entry_decision_date", "entry_execution_date",
    "exit_decision_date", "exit_execution_date", "entry_fill_price", "exit_fill_price",
    "entry_setup_score", "entry_principal", "allowed_drawdown", "predicted_event_date",
    "realized_event_date", "exit_triggers", "primary_exit", "pin_eligible_again",
    "holding_sessions", "gross_return", "net_return", "realized_pl", "entry_cost",
    "exit_cost", "spy_return", "excess_return",
]


def _iso(value: date | None) -> str:
    return "" if value is None else value.isoformat()


def _num(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.10f}"
    return str(value)


def _write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})
    os.replace(tmp, path)


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _mark_row(mark: DailyMark) -> dict[str, str]:
    return {
        "date": _iso(mark.date),
        "arm": mark.arm,
        "cash": _num(mark.cash),
        "stock_market_value": _num(mark.stock_market_value),
        "spy_shares": _num(mark.spy_shares),
        "spy_market_value": _num(mark.spy_market_value),
        "total_equity": _num(mark.total_equity),
        "realized_stock_pl": _num(mark.realized_stock_pl),
        "unrealized_stock_pl": _num(mark.unrealized_stock_pl),
        "realized_spy_pl": _num(mark.realized_spy_pl),
        "unrealized_spy_pl": _num(mark.unrealized_spy_pl),
        "cumulative_stock_costs": _num(mark.cumulative_stock_costs),
        "cumulative_spy_costs": _num(mark.cumulative_spy_costs),
        "strategy_return": _num(mark.strategy_return),
        "benchmark_value": _num(mark.benchmark_value),
        "benchmark_return": _num(mark.benchmark_return),
        "excess_return": _num(mark.excess_return),
        "drawdown": _num(mark.drawdown),
        "stock_exposure_pct": _num(mark.stock_exposure_pct),
        "spy_exposure_pct": _num(mark.spy_exposure_pct),
        "cash_exposure_pct": _num(mark.cash_exposure_pct),
        "stock_position_count": _num(mark.stock_position_count),
        "sector_position_counts": json.dumps(mark.sector_position_counts, sort_keys=True),
        "notes": "|".join(mark.notes),
    }


def _decision_row(record: DecisionRecord) -> dict[str, str]:
    payload = dict(record.payload)
    for key in ("setup_score_components", "sector_open_plus_pending_counts"):
        value = payload.get(key)
        if isinstance(value, dict):
            payload[key] = json.dumps(value, sort_keys=True)
    return {key: _num(value) if not isinstance(value, str) else value
            for key, value in payload.items()}


def _order_row(record: OrderRecord) -> dict[str, str]:
    return {
        "order_id": record.order_id,
        "arm": record.arm,
        "ticker": record.ticker,
        "side": record.side,
        "kind": record.kind,
        "shares": _num(record.shares),
        "decision_date": _iso(record.decision_date),
        "execution_date": _iso(record.execution_date),
        "reference_price": _num(record.reference_price),
        "limit_price": _num(record.limit_price),
        "fill_price": _num(record.fill_price),
        "principal": _num(record.principal),
        "cost": _num(record.cost),
        "status": record.status,
        "reason": record.reason,
        "rank": "" if record.rank is None else str(record.rank),
    }


def _trade_row(record: TradeRecord) -> dict[str, str]:
    return {
        "arm": record.arm,
        "ticker": record.ticker,
        "shares": _num(record.shares),
        "entry_decision_date": _iso(record.entry_decision_date),
        "entry_execution_date": _iso(record.entry_execution_date),
        "exit_decision_date": _iso(record.exit_decision_date),
        "exit_execution_date": _iso(record.exit_execution_date),
        "entry_fill_price": _num(record.entry_fill_price),
        "exit_fill_price": _num(record.exit_fill_price),
        "entry_setup_score": _num(record.entry_setup_score),
        "entry_principal": _num(record.entry_principal),
        "allowed_drawdown": _num(record.allowed_drawdown),
        "predicted_event_date": _iso(record.predicted_event_date),
        "realized_event_date": _iso(record.realized_event_date),
        "exit_triggers": "|".join(record.exit_triggers),
        "primary_exit": record.primary_exit,
        "pin_eligible_again": _iso(record.pin_eligible_again),
        "holding_sessions": _num(record.holding_sessions),
        "gross_return": _num(record.gross_return),
        "net_return": _num(record.net_return),
        "realized_pl": _num(record.realized_pl),
        "entry_cost": _num(record.entry_cost),
        "exit_cost": _num(record.exit_cost),
        "spy_return": _num(record.spy_return),
        "excess_return": _num(record.excess_return),
    }


def _human_report(result: SimulationResult) -> str:
    lines = [
        f"# {result.cfg.study_id} — {result.year}",
        "",
        "Simulated research evidence only. This is not a prediction, recommendation,",
        "or claim of a validated edge. Each historical run requires owner authorization.",
        "",
        f"Setup-score version: `{result.cfg.setup_score_version}`",
        "",
    ]
    for arm, stats in result.summary.get("arms", {}).items():
        lines.extend([
            f"## Arm `{arm}`",
            "",
            f"- Starting equity: {stats.get('starting_equity')}",
            f"- Ending equity: {stats.get('ending_equity')}",
            f"- Total return: {stats.get('total_return')}",
            f"- Passive SPY return (liquidation-marked): {stats.get('benchmark_return')}",
            f"- Excess return: {stats.get('excess_return')}",
            f"- Maximum drawdown: {stats.get('max_drawdown')}",
            f"- Annualized daily volatility: {stats.get('annualized_daily_volatility')}",
            f"- Return-to-drawdown ratio: {stats.get('return_to_drawdown')}",
            f"- Completed trades: {stats.get('completed_trades')}",
            f"- Winning trades: {stats.get('winning_trades')}",
            f"- Exits by reason: {stats.get('exits_by_reason')}",
            f"- Average holding sessions: {stats.get('average_holding_sessions')}",
            f"- Median holding sessions: {stats.get('median_holding_sessions')}",
            f"- Stock costs: {stats.get('total_stock_costs')}",
            f"- SPY costs: {stats.get('total_spy_costs')}",
            f"- Gross stock turnover: {stats.get('gross_stock_turnover_dollars')}",
            f"- Gross SPY turnover: {stats.get('gross_spy_turnover_dollars')}",
            f"- Transaction-cost return drag: {stats.get('annual_transaction_cost_drag')}",
            f"- Average stock positions: {stats.get('average_stock_positions')}",
            f"- Average largest-sector fraction: {stats.get('average_largest_sector_fraction')}",
            f"- Stock exposure distribution: {stats.get('stock_exposure_distribution')}",
            f"- SPY exposure distribution: {stats.get('spy_exposure_distribution')}",
            f"- Cash exposure distribution: {stats.get('cash_exposure_distribution')}",
            f"- Days with no candidates: {stats.get('days_with_no_candidates')}",
            f"- Days fully allocated to SPY: {stats.get('days_fully_allocated_to_spy')}",
            f"- Attempted pinned re-entries: {stats.get('attempted_pinned_reentries')}",
            f"- Delayed exits: {stats.get('delayed_exits')}",
            f"- Stale holding observations: {stats.get('stale_holding_observations')}",
            f"- Cancelled orders: {stats.get('cancelled_orders')}",
            f"- Year-end cash (visible residue): {stats.get('year_end_cash')}",
            f"- Year-end open positions: {stats.get('year_end_open_positions')}",
            f"- Zero-cost shadow ending equity: {stats.get('zero_cost_shadow_ending_equity')}",
            "",
        ])
    if result.notes:
        lines.extend(["## Notes", ""] + [f"- {note}" for note in result.notes] + [""])
    if result.quarantines:
        lines.extend(["## Data quarantines", ""])
        for ticker, reasons in sorted(result.quarantines.items()):
            lines.append(f"- {ticker}: {', '.join(reasons)}")
        lines.append("")
    return "\n".join(lines)


def write_run(
    result: SimulationResult,
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

    decision_columns = sorted({key for record in result.decisions for key in record.payload} | {
        "decision_date", "intended_execution_date", "arm", "ticker", "state",
        "rejection_reasons", "setup_score_version",
    })
    files = {
        "daily_equity.csv": (DAILY_EQUITY_COLUMNS, [_mark_row(item) for item in result.marks]),
        "orders.csv": (ORDER_COLUMNS, [_order_row(item) for item in result.orders]),
        "trades.csv": (TRADE_COLUMNS, [_trade_row(item) for item in result.trades]),
        "decisions.csv": (decision_columns, [_decision_row(item) for item in result.decisions]),
    }
    for name, (columns, rows) in files.items():
        _write_csv(tmp / name, columns, rows)
    _write_json(tmp / "positions_year_end.csv".replace(".csv", ".json"), result.year_end)
    _write_json(tmp / "state_checkpoint.json", checkpoint_payload(result.checkpoint, result.cfg))
    # Spec names positions_year_end.csv; emit both JSON (structured) and CSV summary.
    year_end_rows = []
    for arm, payload in result.year_end.items():
        year_end_rows.append({
            "arm": arm, "kind": "cash", "ticker": "", "shares": "",
            "value": payload["cash"], "detail": "residual_cash",
        })
        year_end_rows.append({
            "arm": arm, "kind": "spy", "ticker": result.cfg.benchmark_symbol,
            "shares": payload["spy_shares"], "value": "", "detail": "spy_sleeve",
        })
        for position in payload["positions"]:
            year_end_rows.append({
                "arm": arm, "kind": "stock", "ticker": position["ticker"],
                "shares": position["shares"], "value": position.get("last_valid_close"),
                "detail": "open_not_liquidated",
            })
        for order in payload["pending"]:
            year_end_rows.append({
                "arm": arm, "kind": "pending", "ticker": order["ticker"],
                "shares": order["shares"], "value": "", "detail": order["kind"],
            })
        for ticker, until in payload["pins"].items():
            year_end_rows.append({
                "arm": arm, "kind": "pin", "ticker": ticker, "shares": "",
                "value": until, "detail": "eligible_again_date",
            })
    _write_csv(
        tmp / "positions_year_end.csv",
        ["arm", "kind", "ticker", "shares", "value", "detail"],
        [{key: _num(row[key]) if key in {"shares", "value"} and not isinstance(row[key], str) else str(row[key])
          for key in ["arm", "kind", "ticker", "shares", "value", "detail"]}
         for row in year_end_rows],
    )
    _write_json(tmp / "summary.json", result.summary)
    report_path = tmp / "report.md"
    report_path.write_text(_human_report(result), encoding="utf-8")

    extra = {
        "study_id": result.cfg.study_id,
        "phase": result.cfg.phase,
        "year": result.year,
        "setup_score_version": result.cfg.setup_score_version,
        "quarantines": result.quarantines,
        "input_hashes": result.summary.get("input_hashes", {}),
        "arms": list(result.cfg.arms),
        "args": args,
        "config": result.cfg.raw,
        "output_hashes": {},
    }
    for name in ("daily_equity.csv", "decisions.csv", "orders.csv", "trades.csv",
                 "positions_year_end.csv", "positions_year_end.json",
                 "state_checkpoint.json", "summary.json", "report.md"):
        path = tmp / name
        manifest_path = write_manifest(
            path,
            command=command,
            args=args,
            config=result.cfg.raw,
            extra={
                "study_id": result.cfg.study_id,
                "phase": result.cfg.phase,
                "year": result.year,
                "setup_score_version": result.cfg.setup_score_version,
                "arms": list(result.cfg.arms),
                "input_hashes": result.summary.get("input_hashes", {}),
                "quarantines": result.quarantines,
            },
        )
        if "git_commit" not in extra:
            artifact_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key in (
                "command", "generated_at_utc", "git_commit", "git_dirty",
                "dependencies", "local_timezone",
            ):
                extra[key] = artifact_manifest.get(key)
        extra["output_hashes"][name] = sha256_file(path)
    _write_json(tmp / "run_manifest.json", extra)
    os.replace(tmp, output_dir)
    return output_dir
