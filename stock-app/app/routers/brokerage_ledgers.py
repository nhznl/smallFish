"""Additive broker-neutral brokerage-ledger endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import brokerage_holdings, brokerage_ledger

router = APIRouter()


@router.get("/brokerage-ledgers/{portfolio}/combined")
def get_combined_brokerage_ledger(portfolio: str) -> dict:
    try:
        return brokerage_ledger.snapshot(portfolio)
    except brokerage_ledger.BrokerageLedgerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/brokerage-ledgers/{portfolio}/holdings")
def get_holdings(portfolio: str) -> dict:
    try:
        return brokerage_holdings.portfolio(portfolio)
    except brokerage_holdings.HoldingsValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.put("/brokerage-ledgers/{portfolio}/holdings/{symbol}/enrichment")
def put_holdings_enrichment(portfolio: str, symbol: str, request: dict) -> dict:
    try:
        return brokerage_holdings.update_enrichment(portfolio, symbol, request or {})
    except brokerage_holdings.HoldingsValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/brokerage-ledgers/{portfolio}/holdings/gain-loss-snapshots")
def post_holdings_gain_loss_snapshot(portfolio: str) -> dict:
    try:
        snapshot = brokerage_holdings.capture_gain_loss_snapshot(portfolio)
        return {"snapshot": snapshot, "portfolio": brokerage_holdings.portfolio(portfolio)}
    except brokerage_holdings.HoldingsValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
