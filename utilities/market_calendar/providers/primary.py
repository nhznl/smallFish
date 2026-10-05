"""Normalize Milestone 2 primary-provider schedule documents.

Provider adapters deliberately emit the same schedule-observation contract as
the BLS adapter.  The JSON representation is the injected offline fixture
contract and an escape hatch for official API surfaces; iCalendar and simple
official XML calendars are accepted directly.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from models.market_events import EASTERN, EVENT_TYPES, format_utc
from utilities.market_calendar.providers.base import ParseReport, ScheduleObservation
from utilities.market_calendar.providers.bls import _events as _ical_events


class PrimaryCalendarParseError(ValueError):
    """A primary schedule document cannot be represented losslessly."""


TITLE_TYPES = (
    (re.compile(r"consumer price index|\bcpi\b", re.I), "CPI"),
    (re.compile(r"producer price index|\bppi\b", re.I), "PPI"),
    (re.compile(r"employment situation|payroll", re.I), "EMPLOYMENT_SITUATION"),
    (re.compile(r"job openings|\bjolts\b", re.I), "JOLTS"),
    (re.compile(r"employment cost index|\beci\b", re.I), "ECI"),
    (re.compile(r"unemployment insurance weekly claims|jobless claims", re.I), "JOBLESS_CLAIMS"),
    (re.compile(r"fomc.*(decision|statement)|federal funds rate", re.I), "FOMC_DECISION"),
    (re.compile(r"fomc minutes|minutes of the federal open market", re.I), "FOMC_MINUTES"),
    (re.compile(r"beige book", re.I), "BEIGE_BOOK"),
    (re.compile(r"speech|testimony|remarks", re.I), "FED_SPEECH"),
    (re.compile(r"refunding", re.I), "TREASURY_REFUNDING"),
    (re.compile(r"auction", re.I), "TREASURY_AUCTION"),
    (re.compile(r"personal income|personal consumption|\bpce\b", re.I), "PCE"),
    (re.compile(r"gross domestic product|\bgdp\b", re.I), "GDP"),
    (re.compile(r"retail sales", re.I), "RETAIL_SALES"),
    (re.compile(r"durable goods", re.I), "DURABLE_GOODS"),
    (re.compile(r"industrial production", re.I), "INDUSTRIAL_PRODUCTION"),
    (re.compile(r"earnings", re.I), "EARNINGS"),
)


def payload_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _event_type(title: str, explicit: str | None = None) -> str | None:
    if explicit:
        value = explicit.strip().upper()
        if value not in EVENT_TYPES:
            raise PrimaryCalendarParseError(f"unsupported event type {value}")
        return value
    for pattern, value in TITLE_TYPES:
        if pattern.search(title):
            return value
    return None


def _moment(raw: str | None, zone: str | None, civil: str | None) -> tuple[str | None, str, str | None, str]:
    if not raw:
        if not civil:
            raise PrimaryCalendarParseError("schedule row has no date")
        date.fromisoformat(civil)
        return None, civil, None, "date_only"
    text = raw.strip()
    if text.endswith("Z"):
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return format_utc(parsed), parsed.astimezone(EASTERN).date().isoformat(), "UTC", "exact"
    parsed = datetime.fromisoformat(text)
    zone_name = zone or "America/New_York"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(zone_name))
    return format_utc(parsed), parsed.astimezone(EASTERN).date().isoformat(), zone_name, "exact"


def _observation(provider: str, source_url: str, raw: dict[str, object]) -> ScheduleObservation | None:
    title = str(raw.get("title") or raw.get("name") or "").strip()
    event_type = _event_type(title, str(raw.get("eventType") or "") or None)
    if event_type is None:
        return None
    source_id = str(raw.get("id") or raw.get("uid") or raw.get("sourceRecordId") or "").strip()
    if not source_id:
        raise PrimaryCalendarParseError("recognized schedule row has no source id")
    reference = str(raw.get("referencePeriod") or raw.get("period") or raw.get("date") or "").strip()
    reference_label = str(raw.get("referenceLabel") or reference).strip()
    scheduled, civil, original, precision = _moment(
        str(raw.get("scheduledAt") or raw.get("scheduled_at") or "") or None,
        str(raw.get("timezone") or "") or None,
        str(raw.get("civilDate") or raw.get("date") or "") or None,
    )
    subject = str(raw.get("subject") or raw.get("symbol") or "").strip().upper()
    identity = str(raw.get("canonicalKey") or "").strip()
    if not identity:
        parts = ["US", provider.upper(), event_type]
        if subject:
            parts.append(subject)
        parts.append(reference or source_id)
        identity = ":".join(parts)
    status = str(raw.get("scheduleStatus") or "confirmed").strip().lower()
    lifecycle = str(raw.get("lifecycleStatus") or "scheduled").strip().lower()
    return ScheduleObservation(
        provider=provider,
        source_record_id=source_id,
        source_url=source_url,
        canonical_key=identity,
        title=title,
        event_type=event_type,
        reference_period=reference,
        reference_label=reference_label,
        scheduled_at_utc=scheduled,
        civil_date=civil,
        original_timezone=original,
        time_precision=precision,
        schedule_status=status,
        lifecycle_status=lifecycle,
        provenance={
            "title": f"{provider}:title",
            "referencePeriod": f"{provider}:referencePeriod",
            "scheduledAt": f"{provider}:scheduledAt",
        },
    )


def _json_rows(text: str) -> list[dict[str, object]]:
    raw = json.loads(text)
    rows = raw.get("events", raw.get("data", [])) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise PrimaryCalendarParseError("schedule JSON must contain an events list")
    if not all(isinstance(row, dict) for row in rows):
        raise PrimaryCalendarParseError("schedule JSON rows must be objects")
    return rows


def _xml_rows(text: str) -> list[dict[str, object]]:
    root = ET.fromstring(text)
    rows: list[dict[str, object]] = []
    for node in root.iter():
        children = {child.tag.rsplit("}", 1)[-1].lower(): (child.text or "").strip() for child in node}
        title = children.get("title") or children.get("name") or children.get("securityterm")
        day = children.get("date") or children.get("auctiondate") or children.get("releasedate")
        if not title or not day:
            continue
        rows.append({
            "id": children.get("id") or children.get("uid") or children.get("cusip") or f"{title}:{day}",
            "title": title,
            "date": day,
            "scheduledAt": children.get("scheduledat") or children.get("datetime"),
            "referencePeriod": children.get("referenceperiod") or day,
            "eventType": children.get("eventtype"),
        })
    return rows


def _ics_rows(text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for props in _ical_events(text):
        title = props.get("SUMMARY", ({}, ""))[1].strip()
        uid = props.get("UID", ({}, ""))[1].strip()
        start = props.get("DTSTART")
        if not title or not uid or start is None:
            continue
        params, raw = start
        row: dict[str, object] = {"id": uid, "title": title}
        compact = raw[:-1] if raw.endswith("Z") else raw
        if "T" not in compact:
            row["date"] = datetime.strptime(compact[:8], "%Y%m%d").date().isoformat()
        else:
            parsed = datetime.strptime(compact, "%Y%m%dT%H%M%S")
            if raw.endswith("Z"):
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.replace(tzinfo=ZoneInfo(params.get("TZID") or "America/New_York"))
            row["scheduledAt"] = parsed.isoformat().replace("+00:00", "Z")
        description = props.get("DESCRIPTION", ({}, ""))[1]
        match = re.search(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2}\b", f"{title} {description}", re.I)
        row["referencePeriod"] = match.group(0) if match else row.get("date", str(row.get("scheduledAt", ""))[:10])
        rows.append(row)
    return rows


def parse_document(text: str, *, provider: str, source_url: str) -> ParseReport:
    stripped = text.lstrip()
    if "BEGIN:VCALENDAR" in stripped[:200].upper():
        rows = _ics_rows(text)
    elif stripped.startswith("{") or stripped.startswith("["):
        rows = _json_rows(text)
    elif stripped.startswith("<"):
        rows = _xml_rows(text)
    else:
        raise PrimaryCalendarParseError("unsupported schedule document")
    observations = tuple(
        item for item in (_observation(provider, source_url, row) for row in rows) if item is not None
    )
    return ParseReport(observations, skipped_other_releases=len(rows) - len(observations))


def parse_legacy_earnings_csv(text: str, *, source_url: str) -> ParseReport:
    observations: list[ScheduleObservation] = []
    for row in csv.DictReader(io.StringIO(text)):
        if str(row.get("event_type") or "").strip().lower() != "earnings":
            continue
        symbol = str(row.get("ticker") or "").strip().upper()
        day = str(row.get("event_date") or "").strip()
        if not symbol or not day:
            continue
        date.fromisoformat(day)
        observations.append(_observation("finnhub", source_url, {
            "id": f"{symbol}:{day}",
            "title": f"{symbol} earnings",
            "eventType": "EARNINGS",
            "symbol": symbol,
            "date": day,
            "referencePeriod": day,
            "canonicalKey": f"US:EARNINGS:{symbol}:{day}",
        }))
    return ParseReport(tuple(item for item in observations if item is not None))
