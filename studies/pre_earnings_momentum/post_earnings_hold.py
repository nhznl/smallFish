"""Guarded CLI for the unrun post-earnings-hold development variants."""

from __future__ import annotations

import argparse
import sys

from studies.pre_earnings_momentum.daily_redeployment import (
    POST_EVENT_CONFIGS,
    main as run_daily_study,
)


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="pre-earnings-post-event-study",
        description=(
            "Unrun development tooling for the equal-arm post-earnings T+7 study. "
            "Every historical invocation requires explicit owner authorization."
        ),
        add_help=False,
    )
    parser.add_argument(
        "--variant", required=True, choices=tuple(POST_EVENT_CONFIGS),
    )
    parser.add_argument(
        "--confirm-historical-run",
        action="store_true",
        help="Required only after the owner separately authorizes the historical run.",
    )
    return parser.parse_known_args(argv)


def _print_help() -> None:
    print(
        "usage: pre-earnings-post-event-study --variant "
        "{baseline,risk-on,risk-on-neutral} --year YEAR "
        "[--origin-year YEAR] [--state-in PATH] [--cache-root PATH] "
        "[--output-root PATH] [--run-id ID] --confirm-historical-run\n\n"
        "Implements the equal-arm post-earnings study. T+7 exits at the open of "
        "the seventh SPY session strictly after the event date. No historical "
        "run is authorized merely because this command exists."
    )


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args_list or "-h" in args_list:
        _print_help()
        return 0
    try:
        args, remainder = parse_args(args_list)
    except SystemExit as exc:
        return int(exc.code)
    if not args.confirm_historical_run:
        print(
            "Historical post-earnings study run is unauthorized. Refusing to run "
            "without --confirm-historical-run.",
            file=sys.stderr,
        )
        return 2
    if "--config" in remainder:
        print("--config is not accepted; --variant selects a frozen config", file=sys.stderr)
        return 2
    return run_daily_study(
        [*remainder, "--config", str(POST_EVENT_CONFIGS[args.variant])],
        command_name="pre-earnings-post-event-study",
    )


if __name__ == "__main__":
    raise SystemExit(main())
