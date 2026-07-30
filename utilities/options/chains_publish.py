"""Immutable premium-archive publication for chains runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from models.premium import PREMIUM_SCHEMA_NAME, PREMIUM_SCHEMA_VERSION
from utilities.manifest import write_manifest
from utilities.options.chains_config import VIEW_ENTRY, VIEW_ROLL_EXIT
from utilities.options.verify_premiums import verify_premium_archive


@dataclass
class ChainsResult:
    report: pd.DataFrame
    meta: dict
    warnings: list[str]
    statuses: list[dict] = field(default_factory=list)


def write_chain_artifacts(output_root: Path, result: ChainsResult, *,
                          args: dict, strategy: dict) -> dict[str, Path]:
    """Write an immutable C2 run plus compatibility daily/latest views.

    The run directory is creation-only: a duplicate run ID raises rather than
    overwriting an observation. The dated CSV remains for existing consumers,
    while ``latest.json`` identifies the immutable source behind that view.
    """
    premiums_root = Path(output_root) / "premiums"
    premiums_root.mkdir(parents=True, exist_ok=True)
    run_id = str(result.meta.get("run_id", "")).strip()
    if not run_id:
        raise ValueError("chain artifact metadata requires a run_id")
    run_dir = premiums_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    immutable_report = run_dir / "premiums.csv"
    immutable_entry_report = run_dir / "entry_candidates.csv"
    immutable_roll_exit_report = run_dir / "roll_exit.csv"
    immutable_meta = run_dir / "run_meta.json"
    result.report.to_csv(immutable_report, index=False)
    if "analysis_view" in result.report.columns:
        entry_report = result.report[result.report["analysis_view"] == VIEW_ENTRY]
        roll_exit_report = result.report[
            result.report["analysis_view"] == VIEW_ROLL_EXIT]
    else:
        entry_report = result.report.iloc[0:0]
        roll_exit_report = result.report.iloc[0:0]
    entry_report.to_csv(immutable_entry_report, index=False)
    roll_exit_report.to_csv(immutable_roll_exit_report, index=False)
    immutable_meta.write_text(
        json.dumps(result.meta, indent=2, default=str) + "\n", encoding="utf-8")
    immutable_manifest = write_manifest(
        immutable_report,
        command="chains",
        args=args,
        config={"chains": strategy.get("chains", {})},
        extra={"run_id": run_id, "chain_run_meta": result.meta},
    )

    daily_report = premiums_root / f"{result.meta['as_of']}.csv"
    daily_meta = premiums_root / f"{result.meta['as_of']}_meta.json"
    daily_view_dir = premiums_root / "views" / str(result.meta["as_of"])
    daily_view_dir.mkdir(parents=True, exist_ok=True)
    daily_entry_report = daily_view_dir / "entry_candidates.csv"
    daily_roll_exit_report = daily_view_dir / "roll_exit.csv"
    result.report.to_csv(daily_report, index=False)
    entry_report.to_csv(daily_entry_report, index=False)
    roll_exit_report.to_csv(daily_roll_exit_report, index=False)
    daily_meta.write_text(
        json.dumps(result.meta, indent=2, default=str) + "\n", encoding="utf-8")
    # Fail closed before promoting a run to latest: all compatibility views must
    # be faithful materializations of this immutable observation.
    verify_premium_archive(premiums_root, run_id)
    latest = premiums_root / "latest.json"
    latest_tmp = premiums_root / ".latest.json.tmp"
    latest_tmp.write_text(json.dumps({
        "run_id": run_id,
        "schema_name": result.meta.get("schema_name", PREMIUM_SCHEMA_NAME),
        "schema_version": result.meta.get("schema_version", PREMIUM_SCHEMA_VERSION),
        "as_of": result.meta["as_of"],
        "quote_provider": result.meta.get("quote_provider", {}),
        "immutable_report": str(immutable_report.relative_to(premiums_root)),
        "immutable_entry_report": str(
            immutable_entry_report.relative_to(premiums_root)),
        "immutable_roll_exit_report": str(
            immutable_roll_exit_report.relative_to(premiums_root)),
        "immutable_meta": str(immutable_meta.relative_to(premiums_root)),
        "immutable_manifest": str(immutable_manifest.relative_to(premiums_root)),
        "daily_report": daily_report.name,
        "daily_entry_report": str(daily_entry_report.relative_to(premiums_root)),
        "daily_roll_exit_report": str(
            daily_roll_exit_report.relative_to(premiums_root)),
    }, indent=2) + "\n", encoding="utf-8")
    latest_tmp.replace(latest)
    return {
        "immutable_report": immutable_report,
        "immutable_entry_report": immutable_entry_report,
        "immutable_roll_exit_report": immutable_roll_exit_report,
        "immutable_meta": immutable_meta,
        "immutable_manifest": immutable_manifest,
        "daily_report": daily_report,
        "daily_entry_report": daily_entry_report,
        "daily_roll_exit_report": daily_roll_exit_report,
        "daily_meta": daily_meta,
        "latest": latest,
    }


def runtime_metadata() -> dict:
    """Display/runtime sidecar fields attached to a chains run meta."""
    # Imported lazily so quote-quality helpers in chains.py stay the owner of
    # market-session classification without a circular import at module load.
    from utilities.options.chains import (
        MARKET_RTH,
        MARKET_UNKNOWN,
        market_session_at,
    )

    generated_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        eastern = generated_utc.astimezone(ZoneInfo("America/New_York"))
        session = market_session_at(generated_utc)
        is_rth = session == MARKET_RTH
        fetched_at = eastern.isoformat()
    except Exception:  # noqa: BLE001 - tz db is a non-fatal display detail
        session = MARKET_UNKNOWN
        is_rth = None
        fetched_at = None
    try:
        import yfinance
        yfinance_version = yfinance.__version__
    except Exception:  # noqa: BLE001 - the fetcher gives the actionable error
        yfinance_version = None
    try:
        from importlib.metadata import version
        tastytrade_version = version("tastytrade")
    except Exception:  # noqa: BLE001 - provider failure is recorded separately
        tastytrade_version = None
    return {
        "run_id": generated_utc.strftime("%Y%m%dT%H%M%S%fZ"),
        "generated_at_utc": generated_utc.isoformat(),
        "yfinance_version": yfinance_version,
        "tastytrade_version": tastytrade_version,
        "fetched_at_et": fetched_at,
        "market_session": session,
        "is_rth": is_rth,
        "rth_note": ("Tastytrade DXLink supplies side-specific provider quote "
                     "timestamps. Freshness uses the older bid/ask timestamp. "
                     "Yahoo values remain diagnostic-only when an exact "
                     "Tastytrade observation is unavailable; timestamped fresh "
                     "quotes use BID as the conservative seller fill."),
    }
