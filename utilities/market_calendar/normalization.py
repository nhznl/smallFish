"""Attach optional CPI measurements without splitting one release into two events."""

from __future__ import annotations

import json

from models.market_events import EventMeasurement, MarketEventContractError


class MeasurementDocumentError(ValueError):
    """A released-values document is not the calendar's text-decimal contract."""


def parse_released_values(text: str) -> dict[str, tuple[EventMeasurement, ...]]:
    """Parse a local measurement document. This is not a BLS API response."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MeasurementDocumentError("released values must be JSON") from exc
    grouped: dict[str, list[EventMeasurement]] = {}
    for item in raw.get("observations") or []:
        key = str(item.get("canonicalKey") or "").strip()
        if not key:
            raise MeasurementDocumentError("each observation needs a canonicalKey")
        measurements: list[EventMeasurement] = []
        for body in item.get("measurements") or []:
            if not isinstance(body, dict):
                raise MeasurementDocumentError("measurements must be objects")
            try:
                measurements.append(EventMeasurement.from_wire(body))
            except MarketEventContractError as exc:
                raise MeasurementDocumentError(str(exc)) from exc
        grouped[key] = measurements
    return {key: tuple(value) for key, value in grouped.items()}
