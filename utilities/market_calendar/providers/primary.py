"""Normalize Milestone 2 primary-provider schedule documents.

Provider adapters deliberately emit the same schedule-observation contract as
the BLS adapter.  The JSON representation is the injected offline fixture
contract and an escape hatch for official API surfaces; iCalendar and simple
official XML calendars are accepted directly.
"""

from __future__ import annotations

import calendar
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin
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
    (re.compile(r"fomc press conference", re.I), "FOMC_PRESS_CONFERENCE"),
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
    (re.compile(r"weekly petroleum status", re.I), "EIA_PETROLEUM_STATUS"),
    (re.compile(r"weekly natural gas storage", re.I), "EIA_NATURAL_GAS_STORAGE"),
    (re.compile(r"world agricultural supply and demand|\bwasde\b", re.I), "USDA_WASDE"),
    (re.compile(r"crop production", re.I), "USDA_CROP_PRODUCTION"),
    (re.compile(r"grain stocks", re.I), "USDA_GRAIN_STOCKS"),
    (re.compile(r"prospective plantings", re.I), "USDA_PROSPECTIVE_PLANTINGS"),
    (re.compile(r"\bacreage\b", re.I), "USDA_ACREAGE"),
    (re.compile(r"weekly export sales|export sales", re.I), "FAS_EXPORT_SALES"),
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


def _clock_text(value: str) -> str:
    return value.strip().replace(".", "").upper()


def _plain_text(value: object) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(str(value or ""))).split())


def _fed_document(text: str, source_url: str) -> tuple[ParseReport, tuple[str, str]]:
    raw = json.loads(text.lstrip("\ufeff"))
    entries = raw.get("events") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise PrimaryCalendarParseError("Federal Reserve calendar has no events list")
    rows = []
    months = []
    meeting_dates = {
        f"{str(item.get('month'))}-{int(raw_day):02d}"
        for item in entries
        if isinstance(item, dict)
        and _plain_text(item.get("title")) == "FOMC Meeting"
        for raw_day in str(item.get("days") or "").split(",")
        if raw_day.strip().isdigit()
    }

    for item in entries:
        if not isinstance(item, dict):
            continue
        month = str(item.get("month") or "")
        if not re.fullmatch(r"20\d{2}-\d{2}", month):
            continue
        months.append(month)
        title = _plain_text(item.get("title"))
        description = _plain_text(item.get("description"))
        location = _plain_text(item.get("location"))
        kind = str(item.get("type") or "").strip().lower()
        if title == "FOMC Meeting":
            event_type = "FOMC_DECISION"
        elif title == "FOMC Press Conference":
            event_type = "FOMC_PRESS_CONFERENCE"
        elif title == "FOMC Minutes":
            event_type = "FOMC_MINUTES"
        elif title == "Beige Book":
            event_type = "BEIGE_BOOK"
        elif kind == "stat" and title.startswith("G.17 - Industrial Production"):
            event_type = "INDUSTRIAL_PRODUCTION"
        elif kind in {"speeches", "testimony"} or re.search(r"speech|testimony|remarks", title, re.I):
            event_type = "FED_SPEECH"
        else:
            continue
        for raw_day in str(item.get("days") or "").split(","):
            if not raw_day.strip().isdigit():
                continue
            civil = f"{month}-{int(raw_day):02d}"
            identity_seed = (
                f"{event_type}|{item.get('link') or ''}|{title}|"
                f"{description}|{location}|{month}"
            )
            source_id = hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()[:24]
            reference = description or civil
            if event_type in {
                "FOMC_DECISION", "FOMC_PRESS_CONFERENCE", "BEIGE_BOOK",
                "INDUSTRIAL_PRODUCTION",
            }:
                reference = civil
            clock = _clock_text(str(item.get("time") or ""))
            scheduled = None
            if clock:
                parsed_clock = datetime.strptime(clock, "%I:%M %p").time()
                scheduled = datetime.combine(
                    date.fromisoformat(civil), parsed_clock, ZoneInfo("America/New_York")
                ).isoformat()
            if event_type == "FOMC_DECISION":
                source_id = f"FOMC_DECISION:{civil}"
            elif event_type == "FOMC_PRESS_CONFERENCE":
                if civil not in meeting_dates:
                    raise PrimaryCalendarParseError(
                        "Federal Reserve press conference has no matching FOMC meeting"
                    )
                source_id = f"FOMC_PRESS_CONFERENCE:{civil}"
            elif event_type == "INDUSTRIAL_PRODUCTION":
                source_id = f"INDUSTRIAL_PRODUCTION:{month}"
            canonical_subject = source_id
            if event_type in {"FOMC_DECISION", "FOMC_PRESS_CONFERENCE"}:
                canonical_subject = civil
            elif event_type == "INDUSTRIAL_PRODUCTION":
                canonical_subject = month
            rows.append({
                "id": source_id,
                "title": title,
                "eventType": event_type,
                "referencePeriod": reference,
                "civilDate": civil,
                "scheduledAt": scheduled,
                "timezone": "America/New_York",
                "canonicalKey": f"US:FED:{event_type}:{canonical_subject}",
            })
    if not months:
        raise PrimaryCalendarParseError("Federal Reserve calendar has no dated coverage")
    first = date.fromisoformat(min(months) + "-01")
    last_month = date.fromisoformat(max(months) + "-01")
    last = last_month.replace(day=calendar.monthrange(last_month.year, last_month.month)[1])
    report = ParseReport(tuple(
        item for item in (_observation("federal_reserve", source_url, row) for row in rows)
        if item is not None
    ))
    return report, (first.isoformat(), last.isoformat())


