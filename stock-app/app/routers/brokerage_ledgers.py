"""Retained broker-neutral combined-ledger read.

Holdings moved to `/api/brokerages/{brokerage_id}/holdings` once its consumer
did. What remains is the combined symbol view, kept as an internal compatibility
read and as the baseline the projection parity tests compare against; removing
it is a separate compatibility decision.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import brokerage_ledger

router = APIRouter()


@router.get("/brokerage-ledgers/{portfolio}/combined")
def get_combined_brokerage_ledger(portfolio: str) -> dict:
    try:
        return brokerage_ledger.snapshot(portfolio)
    except brokerage_ledger.BrokerageLedgerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
