"""Standard-library contracts for the market-event risk calendar.

Wire values use the camelCase names in the approved design. Algorithms,
provider parsing, and database access stay in the runtimes that own them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 1

EVENT_TYPES = frozenset({
    "CPI", "PPI", "EMPLOYMENT_SITUATION", "JOLTS", "ECI",
    "JOBLESS_CLAIMS", "FOMC_DECISION", "FOMC_PRESS_CONFERENCE",
    "FOMC_MINUTES", "FED_SPEECH",
    "BEIGE_BOOK", "TREASURY_REFUNDING", "TREASURY_AUCTION", "PCE",
    "GDP", "RETAIL_SALES", "DURABLE_GOODS", "INDUSTRIAL_PRODUCTION",
    "EARNINGS",
})
EVENT_CATEGORIES = frozenset({
    "inflation", "labor", "central_bank", "rates", "growth",
    "consumption", "production", "earnings",
})
TIME_PRECISIONS = frozenset({"exact", "date_only", "estimated", "unknown"})
SCHEDULE_STATUSES = frozenset({"confirmed", "tentative", "estimated", "unscheduled"})
LIFECYCLE_STATUSES = frozenset({"scheduled", "released", "cancelled", "rescheduled"})
ASSET_CLASSES = frozenset({"equities", "rates", "commodities", "fx", "volatility"})
PORTFOLIO_RELEVANCE = frozenset({
    "DIRECT_BROAD_INDEX",
    "INDIRECT_BROAD_INDEX",
    "SECTOR_OR_COMMODITY",
    "SINGLE_STOCK",
    "LOW_RELEVANCE",
})
ASSESSMENTS = frozenset({
    "avoid",
    "high_risk",
    "mixed_opportunity",
    "conditional",
    "low_direct_relevance",
})
TIMING_RELATIONSHIPS = frozenset({
    "before_entry",
    "during_typical_hold",
    "after_typical_exit_before_hard_exit",
    "after_planned_exit",
    "all_day_exposure",
})
VALUE_KINDS = frozenset({
    "previous",
    "revised_previous",
    "consensus",
    "forecast",
    "actual",
})
ETF_RELATIONSHIPS = frozenset({
    "direct_underlying",
    "sector_equities",
    "industry_equities",
    "rate_sensitive",
    "input_cost_exposure",
    "defensive_or_hedge_proxy",
})
ETF_RELEVANCE = frozenset({"primary", "secondary", "indirect"})
PRICE_DATA_STATES = frozenset({"current", "stale", "missing"})
IMPORTANCE_LABELS = {5: "Extreme", 4: "High", 3: "Moderate", 2: "Low", 1: "Informational"}
BROAD_INDEX_RELEVANCE = frozenset({"DIRECT_BROAD_INDEX", "INDIRECT_BROAD_INDEX"})
EASTERN = ZoneInfo("America/New_York")
PACIFIC = ZoneInfo("America/Los_Angeles")
DISPLAY_ZONES = {"America/New_York": EASTERN, "America/Los_Angeles": PACIFIC}

# iCalendar TZID values that name US Eastern without being IANA identifiers.
# Offsets are never substituted; zoneinfo resolves the named zone.
TZID_ALIASES = {
    "US-Eastern": "America/New_York",
    "US/Eastern": "America/New_York",
    "Eastern": "America/New_York",
    "America/New_York": "America/New_York",
    "America/Los_Angeles": "America/Los_Angeles",
    "UTC": "UTC",
    "Etc/UTC": "UTC",
}


class MarketEventContractError(ValueError):
    """A calendar contract field is missing or not one of the allowed values."""


def importance_label(score: int) -> str:
    try:
        return IMPORTANCE_LABELS[score]
    except KeyError as exc:
        raise MarketEventContractError(f"importance score {score} is not 1..5") from exc


def is_broad_index(relevance: str) -> bool:
    _one_of("portfolio relevance", relevance, PORTFOLIO_RELEVANCE)
    return relevance in BROAD_INDEX_RELEVANCE


def format_utc(moment: datetime) -> str:
    """Render an aware timestamp as UTC ``YYYY-MM-DDTHH:MM:SSZ``."""
    if moment.tzinfo is None:
        raise MarketEventContractError("UTC conversion requires an aware datetime")
    utc = moment.astimezone(timezone.utc).replace(microsecond=0)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    text = str(value).strip()
    if not text.endswith("Z"):
        raise MarketEventContractError("timestamps must be UTC instants ending in Z")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise MarketEventContractError("timestamps must be YYYY-MM-DDTHH:MM:SSZ") from exc
    return parsed.replace(tzinfo=timezone.utc)


def within_freshness_window(
    last_success_utc: str, freshness_hours: int, now: datetime,
) -> bool:
    """Return whether one UTC success instant is current at ``now``."""
    success = parse_utc(last_success_utc)
    if now.tzinfo is None:
        raise MarketEventContractError("freshness evaluation requires an aware datetime")
    current = now.astimezone(timezone.utc)
    return success <= current <= success + timedelta(hours=freshness_hours)


def clock_label(moment: datetime, suffix: str) -> str:
    hour = moment.hour % 12 or 12
    ampm = "AM" if moment.hour < 12 else "PM"
    return f"{hour}:{moment.minute:02d} {ampm} {suffix}"


def display_clocks(scheduled_at_utc: str | None) -> dict[str, str | None]:
    """Derive Eastern and Pacific clocks from one stored UTC instant."""
    if not scheduled_at_utc:
        return {
            "easternTime": None,
            "pacificTime": None,
            "easternDate": None,
            "pacificDate": None,
        }
    instant = parse_utc(scheduled_at_utc)
    eastern = instant.astimezone(EASTERN)
    pacific = instant.astimezone(PACIFIC)
    return {
        "easternTime": clock_label(eastern, "ET"),
        "pacificTime": clock_label(pacific, "PT"),
        "easternDate": eastern.date().isoformat(),
        "pacificDate": pacific.date().isoformat(),
    }


def session_date_for(scheduled_at_utc: str | None, civil_date: str | None, zone: str) -> str:
    """Calendar date used to group an event, in the requested display zone."""
    if zone not in DISPLAY_ZONES:
        raise MarketEventContractError("timezone must be America/New_York or America/Los_Angeles")
    if scheduled_at_utc:
        instant = parse_utc(scheduled_at_utc)
        return instant.astimezone(DISPLAY_ZONES[zone]).date().isoformat()
    if civil_date:
        return civil_date
    raise MarketEventContractError("an event needs a UTC instant or a civil date")


def _one_of(name: str, value: str, allowed: frozenset[str]) -> str:
    if value not in allowed:
        raise MarketEventContractError(f"{name} {value!r} is not a calendar contract value")
    return value


def _text(value: object, field: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise MarketEventContractError(f"{field} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal_text(value: object, field: str) -> str | None:
    """Keep a decimal as text. Floats are rejected so binary rounding cannot land in storage."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        raise MarketEventContractError(f"{field} must be a decimal string or omitted")
    text = value.strip()
    if not text:
        return None
    body = text[1:] if text[0] == "-" else text
    if body.count(".") > 1 or not body.replace(".", "").isdigit() or body in {"", "."}:
        raise MarketEventContractError(f"{field} must be a base-10 decimal string")
    return text