def treasury_landing_details(text: str, source_url: str) -> tuple[str, date]:
    match = re.search(
        r'href=["\']?([^"\' >]*TentativeAuctionSchedule[^"\' >]*\.xml)',
        text,
        re.I,
    )
    if match is None:
        raise PrimaryCalendarParseError("Treasury landing page has no tentative auction XML link")
    tail = text[match.end():match.end() + 3000]
    dates = re.findall(
        r"next release is scheduled for\s+(?:<[^>]+>|&nbsp;|\s)*([A-Za-z]+\s+\d{1,2}(?:<[^>]+>|,|&nbsp;|\s)+20\d{2})",
        tail,
        re.I,
    )
    if not dates:
        raise PrimaryCalendarParseError("Treasury landing page has no next refunding date")
    cleaned = re.sub(r"<[^>]+>|&nbsp;", " ", dates[0])
    cleaned = " ".join(html.unescape(cleaned).replace(",", " ").split())
    refunding_date = datetime.strptime(cleaned, "%B %d %Y").date()
    return urljoin(source_url, html.unescape(match.group(1))), refunding_date


def parse_treasury_document(
    text: str,
    source_url: str,
    *,
    refunding_date: date | None = None,
) -> tuple[ParseReport, tuple[str, str]]:
    root = ET.fromstring(text)
    start = (root.findtext("StartDate") or "").strip()
    end = (root.findtext("EndDate") or "").strip()
    date.fromisoformat(start)
    date.fromisoformat(end)
    rows = []
    if refunding_date is not None:
        quarter = (refunding_date.month - 1) // 3 + 1
        reference = f"{refunding_date.year}-Q{quarter}"
        rows.append({
            "id": f"quarterly-refunding:{reference}",
            "title": "Treasury Quarterly Refunding",
            "eventType": "TREASURY_REFUNDING",
            "referencePeriod": reference,
            "scheduledAt": f"{refunding_date.isoformat()}T08:30:00",
            "timezone": "America/New_York",
            "canonicalKey": f"US:TREASURY:REFUNDING:{reference}",
        })
    for node in root.findall("AuctionCalendarDate"):
        security_type = (node.findtext("SecurityType") or "").strip().upper()
        if security_type not in {"NOTE", "BOND", "TIPS", "FRN"}:
            continue
        term = (node.findtext("SecurityTermWeekYear") or "").strip()
        auction_date = (node.findtext("AuctionDate") or "").strip()
        if not term or not auction_date:
            continue
        date.fromisoformat(auction_date)
        period = auction_date[:7]
        if (node.findtext("TIPS") or "").strip().upper() == "Y":
            instrument = "TIPS"
        elif (node.findtext("FloatingRate") or "").strip().upper() == "Y":
            instrument = "FRN"
        else:
            instrument = security_type
        identity = f"{instrument}:{term}:{period}"
        rows.append({
            "id": identity,
            "title": f"{term} Treasury Auction",
            "eventType": "TREASURY_AUCTION",
            "referencePeriod": period,
            "civilDate": auction_date,
            "scheduledAt": f"{auction_date}T13:00:00",
            "timezone": "America/New_York",
            "canonicalKey": f"US:TREASURY:AUCTION:{identity}",
        })
    report = ParseReport(tuple(
        item for item in (_observation("treasury", source_url, row) for row in rows)
        if item is not None
    ))
    return report, (start, end)


