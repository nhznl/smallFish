"""SQLite calendar repository. Utilities is the only writer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "003"
MIGRATIONS = tuple(sorted((Path(__file__).resolve().parent / "migrations").glob("*.sql")))


def _private(path: Path) -> None:
    try:
        path.chmod(0o700 if path.is_dir() else 0o600)
    except OSError:
        pass


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    _private(path.parent)
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    _migrate(connection)
    _private(path)
    for sidecar in (Path(f"{path}-wal"), Path(f"{path}-shm")):
        if sidecar.exists():
            _private(sidecar)
    return connection


def _migrate(connection: sqlite3.Connection) -> None:
    existing = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    applied = set()
    if existing:
        applied = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
    for migration in MIGRATIONS:
        version = migration.name.split("_", 1)[0]
        if version not in applied:
            connection.executescript(migration.read_text(encoding="utf-8"))
            applied.add(version)


def checkpoint(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
