"""Additive request schemas for brokerage write routes.

Field names match the existing wire format (snake_case). Value typing stays
loose on purpose: ``service`` already emits the stable ``code`` / ``message``
error bodies the Angular client and contract tests expect. Strict Pydantic
coercion here would replace those with FastAPI validation lists.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def request_payload(body: BaseModel | None) -> dict[str, Any]:
    """Dict for service validation, including unknown keys the service rejects."""
    if body is None:
        return {}
    return body.model_dump(mode="python", exclude_unset=True)


class SymbolPatchRequest(BaseModel):
    """PATCH /api/brokerages/{id}/symbols/{symbol} — notes only."""

    model_config = ConfigDict(extra="allow")

    notes: Any = Field(
        default=None,
        description="App-owned free-text note; null or empty clears it.",
        json_schema_extra={"type": ["string", "null"]},
    )


class HoldingsMetadataPatchRequest(BaseModel):
    """PATCH /api/brokerages/{id}/holdings/{symbol}/metadata."""

    model_config = ConfigDict(extra="allow")

    category: Any = Field(
        default=None, json_schema_extra={"type": ["string", "null"]})
    industry: Any = Field(
        default=None, json_schema_extra={"type": ["string", "null"]})
    note: Any = Field(
        default=None, json_schema_extra={"type": ["string", "null"]})
    account_id: Any = Field(
        default=None, json_schema_extra={"type": ["string", "null"]})
    cost_basis: Any = Field(
        default=None, json_schema_extra={"type": ["number", "string", "null"]})
    cost_per_unit: Any = Field(
        default=None, json_schema_extra={"type": ["number", "string", "null"]})


class HoldingsSettingsPatchRequest(BaseModel):
    """PATCH /api/brokerages/{id}/holdings/settings."""

    model_config = ConfigDict(extra="allow")

    total_contributions: Any = Field(
        default=None, json_schema_extra={"type": ["number", "string", "null"]})
    year_beginning_balance: Any = Field(
        default=None, json_schema_extra={"type": ["number", "string", "null"]})
    baseline_year: Any = Field(
        default=None, json_schema_extra={"type": ["integer", "string", "null"]})


class ArchiveCreateRequest(BaseModel):
    """POST /api/brokerages/{id}/symbols/{symbol}/archives."""

    model_config = ConfigDict(extra="allow")

    request_id: Any = Field(
        default=None,
        description="Idempotency key so a retry cannot archive twice.",
        json_schema_extra={"type": ["string", "null"]},
    )
    expected_period_version: Any = Field(
        default=None,
        description="Period version from the ledger the client loaded.",
        json_schema_extra={"type": ["string", "null"]},
    )
    note: Any = Field(
        default=None, json_schema_extra={"type": ["string", "null"]})


class SyncRequest(BaseModel):
    """POST /api/brokerages/{id}/sync."""

    model_config = ConfigDict(extra="allow")

    resources: list[str] | None = Field(
        default=None,
        description="Optional HOLDINGS / ACTIVITY / MARKET_DATA subset.",
    )


class ManualActivityCreateRequest(BaseModel):
    """POST /api/brokerages/{id}/activity/manual."""

    model_config = ConfigDict(extra="allow")

    account: Any = None
    contract_key: Any = None
    contract_symbol: Any = None
    underlying_symbol: Any = None
    quantity: Any = None
    transaction_date: Any = None
    price: Any = None
    net_cash: Any = None
    fees: Any = None
    description: Any = None
    reason: Any = None
    instrument_type: Any = None
    group_id: Any = None


class ManualActivityUpdateRequest(BaseModel):
    """PUT /api/brokerages/{id}/activity/manual/{event_id}."""

    model_config = ConfigDict(extra="allow")

    quantity: Any = None
    transaction_date: Any = None
    price: Any = None
    net_cash: Any = None
    fees: Any = None
    description: Any = None
    reason: Any = None
