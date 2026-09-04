"""Study 4 execution HTTP surface. Money-moving endpoints accept only stored plans."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..execution.risk import RiskBlocked
from ..execution.service import ExecutionService
from ..execution.settings import load_settings

router = APIRouter(prefix="/api/execution/study4", tags=["study4-execution"])


def _service() -> ExecutionService:
    return ExecutionService()


def _guarded(fn):
    try:
        return fn()
    except RiskBlocked as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ConfirmBody(BaseModel):
    token: str
    productionAck: str | None = None


class ExecuteBody(BaseModel):
    planId: int | None = None


class KillSwitchBody(BaseModel):
    active: bool = True


class ResetBody(BaseModel):
    confirmation: str


class CapitalResetBody(BaseModel):
    targetBucket: float = Field(gt=0)
    activeBucket: float | None = Field(default=None, gt=0)
    productionCap: float | None = Field(default=None, gt=0)


class ScanBody(BaseModel):
    session: str | None = None


@router.get("/status")
def status() -> dict:
    settings = load_settings()
    if not settings.configured:
        return ExecutionService(settings=settings).status()
    return _service().status()


@router.get("/cycles/{week}")
def cycle(week: str) -> dict:
    return _guarded(lambda: _service().cycle_detail(week))


@router.post("/cycles/current/scan")
def scan(body: ScanBody | None = None) -> dict:
    session = None if body is None else body.session
    return _guarded(lambda: _service().scan(session))


@router.post("/cycles/current/finalize")
def finalize(body: ScanBody | None = None) -> dict:
    session = None if body is None else body.session
    return _guarded(lambda: _service().finalize(session))


@router.post("/cycles/current/preflight")
def preflight() -> dict:
    return _guarded(lambda: _service().preflight())


@router.post("/cycles/current/confirm")
def confirm(body: ConfirmBody) -> dict:
    return _guarded(lambda: _service().confirm(body.token, body.productionAck))


@router.post("/cycles/current/execute")
def execute(body: ExecuteBody | None = None) -> dict:
    plan_id = None if body is None else body.planId
    return _guarded(lambda: _service().execute(plan_id))


@router.post("/cycles/current/reconcile")
def reconcile() -> dict:
    return _guarded(lambda: _service().reconcile())


@router.post("/cycles/current/final-sync")
def final_sync() -> dict:
    return _guarded(lambda: _service().final_sync())


@router.post("/kill-switch")
def kill_switch(body: KillSwitchBody) -> dict:
    return _guarded(lambda: _service().kill_switch(body.active))


@router.post("/reset")
def reset(body: ResetBody) -> dict:
    settings = load_settings()
    return _guarded(lambda: ExecutionService(settings=settings).reset_ledger(body.confirmation))


@router.post("/capital-resets")
def capital_resets(body: CapitalResetBody) -> dict:
    return _guarded(lambda: _service().capital_reset(
        target_bucket=body.targetBucket,
        active_bucket=body.activeBucket,
        production_cap=body.productionCap,
    ))


@router.get("/performance")
def performance() -> dict:
    return _guarded(lambda: _service().performance_series())


@router.get("/audit")
def audit(limit: int = 50, offset: int = 0) -> dict:
    return _guarded(lambda: _service().audit(limit=limit, offset=offset))
