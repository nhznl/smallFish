"""Read-only endpoint for the latest immutable Wheel quote collection."""

from fastapi import APIRouter, HTTPException

from .. import premium_archive

router = APIRouter()


@router.get("/optionQuotes")
def get_option_quotes() -> dict:
    try:
        return premium_archive.latest_snapshot()
    except premium_archive.PremiumArchiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
