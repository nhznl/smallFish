"""Compatibility shim: stock/weekly/gain-loss models now live in ``analysis``.

This module only re-exports names from ``analysis.stock`` so existing
``app.stock_model`` imports keep working during the shared-analysis migration.
It contains no calculations, constants, or state of its own and is deleted once
every consumer imports from ``analysis`` directly.
"""

from analysis.stock import (
    BEARISH_CONTINUATION,
    BEARISH_REVERSAL,
    BULLISH_CONTINUATION,
    BULLISH_REVERSAL,
    NOT_EVALUATED,
    RECENT_WEEKS_SIZE,
    SETUP_SCORE_VERSION,
    STOCK_TYPES,
    WATCH,
    GainLoss,
    Stock,
    Weekly,
    normalize_stock_type,
)

__all__ = [
    "BEARISH_CONTINUATION",
    "BEARISH_REVERSAL",
    "BULLISH_CONTINUATION",
    "BULLISH_REVERSAL",
    "NOT_EVALUATED",
    "RECENT_WEEKS_SIZE",
    "SETUP_SCORE_VERSION",
    "STOCK_TYPES",
    "WATCH",
    "GainLoss",
    "Stock",
    "Weekly",
    "normalize_stock_type",
]
