"""Loaders for the versioned market-calendar YAML configuration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path

import yaml

from models.market_events import (
    ASSESSMENTS,
    EVENT_TYPES,
    IMPORTANCE_LABELS,
    PORTFOLIO_RELEVANCE,
    TIMING_RELATIONSHIPS,
    importance_label,
)

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
FORBIDDEN_WORDS = ("bullish", "bearish", "guaranteed", "rally", "selloff", "sell-off")


class CalendarConfigError(ValueError):
    """A calendar policy file is missing a required field or contradicts itself."""


@dataclass(frozen=True)
class SourceSpec:
    provider: str
    required: bool
    configured: bool
    parser_version: str
    scope: str
    detail: str
    schedule_url: str | None = None
    freshness_hours: int | None = None


@dataclass(frozen=True)
class ImportanceRule:
    event_type: str
    score: int
    label: str
    portfolio_relevance: str
    category: str
    asset_classes: tuple[str, ...]
    instruments: tuple[str, ...]
    rule_id: str
    policy_version: str
    rationale: str


@dataclass(frozen=True)
class StrategyWindow:
    strategy_id: str
    entry: time
    typical_exit: time
    hard_exit: time


@dataclass(frozen=True)
class RiskRule:
    rule_id: str
    strategy_id: str
    relevance: frozenset[str]
    timing: frozenset[str]
    min_importance: int
    max_importance: int
    assessment: str
    benefits: tuple[str, ...]
    risks: tuple[str, ...]


@dataclass(frozen=True)
class RiskPolicy:
    policy_version: str
    assessment_rank: dict[str, int]
    timing_rank: dict[str, int]
    required_risk_text: dict[str, str]
    rules: tuple[RiskRule, ...]


def _load(path: Path) -> dict:
    if not path.is_file():
        raise CalendarConfigError(f"missing calendar config {path.name}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise CalendarConfigError(f"{path.name} must be a mapping")
    return raw


def _clock(value: object, field: str) -> time:
    text = str(value or "").strip()
    try:
        hour, minute = text.split(":")
        parsed = time(int(hour), int(minute))
    except (TypeError, ValueError) as exc:
        raise CalendarConfigError(f"{field} must be HH:MM") from exc
    return parsed


def _reject_language(text: str, where: str) -> None:
    lowered = text.lower()
    for word in FORBIDDEN_WORDS:
        if word in lowered:
            raise CalendarConfigError(f"{where} must not describe market direction ({word})")


def load_sources(config_dir: Path = CONFIG_DIR) -> dict[str, SourceSpec]:
    raw = _load(config_dir / "market_calendar_sources.yaml")
    specs: dict[str, SourceSpec] = {}
    for provider, body in (raw.get("sources") or {}).items():
        if not isinstance(body, dict):
            raise CalendarConfigError(f"source {provider} must be a mapping")
        specs[str(provider)] = SourceSpec(
            provider=str(provider),
            required=bool(body.get("required")),
            configured=bool(body.get("configured")),
            parser_version=str(body.get("parser_version") or "").strip(),
            scope=str(body.get("scope") or "").strip(),
            detail=" ".join(str(body.get("detail") or "").split()),
            schedule_url=(str(body["schedule_url"]).strip() if body.get("schedule_url") else None),
            freshness_hours=(int(body["freshness_hours"]) if body.get("freshness_hours") is not None else None),
        )
        if not specs[provider].parser_version or not specs[provider].detail:
            raise CalendarConfigError(f"source {provider} needs a parser version and detail")
    if "bls" not in specs:
        raise CalendarConfigError("the BLS source is required in Milestone 1")
    return specs


def load_importance(config_dir: Path = CONFIG_DIR) -> dict[str, ImportanceRule]:
    raw = _load(config_dir / "market_calendar_importance.yaml")
    version = str(raw.get("policy_version") or "").strip()
    if not version:
        raise CalendarConfigError("importance policy_version is required")
    rules: dict[str, ImportanceRule] = {}
    for event_type, body in (raw.get("events") or {}).items():
        if event_type not in EVENT_TYPES:
            raise CalendarConfigError(f"unsupported event type {event_type}")
        score = int(body["score"])
        label = importance_label(score)
        relevance = str(body["portfolio_relevance"])
        if relevance not in PORTFOLIO_RELEVANCE:
            raise CalendarConfigError(f"unknown portfolio relevance {relevance}")
        rationale = " ".join(str(body.get("rationale") or "").split())
        _reject_language(rationale, f"importance {event_type}")
        rules[event_type] = ImportanceRule(
            event_type=event_type,
            score=score,
            label=label,
            portfolio_relevance=relevance,
            category=str(body["category"]).strip(),
            asset_classes=tuple(str(item) for item in body.get("asset_classes") or ()),
            instruments=tuple(str(item).upper() for item in body.get("instruments") or ()),
            rule_id=str(body["rule_id"]).strip(),
            policy_version=version,
            rationale=rationale,
        )
        if rules[event_type].label != IMPORTANCE_LABELS[score]:
            raise CalendarConfigError("importance label does not match the score")
    return rules


def load_strategy_windows(config_dir: Path = CONFIG_DIR) -> tuple[StrategyWindow, ...]:
    raw = _load(config_dir / "market_calendar_strategies.yaml")
    windows: list[StrategyWindow] = []
    for strategy_id, body in (raw.get("strategies") or {}).items():
        if "hard_exit_time_et" in body:
            entry = _clock(body.get("entry_time_et"), strategy_id)
            typical = _clock(body.get("typical_exit_time_et"), strategy_id)
            hard = _clock(body.get("hard_exit_time_et"), strategy_id)
        else:
            entry = _clock(
                body.get("entry_time_et") or body.get("short_leg_entry_time_et"),
                strategy_id,
            )
            end = _clock(
                body.get("exposure_end_et") or body.get("short_leg_exposure_end_et"),
                strategy_id,
            )
            typical = end
            hard = end
        if not (entry <= typical <= hard):
            raise CalendarConfigError(f"{strategy_id} exposure clocks are out of order")
        windows.append(StrategyWindow(str(strategy_id), entry, typical, hard))
    expected = {
        "short-premium-20d-0dte",
        "long-premium-20d-0-or-1dte",
        "long-wings-repeated-0dte-premium",
    }
    found = {window.strategy_id for window in windows}
    if found != expected:
        raise CalendarConfigError("strategy windows must define the three configured profiles")
    return tuple(windows)


def load_risk_policy(config_dir: Path = CONFIG_DIR) -> RiskPolicy:
    raw = _load(config_dir / "market_calendar_risk.yaml")
    version = str(raw.get("policy_version") or "").strip()
    if not version:
        raise CalendarConfigError("risk policy_version is required")
    assessment_rank = {str(key): int(value) for key, value in (raw.get("assessment_rank") or {}).items()}
    timing_rank = {str(key): int(value) for key, value in (raw.get("timing_rank") or {}).items()}
    if set(assessment_rank) != ASSESSMENTS or set(timing_rank) != TIMING_RELATIONSHIPS:
        raise CalendarConfigError("risk ranks must cover every assessment and timing value")
    required = {
        str(key): str(value).strip()
        for key, value in (raw.get("required_risk_text") or {}).items()
    }
    rules: list[RiskRule] = []
    for strategy_id, entries in (raw.get("strategies") or {}).items():
        for body in entries or []:
            benefits = tuple(" ".join(str(item).split()) for item in body.get("benefits") or ())
            risks = tuple(" ".join(str(item).split()) for item in body.get("risks") or ())
            for text in benefits + risks:
                _reject_language(text, body.get("id", strategy_id))
            needed = required.get(str(strategy_id))
            if needed and needed not in risks:
                raise CalendarConfigError(f"{body.get('id')} must retain {needed!r}")
            rules.append(RiskRule(
                rule_id=str(body["id"]).strip(),
                strategy_id=str(strategy_id),
                relevance=frozenset(str(item) for item in body.get("relevance") or ()),
                timing=frozenset(str(item) for item in body.get("timing") or ()),
                min_importance=int(body.get("min_importance", 1)),
                max_importance=int(body.get("max_importance", 5)),
                assessment=str(body["assessment"]),
                benefits=benefits,
                risks=risks,
            ))
            if rules[-1].assessment not in ASSESSMENTS:
                raise CalendarConfigError(f"unknown assessment on {rules[-1].rule_id}")
            if not rules[-1].risks or not rules[-1].rule_id:
                raise CalendarConfigError(f"{rules[-1].rule_id} needs risks and an id")
    return RiskPolicy(version, assessment_rank, timing_rank, required, tuple(rules))


def stale_after_calendar_days(config_dir: Path = CONFIG_DIR) -> int:
    """Use the scraper's published staleness threshold rather than a second number."""
    raw = _load(config_dir / "scraper.yaml")
    try:
        days = int(raw["stale_after_days"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CalendarConfigError("scraper stale_after_days is required for price-cache state") from exc
    if days < 0:
        raise CalendarConfigError("stale_after_days cannot be negative")
    return days
