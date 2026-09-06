"""Guarded Study 5 single-weekly-decision exploratory replay."""

from __future__ import annotations

import argparse
import sys

from studies.pre_earnings_momentum.daily_redeployment import (
    POST_EVENT_WEEKLY_OPEN_DECISION_CONFIGS,
    main as run_daily_study,
    pinned_implementation_revision,
)


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--variant",
        required=True,
        choices=tuple(POST_EVENT_WEEKLY_OPEN_DECISION_CONFIGS),
    )
    parser.add_argument(
        "--confirm-weekly-open-decision-exploratory-run",
        action="store_true",
    )
    return parser.parse_known_args(argv)


def _single_int_option(argv: list[str], flag: str) -> int:
    positions = [index for index, value in enumerate(argv) if value == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise ValueError(f"exactly one {flag} is required")
    return int(argv[positions[0] + 1])


def _clean_pinned_commit() -> bool:
    try:
        _, dirty = pinned_implementation_revision()
    except ValueError:
        return False
    return not dirty


def _print_help() -> None:
    print(
        "usage: pre-earnings-post-event-weekly-open-decision "
        "--variant {baseline,risk-on} --year YEAR --origin-year 2010 "
        "[--state-in PATH] [--cache-root PATH] [--output-root PATH] "
        "[--run-id ID] --confirm-weekly-open-decision-exploratory-run\n\n"
        "Runs the approved Study 5 single pre-open weekly decision method. "
        "The 2010-2025 interval is retrospective exploratory evidence only."
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
    if not args.confirm_weekly_open_decision_exploratory_run:
        print(
            "Study 5 is unauthorized without "
            "--confirm-weekly-open-decision-exploratory-run.",
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
    if origin != 2010 or not 2010 <= year <= 2025:
        print(
            "Study 5 permits only origin 2010 and exploratory years 2010-2025",
            file=sys.stderr,
        )
        return 2
    if not _clean_pinned_commit():
        print(
            "Study 5 requires a clean committed worktree and pinned HEAD",
            file=sys.stderr,
        )
        return 2
    return run_daily_study(
        [
            *remainder,
            "--confirm-historical-run",
            "--confirm-weekly-open-decision-exploratory-run",
            "--config",
            str(POST_EVENT_WEEKLY_OPEN_DECISION_CONFIGS[args.variant]),
        ],
        command_name="pre-earnings-post-event-weekly-open-decision",
    )


if __name__ == "__main__":
    raise SystemExit(main())
