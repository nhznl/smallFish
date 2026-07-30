"""Options-activity CSV store: headers, lock, read, and atomic write."""

from __future__ import annotations

import csv
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

SCHEMA_NAME = "smallfish.options-activity"
SCHEMA_VERSION = 1
SOURCE = "TASTYTRADE"
# Manual reconciliation rows live in their own event-id namespace. `sync()`
# merges broker events by id, so a `manual:` row can never be overwritten or
# dropped by a Tastytrade import no matter how far back the sync window reaches.
MANUAL_SOURCE = "MANUAL"
MANUAL_ID_PREFIX = "manual:"

ACTIVITY_HEADERS = [
    "schema_version", "id", "source", "source_transaction_id", "account",
    "executed_at", "transaction_date", "transaction_type", "transaction_sub_type",
    "instrument_type", "contract_symbol", "contract_key", "underlying_symbol",
    "action", "quantity", "position_delta", "price", "value", "net_value",
    "fee_effect", "commission", "regulatory_fees", "clearing_fees",
    "proprietary_index_option_fees", "other_charge", "order_id", "reverses_id",
    "option_type", "expiry", "strike", "description", "imported_at", "retrieved_at",
]
MARK_HEADERS = [
    "source", "account", "instrument_type", "contract_symbol", "contract_key",
    "underlying_symbol", "quantity", "direction", "signed_quantity", "multiplier",
    "mark", "mark_price", "updated_at", "retrieved_at",
]
COMBINED_POSITION_HEADERS = [
    "schema_version", *MARK_HEADERS, "average_open_price",
]
GREEKS_HEADERS = [
    "schema_version", "source", "account", "contract_symbol", "contract_key",
    "streamer_symbol", "implied_volatility", "option_price", "delta", "gamma",
    "theta", "rho", "vega", "observed_at", "event_time_ms", "retrieved_at",
]
BETA_HEADERS = [
    "schema_version", "source", "symbol", "beta", "beta_updated_at", "retrieved_at",
]

_lock = threading.RLock()


class ActivityValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _read_csv(path: Path, headers: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        actual = reader.fieldnames or []
        if actual != headers:
            raise ActivityValidationError(
                f"unsupported {path.name} schema; expected version {SCHEMA_VERSION}", 409
            )
        return [{key: row.get(key, "") for key in headers} for row in reader]


def _atomic_write(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _text(row.get(key)) for key in headers})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
