"""Brokerage-agnostic read endpoints.

The path identifies a configured brokerage, not an SDK or an aggregation
connector, and the same resource returns the same shape for every brokerage.
These routes are additive: the legacy `/options`, `/retirement`, and
`/brokerage-ledgers` contracts stay untouched until their consumers migrate.

This module resolves nothing and computes nothing. A router that started
branching on `brokerage_id` would defeat the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from ..brokerages import schemas, service

router = APIRouter(prefix="/api/brokerages")


def _fail(exc: service.BrokerageRequestError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail())


@router.get("")
def get_brokerages() -> dict:
    """Discover configured brokerages and what each one supports."""
    return service.catalog()


@router.get("/{brokerage_id}/holdings")
def get_holdings(brokerage_id: str,
                 account_id: str | None = Query(default=None)) -> dict:
    try:
        return service.brokerage_holdings(brokerage_id, account_id=account_id)
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.get("/{brokerage_id}/options")
def get_options(brokerage_id: str,
                state: str = Query(default="all"),
                account_id: str | None = Query(default=None)) -> dict:
    try:
        return service.brokerage_options(
            brokerage_id, state=state, account_id=account_id
        )
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.get("/{brokerage_id}/option-adjusted-basis")
def get_option_adjusted_basis(brokerage_id: str,
                              account_id: str | None = Query(default=None)) -> dict:
    try:
        return service.brokerage_option_adjusted_basis(
            brokerage_id, account_id=account_id
        )
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.patch("/{brokerage_id}/holdings/{symbol:path}/metadata")
def patch_holdings_metadata(
        brokerage_id: str, symbol: str,
        request: schemas.HoldingsMetadataPatchRequest | None = None) -> dict:
    try:
        return service.update_holdings_metadata(
            brokerage_id, symbol, schemas.request_payload(request)
        )
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.post("/{brokerage_id}/holdings/gain-loss-snapshots")
def post_gain_loss_snapshot(brokerage_id: str) -> dict:
    try:
        return service.capture_gain_loss_snapshot(brokerage_id)
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.post("/{brokerage_id}/sync")
def post_sync(brokerage_id: str,
              request: schemas.SyncRequest | None = None) -> dict:
    """Ask for common resource names; the adapter decides what that means."""
    try:
        return service.run_sync(brokerage_id, schemas.request_payload(request))
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


# ------------------------------------------------ manual reconciliation ---

@router.post("/{brokerage_id}/activity/manual")
def post_manual_activity(
        brokerage_id: str,
        request: schemas.ManualActivityCreateRequest) -> dict:
    try:
        return service.create_manual_activity(
            brokerage_id, schemas.request_payload(request)
        )
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.put("/{brokerage_id}/activity/manual/{event_id:path}")
def put_manual_activity(
        brokerage_id: str, event_id: str,
        request: schemas.ManualActivityUpdateRequest) -> dict:
    try:
        return service.update_manual_activity(
            brokerage_id, event_id, schemas.request_payload(request)
        )
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.delete("/{brokerage_id}/activity/manual/{event_id:path}")
def delete_manual_activity(brokerage_id: str, event_id: str) -> dict:
    try:
        return service.delete_manual_activity(brokerage_id, event_id)
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


# ----------------------------------------------------------- symbol ledger ---

@router.get("/{brokerage_id}/symbols")
def get_symbols(brokerage_id: str,
                state: str = Query(default="active"),
                exposure: str = Query(default="all"),
                account_id: str | None = Query(default=None)) -> dict:
    try:
        return service.list_symbols(
            brokerage_id, state=state, exposure=exposure, account_id=account_id
        )
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


# ``{symbol:path}`` accepts futures roots like ``/ESU6`` (URL-encoded as
# ``%2FESU6``). Nested routes must be declared before the bare symbol routes so
# a path converter does not swallow ``/events`` or ``/archives``.
@router.get("/{brokerage_id}/symbols/{symbol:path}/events")
def get_symbol_events(brokerage_id: str, symbol: str,
                      period: str = Query(default="current"),
                      cursor: str | None = Query(default=None),
                      limit: int = Query(default=100)) -> dict:
    try:
        return service.symbol_events(
            brokerage_id, symbol, period=period, cursor=cursor, limit=limit
        )
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.get("/{brokerage_id}/symbols/{symbol:path}/archives/{archive_id}")
def get_symbol_archive(brokerage_id: str, symbol: str, archive_id: str) -> dict:
    try:
        return service.get_archive(brokerage_id, symbol, archive_id)
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.get("/{brokerage_id}/symbols/{symbol:path}/archives")
def get_symbol_archives(brokerage_id: str, symbol: str) -> dict:
    try:
        return service.list_archives(brokerage_id, symbol)
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.post("/{brokerage_id}/symbols/{symbol:path}/archives", status_code=201)
def post_symbol_archive(brokerage_id: str, symbol: str, response: Response,
                        request: schemas.ArchiveCreateRequest | None = None) -> dict:
    """Seal the completed current period.

    Retrying the same ``request_id`` answers 200 with the original archive, so a
    dropped connection cannot produce two boundaries.
    """
    try:
        status_code, body = service.create_archive(
            brokerage_id, symbol, schemas.request_payload(request)
        )
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc
    response.status_code = status_code
    return body


@router.get("/{brokerage_id}/symbols/{symbol:path}")
def get_symbol(brokerage_id: str, symbol: str,
               account_id: str | None = Query(default=None)) -> dict:
    try:
        return service.get_symbol(brokerage_id, symbol, account_id=account_id)
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc


@router.patch("/{brokerage_id}/symbols/{symbol:path}")
def patch_symbol(brokerage_id: str, symbol: str,
                 request: schemas.SymbolPatchRequest | None = None) -> dict:
    try:
        return service.update_symbol(
            brokerage_id, symbol, schemas.request_payload(request)
        )
    except service.BrokerageRequestError as exc:
        raise _fail(exc) from exc
