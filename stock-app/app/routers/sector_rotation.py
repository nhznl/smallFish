"""Read-only endpoint for the latest sector-rotation leadership snapshot."""

from fastapi import APIRouter, HTTPException

from .. import sector_rotation_read

router = APIRouter()


@router.get("/sectorRotation")
def get_sector_rotation() -> dict:
    try:
        return sector_rotation_read.latest_snapshot()
    except sector_rotation_read.SectorRotationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
