"""Read-only endpoint for the latest immutable Wheel quote collection."""

from fastapi import APIRouter, HTTPException, Query

from .. import premium_archive

router = APIRouter()


@router.get("/optionQuotes")
def get_option_quotes(run_id: str | None = Query(default=None, alias="runId")) -> dict:
    try:
        return (premium_archive.snapshot_for_run(run_id)
                if run_id is not None else premium_archive.latest_snapshot())
    except premium_archive.PremiumArchiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/optionQuoteReports")
def get_option_quote_reports() -> dict:
    return {"reports": premium_archive.recent_reports()}
