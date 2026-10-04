"""Shared Pre-Earnings Momentum live-management contracts.

Standard-library only. The operational evaluator writes versioned artifacts that
FastAPI validates against these names and states. Strategy math does not live
here.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

OPERATIONAL_ID = "study7-bc-live-v1"
ARTIFACT_SCHEMA = "smallfish.study4-live-artifact"
ARTIFACT_SCHEMA_VERSION = 2
CALENDAR_SOURCE = "NYSE_STANDARD_HOLIDAY_CALENDAR"
USER_AGENT = "smallFish-pre-earnings-live/2.0"
TASTYTRADE_API_VERSION = "20251101"
SPY_SYMBOL = "SPY"
SPXL_SYMBOL = "SPXL"
STAGING_SYMBOLS = frozenset({SPY_SYMBOL, SPXL_SYMBOL})
ENTRY_LIMIT_BUFFER_PCT = 0.03
DEFAULT_PILOT_CAP = 10_000.0
DEFAULT_PILOT_FRACTION = 0.10


class CycleState(StrEnum):
    AWAITING_SCAN = "AwaitingScan"
    TRACKING = "Tracking"
    PLAN_FINALIZED = "PlanFinalized"
    PREFLIGHT_PASSED = "PreflightPassed"
    ARMED = "Armed"
    SUBMITTING = "Submitting"
    MONITORING = "Monitoring"
    RECONCILING = "Reconciling"
    WEEK_CLOSED = "WeekClosed"
    NEEDS_REVIEW = "NeedsReview"
    PAUSED = "Paused"
    MISSED = "Missed"


class ExecutionEnvironment(StrEnum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class StrategyArm(StrEnum):
    B = "stocks-spy-control"
    C = "stocks-spxl-spy-control"


ARM_PROTOCOL_IDS = {
    StrategyArm.B: "pre-earnings-weekly-defensive-regime-staging-stocks-spy-v1",
    StrategyArm.C: "pre-earnings-weekly-defensive-regime-staging-stocks-spxl-spy-v1",
}
ARM_CONFIG_SHA256 = {
    StrategyArm.B: "464b6c8b458766fa98dd2e3cc73d3f0d73e34f043e860a499fd9e5a56c692010",
    StrategyArm.C: "e57e55358a250b5b20d6a42498d2af132f4ff71c6934602d3c2a56e334f94230",
}


class PlanItemKind(StrEnum):
    STOCK_EXIT = "stock_exit"
    STAGING_EXIT = "staging_exit"
    STAGING_FUNDING = "staging_funding"
    STOCK_ENTRY = "stock_entry"
    STAGING_RESIDUAL = "staging_residual"


class OrderIntentState(StrEnum):
    PENDING_DRY_RUN = "PENDING_DRY_RUN"
    DRY_RUN_PASSED = "DRY_RUN_PASSED"
    DRY_RUN_REJECTED = "DRY_RUN_REJECTED"
    READY = "READY"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    WORKING = "WORKING"
    FILLED = "FILLED"
    IOC_UNFILLED = "IOC_UNFILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    BLOCKED = "BLOCKED"


TERMINAL_ORDER_STATES = frozenset({
    OrderIntentState.FILLED,
    OrderIntentState.IOC_UNFILLED,
    OrderIntentState.REJECTED,
    OrderIntentState.CANCELLED,
    OrderIntentState.EXPIRED,
    OrderIntentState.DRY_RUN_REJECTED,
    OrderIntentState.BLOCKED,
})

BROKER_TERMINAL_STATUSES = frozenset({
    "Filled",
    "Cancelled",
    "Rejected",
    "Expired",
    "Removed",
    "Partially Removed",
})

IOC_UNFILLED_STATUSES = frozenset({"Cancelled", "Expired"})


def canonical_json(payload: object) -> str:
    """Stable UTF-8 JSON used for hashes and artifact checksums."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_payload(payload: object) -> str:
    return sha256_text(canonical_json(payload))


def account_fingerprint(environment: str, account_number: str) -> str:
    """One-way account identity. Never store or return the raw account number."""
    material = f"{environment.strip().lower()}:{account_number.strip()}"
    return sha256_text(material)


def mask_account_label(account_number: str, alias: str) -> str:
    digits = "".join(ch for ch in account_number if ch.isalnum())
    tail = digits[-4:] if len(digits) >= 4 else "????"
    return f"{alias} \u2026{tail}"


def recommended_pilot_cap(target_bucket: float) -> float:
    return min(DEFAULT_PILOT_FRACTION * target_bucket, DEFAULT_PILOT_CAP)
