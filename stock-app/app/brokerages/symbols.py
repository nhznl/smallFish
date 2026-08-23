"""Brokerage-symbol translation into the price-cache convention.

Brokerages are authoritative for the positions they report, but their ticker
spellings are not necessarily the spellings used by the local price cache.
Keep the small, evidence-backed exceptions here so both account adapters make
the same lookup identity before their facts reach common projections.
"""

from __future__ import annotations

from typing import Any

from models.universe import normalize_symbol


# Fidelity reports Berkshire Class B without the cache's class separator.
# Add only confirmed provider spellings; punctuation variants are handled by
# ``normalize_symbol`` itself.
BROKERAGE_TO_CACHE_SYMBOL = {
    "BRKB": "BRK-B",
}


def cache_symbol(value: Any) -> str:
    """Return the canonical price-cache symbol for a brokerage value."""
    normalized = normalize_symbol(value)
    if not normalized:
        # Futures roots (for example ``/ESU6``) deliberately sit outside the
        # equity-cache grammar and must retain their adapter identity.
        return "" if value is None else str(value).strip().upper()
    return BROKERAGE_TO_CACHE_SYMBOL.get(normalized, normalized)