def _bea_document(text: str, source_url: str) -> tuple[ParseReport, tuple[str, str]]:
    raw_rows = _ics_rows(text)
    dated = [
        str(row.get("scheduledAt") or row.get("date") or "")[:10]
        for row in raw_rows
        if row.get("scheduledAt") or row.get("date")
    ]
    if not dated:
        raise PrimaryCalendarParseError("BEA calendar has no dated coverage")
    rows = []
    for row in raw_rows:
        title = str(row.get("title") or "")
        if re.search(r"by state|by county|puerto rico", title, re.I):
            continue
        if re.search(r"gross domestic product|\bgdp\b", title, re.I):
            event_type = "GDP"
        elif re.search(r"personal income and outlays", title, re.I):
            event_type = "PCE"
        else:
            continue
        quarter = re.search(r"([1-4])(?:st|nd|rd|th) Quarter(?: and Year)?\s+(20\d{2})", title, re.I)
        month = re.search(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
            title,
            re.I,
        )
        if quarter:
            reference = f"{quarter.group(2)}-Q{quarter.group(1)}"
        elif month:
            reference = datetime.strptime(month.group(0), "%B %Y").strftime("%Y-%m")
        else:
            continue
        row = dict(row)
        row.update({
            "eventType": event_type,
            "referencePeriod": reference,
            "canonicalKey": f"US:BEA:{event_type}:{reference}",
        })
        rows.append(row)
    report = ParseReport(tuple(
        item for item in (_observation("bea", source_url, row) for row in rows)
        if item is not None
    ))
    return report, (min(dated), max(dated))


class _TableRows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag.lower() == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _census_document(text: str, source_url: str) -> tuple[ParseReport, tuple[str, str]]:
    parser = _TableRows()
    parser.feed(text)
    rows = []
    dated = []
    for cells in parser.rows:
        if len(cells) < 6:
            continue
        title, release, clock, reference, source_id = cells[:5]
        try:
            release_date = datetime.strptime(release, "%B %d, %Y").date()
            release_time = datetime.strptime(clock, "%I:%M %p").time()
        except ValueError:
            continue
        dated.append(release_date.isoformat())
        if title == "Advance Monthly Sales for Retail and Food Services":
            event_type = "RETAIL_SALES"
        elif title.startswith("Advance Report on Durable Goods"):
            event_type = "DURABLE_GOODS"
        else:
            continue
        rows.append({
            "id": f"{event_type}:{cells[5]}",
            "title": title,
            "eventType": event_type,
            "referencePeriod": reference,
            "scheduledAt": datetime.combine(
                release_date, release_time, ZoneInfo("America/New_York")
            ).isoformat(),
            "timezone": "America/New_York",
            "canonicalKey": f"US:CENSUS:{event_type}:{cells[5]}",
        })
    if not dated:
        raise PrimaryCalendarParseError("Census calendar has no dated coverage")
    report = ParseReport(tuple(
        item for item in (_observation("census", source_url, row) for row in rows)
        if item is not None
    ))
    return report, (min(dated), max(dated))


