"""Study 4 weekly lifecycle, confirmation, execution, and reconciliation."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from models.nyse_calendar import (
    CALENDAR_SOURCE,
    REGULAR_OPEN_HOUR,
    REGULAR_OPEN_MINUTE,
    cutoff_for_execution,
    friday_execution_plan,
    nyse_sessions,
)
from models.study4_live import (
    BROKER_TERMINAL_STATUSES,
    IOC_UNFILLED_STATUSES,
    OPERATIONAL_ID,
    PROTOCOL_ID,
    SPY_SYMBOL,
    CycleState,
    OrderIntentState,
    PlanItemKind,
    recommended_pilot_cap,
    sha256_payload,
)

from . import jobs, performance, risk
from .broker import ExecutionBroker, equity_request_from_item
from .ledger import ExecutionLedger
from .settings import ExecutionSettings, artifacts_dir, execution_db_path, load_settings

NY = ZoneInfo("America/New_York")
Evaluator = Callable[..., dict[str, Any]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


class ExecutionService:
    def __init__(
        self,
        settings: ExecutionSettings | None = None,
        ledger: ExecutionLedger | None = None,
        broker: ExecutionBroker | None = None,
        evaluator: Evaluator | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.settings = settings or load_settings()
        self.ledger = ledger or ExecutionLedger(execution_db_path())
        self.broker = broker or ExecutionBroker(self.settings)
        self.evaluator = evaluator or (
            lambda **kwargs: jobs.run_evaluator(**kwargs)
        )
        self.clock = clock or _now
        self.sleeper = time.sleep
        self.recovery_delays = (0.0, 0.05, 0.25)

    def status(self) -> dict[str, Any]:
        settings = self.settings
        cycle = self.ledger.current_cycle()
        capital = self.ledger.latest_capital()
        return {
            "configured": settings.configured,
            "environment": settings.mode or None,
            "productionEnabled": settings.production_enabled,
            "killSwitch": settings.kill_switch or self.ledger.get_kv("kill_switch") == "1",
            "accountAlias": settings.account_alias,
            "accountFingerprint": settings.account_fingerprint or None,
            "protocolId": PROTOCOL_ID,
            "operationalId": OPERATIONAL_ID,
            "exploratoryWarning": (
                "Study 4 remains NO_VERDICT / EXPLORATORY. Live fills are not a "
                "continuation of the historical curve."
            ),
            "setupRequirements": settings.setup_requirements(capital_ready=capital is not None),
            "capital": capital,
            "configuredTargetBucket": settings.target_bucket,
            "recommendedPilotCap": None if settings.target_bucket is None else recommended_pilot_cap(
                settings.target_bucket),
            "cycle": cycle,
            "nextAction": self._next_action(cycle),
            "submissionsAllowed": settings.submissions_allowed and self.ledger.get_kv("kill_switch") != "1",
        }

    def _next_action(self, cycle: dict[str, Any] | None) -> str:
        if not self.settings.configured:
            return "configure_execution_mode"
        if cycle is None:
            return "scan"
        state = cycle["state"]
        mapping = {
            CycleState.AWAITING_SCAN.value: "scan",
            CycleState.TRACKING.value: "scan_or_wait_for_cutoff",
            CycleState.PLAN_FINALIZED.value: "preflight",
            CycleState.PREFLIGHT_PASSED.value: "confirm",
            CycleState.ARMED.value: "wait_for_open_or_execute",
            CycleState.SUBMITTING.value: "monitor",
            CycleState.MONITORING.value: "reconcile",
            CycleState.RECONCILING.value: "final_sync",
            CycleState.NEEDS_REVIEW.value: "reconcile",
            CycleState.WEEK_CLOSED.value: "await_next_signal_session",
            CycleState.PAUSED.value: "review",
            CycleState.MISSED.value: "review_missed_batch",
        }
        return mapping.get(state, "review")

    def _holdings_payload(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        stored = self.ledger.query_one(
            "SELECT payload_json FROM position_snapshots ORDER BY id DESC LIMIT 1")
        if stored:
            return json.loads(stored["payload_json"])
        if snapshot is None:
            return {"cash": 0.0, "spy_shares": 0, "positions": [], "pins": {}, "pending_exits": {}}
        spy = 0
        positions = []
        for row in snapshot.get("normalized_positions") or []:
            qty = int(float(row["quantity"]))
            if row["symbol"] == "SPY":
                spy = qty
            elif qty:
                positions.append({
                    "ticker": row["symbol"],
                    "shares": qty,
                    "entry_fill_price": 0.0,
                    "entry_principal": 0.0,
                    "allowed_drawdown": 0.10,
                    "entry_decision_date": date.today().isoformat(),
                    "entry_execution_date": date.today().isoformat(),
                    "setup_score": 0.0,
                    "sector": "Unknown",
                    "predicted_event_date": date.today().isoformat(),
                    "cost_basis": 0.0,
                    "entry_limit": 0.0,
                    "pending_exit": False,
                })
        return {
            "cash": snapshot.get("cash") or 0.0,
            "spy_shares": spy,
            "positions": positions,
            "pins": {},
            "pending_exits": {},
        }

    def _active_bucket(self) -> float:
        capital = self.ledger.latest_capital()
        if capital:
            return float(capital["active_bucket"])
        if self.settings.mode == "production" and self.settings.production_cap:
            return float(self.settings.production_cap)
        return float(self.settings.target_bucket or 0)

    def scan(self, session: str | None = None) -> dict[str, Any]:
        risk.check_settings(self.settings)
        day = date.fromisoformat(session) if session else self.clock().astimezone(NY).date()
        weeks = nyse_sessions(day - timedelta(days=14), day + timedelta(days=14))
        execution_sessions, by_decision = friday_execution_plan(weeks)
        execution = by_decision.get(day) or (day if day in execution_sessions else None)
        cutoff = cutoff_for_execution(weeks, execution) if execution else None
        iso = _iso_week(execution or day)
        cycle = self.ledger.upsert_cycle(
            iso_week=iso,
            cutoff=None if cutoff is None else cutoff.isoformat(),
            execution=None if execution is None else execution.isoformat(),
            calendar_source=CALENDAR_SOURCE,
            state=CycleState.AWAITING_SCAN.value,
            environment=self.settings.mode,
            account_fingerprint=self.settings.account_fingerprint,
        )
        holdings = self._holdings_payload()
        artifact = self.evaluator(
            session=day.isoformat(),
            holdings=holdings,
            active_bucket=self._active_bucket(),
            output_name=f"{iso}-{day.isoformat()}",
        )
        try:
            self.ledger.insert_snapshot(
                cycle["id"], day.isoformat(), artifact["artifactHash"], artifact)
        except ValueError as exc:
            raise risk.RiskBlocked("immutable", str(exc)) from exc
        current = self.ledger.cycle_by_week(iso) or cycle
        advanced = current["state"] not in {
            CycleState.AWAITING_SCAN.value, CycleState.TRACKING.value,
        }
        if artifact.get("isCutoff") and not advanced:
            self._store_plan(cycle["id"], artifact)
            self.ledger.set_cycle_state(
                cycle["id"], CycleState.PLAN_FINALIZED.value, plan_hash=artifact["artifactHash"])
            new_state = CycleState.PLAN_FINALIZED.value
        elif not advanced:
            new_state = CycleState.TRACKING.value
            self.ledger.set_cycle_state(cycle["id"], new_state)
        else:
            new_state = current["state"]
        self._record_valuation(artifact)
        self.ledger.audit("owner", "scan", "ok", after_hash=artifact["artifactHash"],
                          diagnostics={"session": day.isoformat(), "state": new_state})
        return artifact

    def finalize(self, session: str | None = None) -> dict[str, Any]:
        cycle = self.ledger.current_cycle()
        if cycle is not None:
            existing = self.ledger.query_one(
                "SELECT payload_json FROM plans WHERE cycle_id=?", (cycle["id"],))
            if existing is not None:
                return json.loads(existing["payload_json"])
        artifact = self.scan(session)
        if not artifact.get("isCutoff"):
            raise risk.RiskBlocked("not_cutoff", "Finalize is only valid at the week's cutoff session.")
        return artifact

    def _store_plan(self, cycle_id: int, artifact: dict[str, Any]) -> None:
        existing = self.ledger.query_one("SELECT id FROM plans WHERE cycle_id=?", (cycle_id,))
        if existing:
            raise risk.RiskBlocked("immutable", "A finalized plan already exists for this week.")
        plan_hash = artifact["artifactHash"]
        self.ledger.execute(
            "INSERT INTO plans(cycle_id, plan_hash, payload_json, confirmation_state, created_at) "
            "VALUES(?,?,?,?,?)",
            (cycle_id, plan_hash, json.dumps(artifact, sort_keys=True), "unconfirmed",
             datetime.now(timezone.utc).isoformat()),
        )
        plan = self.ledger.query_one("SELECT id FROM plans WHERE cycle_id=?", (cycle_id,))
        assert plan is not None
        for item in artifact.get("planItems") or []:
            self.ledger.execute(
                "INSERT INTO plan_items(plan_id, priority, kind, payload_json) VALUES(?,?,?,?)",
                (plan["id"], item["priority"], item["kind"], json.dumps(item, sort_keys=True)),
            )

    def preflight(self) -> dict[str, Any]:
        risk.check_settings(self.settings)
        cycle = self._require_cycle()
        if cycle["state"] not in {CycleState.PLAN_FINALIZED.value, CycleState.PREFLIGHT_PASSED.value}:
            raise risk.RiskBlocked("state", "Preflight requires a finalized plan.")
        snapshot = self.broker.snapshot()
        risk.check_broker_snapshot(snapshot)
        plan = self._plan_for(cycle["id"])
        items = self._plan_items(plan["id"])
        self.ledger.execute(
            "DELETE FROM order_intents WHERE plan_item_id IN "
            "(SELECT id FROM plan_items WHERE plan_id=?)",
            (plan["id"],),
        )
        dry_runs = []
        for item in items:
            payload = json.loads(item["payload_json"])
            risk.check_plan_item(payload)
            if payload.get("kind") == PlanItemKind.SPY_RESIDUAL.value:
                continue
            if not payload.get("deltaShares"):
                continue
            external_id = str(uuid.uuid4())
            request = equity_request_from_item(payload, external_id)
            result = self.broker.dry_run(request)
            risk.check_dry_run(result)
            if payload.get("kind") == PlanItemKind.STOCK_ENTRY.value:
                risk.check_dry_run_ioc(result)
            self.ledger.execute(
                "INSERT INTO order_intents(plan_item_id, external_identifier, state, request_hash, "
                "broker_order_id, dry_run_json, payload_json) VALUES(?,?,?,?,?,?,?)",
                (item["id"], external_id, OrderIntentState.DRY_RUN_PASSED.value,
                 sha256_payload(payload), None, json.dumps(result, sort_keys=True),
                 json.dumps(payload, sort_keys=True)),
            )
            dry_runs.append({"item": payload, "dryRun": result, "externalIdentifier": external_id})
        token = secrets.token_urlsafe(24)
        execution = date.fromisoformat(cycle["execution_session"])
        expires_at = datetime(execution.year, execution.month, execution.day,
                              REGULAR_OPEN_HOUR, REGULAR_OPEN_MINUTE, tzinfo=NY)
        expires = expires_at.isoformat()
        self.ledger.execute(
            "INSERT INTO confirmation_challenges(plan_id, token_hash, expires_at) VALUES(?,?,?)",
            (plan["id"], self._challenge_hash(token, plan["plan_hash"], cycle["execution_session"]),
             expires),
        )
        self.ledger.set_cycle_state(cycle["id"], CycleState.PREFLIGHT_PASSED.value)
        self.ledger.audit("owner", "preflight", "ok", after_hash=plan["plan_hash"])
        return {
            "environment": self.settings.mode,
            "accountAlias": self.settings.account_alias,
            "planHash": plan["plan_hash"],
            "executionSession": cycle["execution_session"],
            "dryRuns": dry_runs,
            "confirmationChallenge": token,
            "expiresAt": expires,
        }

    def confirm(self, token: str, production_ack: str | None = None) -> dict[str, Any]:
        risk.check_settings(self.settings)
        cycle = self._require_cycle()
        risk.check_cycle_can_arm(cycle["state"])
        if self.settings.mode == "production" and production_ack != "PRODUCTION":
            raise risk.RiskBlocked("ack", "Type PRODUCTION to confirm a live batch.")
        plan = self._plan_for(cycle["id"])
        row = self.ledger.query_one(
            "SELECT * FROM confirmation_challenges WHERE plan_id=? AND consumed_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (plan["id"],),
        )
        if row is None or row["token_hash"] != self._challenge_hash(
                token, plan["plan_hash"], cycle["execution_session"]):
            raise risk.RiskBlocked("token", "The confirmation challenge is invalid or already used.")
        expires_at = datetime.fromisoformat(row["expires_at"])
        if self.clock() > expires_at:
            raise risk.RiskBlocked("expired", "The confirmation challenge expired at the scheduled open.")
        self.ledger.execute(
            "UPDATE confirmation_challenges SET consumed_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), row["id"]),
        )
        self.ledger.execute(
            "UPDATE plans SET confirmation_state=? WHERE id=?", ("armed", plan["id"]))
        self.ledger.set_cycle_state(cycle["id"], CycleState.ARMED.value, plan_hash=plan["plan_hash"])
        self.ledger.audit("owner", "confirm", "ok", after_hash=plan["plan_hash"])
        return {"state": CycleState.ARMED.value, "planHash": plan["plan_hash"]}

    def execute(self, plan_id: int | None = None) -> dict[str, Any]:
        risk.check_settings(self.settings)
        if self.ledger.get_kv("kill_switch") == "1":
            raise risk.RiskBlocked("kill_switch", "The kill switch is active.")
        cycle = self._require_cycle()
        now = self.clock().astimezone(NY)
        if cycle.get("execution_session") and now.date().isoformat() > cycle["execution_session"]:
            self.ledger.set_cycle_state(cycle["id"], CycleState.MISSED.value)
            self.ledger.audit("system", "missed_batch", "blocked")
            raise risk.RiskBlocked("missed", "The scheduled opening execution was missed.")
        if cycle["state"] != CycleState.ARMED.value:
            raise risk.RiskBlocked("state", "Execute accepts only an armed stored plan.")
        plan = self._plan_for(cycle["id"])
        if plan_id is not None and int(plan_id) != int(plan["id"]):
            raise risk.RiskBlocked("plan", "Execute accepts only the stored finalized plan.")
        risk.check_execution_window(self.clock(), cycle["execution_session"])
        snapshot = self.broker.snapshot()
        risk.check_broker_snapshot(snapshot, allow_open_orders=False)
        self.ledger.set_cycle_state(cycle["id"], CycleState.SUBMITTING.value)
        intents = self.ledger.query(
            "SELECT order_intents.* FROM order_intents "
            "JOIN plan_items ON plan_items.id=order_intents.plan_item_id "
            "WHERE plan_items.plan_id=? ORDER BY plan_items.priority",
            (plan["id"],),
        )
        results = []
        unknown_blocks_buys = False
        for intent in intents:
            payload = json.loads(intent["payload_json"])
            kind = payload.get("kind")
            if unknown_blocks_buys and kind in {
                PlanItemKind.STOCK_ENTRY.value, PlanItemKind.SPY_RESIDUAL.value,
            }:
                self.ledger.execute(
                    "UPDATE order_intents SET state=? WHERE id=?",
                    (OrderIntentState.BLOCKED.value, intent["id"]),
                )
                results.append({
                    "externalIdentifier": intent["external_identifier"],
                    "state": OrderIntentState.BLOCKED.value,
                    "reason": "prior_sell_uncertain",
                })
                continue
            placed = self._submit_intent(intent, payload)
            results.append(placed)
            if (placed["state"] == OrderIntentState.SUBMISSION_UNKNOWN.value
                    and kind in {PlanItemKind.STOCK_EXIT.value, PlanItemKind.SPY_FUNDING.value}):
                unknown_blocks_buys = True
        residual = self._submit_residual_if_ready(plan, results)
        if residual is not None:
            results.append(residual)
        unknown = any(row["state"] == OrderIntentState.SUBMISSION_UNKNOWN.value for row in results)
        blocked = any(row["state"] == OrderIntentState.BLOCKED.value for row in results)
        self.ledger.set_cycle_state(
            cycle["id"],
            CycleState.NEEDS_REVIEW.value if unknown or blocked else CycleState.MONITORING.value,
        )
        self.ledger.audit("system", "execute", "ok" if not unknown else "unknown")
        return {"results": results, "state": self._require_cycle()["state"]}

    def reconcile(self) -> dict[str, Any]:
        cycle = self._require_cycle()
        snapshot = self.broker.snapshot()
        intents = self.ledger.query(
            "SELECT order_intents.* FROM order_intents "
            "JOIN plan_items ON plan_items.id=order_intents.plan_item_id "
            "JOIN plans ON plans.id=plan_items.plan_id WHERE plans.cycle_id=?",
            (cycle["id"],),
        )
        codes: list[str] = []
        for intent in intents:
            if intent["state"] == OrderIntentState.SUBMISSION_UNKNOWN.value:
                found = self.broker.lookup(intent["external_identifier"])
                if found:
                    self.ledger.execute(
                        "UPDATE order_intents SET state=?, broker_order_id=? WHERE id=?",
                        (OrderIntentState.ACKNOWLEDGED.value, found[0].broker_order_id, intent["id"]),
                    )
                else:
                    codes.append("submission_unknown")
        live = snapshot.get("live_orders") or ()
        if live:
            codes.append("unexpected_live_orders")
        planned_symbols = {
            json.loads(item["payload_json"]).get("symbol")
            for item in self.ledger.query(
            "SELECT plan_items.payload_json FROM plan_items "
            "JOIN plans ON plans.id=plan_items.plan_id WHERE plans.cycle_id=?",
                (cycle["id"],),
            )
        }
        planned_symbols.add(SPY_SYMBOL)
        for position in snapshot.get("normalized_positions") or ():
            if float(position.get("quantity") or 0) and position.get("symbol") not in planned_symbols:
                codes.append("unknown_holding")
                break
        ok = not codes
        payload = {"snapshot": {
            "cash": snapshot.get("cash"),
            "positions": snapshot.get("normalized_positions"),
        }, "codes": codes}
        self.ledger.execute(
            "INSERT INTO reconciliations(cycle_id, kind, ok, discrepancy_codes, payload_json, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (cycle["id"], "sync", int(ok), json.dumps(codes), json.dumps(payload, sort_keys=True),
             datetime.now(timezone.utc).isoformat()),
        )
        self.ledger.execute(
            "INSERT INTO position_snapshots(cycle_id, created_at, payload_json) VALUES(?,?,?)",
            (cycle["id"], datetime.now(timezone.utc).isoformat(),
             json.dumps(self._holdings_from_snapshot(snapshot), sort_keys=True)),
        )
        state = CycleState.RECONCILING.value if ok else CycleState.NEEDS_REVIEW.value
        self.ledger.set_cycle_state(cycle["id"], state)
        return {"ok": ok, "codes": codes, "state": state}

    def final_sync(self) -> dict[str, Any]:
        result = self.reconcile()
        cycle = self._require_cycle()
        if result["ok"]:
            self.ledger.set_cycle_state(cycle["id"], CycleState.WEEK_CLOSED.value)
            result["state"] = CycleState.WEEK_CLOSED.value
        return result

    def kill_switch(self, active: bool) -> dict[str, Any]:
        self.ledger.set_kv("kill_switch", "1" if active else "0")
        self.ledger.audit("owner", "kill_switch", "on" if active else "off")
        return {"killSwitch": active}

    def reset_ledger(self, confirmation: str) -> dict[str, Any]:
        if confirmation != "RESET":
            raise ValueError("Type RESET to wipe the local execution ledger.")
        self.ledger.reset()
        directory = artifacts_dir()
        if directory.is_dir():
            for child in directory.iterdir():
                if child.is_file():
                    child.unlink()
        return {"ok": True, "wiped": ["ledger", "artifacts"]}

    def capital_reset(self, *, target_bucket: float, active_bucket: float | None = None,
                      production_cap: float | None = None) -> dict[str, Any]:
        if self.settings.mode == "production":
            cap = production_cap if production_cap is not None else self.settings.production_cap
            if cap is None:
                raise risk.RiskBlocked("production_cap", "A production pilot cap is required.")
            active = min(active_bucket or cap, cap)
        else:
            active = active_bucket or target_bucket
        row = self.ledger.add_capital_version(
            target_bucket=target_bucket,
            active_bucket=active,
            production_cap=production_cap if self.settings.mode == "production" else None,
            cash_flow_status="scheduled",
            approved_by="owner",
        )
        self.ledger.add_cash_flow(
            amount=0.0,
            kind="capital_reset",
            capital_version_id=row["id"],
            note="Versioned capital instruction; not an investment return.",
        )
        self.ledger.audit("owner", "capital_reset", "ok", diagnostics={"id": row["id"]})
        return row

    def cycle_detail(self, week: str) -> dict[str, Any]:
        cycle = self.ledger.cycle_by_week(week) or self.ledger.current_cycle()
        if cycle is None:
            return {"cycle": None}
        plan = self.ledger.query_one("SELECT * FROM plans WHERE cycle_id=?", (cycle["id"],))
        return {
            "cycle": cycle,
            "snapshots": self.ledger.snapshots(cycle["id"]),
            "plan": None if plan is None else {
                **plan,
                "payload": json.loads(plan["payload_json"]),
                "items": [
                    {**item, "payload": json.loads(item["payload_json"])}
                    for item in self._plan_items(plan["id"])
                ],
            },
            "intents": self.ledger.query(
                "SELECT order_intents.* FROM order_intents "
                "JOIN plan_items ON plan_items.id=order_intents.plan_item_id "
                "JOIN plans ON plans.id=plan_items.plan_id WHERE plans.cycle_id=?",
                (cycle["id"],),
            ),
            "reconciliations": self.ledger.query(
                "SELECT * FROM reconciliations WHERE cycle_id=? ORDER BY id", (cycle["id"],)),
        }

    def performance_series(self) -> dict[str, Any]:
        rows = self.ledger.query("SELECT * FROM valuation_snapshots ORDER BY session")
        series = [json.loads(row["payload_json"]) for row in rows]
        unavailable = [row for row in series if not row.get("complete")]
        return {
            "series": series,
            "unavailableDates": [row["session"] for row in unavailable],
            "modeledCostNote": "Broker-net is primary. Modeled Study 4 costs are diagnostic only.",
        }

    def audit(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        rows = self.ledger.query(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        return {"events": rows}

    def _require_cycle(self) -> dict[str, Any]:
        cycle = self.ledger.current_cycle()
        if cycle is None:
            raise risk.RiskBlocked("cycle", "No weekly cycle is active.")
        return cycle

    def _plan_for(self, cycle_id: int) -> dict[str, Any]:
        plan = self.ledger.query_one("SELECT * FROM plans WHERE cycle_id=?", (cycle_id,))
        if plan is None:
            raise risk.RiskBlocked("plan", "No immutable plan exists for this week.")
        return plan

    def _plan_items(self, plan_id: int) -> list[dict[str, Any]]:
        return self.ledger.query(
            "SELECT * FROM plan_items WHERE plan_id=? ORDER BY priority", (plan_id,))

    def _holdings_from_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        spy = 0
        positions = []
        for row in snapshot.get("normalized_positions") or []:
            qty = int(float(row["quantity"]))
            if row["symbol"] == "SPY":
                spy = qty
            elif qty:
                positions.append({"ticker": row["symbol"], "shares": qty})
        return {"cash": snapshot.get("cash") or 0.0, "spy_shares": spy, "positions": positions}

    def _challenge_hash(self, token: str, plan_hash: str, execution_session: str) -> str:
        material = "|".join((
            plan_hash,
            self.settings.account_fingerprint,
            self.settings.mode,
            execution_session,
            token,
        ))
        secret = (self.settings.confirmation_secret or "study4-live-unconfigured").encode("utf-8")
        return hmac.new(secret, material.encode("utf-8"), hashlib.sha256).hexdigest()

    def _intent_state_from_broker(self, payload: dict[str, Any], placed: dict[str, Any]) -> str:
        status = str(placed.get("status") or "")
        if payload.get("kind") == PlanItemKind.STOCK_ENTRY.value and status in IOC_UNFILLED_STATUSES:
            return OrderIntentState.IOC_UNFILLED.value
        if status in BROKER_TERMINAL_STATUSES and status == "Filled":
            return OrderIntentState.FILLED.value
        if status in {"Rejected"}:
            return OrderIntentState.REJECTED.value
        if placed.get("ok"):
            return OrderIntentState.ACKNOWLEDGED.value
        return OrderIntentState.REJECTED.value

    def _lookup_with_backoff(self, external_identifier: str) -> Any:
        found = ()
        for delay in self.recovery_delays:
            if delay:
                self.sleeper(delay)
            found = self.broker.lookup(external_identifier)
            if found:
                return found
        return found

    def _submit_intent(self, intent: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        risk.check_no_prior_submit(intent["state"])
        request = equity_request_from_item(payload, intent["external_identifier"])
        try:
            placed = self.broker.submit(request)
            state = self._intent_state_from_broker(payload, placed)
            self.ledger.execute(
                "UPDATE order_intents SET state=?, broker_order_id=? WHERE id=?",
                (state, placed.get("broker_order_id"), intent["id"]),
            )
            self.ledger.add_order_event(intent["id"], state, placed)
            return {"externalIdentifier": intent["external_identifier"], "state": state, **placed}
        except Exception as exc:
            found = self._lookup_with_backoff(intent["external_identifier"])
            if found:
                self.ledger.execute(
                    "UPDATE order_intents SET state=?, broker_order_id=? WHERE id=?",
                    (OrderIntentState.ACKNOWLEDGED.value, found[0].broker_order_id, intent["id"]),
                )
                return {
                    "externalIdentifier": intent["external_identifier"],
                    "state": OrderIntentState.ACKNOWLEDGED.value,
                    "recovered": True,
                }
            still_open = False
            try:
                risk.check_execution_window(self.clock(), self._require_cycle()["execution_session"])
                still_open = True
            except risk.RiskBlocked:
                still_open = False
            if still_open:
                try:
                    placed = self.broker.submit(request)
                    state = self._intent_state_from_broker(payload, placed)
                    self.ledger.execute(
                        "UPDATE order_intents SET state=?, broker_order_id=? WHERE id=?",
                        (state, placed.get("broker_order_id"), intent["id"]),
                    )
                    return {
                        "externalIdentifier": intent["external_identifier"],
                        "state": state,
                        "resubmitted": True,
                        **placed,
                    }
                except Exception:
                    pass
            self.ledger.execute(
                "UPDATE order_intents SET state=? WHERE id=?",
                (OrderIntentState.SUBMISSION_UNKNOWN.value, intent["id"]),
            )
            return {
                "externalIdentifier": intent["external_identifier"],
                "state": OrderIntentState.SUBMISSION_UNKNOWN.value,
                "errorType": type(exc).__name__,
            }

    def _submit_residual_if_ready(self, plan: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any] | None:
        items = self._plan_items(plan["id"])
        residual_items = [
            item for item in items
            if json.loads(item["payload_json"]).get("kind") == PlanItemKind.SPY_RESIDUAL.value
        ]
        if not residual_items:
            return None
        ioc_open = [
            row for row in results
            if self._intent_kind(row.get("externalIdentifier")) == PlanItemKind.STOCK_ENTRY.value
            and row["state"] not in {
                OrderIntentState.FILLED.value,
                OrderIntentState.IOC_UNFILLED.value,
                OrderIntentState.REJECTED.value,
                OrderIntentState.CANCELLED.value,
                OrderIntentState.EXPIRED.value,
                OrderIntentState.BLOCKED.value,
            }
        ]
        if ioc_open:
            return None
        snapshot = self.broker.snapshot()
        cash = float(snapshot.get("cash") or 0)
        spy_price = None
        for row in snapshot.get("normalized_positions") or ():
            if row.get("symbol") == SPY_SYMBOL and row.get("mark") is not None:
                spy_price = float(row["mark"])
        spy_price = spy_price or snapshot.get("spy_mark")
        if spy_price in (None, 0):
            return {
                "externalIdentifier": None,
                "state": OrderIntentState.BLOCKED.value,
                "reason": "residual_spy_mark_unavailable",
            }
        residual_payload = json.loads(residual_items[0]["payload_json"])
        cost = float(residual_payload.get("modeledCostPerShare") or 0.0)
        buyable = int(math.floor(cash / (float(spy_price) + cost))) if cash > 0 else 0
        if buyable <= 0:
            return {
                "externalIdentifier": None,
                "state": OrderIntentState.BLOCKED.value,
                "reason": "residual_spy_unaffordable",
                "deltaShares": 0,
            }
        existing = self.ledger.query_one(
            "SELECT * FROM order_intents WHERE plan_item_id=?", (residual_items[0]["id"],))
        payload = {
            **residual_payload,
            "deltaShares": buyable,
            "targetShares": buyable,
            "side": "buy",
            "orderType": "Market",
            "timeInForce": "Day",
        }
        if existing is None:
            external_id = str(uuid.uuid4())
            self.ledger.execute(
                "INSERT INTO order_intents(plan_item_id, external_identifier, state, request_hash, "
                "broker_order_id, dry_run_json, payload_json) VALUES(?,?,?,?,?,?,?)",
                (residual_items[0]["id"], external_id, OrderIntentState.READY.value,
                 sha256_payload(payload), None, json.dumps({}), json.dumps(payload, sort_keys=True)),
            )
            existing = self.ledger.query_one(
                "SELECT * FROM order_intents WHERE external_identifier=?", (external_id,))
            assert existing is not None
        dry = self.broker.dry_run(equity_request_from_item(payload, existing["external_identifier"]))
        if not dry.get("ok"):
            self.ledger.execute(
                "UPDATE order_intents SET state=? WHERE id=?",
                (OrderIntentState.DRY_RUN_REJECTED.value, existing["id"]),
            )
            return {
                "externalIdentifier": existing["external_identifier"],
                "state": OrderIntentState.DRY_RUN_REJECTED.value,
            }
        return self._submit_intent({**existing, "state": OrderIntentState.READY.value}, payload)

    def _intent_kind(self, external_identifier: str | None) -> str | None:
        if not external_identifier:
            return None
        row = self.ledger.query_one(
            "SELECT payload_json FROM order_intents WHERE external_identifier=?",
            (external_identifier,),
        )
        if row is None:
            return None
        return json.loads(row["payload_json"]).get("kind")

    def _record_valuation(self, artifact: dict[str, Any]) -> None:
        session = date.fromisoformat(artifact["session"])
        holdings = artifact.get("holdings") or {}
        marks: dict[str, float] = {}
        complete = True
        if artifact.get("spyClose") is not None:
            marks[SPY_SYMBOL] = float(artifact["spyClose"])
        else:
            complete = False
        positions = []
        spy_shares = int(holdings.get("spy_shares") or 0)
        if spy_shares:
            positions.append({"symbol": SPY_SYMBOL, "quantity": spy_shares})
        for row in holdings.get("positions") or []:
            ticker = row.get("ticker") or row.get("symbol")
            qty = row.get("shares") or row.get("quantity") or 0
            if ticker and qty:
                positions.append({"symbol": ticker, "quantity": qty})
        for row in artifact.get("positionDecisions") or []:
            if row.get("decisionClose") is not None and row.get("ticker"):
                marks[row["ticker"]] = float(row["decisionClose"])
            elif row.get("ticker") and int(row.get("shares") or 0):
                complete = False
        for row in artifact.get("scanRows") or []:
            if row.get("decision_close") is not None and row.get("ticker"):
                marks.setdefault(row["ticker"], float(row["decision_close"]))
        for row in positions:
            if row["symbol"] not in marks:
                complete = False
        prior = self.ledger.query_one(
            "SELECT payload_json FROM valuation_snapshots ORDER BY session DESC LIMIT 1")
        prior_payload = json.loads(prior["payload_json"]) if prior else {}
        spy_ledger = json.loads(self.ledger.get_kv("spy_benchmark") or '{"cash":0,"shares":0,"cost":0}')
        flows = self.ledger.cash_flows()
        external = sum(float(item["amount"]) for item in flows if item["kind"] == "contribution")
        payload = performance.value_paths(
            session=session,
            cash=float(holdings.get("cash") or 0),
            positions=positions,
            marks=marks,
            spy_ledger=spy_ledger,
            spy_price=marks.get(SPY_SYMBOL),
            fees=0.0,
            external_flow=0.0,
            prior_equity=prior_payload.get("strategyEquity"),
            peak_equity=prior_payload.get("peakEquity"),
            complete=complete,
        )
        if complete and prior is None and self.settings.target_bucket:
            spy_price = marks.get(SPY_SYMBOL)
            if spy_price:
                spy_ledger = performance.apply_passive_spy_flow(
                    spy_ledger,
                    amount=float(self._active_bucket()),
                    spy_price=spy_price,
                    cost_per_share=0.0,
                )
                self.ledger.set_kv("spy_benchmark", json.dumps(spy_ledger, sort_keys=True))
                payload = performance.value_paths(
                    session=session,
                    cash=float(holdings.get("cash") or 0),
                    positions=positions,
                    marks=marks,
                    spy_ledger=spy_ledger,
                    spy_price=spy_price,
                    fees=0.0,
                    external_flow=external,
                    prior_equity=None,
                    peak_equity=None,
                    complete=True,
                )
        try:
            self.ledger.execute(
                "INSERT INTO valuation_snapshots(session, payload_json, created_at) VALUES(?,?,?)",
                (session.isoformat(), json.dumps(payload, sort_keys=True),
                 datetime.now(timezone.utc).isoformat()),
            )
        except Exception:
            existing = self.ledger.query_one(
                "SELECT id FROM valuation_snapshots WHERE session=?", (session.isoformat(),))
            if existing is None:
                raise
