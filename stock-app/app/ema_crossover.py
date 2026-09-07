"""Compatibility shim: EMA crossover evidence now lives in ``analysis``.

This module only re-exports names from ``analysis.ema_crossover`` so existing
``app.ema_crossover`` imports keep working during the shared-analysis
migration. It contains no calculations or state of its own and is deleted once
every consumer imports from ``analysis`` directly.
"""

from analysis.ema_crossover import (
    MAX_CROSSOVER_AGE,
    MIN_EMA_GAP_DOLLARS,
    EmaCrossover,
    ema14_over_20_crossover,
    has_crossover_session_coverage,
)

__all__ = [
    "MAX_CROSSOVER_AGE",
    "MIN_EMA_GAP_DOLLARS",
    "EmaCrossover",
    "ema14_over_20_crossover",
    "has_crossover_session_coverage",
]
