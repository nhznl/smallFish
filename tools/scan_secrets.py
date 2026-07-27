#!/usr/bin/env python3
"""Scan smallFish for committed secrets and personal data.

Two modes:

    python3 tools/scan_secrets.py              # tracked working-tree files (CI)
    python3 tools/scan_secrets.py --history    # every reachable Git blob (manual)

The working-tree scan is fast and runs in CI on every pull request. The history
scan walks all reachable objects, takes minutes, and is intentionally manual --
its findings need a human decision.

Findings are always reported with masked excerpts. This tool never prints a
secret value, so its output is safe to paste into a CI log or an issue.

Standard library only, so it runs without either project virtual environment.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (name, pattern, blocking). Non-blocking patterns are reported for review but
# do not fail the scan; they flag things that are usually, but not always, a
# problem.
PATTERNS: list[tuple[str, re.Pattern[bytes], bool]] = [
    ("google_oauth_client_secret", re.compile(rb"GOCSPX-[A-Za-z0-9_\-]{20,}"), True),
    ("google_oauth_client_id",
     re.compile(rb"\d{10,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com"), True),
    ("google_refresh_token", re.compile(rb"\b1//[A-Za-z0-9_\-]{60,}"), True),
    ("aws_access_key", re.compile(rb"AKIA[0-9A-Z]{16}"), True),
    ("slack_token", re.compile(rb"xox[baprs]-[A-Za-z0-9\-]{10,}"), True),
    ("github_token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"), True),
    ("private_key_block", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), True),
    ("snaptrade_personal_client_id", re.compile(rb"PERS-[A-Za-z0-9\-]{4,}"), True),
    ("jwt", re.compile(
        rb"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), True),
    ("assigned_secret_literal", re.compile(
        rb"(?i)\b(client_secret|clientsecret|consumer_key|refresh_token|refreshtoken"
        rb"|api_key|apikey|secret_key|access_token|user_secret|password|passwd)"
        rb"\s*[:=]\s*[\'\"]([^\'\"\s]{16,})[\'\"]"), True),
    ("email_address", re.compile(
        rb"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.(?:com|net|org|io|edu|gov)\b"), False),
    ("absolute_home_path", re.compile(rb"/(?:Users|home)/[A-Za-z0-9._\-]+/"), False),
]

# Paths where a pattern is expected and harmless.
ALLOWLIST: list[tuple[str, re.Pattern[str]]] = [
    # Documentation of the scanner and the audit necessarily names the patterns.
    ("*", re.compile(r"^tools/scan_secrets\.py$")),
    # Setup docs legitimately show example absolute paths and placeholders.
    ("absolute_home_path", re.compile(r"^(docs/|README\.md|app\.env\.example)")),
    ("email_address", re.compile(r"^(docs/|CODE_OF_CONDUCT\.md|SECURITY\.md)")),
    # A SnapTrade PERS- prefix is a public client-id form, not a secret, and the
    # provider tests need it literally to exercise personal-vs-commercial
    # detection. Scoped to test files so real config is still checked.
    ("snaptrade_personal_client_id", re.compile(r"(^|/)tests?/|_test\.py$|\.spec\.ts$")),
]

SKIP_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff",
               ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".gz", ".map")


def allowed(name: str, path: str) -> bool:
    return any(
        (scope in ("*", name)) and rx.search(path)
        for scope, rx in ALLOWLIST
    )


def mask(raw: bytes) -> str:
    value = raw.decode("utf-8", "replace").strip()
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-2:]} (len={len(value)})"


def git(*args: str) -> str:
    return subprocess.run(("git", *args), cwd=REPO,
                          capture_output=True, text=True, check=True).stdout


def scan_bytes(payload: bytes, path: str, findings: dict) -> None:
    if b"\x00" in payload[:4096]:
        return
    for name, rx, blocking in PATTERNS:
        if allowed(name, path):
            continue
        for match in rx.finditer(payload):
            findings[(name, path, blocking)].add(mask(match.group(0)))


def scan_worktree(findings: dict) -> int:
    files = [f for f in git("ls-files").splitlines() if f]
    checked = 0
    for rel in files:
        if rel.lower().endswith(SKIP_SUFFIX):
            continue
        target = REPO / rel
        if not target.is_file():
            continue
        checked += 1
        scan_bytes(target.read_bytes(), rel, findings)
    return checked


def scan_history(findings: dict) -> int:
    blobs: dict[str, str] = {}
    for line in git("rev-list", "--objects", "--all").splitlines():
        sha, _, path = line.partition(" ")
        path = path.strip()
        if not path or path.lower().endswith(SKIP_SUFFIX):
            continue
        blobs.setdefault(sha, path)

    shas = list(blobs)
    proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=REPO,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert proc.stdin and proc.stdout

    def feed() -> None:
        # Written from a thread: git streams object contents back while we are
        # still writing, so a single-threaded write would deadlock on the pipe.
        try:
            for sha in shas:
                proc.stdin.write(f"{sha}\n".encode())
            proc.stdin.close()
        except BrokenPipeError:
            pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()

    checked = 0
    for sha in shas:
        header = proc.stdout.readline()
        if not header:
            break
        parts = header.split()
        if len(parts) < 3:
            continue
        obj_type, size = parts[1].decode(), int(parts[2])
        payload = proc.stdout.read(size)
        proc.stdout.read(1)
        if obj_type != "blob":
            continue
        checked += 1
        scan_bytes(payload, blobs[sha], findings)

    writer.join(timeout=5)
    return checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--history", action="store_true",
                        help="scan every reachable Git blob instead of the working tree")
    args = parser.parse_args()

    findings: dict[tuple[str, str, bool], set[str]] = defaultdict(set)
    scope = "reachable Git history" if args.history else "tracked working tree"
    checked = scan_history(findings) if args.history else scan_worktree(findings)

    print(f"scanned {checked} objects in the {scope}\n")

    blocking = sorted(k for k in findings if k[2])
    review = sorted(k for k in findings if not k[2])

    for label, keys in (("BLOCKING", blocking), ("REVIEW", review)):
        if not keys:
            continue
        print(f"--- {label} ---")
        for name, path, _ in keys:
            values = sorted(findings[(name, path, _)])
            print(f"[{name}] {path}")
            for value in values[:3]:
                print(f"    {value}")
            if len(values) > 3:
                print(f"    ... {len(values) - 3} more distinct")
        print()

    if blocking:
        print(f"FAIL: {len(blocking)} blocking finding(s).")
        if args.history:
            print("Revoke any exposed credential at its provider FIRST. Removing "
                  "a file, or even rewriting history, does not invalidate a live "
                  "token.")
        return 1

    print("PASS: no blocking findings."
          + (f" {len(review)} item(s) flagged for review." if review else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