def _dol_document(
    text: str, source_url: str, start: date, end: date,
) -> tuple[ParseReport, tuple[str, str]]:
    marker = "published each week on Thursday morning at 8:30am EST"
    if marker.lower() not in text.lower():
        raise PrimaryCalendarParseError("DOL weekly publication rule is missing")
    exception_dates = {
        datetime.strptime(value, "%A, %B %d, %Y").date()
        for value in re.findall(
            r"(?:Monday|Tuesday|Wednesday|Friday),\s+[A-Z][a-z]+\s+\d{1,2},\s+20\d{2}",
            text,
        )
    }
    release_dates = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() == 3:
            release_dates.append(cursor)
        cursor += timedelta(days=1)
    for exception in exception_dates:
        normal = exception + timedelta(days=(3 - exception.weekday()) % 7)
        if normal in release_dates:
            release_dates.remove(normal)
        if start <= exception <= end:
            release_dates.append(exception)
    rows = []
    for release in sorted(release_dates):
        week_ending = release - timedelta(days=(release.weekday() - 5) % 7)
        rows.append({
            "id": f"weekly-claims:{week_ending.isoformat()}",
            "title": "Unemployment Insurance Weekly Claims",
            "eventType": "JOBLESS_CLAIMS",
            "referencePeriod": week_ending.isoformat(),
            "scheduledAt": datetime.combine(
                release, datetime.strptime("08:30 AM", "%I:%M %p").time(),
                ZoneInfo("America/New_York"),
            ).isoformat(),
            "timezone": "America/New_York",
            "canonicalKey": f"US:DOL:JOBLESS_CLAIMS:{week_ending.isoformat()}",
        })
    report = ParseReport(tuple(
        item for item in (_observation("dol_claims", source_url, row) for row in rows)
        if item is not None
    ))
    return report, (start.isoformat(), end.isoformat())


def _eia_weekly_document(
    text: str, source_url: str, start: date, end: date, *, natural_gas: bool,
) -> tuple[ParseReport, tuple[str, str]]:
    weekday = 3 if natural_gas else 2
    day_name = "Thursdays" if natural_gas else "Wednesday"
    if "10:30 a.m." not in text or day_name.lower() not in text.lower():
        raise PrimaryCalendarParseError("EIA weekly publication rule is missing")
    parser = _TableRows()
    parser.feed(text)
    exceptions: dict[date, tuple[date, str]] = {}
    exception_years: set[int] = set()
    for cells in parser.rows:
        if natural_gas:
            if len(cells) < 3:
                continue
            release_text, clock = cells[0], cells[2]
            try:
                release = datetime.strptime(release_text, "%B %d, %Y").date()
            except ValueError:
                continue
            nominal = release - timedelta(days=(release.weekday() - weekday) % 7)
            reference = nominal - timedelta(days=6)
        else:
            if len(cells) < 4:
                continue
            reference_text, release_text, clock = cells[0], cells[1], cells[3]
            try:
                reference = datetime.strptime(reference_text, "%B %d, %Y").date()
                release = datetime.strptime(release_text, "%B %d, %Y").date()
            except ValueError:
                continue
            nominal = reference + timedelta(days=(weekday - reference.weekday()) % 7)
        exception_years.add(release.year)
        exceptions[nominal] = (release, clock)

    requested_years = set(range(start.year, end.year + 1))
    if not requested_years.issubset(exception_years):
        raise PrimaryCalendarParseError("EIA holiday table does not cover the requested year")

    release_rows = []
    cursor = start - timedelta(days=7)
    while cursor <= end + timedelta(days=7):
        if cursor.weekday() == weekday:
            release, clock = exceptions.get(cursor, (cursor, "10:30 a.m."))
            if start <= release <= end:
                reference = cursor - timedelta(days=6 if natural_gas else 5)
                event_type = (
                    "EIA_NATURAL_GAS_STORAGE" if natural_gas else "EIA_PETROLEUM_STATUS"
                )
                title = (
                    "Weekly Natural Gas Storage Report"
                    if natural_gas else "Weekly Petroleum Status Report"
                )
                try:
                    release_time = datetime.strptime(
                        clock.lower().replace(".", ""), "%I:%M %p"
                    ).time()
                except ValueError as exc:
                    raise PrimaryCalendarParseError("invalid EIA release time") from exc
                release_rows.append({
                    "id": f"{event_type}:{reference.isoformat()}",
                    "title": title,
                    "eventType": event_type,
                    "referencePeriod": reference.isoformat(),
                    "scheduledAt": datetime.combine(
                        release, release_time, ZoneInfo("America/New_York")
                    ).isoformat(),
                    "timezone": "America/New_York",
                    "canonicalKey": f"US:EIA:{event_type}:{reference.isoformat()}",
                })
        cursor += timedelta(days=1)
    provider = "eia_natural_gas" if natural_gas else "eia_petroleum"
    report = ParseReport(tuple(
        item for item in (_observation(provider, source_url, row) for row in release_rows)
        if item is not None
    ))
    return report, (start.isoformat(), end.isoformat())


