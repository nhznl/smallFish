"""Deterministic strategy assessments. Rules stay in configuration."""

from __future__ import annotations

from datetime import datetime

from models.market_events import (
    EASTERN,
    StrategyEventAssessment,
    parse_utc,
)
from utilities.market_calendar.config import RiskPolicy, StrategyWindow


class RiskRuleGap(ValueError):
    """No configured rule covered this event. The scan must stop rather than guess."""


def timing_relationship(
    window: StrategyWindow,
    scheduled_at_utc: str | None,
    time_precision: str,
) -> str:
    """Place one instant on a strategy's Eastern clock. Unknown times stay all-day."""
    if time_precision != "exact" or not scheduled_at_utc:
        return "all_day_exposure"
    clock = parse_utc(scheduled_at_utc).astimezone(EASTERN).time().replace(second=0, microsecond=0)
    if clock < window.entry:
        return "before_entry"
    if clock <= window.typical_exit:
        return "during_typical_hold"
    if clock <= window.hard_exit:
        return "after_typical_exit_before_hard_exit"
    return "after_planned_exit"


def _match(rule, relevance: str, timing: str, importance: int) -> bool:
    return (
        relevance in rule.relevance
        and timing in rule.timing
        and rule.min_importance <= importance <= rule.max_importance
    )


def assess(
    *,
    windows: tuple[StrategyWindow, ...],
    policy: RiskPolicy,
    portfolio_relevance: str,
    importance_score: int,
    scheduled_at_utc: str | None,
    time_precision: str,
    lifecycle_status: str,
) -> tuple[StrategyEventAssessment, ...]:
    results: list[StrategyEventAssessment] = []
    for window in windows:
        timing = timing_relationship(window, scheduled_at_utc, time_precision)
        if lifecycle_status == "cancelled":
            risks = ["The source no longer lists this release on the scanned schedule."]
            required = policy.required_risk_text.get(window.strategy_id)
            if required:
                risks.append(required)
            results.append(StrategyEventAssessment(
                strategy_id=window.strategy_id,
                assessment="conditional",
                timing_relationship=timing,
                potential_benefits=(),
                potential_risks=tuple(risks),
                rule_ids=("lifecycle-cancelled",),
                policy_version=policy.policy_version,
            ))
            continue
        matched = next(
            (
                rule for rule in policy.rules
                if rule.strategy_id == window.strategy_id
                and _match(rule, portfolio_relevance, timing, importance_score)
            ),
            None,
        )
        if matched is None:
            raise RiskRuleGap(
                f"no rule for {window.strategy_id} {portfolio_relevance} {timing} importance {importance_score}"
            )
        results.append(StrategyEventAssessment(
            strategy_id=window.strategy_id,
            assessment=matched.assessment,
            timing_relationship=timing,
            potential_benefits=matched.benefits,
            potential_risks=matched.risks,
            rule_ids=(matched.rule_id,),
            policy_version=policy.policy_version,
        ))
    return tuple(results)


def rank_key(policy: RiskPolicy, assessment: StrategyEventAssessment, importance_score: int) -> list[int]:
    """Explainable ordering tuple. It is not a blended score and is not shown as one."""
    return [
        policy.assessment_rank[assessment.assessment],
        policy.timing_rank[assessment.timing_relationship],
        importance_score,
    ]


def eastern_stamp(scheduled_at_utc: str) -> datetime:
    return parse_utc(scheduled_at_utc).astimezone(EASTERN)
