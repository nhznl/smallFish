"""Guarded Friday-only execution replay for the post-earnings strategy."""

from __future__ import annotations

import argparse
import sys

from studies.pre_earnings_momentum.daily_redeployment import (
    POST_EVENT_WEEKLY_BATCH_CONFIGS,
    main as run_daily_study,
)


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="pre-earnings-post-event-weekly-batch",
        description=(
            "Exploratory 2022-2025 Friday-only execution replay for the "
            "equal-arm post-earnings strategy."
        ),
        add_help=False,
    )
    parser.add_argument(
        "--variant", required=True, choices=tuple(POST_EVENT_WEEKLY_BATCH_CONFIGS),
    )
    parser.add_argument(
        "--confirm-weekly-batch-development-run",
        action="store_true",
        help="Required after the owner's explicit 2022-2025 authorization.",
    )
    return parser.parse_known_args(argv)


def _print_help() -> None:
    print(
        "usage: pre-earnings-post-event-weekly-batch --variant {baseline,risk-on} "
        "--year YEAR --origin-year 2022 [--state-in PATH] [--cache-root PATH] "
        "[--output-root PATH] [--run-id ID] "
        "--confirm-weekly-batch-development-run\n\n"
        "Runs the separate Friday-only execution replay.  It is limited to "
        "the already-observed 2022-2025 exploratory development window."
    )


def _single_int_option(argv: list[str], flag: str) -> int:
    positions = [index for index, value in enumerate(argv) if value == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise ValueError(f"exactly one {flag} is required")
    return int(argv[positions[0] + 1])


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args_list or "-h" in args_list:
        _print_help()
        return 0
    try:
        args, remainder = parse_args(args_list)
    except SystemExit as exc:
        return int(exc.code)
    if not args.confirm_weekly_batch_development_run:
        print(
            "Weekly-batch development run is unauthorized without "
            "--confirm-weekly-batch-development-run.",
            file=sys.stderr,
        )
        return 2
    if "--config" in remainder:
        print("--config is not accepted; --variant selects a frozen config", file=sys.stderr)
        return 2
    try:
        year = _single_int_option(remainder, "--year")
        origin = _single_int_option(remainder, "--origin-year")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if origin != 2022:
        print("weekly-batch development runs require --origin-year 2022", file=sys.stderr)
        return 2
    if not 2022 <= year <= 2025:
        print("only exploratory development years 2022-2025 are authorized", file=sys.stderr)
        return 2
    return run_daily_study(
        [
            *remainder,
            "--confirm-historical-run",
            "--config",
            str(POST_EVENT_WEEKLY_BATCH_CONFIGS[args.variant]),
        ],
        command_name="pre-earnings-post-event-weekly-batch",
    )


if __name__ == "__main__":
    raise SystemExit(main())