@dataclass(frozen=True)
class EventMeasurement:
    metric: str
    label: str
    value_kind: str
    value: str | None = None
    numeric_value: str | None = None
    unit: str | None = None
    scale: str | None = None
    seasonal_adjustment: str | None = None
    provider: str | None = None
    observed_at_utc: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric", _text(self.metric, "metric"))
        object.__setattr__(self, "label", _text(self.label, "measurement label"))
        object.__setattr__(self, "value_kind", _one_of("value kind", self.value_kind, VALUE_KINDS))
        value = _optional_text(self.value)
        numeric = _decimal_text(self.numeric_value, "numericValue")
        if value is not None and numeric is not None and value != numeric:
            raise MarketEventContractError("value and numericValue must match when both are present")
        if self.observed_at_utc:
            parse_utc(self.observed_at_utc)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "numeric_value", numeric)
        object.__setattr__(self, "unit", _optional_text(self.unit))
        object.__setattr__(self, "scale", _optional_text(self.scale))
        object.__setattr__(self, "seasonal_adjustment", _optional_text(self.seasonal_adjustment))
        object.__setattr__(self, "provider", _optional_text(self.provider))

    def to_wire(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "label": self.label,
            "value": self.value,
            "numericValue": self.numeric_value,
            "unit": self.unit,
            "scale": self.scale,
            "seasonalAdjustment": self.seasonal_adjustment,
            "valueKind": self.value_kind,
            "provider": self.provider,
            "observedAtUtc": self.observed_at_utc,
        }

    @classmethod
    def from_wire(cls, raw: dict[str, object]) -> "EventMeasurement":
        return cls(
            metric=str(raw.get("metric", "")),
            label=str(raw.get("label", "")),
            value_kind=str(raw.get("valueKind", "")),
            value=_optional_text(raw.get("value")),
            numeric_value=raw.get("numericValue") if "numericValue" in raw else None,  # type: ignore[arg-type]
            unit=_optional_text(raw.get("unit")),
            scale=_optional_text(raw.get("scale")),
            seasonal_adjustment=_optional_text(raw.get("seasonalAdjustment")),
            provider=_optional_text(raw.get("provider")),
            observed_at_utc=_optional_text(raw.get("observedAtUtc")),
        )


