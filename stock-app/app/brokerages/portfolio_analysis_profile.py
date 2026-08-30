"""Owner-reviewed portfolio-analysis profiles and allocation classifications.

These records are app metadata.  They never alter a provider position or event,
and an absent percentage remains absent rather than receiving a product default.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import threading
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .. import options_activity
from .contracts import PortfolioAnalysisPolicy

SCHEMA_VERSION = 1
BUCKETS = frozenset({"GROWTH", "SPECULATIVE", "DEFENSIVE", "CASH", "UNKNOWN"})
CLASSIFICATION_HEADERS = (
    "brokerage_id", "account_id", "symbol", "allocation_bucket", "updated_at",
)

TEXT_FIELDS = frozenset({"notes"})
SERVER_FIELDS = frozenset({"reviewed_at"})

_lock = threading.RLock()


class ProfileValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_profiles(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileValidationError(
            "UNSUPPORTED_PROFILE_ARTIFACT",
            "The saved portfolio-analysis profile cannot be read.",
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ProfileValidationError(
            "UNSUPPORTED_PROFILE_ARTIFACT",
            "The saved portfolio-analysis profile schema is unsupported.",
        )
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        raise ProfileValidationError(
            "UNSUPPORTED_PROFILE_ARTIFACT",
            "The saved portfolio-analysis profile schema is unsupported.",
        )
    return {
        str(key): dict(value) for key, value in profiles.items()
        if isinstance(value, dict)
    }


def read_profile(path: Path, brokerage_id: str,
                 policy: PortfolioAnalysisPolicy) -> dict[str, Any]:
    saved = read_profiles(path).get(brokerage_id, {})
    profile = {**saved, "objective": policy.objective}
    return {**profile, "status": profile_status(profile, policy)}


def allowed_fields(policy: PortfolioAnalysisPolicy) -> frozenset[str]:
    return frozenset({
        "objective", *policy.required_fields, *policy.optional_fields,
        *TEXT_FIELDS,
    })


def required_fields(policy: PortfolioAnalysisPolicy) -> frozenset[str]:
    return frozenset(policy.required_fields)


def profile_status(profile: dict[str, Any], policy: PortfolioAnalysisPolicy) -> str:
    configured = [field for field in required_fields(policy) if profile.get(field) is not None]
    if not configured and not str(profile.get("notes") or "").strip():
        return "UNCONFIGURED"
    if all(profile.get(field) is not None for field in required_fields(policy)):
        return "COMPLETE"
    return "PARTIAL"


def _percentage(value: Any, field: str, *, gross: bool = False) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ProfileValidationError("INVALID_PROFILE", f"{field} must be a finite percentage.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProfileValidationError("INVALID_PROFILE", f"{field} must be a finite percentage.") from exc
    maximum = None if gross else Decimal("100")
    if not parsed.is_finite() or parsed < 0 or (maximum is not None and parsed > maximum):
        range_text = "zero or greater" if gross else "between 0 and 100"
        raise ProfileValidationError("INVALID_PROFILE", f"{field} must be {range_text}.")
    return float(parsed)


def validate_patch(current: dict[str, Any], updates: dict[str, Any],
                   policy: PortfolioAnalysisPolicy
                   ) -> dict[str, Any]:
    unknown = set(updates) - allowed_fields(policy)
    if unknown:
        raise ProfileValidationError(
            "UNSUPPORTED_FIELD", f"Cannot update {', '.join(sorted(unknown))}.",
        )
    if not updates:
        raise ProfileValidationError("NOTHING_TO_UPDATE", "Send at least one profile field.")

    expected = policy.objective
    if "objective" in updates and updates["objective"] not in (None, expected):
        raise ProfileValidationError(
            "ROLE_OBJECTIVE_MISMATCH", "The objective is owned by the brokerage role.",
        )
    result = {
        key: value for key, value in current.items()
        if key not in {"status", "objective", *SERVER_FIELDS}
    }
    for field, value in updates.items():
        if field == "objective":
            continue
        if field == "notes":
            if value is not None and not isinstance(value, str):
                raise ProfileValidationError("INVALID_PROFILE", "notes must be text.")
            text = (value or "").strip()
            if len(text) > 2000:
                raise ProfileValidationError("INVALID_PROFILE", "notes cannot exceed 2000 characters.")
            result[field] = text
        elif field == "first_expected_withdrawal_date":
            if value in (None, ""):
                result[field] = None
            else:
                try:
                    result[field] = date.fromisoformat(str(value)).isoformat()
                except ValueError as exc:
                    raise ProfileValidationError(
                        "INVALID_PROFILE", "first_expected_withdrawal_date must be YYYY-MM-DD.",
                    ) from exc
        else:
            result[field] = _percentage(
                value, field, gross=(field == "max_gross_exposure_pct")
            )

    for low, high in (
        ("deployment_min_pct", "deployment_max_pct"),
        ("growth_min_pct", "growth_max_pct"),
        ("cash_min_pct", "cash_max_pct"),
    ):
        if result.get(low) is not None and result.get(high) is not None:
            if result[low] > result[high]:
                raise ProfileValidationError(
                    "INVALID_PROFILE_RANGE", f"{low} cannot exceed {high}.",
                )
    if (
        result.get("growth_min_pct") is not None
        and result.get("cash_min_pct") is not None
        and result["growth_min_pct"] + result["cash_min_pct"] > 100
    ):
        raise ProfileValidationError(
            "INVALID_PROFILE_RANGE",
            "Minimum growth and cash allocations cannot exceed 100% together.",
        )
    return {**result, "objective": expected, "reviewed_at": _now()}


def update_profile(path: Path, brokerage_id: str, policy: PortfolioAnalysisPolicy,
                   updates: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        profiles = read_profiles(path)
        current = profiles.get(brokerage_id, {})
        validated = validate_patch(current, updates, policy)
        profiles[brokerage_id] = validated
        _atomic_json(path, {"schema_version": SCHEMA_VERSION, "profiles": profiles})
    return {**validated, "status": profile_status(validated, policy)}


def read_classifications(path: Path, brokerage_id: str | None = None
                         ) -> dict[tuple[str, str, str], dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {
        (row.get("brokerage_id", ""), row.get("account_id", ""), row.get("symbol", "")):
        {field: row.get(field, "") for field in CLASSIFICATION_HEADERS}
        for row in rows
        if row.get("symbol") and (brokerage_id is None or row.get("brokerage_id") == brokerage_id)
    }


def set_classification(path: Path, *, brokerage_id: str, account_id: str,
                       symbol: str, bucket: Any) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_account = str(account_id or "").strip()
    normalized_bucket = str(bucket or "").strip().upper()
    if not normalized_symbol:
        raise ProfileValidationError("INVALID_SYMBOL", "A symbol is required.")
    if not normalized_account:
        raise ProfileValidationError("ACCOUNT_REQUIRED", "An account is required.")
    if normalized_bucket and normalized_bucket not in BUCKETS - {"UNKNOWN"}:
        raise ProfileValidationError(
            "INVALID_CLASSIFICATION", "allocation_bucket is not supported.",
        )
    with _lock:
        rows = read_classifications(path)
        key = (brokerage_id, normalized_account, normalized_symbol)
        if normalized_bucket:
            row = {
                "brokerage_id": brokerage_id,
                "account_id": normalized_account,
                "symbol": normalized_symbol,
                "allocation_bucket": normalized_bucket,
                "updated_at": _now(),
            }
            rows[key] = row
        else:
            rows.pop(key, None)
            row = None
        options_activity._atomic_write(
            path, list(CLASSIFICATION_HEADERS), [rows[item] for item in sorted(rows)]
        )
    return {"classification": row, "cleared": row is None}
