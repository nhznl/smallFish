"""Options activity, grouping, and portfolio-risk endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from .. import options_activity, options_portfolio

router = APIRouter()


def _raise_validation(exc: options_portfolio.PortfolioValidationError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _raise_activity_validation(exc: options_activity.ActivityValidationError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _optional_date(value: object, field: str) -> str:
    if value in (None, ""):
        return ""
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValueError(f"{field} must be YYYY-MM-DD") from None


@router.get("/options")
def get_options(account: str | None = Query(default=None)) -> dict:
    try:
        return options_portfolio.snapshot(account)
    except options_portfolio.PortfolioValidationError as exc:
        _raise_validation(exc)


@router.get("/options/activity")
def get_options_activity(account: str | None = Query(default=None)) -> dict:
    try:
        return options_activity.snapshot(account)
    except options_activity.ActivityValidationError as exc:
        _raise_activity_validation(exc)


@router.post("/options/activity/sync")
def post_options_activity_sync(request: dict | None = None) -> dict:
    request = request or {}
    try:
        start = _optional_date(request.get("start_date"), "start_date")
        end = _optional_date(request.get("end_date"), "end_date")
        return options_activity.sync(
            date.fromisoformat(start) if start else None,
            date.fromisoformat(end) if end else None,
        )
    except (options_activity.ActivityValidationError, ValueError) as exc:
        status_code = getattr(exc, "status_code", 422)
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Tastytrade sync failed") from exc


@router.post("/options/groups")
def post_options_group(request: dict) -> dict:
    try:
        return options_activity.create_group(request)
    except options_activity.ActivityValidationError as exc:
        _raise_activity_validation(exc)


@router.put("/options/groups/{group_id}")
def put_options_group(group_id: str, request: dict) -> dict:
    try:
        return options_activity.update_group(group_id, request)
    except options_activity.ActivityValidationError as exc:
        _raise_activity_validation(exc)


@router.put("/options/activity/{event_id}/group")
def put_options_event_group(event_id: str, request: dict) -> dict:
    try:
        return options_activity.assign_event(event_id, request.get("group_id"))
    except options_activity.ActivityValidationError as exc:
        _raise_activity_validation(exc)


@router.post("/options/activity/manual")
def post_options_manual_event(request: dict) -> dict:
    try:
        return options_activity.create_manual_event(request)
    except options_activity.ActivityValidationError as exc:
        _raise_activity_validation(exc)


@router.put("/options/activity/manual/{event_id:path}")
def put_options_manual_event(event_id: str, request: dict) -> dict:
    try:
        return options_activity.update_manual_event(event_id, request)
    except options_activity.ActivityValidationError as exc:
        _raise_activity_validation(exc)


@router.delete("/options/activity/manual/{event_id:path}")
def delete_options_manual_event(event_id: str) -> dict:
    try:
        return options_activity.delete_manual_event(event_id)
    except options_activity.ActivityValidationError as exc:
        _raise_activity_validation(exc)
