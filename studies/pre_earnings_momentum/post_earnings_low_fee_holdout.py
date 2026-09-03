"""Guarded CLI for the one-shot 2023-2025 low-fee holdout."""

from __future__ import annotations

import argparse
import sys

from studies.pre_earnings_momentum.daily_redeployment import (
    POST_EVENT_LOW_FEE_HOLDOUT_CONFIGS,
    main as run_daily_study,
)


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--variant", required=True, choices=tuple(POST_EVENT_LOW_FEE_HOLDOUT_CONFIGS),
    )
    parser.add_argument("--confirm-low-fee-holdout", action="store_true")
    return parser.parse_known_args(argv)


def _single_int_option(argv: list[str], flag: str) -> int:
    positions = [index for index, value in enumerate(argv) if value == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise ValueError(f"exactly one {flag} is required")
    return int(argv[positions[0] + 1])


def _print_help() -> None:
    print(
        "usage: pre-earnings-post-event-low-fee-holdout --variant "
        "{baseline,risk-on} --year YEAR --origin-year 2023 [--state-in PATH] "
        "[--cache-root PATH] [--output-root PATH] [--run-id ID] "
        "--confirm-low-fee-holdout\n\n"
        "Runs the one-shot, independently capitalized 2023-2025 holdout."
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
    if not args.confirm_low_fee_holdout:
        print(
            "Low-fee holdout is unauthorized without --confirm-low-fee-holdout.",
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
    if origin != 2023:
        print("low-fee holdout runs require --origin-year 2023", file=sys.stderr)
        return 2
    if not 2023 <= year <= 2025:
        print("only holdout years 2023-2025 are authorized", file=sys.stderr)
        return 2
    return run_daily_study(
        [
            *remainder,
            "--confirm-historical-run",
            "--config",
            str(POST_EVENT_LOW_FEE_HOLDOUT_CONFIGS[args.variant]),
        ],
        command_name="pre-earnings-post-event-low-fee-holdout",
    )


if __name__ == "__main__":
    raise SystemExit(main())
