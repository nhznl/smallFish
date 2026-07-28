#!/usr/bin/env python3
"""Prerequisite checks and first-run configuration for ``setup.sh``.

Standard library only, and deliberately so: this runs on the system
interpreter *before* either project virtual environment exists.

``setup.sh`` shells out to the subcommands below. The functions they wrap are
plain and importable, so the decisions that matter — is this runtime supported,
what does a fresh ``app.env`` contain, which values are secret — are unit
tested rather than buried in shell.

Subcommands:

    check-runtimes      verify Python/Node/npm/Git against the support matrix
    ensure-env          create app.env from the template if it is missing
    ensure-dirs         create the mutable runtime directories
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Keep in step with docs/SUPPORT_MATRIX.md.
MIN_PYTHON = (3, 12)
MIN_NODE = (22, 22, 3)
MIN_NPM = (10,)
MIN_GIT = (2, 30)

# Runtime directories setup creates. Everything else is created on demand by
# the command that owns it.
RUNTIME_DIRS = ("data", "logs")

# Settings whose values must never be echoed by setup, doctor, or brokerage
# status output.
SECRET_KEYS = frozenset({
    "FINNHUB_API_KEY",
    "TT_CLIENT_ID",
    "TT_CLIENT_SECRET",
    "TT_REFRESH_TOKEN",
    "SNAPTRADE_CLIENT_ID",
    "SNAPTRADE_CONSUMER_KEY",
    "SNAPTRADE_USER_ID",
    "SNAPTRADE_USER_SECRET",
})


class PreflightError(RuntimeError):
    """A prerequisite is missing or unsupported."""


# --------------------------------------------------------------- versions

def parse_version(text: str) -> tuple[int, ...]:
    """Pull the first dotted numeric version out of a tool's --version output."""
    match = re.search(r"(\d+(?:\.\d+)*)", text or "")
    if not match:
        raise PreflightError(f"could not parse a version from {text!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def meets(found: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    """True when ``found`` is at least ``minimum``, compared field by field."""
    return found[: len(minimum)] >= minimum


def _tool_version(command: list[str]) -> tuple[int, ...] | None:
    executable = shutil.which(command[0])
    if executable is None:
        return None
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return parse_version(result.stdout or result.stderr)


def format_version(version: tuple[int, ...] | None) -> str:
    return ".".join(str(part) for part in version) if version else "not found"


def check_runtimes(*, require_ui: bool = True,
                   require_python: bool = True) -> list[tuple[str, str, bool, str]]:
    """Return ``(name, found, ok, requirement)`` rows for the support matrix.

    Reports every runtime rather than stopping at the first failure, so one run
    tells the user everything they need to install.
    """
    rows: list[tuple[str, str, bool, str]] = []

    python_version = tuple(sys.version_info[:2])
    rows.append((
        "Python", format_version(python_version),
        (not require_python) or meets(python_version, MIN_PYTHON),
        f">= {format_version(MIN_PYTHON)}",
    ))

    git_version = _tool_version(["git", "--version"])
    rows.append((
        "Git", format_version(git_version),
        git_version is not None and meets(git_version, MIN_GIT),
        f">= {format_version(MIN_GIT)}",
    ))

    node_version = _tool_version(["node", "--version"])
    rows.append((
        "Node.js", format_version(node_version),
        (not require_ui) or (node_version is not None and meets(node_version, MIN_NODE)),
        f">= {format_version(MIN_NODE)} (LTS)",
    ))

    npm_version = _tool_version(["npm", "--version"])
    rows.append((
        "npm", format_version(npm_version),
        (not require_ui) or (npm_version is not None and meets(npm_version, MIN_NPM)),
        f">= {format_version(MIN_NPM)}",
    ))

    return rows


# --------------------------------------------------------------- app.env

def shell_quote(value: str) -> str:
    """Quote a value so `set -a; . app.env` assigns it verbatim.

    commands.sh sources app.env as shell, so an unquoted checkout path
    containing a space would be split into an assignment plus a stray command.
    Single quotes also stop `$` and backticks from expanding, which is what a
    filesystem path wants.
    """
    if value and not re.search(r"""[\s"'$`\\!*?~<>|&;()\[\]{}#]""", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def render_app_env(template: str, root: Path) -> str:
    """Point the template's placeholder paths at this checkout.

    Only the two placeholder path assignments are rewritten. Comments, ordering,
    and every other setting — including the empty credential slots — are left
    exactly as written, so the generated file still documents itself.
    """
    replacements = {
        "SFP_DATA_DIR": str(root / "data"),
        "SFP_LOG_DIR": str(root / "logs"),
    }
    lines = []
    for line in template.splitlines():
        key, separator, _ = line.partition("=")
        name = key.strip()
        if separator and name in replacements and not line.lstrip().startswith("#"):
            lines.append(f"{name}={shell_quote(replacements[name])}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def ensure_env(root: Path = REPO_ROOT) -> tuple[Path, bool]:
    """Create ``app.env`` from the template when absent. Never overwrite.

    Returns ``(path, created)``. An existing file is left byte-for-byte alone —
    it holds the user's credentials, and setup must be safe to rerun.
    """
    destination = root / "app.env"
    if destination.exists():
        return destination, False
    template_path = root / "app.env.example"
    if not template_path.is_file():
        raise PreflightError(f"missing configuration template: {template_path}")
    destination.write_text(
        render_app_env(template_path.read_text(encoding="utf-8"), root),
        encoding="utf-8",
    )
    # The file is a credential store the moment the user fills it in.
    os.chmod(destination, 0o600)
    return destination, True


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` settings, ignoring comments, blanks, and ``export``."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        key, separator, value = stripped.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def mask(value: str) -> str:
    """Render a secret as evidence-of-presence only. Never reversible."""
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "set (" + "*" * len(value) + ")"
    return f"set ({value[:2]}…{value[-2:]}, len={len(value)})"


# --------------------------------------------------------------- directories

def ensure_dirs(root: Path = REPO_ROOT) -> list[Path]:
    """Create the mutable runtime directories. Idempotent; never deletes."""
    created = []
    for name in RUNTIME_DIRS:
        target = root / name
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            created.append(target)
    return created


# --------------------------------------------------------------- entry point

def _cmd_check_runtimes(args: argparse.Namespace) -> int:
    rows = check_runtimes(require_ui=not args.skip_ui,
                          require_python=not args.skip_python)
    width = max(len(name) for name, _, _, _ in rows)
    failed = []
    for name, found, ok, requirement in rows:
        status = "ok" if ok else "FAIL"
        print(f"  [{status:>4}] {name:<{width}}  {found:<12} (needs {requirement})")
        if not ok:
            failed.append(name)
    if failed:
        print()
        print(f"Unsupported or missing: {', '.join(failed)}.")
        print("See docs/SUPPORT_MATRIX.md for the supported versions and how to")
        print("install them (pyenv/asdf for Python, nvm for Node).")
        return 1
    return 0


def _cmd_ensure_env(args: argparse.Namespace) -> int:
    path, created = ensure_env(Path(args.root))
    print(f"  {'created' if created else 'kept existing'}  {path}")
    return 0


def _cmd_ensure_dirs(args: argparse.Namespace) -> int:
    created = ensure_dirs(Path(args.root))
    for path in created:
        print(f"  created  {path}")
    if not created:
        print("  runtime directories already present")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    runtimes = sub.add_parser("check-runtimes")
    runtimes.add_argument("--skip-ui", action="store_true")
    runtimes.add_argument("--skip-python", action="store_true")
    runtimes.set_defaults(func=_cmd_check_runtimes)

    env = sub.add_parser("ensure-env")
    env.add_argument("--root", default=str(REPO_ROOT))
    env.set_defaults(func=_cmd_ensure_env)

    dirs = sub.add_parser("ensure-dirs")
    dirs.add_argument("--root", default=str(REPO_ROOT))
    dirs.set_defaults(func=_cmd_ensure_dirs)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PreflightError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
