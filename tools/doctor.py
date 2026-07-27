#!/usr/bin/env python3
"""Report smallFish's local state: `./commands.sh doctor`.

Strictly local and read-only. It makes **no network call and no broker call**,
and it never prints a secret value — only whether one is set, and a masked
fingerprint. Its output is safe to paste into a bug report.

The distinction it draws is the important one: a *required* failure means the
application will not work, an *optional* item that is unavailable simply means a
feature is switched off. Missing credentials are a capability state, not an
error.

Standard library only: doctor must be able to run before, and diagnose, a failed
environment setup.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from preflight import (  # noqa: E402
    REPO_ROOT,
    SECRET_KEYS,
    check_runtimes,
    mask,
    parse_env_file,
)

OK = "ok"
WARN = "warn"
FAIL = "FAIL"
OFF = "off"

_MARK = {OK: "  [  ok]", WARN: "  [warn]", FAIL: "  [FAIL]", OFF: "  [ off]"}


class Report:
    """Accumulates rows and tracks whether anything *required* failed."""

    def __init__(self) -> None:
        self.sections: list[tuple[str, list[tuple[str, str, str]]]] = []
        self.required_failures = 0

    def section(self, title: str) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        self.sections.append((title, rows))
        return rows

    def add(self, rows: list, status: str, label: str, detail: str = "",
            *, required: bool = False) -> None:
        rows.append((status, label, detail))
        if required and status == FAIL:
            self.required_failures += 1

    def render(self, brief: bool = False) -> None:
        for title, rows in self.sections:
            if brief and not rows:
                continue
            print(f"\n{title}")
            width = max((len(label) for _, label, _ in rows), default=0)
            for status, label, detail in rows:
                line = f"{_MARK[status]} {label:<{width}}"
                if detail:
                    line += f"  {detail}"
                print(line)


def _human_size(path: Path) -> str:
    try:
        return f"{sum(f.stat().st_size for f in path.rglob('*') if f.is_file()) / 1e6:.1f} MB"
    except OSError:
        return "unreadable"


def build_report(root: Path) -> Report:
    report = Report()
    env_path = root / "app.env"
    settings = parse_env_file(env_path.read_text(encoding="utf-8")) if env_path.is_file() else {}

    # Prefer a real exported value (commands.sh sources app.env) but fall back
    # to the file so doctor works when invoked directly.
    def setting(key: str, default: str = "") -> str:
        return os.environ.get(key) or settings.get(key, default)

    # ---------------------------------------------------------- runtimes
    rows = report.section("Runtimes")
    for name, found, ok, requirement in check_runtimes():
        report.add(rows, OK if ok else FAIL, name, f"{found} (needs {requirement})",
                   required=True)

    # ------------------------------------------------------ installation
    rows = report.section("Installation")
    for label, path in (
        ("utilities/.venv", root / "utilities/.venv/bin/python"),
        ("stock-app/.venv", root / "stock-app/.venv/bin/python"),
    ):
        if path.exists():
            report.add(rows, OK, label, "installed")
        else:
            report.add(rows, FAIL, label, "missing — run ./setup.sh", required=True)

    node_modules = root / "stock-app-ui/node_modules"
    if node_modules.is_dir():
        report.add(rows, OK, "UI node_modules", "installed")
    else:
        report.add(rows, FAIL, "UI node_modules", "missing — run ./setup.sh", required=True)

    static_index = root / "stock-app/static/index.html"
    if static_index.is_file():
        report.add(rows, OK, "built UI", "stock-app/static")
    else:
        report.add(rows, WARN, "built UI", "not built — run ./commands.sh build-ui "
                                           "(not needed if you use ng serve)")

    # --------------------------------------------------------- configuration
    rows = report.section("Configuration")
    if env_path.is_file():
        mode = oct(env_path.stat().st_mode & 0o777)[2:]
        report.add(rows, OK, "app.env", f"present (mode {mode})")
    else:
        report.add(rows, FAIL, "app.env", "missing — run ./setup.sh", required=True)

    for key in ("SFP_DATA_DIR", "SFP_LOG_DIR"):
        value = setting(key)
        if not value:
            report.add(rows, FAIL, key, "not set", required=True)
        elif value.startswith("/absolute/path/to/"):
            report.add(rows, FAIL, key, "still the template placeholder", required=True)
        elif not Path(value).expanduser().is_dir():
            report.add(rows, FAIL, key, f"{value} does not exist", required=True)
        else:
            report.add(rows, OK, key, value)

    host, port = setting("APP_HOST", "127.0.0.1"), setting("APP_PORT", "8000")
    if host in ("127.0.0.1", "localhost", "::1"):
        report.add(rows, OK, "APP_HOST", f"{host}:{port}")
    else:
        report.add(rows, WARN, "APP_HOST",
                   f"{host}:{port} — smallFish has no authentication layer; "
                   "do not expose it to an untrusted network")

    # ------------------------------------------------------------- data
    rows = report.section("Data")
    data_dir_value = setting("SFP_DATA_DIR")
    data_dir = Path(data_dir_value).expanduser() if data_dir_value else None

    if data_dir and data_dir.is_dir():
        years = sorted(p.name for p in data_dir.glob("[12][0-9][0-9][0-9]") if p.is_dir())
        if years:
            current = str(date.today().year)
            symbol_count = len(list((data_dir / years[-1]).glob("*.txt")))
            report.add(rows, OK, "price cache",
                       f"years {years[0]}–{years[-1]}, {symbol_count} symbols in {years[-1]}")
            if current not in years:
                report.add(rows, WARN, "current year",
                           f"no {current} data — run ./commands.sh bootstrap-data")
        else:
            report.add(rows, WARN, "price cache",
                       "empty — run ./commands.sh bootstrap-data")

        universe = data_dir / "universe.csv"
        if universe.is_file():
            rows_count = max(0, sum(1 for _ in universe.open(encoding="utf-8")) - 1)
            report.add(rows, OK, "universe registry", f"{rows_count} symbols")
        else:
            report.add(rows, WARN, "universe registry",
                       "missing — run ./commands.sh bootstrap-data")

        rotation = data_dir / "sector_rotation"
        report.add(rows, OK if rotation.is_dir() else WARN, "sector rotation",
                   "snapshot present" if rotation.is_dir()
                   else "none — run ./commands.sh sector-rotation")
        if data_dir.is_dir():
            report.add(rows, OK, "data directory size", _human_size(data_dir))
    else:
        report.add(rows, FAIL, "price cache", "data directory unavailable", required=True)

    # Bundled studies are packaged read-only artifacts; they must resolve even
    # when the mutable data root is elsewhere.
    bundled = root / "data/studies/catalog.json"
    runtime_catalog = (data_dir / "studies/catalog.json") if data_dir else None
    if runtime_catalog and runtime_catalog.is_file():
        report.add(rows, OK, "Research Studies", "catalog present in the data directory")
    elif bundled.is_file():
        report.add(rows, OK, "Research Studies", "bundled catalog (packaged with the repo)")
    else:
        report.add(rows, FAIL, "Research Studies",
                   "catalog missing — run ./commands.sh studies build", required=True)

    # --------------------------------------------- optional integrations
    rows = report.section("Optional integrations (blank is a valid state)")

    finnhub = setting("FINNHUB_API_KEY")
    report.add(rows, OK if finnhub else OFF, "Finnhub earnings",
               mask(finnhub) if finnhub
               else "not configured — upcoming earnings and the live pre-earnings scan are unavailable")

    tt_secret, tt_token = setting("TT_CLIENT_SECRET"), setting("TT_REFRESH_TOKEN")
    tt_env = setting("TT_ENV", "sandbox").lower()
    if tt_secret and tt_token:
        status = OK if tt_env in ("sandbox", "live") else FAIL
        detail = f"credentials present, TT_ENV={tt_env or '(unset)'}"
        if tt_env not in ("sandbox", "live"):
            detail = f"TT_ENV must be 'sandbox' or 'live', found {tt_env!r}"
        report.add(rows, status, "Tastytrade", detail)
    elif tt_secret or tt_token:
        report.add(rows, WARN, "Tastytrade",
                   "partially configured — both TT_CLIENT_SECRET and TT_REFRESH_TOKEN "
                   "are required. Run ./setup-brokerages.sh setup tastytrade")
    else:
        report.add(rows, OFF, "Tastytrade",
                   "not configured — the options ledger and quote collection are unavailable")

    st_id, st_key = setting("SNAPTRADE_CLIENT_ID"), setting("SNAPTRADE_CONSUMER_KEY")
    st_user, st_user_secret = setting("SNAPTRADE_USER_ID"), setting("SNAPTRADE_USER_SECRET")
    if st_id and st_key:
        if st_id.startswith("PERS-"):
            report.add(rows, OK, "SnapTrade",
                       "personal keys — link brokerages on the SnapTrade dashboard")
        elif st_user and st_user_secret:
            report.add(rows, OK, "SnapTrade", "commercial keys, user registered")
        else:
            report.add(rows, WARN, "SnapTrade",
                       "commercial keys without a registered user — run "
                       "./setup-brokerages.sh setup snaptrade")
    elif st_id or st_key:
        report.add(rows, WARN, "SnapTrade",
                   "partially configured — both SNAPTRADE_CLIENT_ID and "
                   "SNAPTRADE_CONSUMER_KEY are required")
    else:
        report.add(rows, OFF, "SnapTrade",
                   "not configured — the retirement ledger is unavailable")

    assert_no_secret_leak(report, settings)
    return report


def assert_no_secret_leak(report: Report, settings: dict[str, str]) -> None:
    """Refuse to emit a report containing a raw secret.

    A backstop, not the primary defence: every branch above is written to print
    masked or categorical values. This catches a future edit that forgets.
    """
    rendered = "\n".join(
        f"{label} {detail}"
        for _, rows in report.sections
        for _, label, detail in rows
    )
    for key in SECRET_KEYS:
        value = settings.get(key, "").strip()
        # Very short values would cause false positives against ordinary words.
        if len(value) >= 8 and value in rendered:
            raise SystemExit(
                f"doctor refused to print its report: the value of {key} appeared "
                "in the output. This is a bug — please report it."
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report smallFish's local state.")
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--brief", action="store_true",
                        help="omit the header and the closing guidance")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not args.brief:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"smallFish doctor — {root}\n{stamp}")
        print("Local checks only: no network, provider, or brokerage calls. "
              "Secrets are masked.")

    report = build_report(root)
    report.render()

    print()
    if report.required_failures:
        print(f"{report.required_failures} required check(s) failed. "
              "smallFish will not work until these are fixed.")
        print("Start with ./setup.sh, then see docs/TROUBLESHOOTING.md.")
        return 1

    print("All required checks passed. Items marked [ off] are optional "
          "integrations you have not configured; the core application does not "
          "need them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
