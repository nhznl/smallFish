"""Normalized schedule observations produced by a provider parser."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScheduleObservation:
    provider: str
    source_record_id: str
    source_url: str
    canonical_key: str
    title: str
    event_type: str
    reference_period: str
    reference_label: str
    scheduled_at_utc: str | None
    civil_date: str
    original_timezone: str | None
    time_precision: str
    schedule_status: str
    lifecycle_status: str
    provenance: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ParseReport:
    observations: tuple[ScheduleObservation, ...]
    skipped_other_releases: int = 0
    skipped_without_reference_period: int = 0
    skipped_unresolved_timezone: int = 0
