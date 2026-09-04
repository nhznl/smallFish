"""Allowlisted Study 4 live evaluator command."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from studies.pre_earnings_momentum.operational.evaluator import (
    LiveHoldings,
    default_config,
    evaluate_live_session,
    write_artifact,
)
from studies.pre_earnings_momentum.operational.market import (
    load_live_market,
    load_upcoming_forecasts,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="study4-live-evaluate")
    parser.add_argument("--session", required=True)
    parser.add_argument("--holdings", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--active-bucket", type=float, required=True)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--universe", type=Path)
    parser.add_argument("--retired", type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--earnings-history", type=Path)
    return parser.parse_args(argv)


def _data_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    value = os.environ.get("SFP_DATA_DIR", "").strip()
    if not value:
        raise SystemExit("SFP_DATA_DIR is required unless --cache-root is provided")
    return Path(value).expanduser().resolve()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = date.fromisoformat(args.session)
    holdings_payload = json.loads(args.holdings.read_text(encoding="utf-8"))
    holdings = LiveHoldings.from_payload(holdings_payload)
    cache_root = _data_root(args.cache_root)
    universe_csv = args.universe or cache_root / "universe.csv"
    retired_csv = args.retired or cache_root / "retired_symbols.csv"
    events_csv = args.events or cache_root / "events.csv"
    history = args.earnings_history or cache_root / "earnings_history.csv"
    cfg = default_config()
    market = load_live_market(
        session=session,
        cache_root=cache_root,
        universe_csv=universe_csv,
        retired_csv=retired_csv,
        earnings_history=history,
        warmup_years=cfg.warmup_calendar_years,
    )
    artifact = evaluate_live_session(
        cfg=cfg,
        market=market,
        session=session,
        holdings=holdings,
        active_bucket=args.active_bucket,
        forecast_overrides=load_upcoming_forecasts(events_csv),
    )
    write_artifact(artifact, args.output)
    print(json.dumps({
        "status": "ok",
        "session": artifact["session"],
        "artifactHash": artifact["artifactHash"],
        "isCutoff": artifact["isCutoff"],
        "scanRows": len(artifact["scanRows"]),
        "planItems": len(artifact["planItems"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
