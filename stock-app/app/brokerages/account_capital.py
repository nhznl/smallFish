"""Provider-neutral account-capital artifact contract.

Brokerage sync is the only writer. Read adapters consume the latest immutable
snapshot without contacting a provider. Blank numeric fields remain ``None``
and carry one stable reason per field; they are never coerced to zero.
"""

from __future__ import annotations

import csv
import os
import tempfile
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    MISSING_BUYING_POWER,
    MISSING_CASH_BALANCE,
    MISSING_MAINTENANCE_REQUIREMENT,
    MISSING_NET_LIQUIDATING_VALUE,
    AccountCapitalFact,
    AccountRef,
    Provenance,
)

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

FIELD_REASONS = {
    "net_liquidating_value": MISSING_NET_LIQUIDATING_VALUE,
    "cash_balance": MISSING_CASH_BALANCE,
    "buying_power": MISSING_BUYING_POWER,
    "maintenance_requirement": MISSING_MAINTENANCE_REQUIREMENT,
}

HEADERS = [
    "schema_version", "brokerage_id", "account_id", "account", "currency",
    "net_liquidating_value", "net_liquidating_value_missing_reason",
    "cash_balance", "cash_balance_missing_reason",
    "buying_power", "buying_power_missing_reason",
    "maintenance_requirement", "maintenance_requirement_missing_reason",
    "source", "retrieved_at",
]

_lock = threading.RLock()


class AccountCapitalArtifactError(ValueError):
    """The materialized capital artifact is unsupported or malformed."""


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def optional_decimal(value: Any) -> Decimal | None:
    text = _text(value).strip()
    if not text:
        return None
    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _number(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f") if value else "0"


def _reason_for(fact: AccountCapitalFact, field: str) -> str:
    value = getattr(fact, field)
    stable_reason = FIELD_REASONS[field]
    return stable_reason if value is None else ""


def write_facts(path: Path, facts: Iterable[AccountCapitalFact]) -> None:
    """Atomically replace one ledger namespace's latest capital snapshot."""
    rows = []
    for fact in facts:
        row = {
            "schema_version": SCHEMA_VERSION,
            "brokerage_id": fact.brokerage_id,
            "account_id": fact.account.account_id,
            "account": fact.account.label,
            "currency": fact.currency,
            "source": fact.provenance.source,
            "retrieved_at": fact.provenance.retrieved_at or "",
        }
        for field in FIELD_REASONS:
            row[field] = _number(getattr(fact, field))
            row[f"{field}_missing_reason"] = _reason_for(fact, field)
        rows.append(row)

    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=HEADERS, lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise


def read_facts(path: Path, *, brokerage_id: str,
               source: str | None = None) -> list[AccountCapitalFact]:
    """Read canonical facts, injecting public identity and provenance source."""
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    facts = []
    for row in rows:
        version = row.get("schema_version", "")
        try:
            parsed_version = int(version)
        except (TypeError, ValueError) as exc:
            raise AccountCapitalArtifactError(
                f"unsupported {path.name} schema; expected version {SCHEMA_VERSION}"
            ) from exc
        if parsed_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise AccountCapitalArtifactError(
                f"unsupported {path.name} schema; expected version {SCHEMA_VERSION}"
            )

        stored_brokerage_id = _text(row.get("brokerage_id")).strip()
        if stored_brokerage_id and stored_brokerage_id != brokerage_id:
            raise AccountCapitalArtifactError(
                f"{path.name} belongs to a different brokerage"
            )

        values = {field: optional_decimal(row.get(field)) for field in FIELD_REASONS}
        missing = []
        for field, stable_reason in FIELD_REASONS.items():
            if values[field] is None:
                # Only the stable contract reason reaches projections. The
                # artifact's explicit column proves why the blank is not zero.
                missing.append(stable_reason)
        facts.append(AccountCapitalFact(
            brokerage_id=brokerage_id,
            account=AccountRef(
                account_id=_text(row.get("account_id")).strip(),
                label=_text(row.get("account")).strip(),
            ),
            currency=_text(row.get("currency")).strip().upper(),
            provenance=Provenance(
                source=source or _text(row.get("source")).strip(),
                retrieved_at=_text(row.get("retrieved_at")).strip() or None,
            ),
            missing=tuple(missing),
            **values,
        ))
    return facts