def _nass_document(text: str, source_url: str) -> tuple[ParseReport, tuple[str, str]]:
    rows = _ics_rows(text)
    dated = [
        str(row.get("scheduledAt") or row.get("date") or "")[:10]
        for row in rows if row.get("scheduledAt") or row.get("date")
    ]
    if not dated:
        raise PrimaryCalendarParseError("NASS calendar has no dated coverage")
    selected = []
    for raw in rows:
        title = str(raw.get("title") or "")
        event_type = _event_type(title)
        if event_type not in {
            "USDA_CROP_PRODUCTION", "USDA_GRAIN_STOCKS",
            "USDA_PROSPECTIVE_PLANTINGS", "USDA_ACREAGE",
        }:
            continue
        row = dict(raw)
        civil = str(row.get("scheduledAt") or row.get("date") or "")[:10]
        source_id = str(row.get("id") or "").strip()
        row.update({
            "eventType": event_type,
            "referencePeriod": civil[:7],
            "canonicalKey": f"US:USDA_NASS:{event_type}:{source_id}",
        })
        selected.append(row)
    report = ParseReport(tuple(
        item for item in (_observation("usda_nass", source_url, row) for row in selected)
        if item is not None
    ), skipped_other_releases=len(rows) - len(selected))
    return report, (min(dated), max(dated))


def _agency_table_document(
    text: str, source_url: str, provider: str, event_type: str,
) -> tuple[ParseReport, tuple[str, str]]:
    """Normalize official WASDE/FAS HTML tables with explicit dated rows."""
    parser = _TableRows()
    parser.feed(text)
    rows = []
    dated = []
    wanted = "wasde" if event_type == "USDA_WASDE" else "export sales"
    for cells in parser.rows:
        joined = " | ".join(cells)
        if wanted not in joined.lower():
            continue
        match = re.search(
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday)?[,]?\s*"
            r"(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+(\d{1,2}),?\s+(20\d{2})",
            joined,
            re.I,
        )
        if not match:
            match = re.search(r"\b(\d{1,2}/\d{1,2}/20\d{2})\b", joined)
        if not match:
            continue
        raw_date = match.group(0).strip(" ,")
        parsed_date = None
        for pattern in ("%A, %B %d, %Y", "%A %B %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
            try:
                parsed_date = datetime.strptime(raw_date, pattern).date()
                break
            except ValueError:
                continue
        if parsed_date is None:
            continue
        clock_match = re.search(r"\b(\d{1,2}:\d{2}\s*[ap]\.?m\.?)\b", joined, re.I)
        clock = clock_match.group(1).lower().replace(".", "") if clock_match else "08:30 am"
        release_time = datetime.strptime(clock, "%I:%M %p").time()
        civil = parsed_date.isoformat()
        dated.append(civil)
        rows.append({
            "id": f"{event_type}:{civil}",
            "title": "World Agricultural Supply and Demand Estimates" if event_type == "USDA_WASDE" else "Weekly Export Sales",
            "eventType": event_type,
            "referencePeriod": civil[:7] if event_type == "USDA_WASDE" else civil,
            "scheduledAt": datetime.combine(
                parsed_date, release_time, ZoneInfo("America/New_York")
            ).isoformat(),
            "timezone": "America/New_York",
            "canonicalKey": f"US:{provider.upper()}:{event_type}:{civil}",
        })
    if not dated:
        raise PrimaryCalendarParseError(f"{provider} calendar has no selected dated rows")
    report = ParseReport(tuple(
        item for item in (_observation(provider, source_url, row) for row in rows)
        if item is not None
    ))
    return report, (min(dated), max(dated))


def _wasde_document(text: str, source_url: str) -> tuple[ParseReport, tuple[str, str]]:
    if "BEGIN:VCALENDAR" not in text[:300].upper():
        return _agency_table_document(text, source_url, "usda_wasde", "USDA_WASDE")
    raw_rows = _ics_rows(text)
    dated = [
        str(row.get("scheduledAt") or row.get("date") or "")[:10]
        for row in raw_rows if row.get("scheduledAt") or row.get("date")
    ]
    if not dated:
        raise PrimaryCalendarParseError("USDA calendar has no dated coverage")
    rows = []
    for raw in raw_rows:
        if str(raw.get("title") or "").strip().lower() != "crop production":
            continue
        row = dict(raw)
        civil = str(row.get("scheduledAt") or row.get("date") or "")[:10]
        source_id = str(row.get("id") or "").strip()
        row.update({
            "id": f"wasde:{source_id}",
            "title": "World Agricultural Supply and Demand Estimates",
            "eventType": "USDA_WASDE",
            "referencePeriod": civil[:7],
            "canonicalKey": f"US:USDA:USDA_WASDE:{source_id}",
        })
        rows.append(row)
    report = ParseReport(tuple(
        item for item in (_observation("usda_wasde", source_url, row) for row in rows)
        if item is not None
    ), skipped_other_releases=len(raw_rows) - len(rows))
    return report, (min(dated), max(dated))


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _federal_holidays(year: int) -> set[date]:
    return {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 10, 0, 2),
        _observed(date(year, 11, 11)),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }


