#!/usr/bin/env python3
"""Check that the documentation still describes this repository.

Three classes of drift, all of which have bitten this project before:

1. **Dead relative links** — a README pointing at a file that moved or was
   deleted.
2. **Missing referenced files** — prose naming a config or module path that no
   longer exists.
3. **Command drift** — documentation promising a `./commands.sh` subcommand the
   dispatcher does not implement, or a subcommand nobody documents.

Standard library only, so CI can run it without either virtual environment.

    python3 tools/check_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "__pycache__",
             ".pytest_cache", "static", "data", "logs"}

# Markdown link targets that are deliberately not repository paths.
EXTERNAL = re.compile(r"^(https?:|mailto:|#)")

# Inline code spans that look like repository paths and are worth verifying.
PATH_LIKE = re.compile(
    r"`([A-Za-z0-9_.\-/]+\.(?:py|ts|html|css|scss|md|json|yaml|yml|txt|sh))`")

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# Documents that deliberately name paths which do not exist -- a plan describing
# files yet to be created, say. Path-existence checks are skipped for these.
NARRATIVE_FILES: set[str] = {
    "docs/OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md",
    "docs/CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md",
    "docs/CONTRACT_TIGHTENING_PHASE4_DESIGN.md",
    "docs/ANGULAR_LIFECYCLE_TESTS_PHASE5_DESIGN.md",
    "docs/OPTIONS_ACTIVITY_DECOMPOSITION_DESIGN.md",
    "docs/CHAINS_PIPELINE_DECOMPOSITION_DESIGN.md",
    "docs/BETA_GREEK_CONSUMER_MEASUREMENT.md",
}

# Roots holding generated runtime artifacts. Documentation legitimately names
# paths under these, and they do not exist in a clean checkout -- only after a
# scrape, bootstrap, or sync has run. Validating them passes on a developer
# machine and fails in CI, which is exactly the drift this tool exists to catch,
# so existence is not required for them.
#
# data/studies/ is the exception: those artifacts are committed. They are
# checked normally because they resolve from the repository root.
GENERATED_ROOTS = ("data/", "logs/")

# Paths that appear in docs as illustrative examples, not real files.
EXAMPLE_PATHS = {
    "utilities/config/universe.local.yaml",   # created by the user, git-ignored
    "app.env",                                # created by setup.sh
    "data/universe.csv",                      # generated
    "data/retired_symbols.csv",               # generated
    "docs/screenshots/momentum-scanner.png",  # added in the screenshots phase
}


def markdown_files() -> list[Path]:
    return sorted(
        path for path in REPO_ROOT.rglob("*.md")
        if not any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts)
    )


def check_links(path: Path, body: str) -> list[str]:
    problems = []
    for target in MARKDOWN_LINK.findall(body):
        target = target.strip()
        if not target or EXTERNAL.match(target):
            continue
        clean = target.split("#", 1)[0].split("?", 1)[0]
        if not clean:
            continue
        resolved = (path.parent / clean).resolve()
        if str(clean) in EXAMPLE_PATHS or clean in EXAMPLE_PATHS:
            continue
        if not resolved.exists():
            problems.append(f"{path.relative_to(REPO_ROOT)}: dead link -> {target}")
    return problems


def check_referenced_paths(path: Path, body: str) -> list[str]:
    if path.relative_to(REPO_ROOT).as_posix() in NARRATIVE_FILES:
        return []

    problems = []
    for candidate in PATH_LIKE.findall(body):
        if candidate in EXAMPLE_PATHS or "/" not in candidate:
            continue
        if candidate.startswith(GENERATED_ROOTS) and not (REPO_ROOT / candidate).exists():
            continue
        # A path may be written relative to the file that mentions it, or from
        # the repository root. Either resolving is good enough.
        if (path.parent / candidate).exists() or (REPO_ROOT / candidate).exists():
            continue
        # Only complain about paths that look rooted in this repository at all,
        # so prose mentioning an unrelated file does not fail the check.
        head = candidate.split("/", 1)[0]
        if head != ".." and not (REPO_ROOT / head).exists():
            continue
        problems.append(
            f"{path.relative_to(REPO_ROOT)}: references missing file -> {candidate}")
    return problems


def commands_sh_subcommands() -> set[str]:
    """Subcommands the dispatcher actually implements."""
    body = (REPO_ROOT / "commands.sh").read_text(encoding="utf-8")
    implemented = set(re.findall(r'\[ "\$1" = "([a-z-]+)" \]', body))
    case_block = body.split("case \"$1\" in", 1)[-1]
    implemented |= set(re.findall(r"^\s{2}([a-z][a-z-]*)\)", case_block, re.MULTILINE))
    return implemented


def documented_subcommands() -> set[str]:
    """Subcommands the README's command table promises."""
    body = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    return set(re.findall(r"`\./commands\.sh ([a-z][a-z-]*)", body))


def check_commands() -> list[str]:
    implemented = commands_sh_subcommands()
    documented = documented_subcommands()

    problems = []
    for name in sorted(documented - implemented):
        problems.append(
            f"README.md documents './commands.sh {name}' but commands.sh does not "
            "implement it")
    for name in sorted(implemented - documented):
        problems.append(
            f"commands.sh implements '{name}' but README.md does not document it")
    return problems


def main() -> int:
    problems: list[str] = []
    files = markdown_files()
    for path in files:
        body = path.read_text(encoding="utf-8")
        problems.extend(check_links(path, body))
        problems.extend(check_referenced_paths(path, body))
    problems.extend(check_commands())

    print(f"checked {len(files)} markdown files")
    if problems:
        print(f"\n{len(problems)} problem(s):\n")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("PASS: links, referenced paths, and command references all resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