@dataclass(frozen=True)
class StrategyEventAssessment:
    strategy_id: str
    assessment: str
    timing_relationship: str
    potential_benefits: tuple[str, ...]
    potential_risks: tuple[str, ...]
    rule_ids: tuple[str, ...]
    policy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", _text(self.strategy_id, "strategyId"))
        object.__setattr__(self, "assessment", _one_of("assessment", self.assessment, ASSESSMENTS))
        object.__setattr__(
            self, "timing_relationship",
            _one_of("timing relationship", self.timing_relationship, TIMING_RELATIONSHIPS),
        )
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policyVersion"))
        benefits = tuple(_text(item, "potential benefit") for item in self.potential_benefits)
        risks = tuple(_text(item, "potential risk") for item in self.potential_risks)
        rules = tuple(_text(item, "rule id") for item in self.rule_ids)
        if not rules:
            raise MarketEventContractError("an assessment requires at least one rule id")
        object.__setattr__(self, "potential_benefits", benefits)
        object.__setattr__(self, "potential_risks", risks)
        object.__setattr__(self, "rule_ids", rules)

    def to_wire(self) -> dict[str, object]:
        return {
            "strategyId": self.strategy_id,
            "assessment": self.assessment,
            "timingRelationship": self.timing_relationship,
            "potentialBenefits": list(self.potential_benefits),
            "potentialRisks": list(self.potential_risks),
            "ruleIds": list(self.rule_ids),
            "policyVersion": self.policy_version,
        }


@dataclass(frozen=True)
class EventEtfExposure:
    symbol: str
    relationship: str
    relevance: str
    impact_channel: str
    price_data_state: str
    latest_price_session: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "ETF symbol"))
        object.__setattr__(self, "relationship", _one_of("ETF relationship", self.relationship, ETF_RELATIONSHIPS))
        object.__setattr__(self, "relevance", _one_of("ETF relevance", self.relevance, ETF_RELEVANCE))
        object.__setattr__(self, "impact_channel", _text(self.impact_channel, "impact channel"))
        object.__setattr__(
            self, "price_data_state",
            _one_of("price data state", self.price_data_state, PRICE_DATA_STATES),
        )
        latest = _optional_text(self.latest_price_session)
        if self.price_data_state == "missing" and latest:
            raise MarketEventContractError("missing price data cannot carry a session date")
        object.__setattr__(self, "latest_price_session", latest)

    def to_wire(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "relationship": self.relationship,
            "relevance": self.relevance,
            "impactChannel": self.impact_channel,
            "priceDataState": self.price_data_state,
            "latestPriceSession": self.latest_price_session,
        }
