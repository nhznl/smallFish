"""SQLite execution ledger for Study 4 live management."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS strategy_versions (
    id INTEGER PRIMARY KEY,
    protocol_id TEXT NOT NULL,
    operational_id TEXT NOT NULL,
    config_json TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capital_versions (
    id INTEGER PRIMARY KEY,
    effective_at TEXT NOT NULL,
    target_bucket REAL NOT NULL,
    active_bucket REAL NOT NULL,
    production_cap REAL,
    cash_flow_status TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_cycles (
    id INTEGER PRIMARY KEY,
    iso_week TEXT NOT NULL UNIQUE,
    cutoff_session TEXT,
    execution_session TEXT,
    calendar_source TEXT,
    state TEXT NOT NULL,
    environment TEXT NOT NULL,
    account_fingerprint TEXT NOT NULL,
    plan_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_snapshots (
    id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL,
    session TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(cycle_id, session),
    FOREIGN KEY(cycle_id) REFERENCES weekly_cycles(id)
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL UNIQUE,
    plan_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    confirmation_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES weekly_cycles(id)
);
CREATE TABLE IF NOT EXISTS plan_items (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES plans(id)
);
CREATE TABLE IF NOT EXISTS order_intents (
    id INTEGER PRIMARY KEY,
    plan_item_id INTEGER NOT NULL,
    external_identifier TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    broker_order_id TEXT,
    dry_run_json TEXT,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(plan_item_id) REFERENCES plan_items(id)
);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY,
    intent_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES order_intents(id)
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY,
    intent_id INTEGER NOT NULL,
    broker_fill_id TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL,
    filled_at TEXT NOT NULL,
    UNIQUE(intent_id, broker_fill_id),
    FOREIGN KEY(intent_id) REFERENCES order_intents(id)
);
CREATE TABLE IF NOT EXISTS reconciliations (
    id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ok INTEGER NOT NULL,
    discrepancy_codes TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES weekly_cycles(id)
);
CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES weekly_cycles(id)
);
CREATE TABLE IF NOT EXISTS valuation_snapshots (
    id INTEGER PRIMARY KEY,
    session TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_flows (
    id INTEGER PRIMARY KEY,
    effective_at TEXT NOT NULL,
    amount REAL NOT NULL,
    kind TEXT NOT NULL,
    capital_version_id INTEGER,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    before_hash TEXT,
    after_hash TEXT,
    diagnostics TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirmation_challenges (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    FOREIGN KEY(plan_id) REFERENCES plans(id)
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ExecutionLedger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connect().close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return conn

    def reset(self) -> None:
        """Delete the ledger file (and WAL sidecars) and recreate an empty schema."""
        for sidecar in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            sidecar.unlink(missing_ok=True)
        self._connect().close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._connect() as conn:
            conn.execute(sql, tuple(params))
            conn.commit()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def set_kv(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_kv(self, key: str) -> str | None:
        row = self.query_one("SELECT value FROM kv WHERE key=?", (key,))
        return None if row is None else row["value"]

    def audit(self, actor: str, action: str, result: str, *,
              before_hash: str | None = None, after_hash: str | None = None,
              diagnostics: dict[str, Any] | None = None) -> None:
        self.execute(
            "INSERT INTO audit_events(created_at, actor, action, result, before_hash, after_hash, diagnostics) "
            "VALUES(?,?,?,?,?,?,?)",
            (_now(), actor, action, result, before_hash, after_hash,
             json.dumps(diagnostics or {}, sort_keys=True)),
        )

    def current_cycle(self) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM weekly_cycles ORDER BY id DESC LIMIT 1")

    def cycle_by_week(self, iso_week: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM weekly_cycles WHERE iso_week=?", (iso_week,))

    def upsert_cycle(self, *, iso_week: str, cutoff: str | None, execution: str | None,
                     calendar_source: str, state: str, environment: str,
                     account_fingerprint: str) -> dict[str, Any]:
        existing = self.cycle_by_week(iso_week)
        now = _now()
        if existing is None:
            self.execute(
                "INSERT INTO weekly_cycles(iso_week, cutoff_session, execution_session, calendar_source, "
                "state, environment, account_fingerprint, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (iso_week, cutoff, execution, calendar_source, state, environment,
                 account_fingerprint, now, now),
            )
        else:
            advanced = existing["state"] not in {"AwaitingScan", "Tracking"}
            kept_state = existing["state"] if advanced else state
            self.execute(
                "UPDATE weekly_cycles SET cutoff_session=?, execution_session=?, calendar_source=?, "
                "state=?, environment=?, account_fingerprint=?, updated_at=? WHERE iso_week=?",
                (cutoff, execution, calendar_source, kept_state, environment,
                 account_fingerprint, now, iso_week),
            )
        cycle = self.cycle_by_week(iso_week)
        assert cycle is not None
        return cycle

    def set_cycle_state(self, cycle_id: int, state: str, *, plan_hash: str | None = None) -> None:
        if plan_hash is None:
            self.execute(
                "UPDATE weekly_cycles SET state=?, updated_at=? WHERE id=?",
                (state, _now(), cycle_id),
            )
        else:
            self.execute(
                "UPDATE weekly_cycles SET state=?, plan_hash=?, updated_at=? WHERE id=?",
                (state, plan_hash, _now(), cycle_id),
            )

    def insert_snapshot(self, cycle_id: int, session: str, artifact_hash: str, payload: dict[str, Any]) -> None:
        existing = self.query_one(
            "SELECT artifact_hash FROM scan_snapshots WHERE cycle_id=? AND session=?",
            (cycle_id, session),
        )
        if existing is not None:
            if existing["artifact_hash"] != artifact_hash:
                raise ValueError("scan snapshots are immutable; a different artifact already exists for this session")
            return
        self.execute(
            "INSERT INTO scan_snapshots(cycle_id, session, artifact_hash, payload_json, created_at) "
            "VALUES(?,?,?,?,?)",
            (cycle_id, session, artifact_hash, json.dumps(payload, sort_keys=True), _now()),
        )

    def snapshots(self, cycle_id: int) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM scan_snapshots WHERE cycle_id=? ORDER BY session", (cycle_id,))
        for row in rows:
            row["payload"] = json.loads(row["payload_json"])
        return rows

    def latest_capital(self) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM capital_versions ORDER BY id DESC LIMIT 1")

    def add_capital_version(self, *, target_bucket: float, active_bucket: float,
                            production_cap: float | None, cash_flow_status: str,
                            approved_by: str) -> dict[str, Any]:
        now = _now()
        self.execute(
            "INSERT INTO capital_versions(effective_at, target_bucket, active_bucket, production_cap, "
            "cash_flow_status, approved_by, created_at) VALUES(?,?,?,?,?,?,?)",
            (now, target_bucket, active_bucket, production_cap, cash_flow_status, approved_by, now),
        )
        row = self.latest_capital()
        assert row is not None
        return row

    def add_cash_flow(self, *, amount: float, kind: str, capital_version_id: int | None,
                      note: str, effective_at: str | None = None) -> None:
        stamp = effective_at or _now()
        self.execute(
            "INSERT INTO cash_flows(effective_at, amount, kind, capital_version_id, note, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (stamp, amount, kind, capital_version_id, note, _now()),
        )

    def cash_flows(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM cash_flows ORDER BY id")

    def add_order_event(self, intent_id: int, status: str, payload: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO order_events(intent_id, observed_at, status, payload_json) VALUES(?,?,?,?)",
            (intent_id, _now(), status, json.dumps(payload, sort_keys=True)),
        )

    def add_fill(self, intent_id: int, *, broker_fill_id: str, quantity: float, price: float,
                 fee: float | None, filled_at: str) -> None:
        self.execute(
            "INSERT OR IGNORE INTO fills(intent_id, broker_fill_id, quantity, price, fee, filled_at) "
            "VALUES(?,?,?,?,?,?)",
            (intent_id, broker_fill_id, quantity, price, fee, filled_at),
        )
