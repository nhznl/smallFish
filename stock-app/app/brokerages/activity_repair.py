"""Tests-backed recovery helpers for the options-activity ledger.

These are not exposed as HTTP or CLI surfaces in Phase 6; keep them for
narrowly scoped pre-window repairs and symbol cleanup after review.
"""

from __future__ import annotations

from typing import Any

from .. import config
from .activity_normalize import _normalize_event, _now, _text, _value
from .activity_store import (
    ACTIVITY_HEADERS,
    BETA_HEADERS,
    GREEKS_HEADERS,
    MARK_HEADERS,
    ActivityValidationError,
    _atomic_write,
    _lock,
    _read_csv,
)


def import_broker_events(transactions: list[Any], *, account: str | None = None) -> dict[str, Any]:
    """Merge an explicitly selected set of broker events into the activity ledger.

    This is used for narrowly scoped pre-window repairs after the provider
    transactions have been reviewed. Provider IDs keep repeated imports
    idempotent. Grouping is retired, so a repaired event joins its Symbol Ledger
    by its own underlying rather than needing a membership row.
    """
    account = _text(account or "TRADING").upper()
    if account not in {"RETIREMENT", "TRADING"}:
        raise ActivityValidationError("account must be RETIREMENT or TRADING")
    excluded_symbols = config.options_activity_excluded_symbols()
    transactions = [
        row for row in transactions
        if _text(_value(row, "underlying_symbol") or _value(row, "symbol")).upper()
        not in excluded_symbols
    ]
    retrieved_at = _now()
    with _lock:
        existing = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        existing_by_id = {row["id"]: row for row in existing}
        normalized = []
        for row in transactions:
            transaction_id = _text(_value(row, "id"))
            event_id = f"tastytrade:{account}:{transaction_id}"
            normalized.append(_normalize_event(
                row, account, retrieved_at,
                imported_at=existing_by_id.get(event_id, {}).get("imported_at") or None,
            ))
        merged = {row["id"]: row for row in existing}
        merged.update({row["id"]: row for row in normalized})
        events = sorted(merged.values(), key=lambda row: (row["executed_at"], row["id"]))
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, events)

        groups_created = events_grouped = groups_reactivated = 0
    return {
        "events_received": len(normalized),
        "events_inserted": sum(1 for row in normalized if row["id"] not in existing_by_id),
        "events_updated": sum(1 for row in normalized if row["id"] in existing_by_id),
        "groups_created": groups_created,
        "events_auto_grouped": events_grouped,
        "groups_reactivated": groups_reactivated,
        "retrieved_at": retrieved_at,
    }


def remove_symbols(symbols: set[str]) -> dict[str, int]:
    """Remove selected symbols from all local broker-ledger projections.

    Callers should configure the same symbols in
    ``SFP_OPTIONS_ACTIVITY_EXCLUDED_SYMBOLS`` before the next broker sync if
    the removal is intended to persist.
    """
    normalized = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
    if not normalized:
        raise ActivityValidationError("at least one symbol is required")
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        marks = _read_csv(config.options_position_marks_csv(), MARK_HEADERS)
        greeks = _read_csv(config.options_greeks_csv(), GREEKS_HEADERS)
        betas = _read_csv(config.options_betas_csv(), BETA_HEADERS)

        retained_events = [
            row for row in events if row["underlying_symbol"].upper() not in normalized
        ]
        retained_marks = [
            row for row in marks if row["underlying_symbol"].upper() not in normalized
        ]
        retained_greeks = [
            row for row in greeks
            if row["contract_key"].split(maxsplit=1)[0].upper() not in normalized
        ]
        retained_betas = [row for row in betas if row["symbol"].upper() not in normalized]

        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, retained_events)
        _atomic_write(config.options_position_marks_csv(), MARK_HEADERS, retained_marks)
        _atomic_write(config.options_greeks_csv(), GREEKS_HEADERS, retained_greeks)
        _atomic_write(config.options_betas_csv(), BETA_HEADERS, retained_betas)
    return {
        "events_removed": len(events) - len(retained_events),
        "marks_removed": len(marks) - len(retained_marks),
        "greeks_removed": len(greeks) - len(retained_greeks),
        "betas_removed": len(betas) - len(retained_betas),
    }
