"""The one service the brokerage routers call.

Routers serialize; this resolves identity and delegates to a projection. Keeping
both thin is what makes adding an institution a registry entry rather than a new
router, projection, or component.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .. import config
from . import (activity_manual, portfolio_analysis_profile, registry,
               sold_tracking, store, sync)
from .activity_store import ActivityValidationError
from .contracts import BrokerageSnapshot
from .projections import (components as component_projection, envelope, events,
                          holdings, option_adjusted_basis, options,
                          portfolio_analysis, portfolio_preview, symbol_ledger)

CATALOG_SCHEMA_NAME = "smallfish.brokerage-catalog"
MAX_NOTE_LENGTH = 2000


class BrokerageRequestError(ValueError):
    """A safe, machine-readable public failure.

    Provider exception detail stays in the server log; a caller gets a stable
    code and a message that names no token, account, or position.
    """

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _snapshot(brokerage_id: str) -> tuple[BrokerageSnapshot, registry.BrokerageRegistration]:
    try:
        entry = registry.registration(brokerage_id)
    except registry.UnknownBrokerageError as exc:
        raise BrokerageRequestError(
            "UNKNOWN_BROKERAGE", "That brokerage is not configured.", 404
        ) from exc
    adapter = entry.factory(
        entry.descriptor, entry.capabilities, entry.account_capital_path()
    )
    return adapter.snapshot(), entry


def catalog() -> dict[str, Any]:
    """Discovery. Angular may use declared capabilities to decide what to show;
    it must never branch on the identity itself to interpret data."""
    brokerages = []
    for entry in registry.REGISTRY.values():
        adapter = entry.factory(
            entry.descriptor, entry.capabilities, entry.account_capital_path()
        )
        brokerages.append({
            "id": entry.descriptor.id,
            "label": entry.descriptor.label,
            "institution": entry.descriptor.institution,
            "portfolio_role": entry.descriptor.portfolio_role,
            "capabilities": {
                "holdings": entry.capabilities.holdings,
                "options": entry.capabilities.options,
                "option_adjusted_basis": entry.capabilities.option_adjusted_basis,
                "activity": entry.capabilities.activity,
                "sync": entry.capabilities.sync,
            },
            "availability": {
                "status": "AVAILABLE" if not adapter.availability_reasons() else "PARTIAL",
                "reasons": list(adapter.availability_reasons()),
            },
        })
    return {
        "schema_name": CATALOG_SCHEMA_NAME,
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerages": brokerages,
    }


def brokerage_holdings(brokerage_id: str, *,
                       account_id: str | None = None) -> dict[str, Any]:
    snapshot, entry = _snapshot(brokerage_id)
    return holdings.build(
        snapshot, metadata_path=entry.holdings_metadata_path(),
        trend_path=entry.holdings_trend_path(),
        settings_path=entry.holdings_settings_path(),
        account_id=account_id,
    )


def brokerage_options(brokerage_id: str, *, state: str = "all",
                      account_id: str | None = None) -> dict[str, Any]:
    snapshot, _entry = _snapshot(brokerage_id)
    return options.build(snapshot, state=state, account_id=account_id)


def brokerage_option_adjusted_basis(brokerage_id: str, *,
                                    account_id: str | None = None) -> dict[str, Any]:
    snapshot, _entry = _snapshot(brokerage_id)
    return option_adjusted_basis.build(snapshot, account_id=account_id)


# ------------------------------------------------------ portfolio analysis --

def brokerage_portfolio_analysis(brokerage_id: str) -> dict[str, Any]:
    snapshot, entry = _snapshot(brokerage_id)
    try:
        return portfolio_analysis.build(
            snapshot,
            profile_path=config.portfolio_analysis_profiles_json(),
            classifications_path=config.portfolio_analysis_classifications_csv(),
            metadata_path=entry.holdings_metadata_path(),
        )
    except portfolio_analysis_profile.ProfileValidationError as exc:
        raise BrokerageRequestError(exc.code, exc.message, 409) from exc


def get_portfolio_analysis_profile(brokerage_id: str) -> dict[str, Any]:
    snapshot, _entry = _snapshot(brokerage_id)
    try:
        profile = portfolio_analysis_profile.read_profile(
            config.portfolio_analysis_profiles_json(), brokerage_id,
            snapshot.descriptor.analysis_policy,
        )
    except portfolio_analysis_profile.ProfileValidationError as exc:
        raise BrokerageRequestError(exc.code, exc.message, 409) from exc
    return {
        "schema_name": "smallfish.portfolio-analysis-profile",
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerage": envelope.brokerage_block(snapshot),
        "profile": profile,
    }


def update_portfolio_analysis_profile(brokerage_id: str,
                                      payload: dict[str, Any]) -> dict[str, Any]:
    snapshot, _entry = _snapshot(brokerage_id)
    try:
        profile = portfolio_analysis_profile.update_profile(
            config.portfolio_analysis_profiles_json(), brokerage_id,
            snapshot.descriptor.analysis_policy, payload,
        )
    except portfolio_analysis_profile.ProfileValidationError as exc:
        raise BrokerageRequestError(exc.code, exc.message, 422) from exc
    return {
        "schema_name": "smallfish.portfolio-analysis-profile",
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerage": envelope.brokerage_block(snapshot),
        "profile": profile,
    }


def update_portfolio_analysis_classification(
        brokerage_id: str, symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
    snapshot, _entry = _snapshot(brokerage_id)
    unknown = set(payload) - {"account_id", "allocation_bucket"}
    if unknown:
        raise BrokerageRequestError(
            "UNSUPPORTED_FIELD", f"Cannot update {', '.join(sorted(unknown))}.", 422
        )
    account_id = str(payload.get("account_id") or "").strip()
    if not any(
        row.account.account_id == account_id and row.symbol == str(symbol).strip().upper()
        and row.instrument != "OPTION" for row in snapshot.positions
    ):
        raise BrokerageRequestError(
            "UNKNOWN_HOLDING", "That account does not hold this symbol.", 404,
        )
    try:
        result = portfolio_analysis_profile.set_classification(
            config.portfolio_analysis_classifications_csv(),
            brokerage_id=brokerage_id, account_id=account_id, symbol=symbol,
            bucket=payload.get("allocation_bucket"),
        )
    except portfolio_analysis_profile.ProfileValidationError as exc:
        raise BrokerageRequestError(exc.code, exc.message, 422) from exc
    return {
        "schema_name": "smallfish.portfolio-analysis-classification",
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerage": envelope.brokerage_block(snapshot), **result,
    }


def preview_portfolio_analysis(brokerage_id: str,
                               payload: dict[str, Any]) -> dict[str, Any]:
    snapshot, entry = _snapshot(brokerage_id)
    try:
        return portfolio_preview.build(
            snapshot, payload=payload,
            profile_path=config.portfolio_analysis_profiles_json(),
            classifications_path=config.portfolio_analysis_classifications_csv(),
            metadata_path=entry.holdings_metadata_path(),
        )
    except portfolio_preview.PreviewValidationError as exc:
        raise BrokerageRequestError(exc.code, exc.message, 422) from exc
    except portfolio_analysis_profile.ProfileValidationError as exc:
        raise BrokerageRequestError(exc.code, exc.message, 409) from exc


# --------------------------------------------------------------- symbols ---

def _ledgers(brokerage_id: str, *, account_id: str | None = None):
    snapshot, entry = _snapshot(brokerage_id)
    try:
        archives = store.read_archives(entry.descriptor.id)
        metadata = store.read_metadata()
    except store.LedgerStoreError as exc:
        raise BrokerageRequestError(exc.code, exc.message, exc.status_code) from exc
    return snapshot, symbol_ledger.build(
        snapshot, archives=archives, metadata=metadata, account_id=account_id,
    )


def _one_ledger(brokerage_id: str, symbol: str,
                *, account_id: str | None = None):
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        raise BrokerageRequestError(
            "INVALID_SYMBOL", "A symbol is required.", 422
        )
    snapshot, ledgers = _ledgers(brokerage_id, account_id=account_id)
    ledger = next((row for row in ledgers if row.symbol == normalized), None)
    if ledger is None:
        raise BrokerageRequestError(
            "UNKNOWN_SYMBOL", "That symbol has no ledger for this brokerage.", 404
        )
    return snapshot, ledger


def list_symbols(brokerage_id: str, *, state: str = "active", exposure: str = "all",
                 account_id: str | None = None) -> dict[str, Any]:
    snapshot, ledgers = _ledgers(brokerage_id, account_id=account_id)
    return symbol_ledger.list_response(
        snapshot, ledgers, state=state, exposure=exposure
    )


def get_symbol(brokerage_id: str, symbol: str, *,
               account_id: str | None = None,
               exposure: str = "all") -> dict[str, Any]:
    snapshot, ledger = _one_ledger(brokerage_id, symbol, account_id=account_id)
    options_only = str(exposure or "all").strip().lower() == "options"
    detail = ledger.detail(options_only=options_only)
    return {
        "schema_name": symbol_ledger.DETAIL_SCHEMA_NAME,
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerage": envelope.brokerage_block(snapshot),
        "availability": envelope.availability_block(snapshot),
        "as_of": envelope.as_of_block(snapshot),
        "coverage": envelope.coverage_block(
            snapshot, status=detail["pnl_completeness"]
        ),
        "symbol": detail,
        "warnings": [
            {
                "code": "SYMBOL_NEEDS_REVIEW", "scope": "SYMBOL",
                "symbol": ledger.symbol, "component_id": None, "message": reason,
            }
            for reason in detail["warnings"]
        ],
    }


def update_symbol(brokerage_id: str, symbol: str,
                  payload: dict[str, Any]) -> dict[str, Any]:
    """Version 1 patches app-owned metadata and nothing else.

    The symbol, its lifecycle, its P/L, its accounts, and its archives are all
    derived or immutable; there is deliberately no way to set them by hand.
    """
    _snapshot, ledger = _one_ledger(brokerage_id, symbol)
    unknown = set(payload) - {"notes"}
    if unknown:
        raise BrokerageRequestError(
            "UNSUPPORTED_FIELD",
            f"Only notes can be updated; received {', '.join(sorted(unknown))}.",
            422,
        )
    if "notes" not in payload:
        raise BrokerageRequestError(
            "NOTHING_TO_UPDATE", "Send a notes value to update.", 422
        )
    notes = payload["notes"]
    if notes is not None and not isinstance(notes, str):
        raise BrokerageRequestError("INVALID_NOTES", "Notes must be text.", 422)
    notes = (notes or "").strip()
    if len(notes) > MAX_NOTE_LENGTH:
        raise BrokerageRequestError(
            "INVALID_NOTES",
            f"Notes cannot exceed {MAX_NOTE_LENGTH} characters.", 422,
        )
    store.set_notes(brokerage_id, ledger.symbol, notes)
    return get_symbol(brokerage_id, ledger.symbol)


def symbol_events(brokerage_id: str, symbol: str, *, period: str = "current",
                  cursor: str | None = None,
                  limit: int = events.DEFAULT_LIMIT) -> dict[str, Any]:
    snapshot, ledger = _one_ledger(brokerage_id, symbol)
    every = ledger.all_events
    wanted = str(period or "current").strip()

    if wanted.lower() == "all":
        selected = every
    elif wanted.lower() == "current":
        selected = symbol_ledger.events_in_period(
            every, after=ledger.boundary, through=None
        )
    else:
        boundaries = ledger.archives
        index = next(
            (i for i, row in enumerate(boundaries) if row.archive_id == wanted), None
        )
        if index is None:
            raise BrokerageRequestError(
                "UNKNOWN_ARCHIVE", "That archived period does not exist.", 404
            )
        selected = symbol_ledger.events_in_period(
            every,
            after=boundaries[index - 1].order_key if index else None,
            through=boundaries[index].order_key,
        )

    try:
        page = events.page(selected, cursor=cursor, limit=limit)
    except events.CursorError as exc:
        raise BrokerageRequestError(events.CursorError.code, str(exc), 422) from exc
    return {
        "schema_name": events.SCHEMA_NAME,
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerage": envelope.brokerage_block(snapshot),
        "symbol": ledger.symbol,
        "period": wanted,
        "total_event_count": len(selected),
        **page,
    }


# -------------------------------------------------------------- archives ---

def list_archives(brokerage_id: str, symbol: str) -> dict[str, Any]:
    snapshot, ledger = _one_ledger(brokerage_id, symbol)
    return {
        "schema_name": "smallfish.symbol-ledger-archives",
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerage": envelope.brokerage_block(snapshot),
        "symbol": ledger.symbol,
        "items": ledger.archive_summaries,
        "summary": {
            "archived_period_count": len(ledger.archive_summaries),
            "archived_pnl": ledger.archived_pnl,
        },
    }


def get_archive(brokerage_id: str, symbol: str, archive_id: str) -> dict[str, Any]:
    body = list_archives(brokerage_id, symbol)
    archive = next(
        (row for row in body["items"] if row["archive_id"] == archive_id), None
    )
    if archive is None:
        raise BrokerageRequestError(
            "UNKNOWN_ARCHIVE", "That archived period does not exist.", 404
        )
    return {
        "schema_name": "smallfish.symbol-ledger-archive",
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerage": body["brokerage"],
        "symbol": body["symbol"],
        "archive": archive,
    }


def create_archive(brokerage_id: str, symbol: str,
                   payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Seal the completed current period. Broker facts are never touched.

    Returns ``(status_code, body)`` so a repeated ``request_id`` can answer 200
    with the original archive instead of creating a second boundary.
    """
    _snapshot, ledger = _one_ledger(brokerage_id, symbol)
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        raise BrokerageRequestError(
            "MISSING_REQUEST_ID",
            "A unique request_id is required so a retry cannot archive twice.",
            422,
        )
    existing = store.find_by_request_id(brokerage_id, ledger.symbol, request_id)
    if existing is not None:
        return 200, _archive_response(brokerage_id, ledger.symbol, existing.archive_id)

    expected = str(payload.get("expected_period_version") or "").strip()
    if not expected:
        raise BrokerageRequestError(
            "MISSING_PERIOD_VERSION",
            "Send the expected_period_version returned with the period you loaded.",
            422,
        )
    # Staleness is checked before eligibility on purpose. If the period moved,
    # every other verdict is about data the user never saw, and "refresh" is
    # the only useful thing to tell them.
    if expected != ledger.period_version:
        raise BrokerageRequestError(
            "PERIOD_CHANGED",
            "This period changed since you loaded it. Refresh and try again.",
            409,
        )
    blockers = ledger.reset_blockers()
    if blockers:
        raise BrokerageRequestError(
            blockers[0], _blocker_message(blockers[0], ledger.symbol), 409
        )

    note = str(payload.get("note") or "").strip()
    if len(note) > MAX_NOTE_LENGTH:
        raise BrokerageRequestError(
            "INVALID_NOTE", f"The note cannot exceed {MAX_NOTE_LENGTH} characters.",
            422,
        )
    period = ledger.current_period
    last = ledger.current_events[-1]
    boundary = store.ArchiveBoundary(
        archive_id=store.new_archive_id(),
        brokerage_id=brokerage_id,
        symbol=ledger.symbol,
        period_started_at=period["started_at"],
        period_ended_at=datetime.now(timezone.utc).isoformat(),
        first_event_at=period["first_event_at"],
        last_event_at=last.executed_at,
        boundary_event_id=last.provider_event_id,
        event_count_at_creation=period["event_count"],
        realized_pnl_at_creation=(
            None if period["realized_pnl"] is None
            else Decimal(str(period["realized_pnl"]))
        ),
        event_set_hash_at_creation=store.event_set_hash(ledger.current_events),
        period_version=ledger.period_version,
        request_id=request_id,
        note=note,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        store.append_archive(boundary)
    except store.LedgerStoreError as exc:
        raise BrokerageRequestError(exc.code, exc.message, exc.status_code) from exc
    return 201, _archive_response(brokerage_id, ledger.symbol, boundary.archive_id)


def _archive_response(brokerage_id: str, symbol: str,
                      archive_id: str) -> dict[str, Any]:
    """The created archive plus the refreshed symbol, in one round trip."""
    archive = get_archive(brokerage_id, symbol, archive_id)
    return {
        "schema_name": "smallfish.symbol-ledger-archive-created",
        "schema_version": envelope.SCHEMA_VERSION,
        "archive": archive["archive"],
        "symbol": get_symbol(brokerage_id, symbol)["symbol"],
    }


_BLOCKER_MESSAGES = {
    "PERIOD_EMPTY": "{symbol} has no events in the current period to archive.",
    "SYMBOL_NOT_FLAT": "{symbol} still has open exposure and cannot be archived.",
    "SYMBOL_NOT_RECONCILED": (
        "{symbol} does not reconcile with the broker position yet."
    ),
    "PERIOD_INCOMPLETE": (
        "{symbol} has incomplete history, so its result cannot be sealed."
    ),
}


def _blocker_message(code: str, symbol: str) -> str:
    return _BLOCKER_MESSAGES.get(code, "{symbol} cannot be archived.").format(
        symbol=symbol
    )


# ------------------------------------------------------ holdings metadata ---

def update_holdings_metadata(brokerage_id: str, symbol: str,
                             payload: dict[str, Any]) -> dict[str, Any]:
    """Edit app-owned holding metadata. Broker facts stay immutable."""
    facts, entry = _snapshot(brokerage_id)
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        raise BrokerageRequestError("INVALID_SYMBOL", "A symbol is required.", 422)
    updates: dict[str, str] = {}
    for field in ("category", "industry", "note", "display_name"):
        if field in payload:
            value = payload[field]
            if value is not None and not isinstance(value, str):
                raise BrokerageRequestError(
                    "INVALID_FIELD", f"{field} must be text.", 422
                )
            value = (value or "").strip()
            if field == "display_name":
                value = " ".join(value.split())
            updates[field] = value if field in {"note", "display_name"} else value.upper()
    basis_fields = {"cost_basis", "cost_per_unit"}
    basis_requested = bool(set(payload) & basis_fields)
    account_id: str | None = None
    if basis_requested:
        raw_account_id = payload.get("account_id")
        if not isinstance(raw_account_id, str) or not raw_account_id.strip():
            raise BrokerageRequestError(
                "ACCOUNT_REQUIRED",
                "An account is required to save a holding cost basis.", 422,
            )
        account_id = raw_account_id.strip()
        component = next(
            (
                row for row in holdings.held_equity(facts)
                if row.symbol == normalized and row.account_id == account_id
            ),
            None,
        )
        if component is None:
            raise BrokerageRequestError(
                "UNKNOWN_HOLDING", "That account does not hold this symbol.", 404
            )
        supplied = [
            (field, payload[field]) for field in basis_fields
            if field in payload and payload[field] not in (None, "")
        ]
        if len(supplied) > 1:
            raise BrokerageRequestError(
                "AMBIGUOUS_COST_BASIS",
                "Send either total cost basis or cost per share, not both.", 422,
            )
        if supplied and component_projection.EQUITY_COST_BASIS not in component.missing:
            raise BrokerageRequestError(
                "COST_BASIS_AVAILABLE",
                "The brokerage already supplies cost basis for this holding.", 409,
            )
        updates.update({
            "cost_basis_override": "",
            "cost_per_unit_override": "",
            "cost_basis_mode": "",
        })
        if supplied:
            field, raw_value = supplied[0]
            if isinstance(raw_value, bool):
                raise BrokerageRequestError(
                    "INVALID_COST_BASIS", "Cost basis must be zero or greater.", 422
                )
            try:
                value = Decimal(str(raw_value))
            except (InvalidOperation, ValueError) as exc:
                raise BrokerageRequestError(
                    "INVALID_COST_BASIS", "Cost basis must be zero or greater.", 422
                ) from exc
            if not value.is_finite() or value < 0:
                raise BrokerageRequestError(
                    "INVALID_COST_BASIS", "Cost basis must be zero or greater.", 422
                )
            serialized = format(value.normalize(), "f") if value else "0"
            if field == "cost_basis":
                updates["cost_basis_override"] = serialized
                updates["cost_basis_mode"] = "TOTAL"
            else:
                updates["cost_per_unit_override"] = serialized
                updates["cost_basis_mode"] = "PER_UNIT"

    allowed = {
        "category", "industry", "note", "display_name", "account_id", *basis_fields,
    }
    unknown = set(payload) - allowed
    if unknown:
        raise BrokerageRequestError(
            "UNSUPPORTED_FIELD",
            f"Cannot update {', '.join(sorted(unknown))}.", 422,
        )
    if not updates:
        raise BrokerageRequestError(
            "NOTHING_TO_UPDATE",
            "Send a category, industry, note, display name, or missing cost basis.",
            422,
        )
    row = holdings.write_metadata(
        entry.holdings_metadata_path(), normalized, updates,
        account_id=account_id,
    )
    return {
        "schema_name": "smallfish.brokerage-holdings-metadata",
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerage_id": brokerage_id,
        "metadata": row,
    }


def capture_gain_loss_snapshot(brokerage_id: str) -> dict[str, Any]:
    snapshot, entry = _snapshot(brokerage_id)
    try:
        return holdings.capture_snapshot(
            snapshot, brokerage_id=entry.descriptor.id,
            metadata_path=entry.holdings_metadata_path(),
        )
    except holdings.SnapshotUnavailable as exc:
        raise BrokerageRequestError(exc.code, str(exc), 409) from exc


def update_holdings_settings(brokerage_id: str,
                             payload: dict[str, Any]) -> dict[str, Any]:
    """Edit ledger-level performance baselines used on the Holdings page."""
    _snapshot(brokerage_id)
    entry = registry.registration(brokerage_id)
    allowed = {"total_contributions", "year_beginning_balance", "baseline_year"}
    unknown = set(payload) - allowed
    if unknown:
        raise BrokerageRequestError(
            "UNSUPPORTED_FIELD",
            f"Cannot update {', '.join(sorted(unknown))}.", 422,
        )
    if not payload:
        raise BrokerageRequestError(
            "NOTHING_TO_UPDATE",
            "Send total contributions and/or a year-start balance.", 422,
        )

    updates: dict[str, str] = {}
    current_year = str(datetime.now(timezone.utc).date().year)

    if "total_contributions" in payload:
        value = payload["total_contributions"]
        if value in (None, ""):
            updates["total_contributions"] = ""
        else:
            updates["total_contributions"] = _serialized_amount(
                value, "INVALID_CONTRIBUTIONS",
                "Total contributions must be zero or greater.",
            )

    if "year_beginning_balance" in payload:
        value = payload["year_beginning_balance"]
        if value in (None, ""):
            updates["year_beginning_balance"] = ""
            updates["baseline_year"] = ""
        else:
            updates["year_beginning_balance"] = _serialized_amount(
                value, "INVALID_YEAR_BALANCE",
                "Beginning balance must be zero or greater.",
            )
            raw_year = payload.get("baseline_year")
            if raw_year in (None, ""):
                updates["baseline_year"] = current_year
            else:
                updates["baseline_year"] = _serialized_year(raw_year)

    if "baseline_year" in payload and "year_beginning_balance" not in payload:
        value = payload["baseline_year"]
        if value in (None, ""):
            updates["baseline_year"] = ""
        else:
            updates["baseline_year"] = _serialized_year(value)

    row = holdings.write_settings(entry.holdings_settings_path(), updates)
    return {
        "schema_name": "smallfish.brokerage-holdings-settings",
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerage_id": brokerage_id,
        "settings": holdings._performance_baselines(
            market_value=None, settings=row,
        ),
    }


def _serialized_amount(value: Any, code: str, message: str) -> str:
    if isinstance(value, bool):
        raise BrokerageRequestError(code, message, 422)
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BrokerageRequestError(code, message, 422) from exc
    if not parsed.is_finite() or parsed < 0:
        raise BrokerageRequestError(code, message, 422)
    return format(parsed.normalize(), "f") if parsed else "0"


def _serialized_year(value: Any) -> str:
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise BrokerageRequestError(
            "INVALID_BASELINE_YEAR", "Baseline year must be a four-digit year.", 422,
        ) from exc
    if year < 1900 or year > 9999:
        raise BrokerageRequestError(
            "INVALID_BASELINE_YEAR", "Baseline year must be a four-digit year.", 422,
        )
    return str(year)


# ------------------------------------------------ manual reconciliation ---

def _manual_activity_context(
        brokerage_id: str, payload: dict[str, Any] | None = None,
        ) -> tuple[registry.BrokerageRegistration, dict[str, Any]]:
    try:
        entry = registry.registration(brokerage_id)
    except registry.UnknownBrokerageError as exc:
        raise BrokerageRequestError(
            "UNKNOWN_BROKERAGE", "That brokerage is not configured.", 404
        ) from exc
    if not entry.capabilities.activity:
        raise BrokerageRequestError(
            "ACTIVITY_UNAVAILABLE",
            "Manual reconciliation is unavailable for this brokerage.", 409,
        )
    normalized = dict(payload or {})
    expected_account = entry.descriptor.portfolio_role
    requested_account = str(normalized.get("account") or "").strip().upper()
    if requested_account and requested_account != expected_account:
        raise BrokerageRequestError(
            "BROKERAGE_ACCOUNT_MISMATCH",
            "The request account does not match the selected brokerage.", 422,
        )
    normalized["account"] = expected_account
    return entry, normalized


def create_manual_activity(brokerage_id: str,
                           payload: dict[str, Any]) -> dict[str, Any]:
    entry, normalized = _manual_activity_context(brokerage_id, payload)
    try:
        return activity_manual.create_manual_event(
            normalized, path=entry.activity_path()
        )
    except ActivityValidationError as exc:
        raise BrokerageRequestError(
            "INVALID_MANUAL_ACTIVITY", str(exc), exc.status_code
        ) from exc


def update_manual_activity(brokerage_id: str, event_id: str,
                           payload: dict[str, Any]) -> dict[str, Any]:
    entry, normalized = _manual_activity_context(brokerage_id, payload)
    try:
        return activity_manual.update_manual_event(
            event_id, normalized, path=entry.activity_path()
        )
    except ActivityValidationError as exc:
        raise BrokerageRequestError(
            "INVALID_MANUAL_ACTIVITY", str(exc), exc.status_code
        ) from exc


def delete_manual_activity(brokerage_id: str,
                           event_id: str) -> dict[str, Any]:
    entry, _normalized = _manual_activity_context(brokerage_id)
    try:
        return activity_manual.delete_manual_event(
            event_id, path=entry.activity_path()
        )
    except ActivityValidationError as exc:
        raise BrokerageRequestError(
            "INVALID_MANUAL_ACTIVITY", str(exc), exc.status_code
        ) from exc


# ------------------------------------------------------------------ sync ---

def run_sync(brokerage_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        entry = registry.registration(brokerage_id)
    except registry.UnknownBrokerageError as exc:
        raise BrokerageRequestError(
            "UNKNOWN_BROKERAGE", "That brokerage is not configured.", 404
        ) from exc
    try:
        resources = sync.normalize_resources((payload or {}).get("resources"))
    except ValueError as exc:
        raise BrokerageRequestError("INVALID_RESOURCES", str(exc), 422) from exc
    return sync.run(
        brokerage_id=entry.descriptor.id, resources=resources,
        commands=sold_tracking.wrap_holdings_commands(
            entry.descriptor.id, dict(entry.sync_commands),
        ),
        capabilities=entry.capabilities,
    )