def _fas_document(
    text: str, source_url: str, start: date, end: date,
) -> tuple[ParseReport, tuple[str, str]]:
    lower = " ".join(_plain_text(text).lower().split())
    if "weekly summary" not in lower or "8:30" not in lower or "thursday" not in lower:
        return _agency_table_document(text, source_url, "fas_export_sales", "FAS_EXPORT_SALES")
    holidays = set().union(*(
        _federal_holidays(year) for year in range(start.year - 1, end.year + 1)
    ))
    rows = []
    cursor = start - timedelta(days=7)
    while cursor <= end:
        if cursor.weekday() == 3:
            preceding_friday = cursor - timedelta(days=6)
            preceding_monday = cursor - timedelta(days=3)
            release = cursor + timedelta(days=1) if (
                preceding_friday in holidays or preceding_monday in holidays
            ) else cursor
            if start <= release <= end:
                reference = preceding_friday.isoformat()
                rows.append({
                    "id": f"weekly-export-sales:{reference}",
                    "title": "Weekly Export Sales",
                    "eventType": "FAS_EXPORT_SALES",
                    "referencePeriod": reference,
                    "scheduledAt": datetime.combine(
                        release, datetime.strptime("08:30 AM", "%I:%M %p").time(),
                        ZoneInfo("America/New_York"),
                    ).isoformat(),
                    "timezone": "America/New_York",
                    "canonicalKey": f"US:FAS:FAS_EXPORT_SALES:{reference}",
                })
        cursor += timedelta(days=1)
    report = ParseReport(tuple(
        item for item in (_observation("fas_export_sales", source_url, row) for row in rows)
        if item is not None
    ))
    return report, (start.isoformat(), end.isoformat())


def parse_official_document(
    text: str,
    *,
    provider: str,
    source_url: str,
    horizon_start: date,
    horizon_end: date,
) -> tuple[ParseReport, tuple[str, str]]:
    """Parse one documented official schedule surface and prove its coverage."""
    if provider == "federal_reserve":
        return _fed_document(text, source_url)
    if provider == "treasury":
        return parse_treasury_document(text, source_url)
    if provider == "bea":
        return _bea_document(text, source_url)
    if provider == "census":
        return _census_document(text, source_url)
    if provider == "dol_claims":
        return _dol_document(text, source_url, horizon_start, horizon_end)
    if provider == "eia_petroleum":
        return _eia_weekly_document(
            text, source_url, horizon_start, horizon_end, natural_gas=False,
        )
    if provider == "eia_natural_gas":
        return _eia_weekly_document(
            text, source_url, horizon_start, horizon_end, natural_gas=True,
        )
    if provider == "usda_nass":
        return _nass_document(text, source_url)
    if provider == "usda_wasde":
        return _wasde_document(text, source_url)
    if provider == "fas_export_sales":
        return _fas_document(text, source_url, horizon_start, horizon_end)
    raise PrimaryCalendarParseError(f"no official parser for {provider}")
