"""Per-holding adverse-move state, owned once for every brokerage.

The reference is a peak high-water mark of a holding's own gain/loss
percentage. A favorable move (a gain growing, or a loss shrinking) ratchets the
peak up and clears any alert. An adverse move that worsens the percentage by at
least the relative threshold trips a sticky alert and re-baselines the peak, so
a further leg down alerts again. A sub-threshold adverse move holds the peak, so
a slow multi-sync slide keeps accumulating toward the threshold.

Every provider already recorded exactly this, with the same columns and the same
``(account, symbol)`` key, in its own file. What differs between them is only how
a current percentage is read off a provider row — so that extraction stays in the
provider module and this owns the rule. The path comes from the registry, which
is why nothing here learns which brokerage it is advancing.

Trend is advisory. A sync that cannot advance it must still succeed.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from .. import options_activity

HEADERS = (
    "account_id", "account_name", "symbol", "peak_pct", "peak_at", "last_pct",
    "last_synced_at", "alert", "alert_from_pct", "alert_from_at", "alert_to_pct",
    "alert_drop_pct", "alert_at",
)

TrendKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Observation:
    """One holding's current gain/loss percentage, already normalized."""

    account_id: str
    account_name: str
    symbol: str
    gain_loss_pct: Decimal


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def threshold() -> Decimal:
    """Relative adverse move that trips an alert, as a fraction (default 0.10 =
    a 10% worsening of the holding's own gain/loss percentage)."""
    return _decimal(os.environ.get("SFP_HOLDINGS_TREND_THRESHOLD"), Decimal("0.10"))


def min_base() -> Decimal:
    """Materiality floor in gain/loss percentage points: holdings whose peak is
    within ±this of breakeven (and cash) are treated as flat and never alert,
    which also avoids a divide-by-near-zero on the relative move."""
    return _decimal(os.environ.get("SFP_HOLDINGS_TREND_MIN_BASE"), Decimal("5"))


def key(account_id: str, symbol: str) -> TrendKey:
    """The same symbol held in two accounts trends independently, because the
    gain/loss it is measured against is per-account."""
    return (str(account_id or ""), str(symbol or "").upper())


def read(path: Path) -> dict[TrendKey, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        return {
            key(row.get("account_id", ""), row.get("symbol", "")): {
                field: str(row.get(field, "")) for field in HEADERS
            }
            for row in csv.DictReader(handle)
            if str(row.get("symbol", "")).strip()
        }


def display(state: dict[str, str] | None,
            current_pct: float | None) -> dict[str, Any]:
    """Stored state in the common response shape.

    ``alert`` is sticky. The from/to/drop values describe the move that set it,
    so they read as null while no alert stands rather than showing a stale one.
    """
    direction = "GAIN" if current_pct is not None and current_pct >= 0 else "LOSS"
    if not state:
        return {
            "alert": False, "peak_pct": None, "peak_at": "", "drop_pct": None,
            "from_pct": None, "to_pct": None, "alert_at": None,
            "direction": direction,
        }
    alert = state.get("alert", "").lower() == "true"

    def optional(field: str) -> float | None:
        value = state.get(field)
        return None if value in (None, "") else float(_decimal(value))

    return {
        "alert": alert,
        "peak_pct": optional("peak_pct"),
        "peak_at": state.get("peak_at", ""),
        "drop_pct": optional("alert_drop_pct") if alert else None,
        "from_pct": optional("alert_from_pct") if alert else None,
        "to_pct": optional("alert_to_pct") if alert else None,
        "alert_at": (state.get("alert_at") or None) if alert else None,
        "direction": direction,
    }


def _cleared(state: dict[str, str]) -> dict[str, str]:
    return {
        **state, "alert": "", "alert_from_pct": "", "alert_from_at": "",
        "alert_to_pct": "", "alert_drop_pct": "", "alert_at": "",
    }


def advance(observations: Iterable[Observation], *, path: Path,
            now: str) -> dict[TrendKey, dict[str, str]]:
    """Advance every observed holding one sync and persist the result.

    A holding that was not observed this sync is dropped: it is no longer held,
    and keeping its peak would alert against a position that does not exist.
    """
    trip, floor = threshold(), min_base()
    previous = read(path)
    updated: dict[TrendKey, dict[str, str]] = {}

    for observation in observations:
        identity = key(observation.account_id, observation.symbol)
        if not identity[1]:
            continue
        current = observation.gain_loss_pct
        prior = previous.get(identity)
        if prior is None:
            updated[identity] = _cleared({
                "account_id": identity[0],
                "account_name": observation.account_name,
                "symbol": identity[1], "peak_pct": str(current), "peak_at": now,
                "last_pct": str(current), "last_synced_at": now,
            })
            continue

        state = dict(prior)
        state["account_name"] = observation.account_name or prior.get("account_name", "")
        state["last_pct"] = str(current)
        state["last_synced_at"] = now
        peak = _decimal(prior.get("peak_pct"))
        if current > peak:
            # Favorable: a new high-water mark clears the alert.
            state = _cleared({**state, "peak_pct": str(current), "peak_at": now})
        else:
            drop = (peak - current) / abs(peak) if abs(peak) >= floor else Decimal("0")
            if drop >= trip:
                # Adverse past the threshold: trip, and re-baseline so a further
                # leg down can alert again.
                state.update({
                    "alert": "true",
                    "alert_from_pct": str(peak),
                    "alert_from_at": prior.get("peak_at", ""),
                    "alert_to_pct": str(current),
                    "alert_drop_pct": str(drop * Decimal("100")),
                    "alert_at": now,
                    "peak_pct": str(current), "peak_at": now,
                })
            # else: sub-threshold — hold the peak and any standing alert.
        updated[identity] = state

    options_activity._atomic_write(
        path, list(HEADERS), [updated[identity] for identity in sorted(updated)]
    )
    return updated


__all__ = [
    "HEADERS", "Observation", "advance", "display", "key", "min_base", "read",
    "threshold",
]
