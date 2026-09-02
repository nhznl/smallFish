"""Guarded CLI for the brokerage per-share post-earnings study."""

from __future__ import annotations

import argparse
import sys

from studies.pre_earnings_momentum.daily_redeployment import (
    POST_EVENT_LOW_FEE_CONFIGS,
    main as run_daily_study,
)


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="pre-earnings-post-event-low-fee-study",
        description=(
            "Authorized 2010-2022 development tooling for the equal-arm "
            "post-earnings study with $0.0008 per filled share per side."
        ),
        add_help=False,
    )
    parser.add_argument(
        "--variant", required=True, choices=tuple(POST_EVENT_LOW_FEE_CONFIGS),
    )
    parser.add_argument(
        "--confirm-low-fee-development-run",
        action="store_true",
        help="Required after the owner's explicit 2010-2022 authorization.",
    )
    return parser.parse_known_args(argv)


def _print_help() -> None:
    print(
        "usage: pre-earnings-post-event-low-fee-study --variant "
        "{baseline,risk-on} --year YEAR --origin-year 2010 "
        "[--state-in PATH] [--cache-root PATH] [--output-root PATH] "
        "[--run-id ID] --confirm-low-fee-development-run\n\n"
        "Runs the separately identified $0.0008-per-filled-share development "
        "study. Only 2010-2022 is authorized; 2023-2025 remains unavailable."
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
    if not args.confirm_low_fee_development_run:
        print(
            "Low-fee development run is unauthorized without "
            "--confirm-low-fee-development-run.",
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
    if origin != 2010:
        print("low-fee development runs require --origin-year 2010", file=sys.stderr)
        return 2
    if not 2010 <= year <= 2022:
        print("only development years 2010-2022 are authorized", file=sys.stderr)
        return 2
    return run_daily_study(
        [
            *remainder,
            "--confirm-historical-run",
            "--config",
            str(POST_EVENT_LOW_FEE_CONFIGS[args.variant]),
        ],
        command_name="pre-earnings-post-event-low-fee-study",
    )


if __name__ == "__main__":
    raise SystemExit(main())
