"""Brokerage-agnostic read endpoints.

The path identifies a configured brokerage, not an SDK or an aggregation
connector, and the same resource returns the same shape for every brokerage.
These routes are additive: the legacy `/options`, `/retirement`, and
`/brokerage-ledgers` contracts stay untouched until their consumers migrate.

This module resolves nothing and computes nothing. A router that started
branching on `brokerage_id` would defeat the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..brokerages import service

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
