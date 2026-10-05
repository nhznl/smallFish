"""Parse required BLS news releases from the official iCalendar."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from models.market_events import TZID_ALIASES, format_utc
from utilities.market_calendar.providers.base import ParseReport, ScheduleObservation

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_PATTERN = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
    re.IGNORECASE,
)
_BLS_TYPES = (
    (re.compile(r"^Consumer Price Index\b", re.I), "CPI", "Consumer Price Index"),
    (re.compile(r"^Producer Price Index\b", re.I), "PPI", "Producer Price Index"),
    (re.compile(r"^Employment Situation\b", re.I), "EMPLOYMENT_SITUATION", "Employment Situation"),
    (re.compile(r"^Job Openings and Labor Turnover|^JOLTS\b", re.I), "JOLTS", "Job Openings and Labor Turnover"),
    (re.compile(r"^Employment Cost Index\b", re.I), "ECI", "Employment Cost Index"),
)
_PROPERTY = re.compile(r"^([A-Z0-9-]+)((?:;[A-Za-z0-9-]+=(?:\"[^\"]*\"|[^:;]*))*):(.*)$")


class BLSCalendarParseError(ValueError):
    """A CPI row cannot be represented without losing schedule facts."""


def payload_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _unfold(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", normalized)


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n").replace("\\N", "\n")
        .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    )


def _params(raw: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for part in raw.split(";")[1:]:
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        found[name.upper()] = value.strip().strip('"')
    return found


def _events(text: str) -> list[dict[str, tuple[dict[str, str], str]]]:
    blocks: list[dict[str, tuple[dict[str, str], str]]] = []
    current: dict[str, tuple[dict[str, str], str]] | None = None
    for line in _unfold(text).split("\n"):
        upper = line.strip().upper()
        if upper == "BEGIN:VEVENT":
            current = {}
            continue
        if upper == "END:VEVENT":
            if current:
                blocks.append(current)
            current = None
            continue
        if current is None:
            continue
        match = _PROPERTY.match(line.strip())
        if not match:
            continue
        name, raw_params, value = match.groups()
        current[name.upper()] = (_params(";" + raw_params if raw_params else ";"), _unescape(value.strip()))
    return blocks


def _reference(summary: str, description: str) -> tuple[str, str] | None:
    match = _MONTH_PATTERN.search(f"{summary}\n{description}")
    if not match:
        return None
    month = _MONTHS[match.group(1).lower()]
    year = int(match.group(2))
    return f"{year:04d}-{month:02d}", f"{match.group(1).title()} {year}"


def _schedule_bits(props: dict[str, tuple[dict[str, str], str]]) -> tuple[str, str]:
    status = props.get("STATUS", ({}, ""))[1].upper()
    if status == "CANCELLED":
        return "confirmed", "cancelled"
    if status == "TENTATIVE":
        return "tentative", "scheduled"
    return "confirmed", "scheduled"


def parse_ics(text: str, *, source_url: str) -> ParseReport:
    observations: list[ScheduleObservation] = []
    skipped_other = 0
    skipped_reference = 0
    skipped_zone = 0
    for props in _events(text):
        summary = props.get("SUMMARY", ({}, ""))[1]
        description = props.get("DESCRIPTION", ({}, ""))[1]
        recognized = next((item for item in _BLS_TYPES if item[0].match(summary.strip())), None)
        if recognized is None:
            skipped_other += 1
            continue
        _, event_type, canonical_title = recognized
        reference = _reference(summary, description)
        if reference is None:
            raise BLSCalendarParseError("recognized BLS row has no usable reference period")
        reference_period, reference_label = reference
        uid = props.get("UID", ({}, ""))[1].strip()
        start = props.get("DTSTART")
        if not uid:
            raise BLSCalendarParseError("recognized BLS row has no source identifier")
        if start is None:
            raise BLSCalendarParseError("recognized BLS row has no schedule time")
        params, raw_start = start
        schedule_status, lifecycle = _schedule_bits(props)
        if params.get("VALUE", "").upper() == "DATE" or "T" not in raw_start:
            try:
                civil = datetime.strptime(raw_start[:8], "%Y%m%d").date().isoformat()
            except ValueError as exc:
                raise BLSCalendarParseError("CPI row has an invalid calendar date") from exc
            observations.append(ScheduleObservation(
                provider="bls",
                source_record_id=uid,
                source_url=source_url,
                canonical_key=f"US:BLS:{event_type}:{reference_period}",
                title=canonical_title,
                event_type=event_type,
                reference_period=reference_period,
                reference_label=reference_label,
                scheduled_at_utc=None,
                civil_date=civil,
                original_timezone=None,
                time_precision="date_only",
                schedule_status=schedule_status,
                lifecycle_status=lifecycle,
                provenance={"title": "bls:SUMMARY", "referencePeriod": "bls:DESCRIPTION", "scheduledAt": "bls:DTSTART"},
            ))
            continue
        is_utc = raw_start.endswith("Z")
        tzid = params.get("TZID", "")
        zone_name = "UTC" if is_utc else TZID_ALIASES.get(tzid)
        if zone_name is None:
            raise BLSCalendarParseError("CPI row has an unresolved timezone")
        try:
            clock = raw_start[:-1] if is_utc else raw_start
            naive = datetime.strptime(clock, "%Y%m%dT%H%M%S")
        except ValueError as exc:
            raise BLSCalendarParseError("CPI row has an invalid schedule time") from exc
        if zone_name == "UTC":
            aware = naive.replace(tzinfo=ZoneInfo("UTC"))
            original = "UTC"
        else:
            aware = naive.replace(tzinfo=ZoneInfo(zone_name))
            original = zone_name
        utc = format_utc(aware)
        civil = aware.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        observations.append(ScheduleObservation(
            provider="bls",
            source_record_id=uid,
            source_url=source_url,
            canonical_key=f"US:BLS:{event_type}:{reference_period}",
            title=canonical_title,
            event_type=event_type,
            reference_period=reference_period,
            reference_label=reference_label,
            scheduled_at_utc=utc,
            civil_date=civil,
            original_timezone=original,
            time_precision="exact",
            schedule_status=schedule_status,
            lifecycle_status=lifecycle,
            provenance={"title": "bls:SUMMARY", "referencePeriod": "bls:DESCRIPTION", "scheduledAt": "bls:DTSTART"},
        ))
    return ParseReport(tuple(observations), skipped_other, skipped_reference, skipped_zone)
