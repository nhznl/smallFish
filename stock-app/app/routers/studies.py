"""Read-only materialized Research Studies endpoints."""

import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .. import studies_read
from . import run_jobs


router = APIRouter(prefix="/api/studies", tags=["research-studies"])
_scan_lock = threading.Lock()


@router.get("")
def list_studies() -> dict:
    """List materialized studies without importing study or utility runtime code."""
    try:
        return studies_read.list_studies()
    except studies_read.StudyArtifactError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{study_id}")
def get_study(study_id: str) -> dict:
    try:
        study = studies_read.get_study(study_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except studies_read.StudyArtifactError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if study is None:
        raise HTTPException(status_code=404, detail=f"Unknown study: {study_id}")
    return study


def _scan_capability(study_id: str) -> dict:
    try:
        study = studies_read.get_study(study_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except studies_read.StudyArtifactError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if study is None:
        raise HTTPException(status_code=404, detail=f"Unknown study: {study_id}")
    variation = next(item for item in study["variations"] if item["id"] == study["defaultVariationId"])
    if not variation["scan"] or not variation["scan"]["executionSupported"]:
        raise HTTPException(status_code=409, detail="This study has no operational scan.")
    return study


@router.get("/{study_id}/scan")
def get_study_scan(study_id: str) -> dict:
    _scan_capability(study_id)
    try:
        return studies_read.get_scan_snapshot(study_id)
    except studies_read.StudyArtifactError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{study_id}/scan")
def run_study_scan(study_id: str) -> JSONResponse:
    _scan_capability(study_id)
    if study_id != "pre-earnings-momentum":
        raise HTTPException(status_code=409, detail="This study has no supported scan dispatcher.")
    if not _scan_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A pre-earnings study scan is already running.")
    try:
        result = run_jobs._run_command("scan")
        if result.get("status") == "ok":
            studies_read.materialize_scan_snapshot(study_id)
        return JSONResponse(content=result)
    finally:
        _scan_lock.release()
