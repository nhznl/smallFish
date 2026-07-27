"""Deterministic fixture tests for the swing scanner's universe sourcing.
Not a full scan suite (run_scan needs configuration, events.csv,
and price history -- out of scope for this fix); covers the specific bug found
2026-07-17: scan.py sourced its candidate tickers from a raw filesystem glob of
data/{year}/*.txt, so a retired/delisted symbol's cache file (deliberately kept
on disk for audit history) meant scan.py would evaluate its now-permanently-
stale last cached row forever, with no gate ever excluding it. The fix checks
retired_symbols.csv directly for immediate exclusion.

Runnable standalone with ``python -m utilities.tests.test_scan`` (no network).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from utilities import universe as U
from studies.pre_earnings_momentum.scan import _scan_universe_symbols


def _write_registry(tmp: Path, rows: list[dict]) -> Path:
    reg = {}
    for row in rows:
        reg[row["symbol"]] = {
            "symbol": row["symbol"], "name": "", "type": row.get("type", "STOCK"),
            "memberships": set(), "source": row.get("source", "auto"),
            "pinned": row.get("pinned", False), "last_seen": "2026-07-17",
            "sector": ""}
    path = tmp / "universe.csv"
    U.write_registry(path, reg)
    return path


def test_scan_symbols_come_from_the_live_registry_not_a_filesystem_glob():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        reg_path = _write_registry(tmp, [
            {"symbol": "AAPL"},
            {"symbol": "MSFT"},
        ])
        retired_path = tmp / "retired_symbols.csv"  # no file -> empty retired set
        symbols = _scan_universe_symbols(registry_path=reg_path, retired_path=retired_path)
        assert symbols == ["AAPL", "MSFT"]


def test_scan_symbols_keep_registry_members_without_a_retirement():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        reg_path = _write_registry(tmp, [
            {"symbol": "AAPL"},
            {"symbol": "MASI"},
        ])
        retired_path = tmp / "retired_symbols.csv"
        symbols = _scan_universe_symbols(registry_path=reg_path, retired_path=retired_path)
        assert symbols == ["AAPL", "MASI"]


def test_scan_symbols_exclude_just_retired_rows_before_the_next_universe_refresh():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        # The REAL bug found live 2026-07-17: the scraper's staleness check
        # retires BLD/JHG/MASI into retired_symbols.csv immediately, but
        # The retirement journal changes immediately, before a later universe
        # refresh. Reading it directly must exclude the symbol in this window.
        reg_path = _write_registry(tmp, [
            {"symbol": "AAPL"},
            {"symbol": "MASI"},
        ])
        retired_path = tmp / "retired_symbols.csv"
        U.write_retired(retired_path,
                        {"MASI": {"last_seen": "2026-07-17", "reason": U.REASON_NO_DATA}})
        symbols = _scan_universe_symbols(registry_path=reg_path, retired_path=retired_path)
        assert symbols == ["AAPL"]
        assert "MASI" not in symbols


def test_scan_symbols_exclude_fully_dropped_rows():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        # A symbol dropped from EVERY source is removed from universe.csv
        # entirely -- the old filesystem-glob sourcing
        # would still pick up its lingering cache file; the registry won't.
        reg_path = _write_registry(tmp, [{"symbol": "AAPL"}])
        retired_path = tmp / "retired_symbols.csv"
        symbols = _scan_universe_symbols(registry_path=reg_path, retired_path=retired_path)
        assert symbols == ["AAPL"]


def _run_all():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
