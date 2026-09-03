"""Guarded 2010-2021 historical extension of the Friday-only replay."""

from __future__ import annotations

import argparse
import sys

from studies.pre_earnings_momentum.daily_redeployment import (
    POST_EVENT_WEEKLY_BATCH_EXTENSION_CONFIGS,
    main as run_daily_study,
)


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--variant", required=True, choices=tuple(POST_EVENT_WEEKLY_BATCH_EXTENSION_CONFIGS),
    )
    parser.add_argument("--confirm-weekly-batch-extension-run", action="store_true")
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args_list or "-h" in args_list:
        print(
            "usage: pre-earnings-post-event-weekly-batch-extension "
            "--variant {baseline,risk-on} --year YEAR --origin-year 2010 "
            "[--state-in PATH] [--cache-root PATH] [--output-root PATH] [--run-id ID] "
            "--confirm-weekly-batch-extension-run"
        )
        return 0
    try:
        args, remainder = parse_args(args_list)
    except SystemExit as exc:
        return int(exc.code)
    if not args.confirm_weekly_batch_extension_run:
        print("historical extension is unauthorized without --confirm-weekly-batch-extension-run", file=sys.stderr)
        return 2
    if "--config" in remainder:
        print("--config is not accepted; --variant selects a frozen config", file=sys.stderr)
        return 2
    try:
        index = remainder.index("--year")
        year = int(remainder[index + 1])
        origin_index = remainder.index("--origin-year")
        origin = int(remainder[origin_index + 1])
    except (ValueError, IndexError):
        print("exactly one --year and --origin-year are required", file=sys.stderr)
        return 2
    if origin != 2010 or not 2010 <= year <= 2021:
        print("historical extension permits only origin 2010 and years 2010-2021", file=sys.stderr)
        return 2
    return run_daily_study(
        [*remainder, "--confirm-historical-run", "--config",
         str(POST_EVENT_WEEKLY_BATCH_EXTENSION_CONFIGS[args.variant])],
        command_name="pre-earnings-post-event-weekly-batch-extension",
    )


if __name__ == "__main__":
    raise SystemExit(main())
