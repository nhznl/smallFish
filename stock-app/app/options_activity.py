"""Tastytrade-backed options activity: immutable broker facts and their marks.

Broker transactions are facts keyed by the provider transaction ID, so a repeat
sync merges rather than rewrites. The Symbol Ledger derives lifecycle from these
events by normalized underlying; there is no grouping concept here at all.

Implementation lives under ``brokerages/activity_*.py``. This module is a thin
re-export facade so registry, adapters, and tests keep a stable import surface.
"""

from __future__ import annotations

from . import config
from .brokerages.activity_manual import (
    create_manual_event,
    delete_manual_event,
    update_manual_event,
)
from .brokerages.activity_normalize import (
    _OPTION_RE,
    _contract_key,
    _decimal,
    _enum_text,
    _epoch_ms_iso,
    _is_option_instrument,
    _normalize_beta,
    _normalize_combined_position,
    _normalize_event,
    _normalize_greek,
    _normalize_mark,
    _now,
    _option_terms,
    _position_delta,
    _select_transactions,
    _value,
)
from .brokerages.activity_repair import import_broker_events, remove_symbols
from .brokerages.activity_store import (
    ACTIVITY_HEADERS,
    BETA_HEADERS,
    COMBINED_POSITION_HEADERS,
    GREEKS_HEADERS,
    MANUAL_ID_PREFIX,
    MANUAL_SOURCE,
    MARK_HEADERS,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    SOURCE,
    ActivityValidationError,
    _atomic_write,
    _lock,
    _read_csv,
    _text,
)
from .brokerages.activity_sync import (
    BrokerProvider,
    _fetch_option_greeks,
    _fetch_underlying_betas,
    _safe_market_data_error,
    _trend_observations,
    fetch_tastytrade,
    sync,
)

__all__ = [
    "ACTIVITY_HEADERS",
    "BETA_HEADERS",
    "BrokerProvider",
    "COMBINED_POSITION_HEADERS",
    "GREEKS_HEADERS",
    "MANUAL_ID_PREFIX",
    "MANUAL_SOURCE",
    "MARK_HEADERS",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "SOURCE",
    "ActivityValidationError",
    "config",
    "create_manual_event",
    "delete_manual_event",
    "fetch_tastytrade",
    "import_broker_events",
    "remove_symbols",
    "sync",
    "update_manual_event",
    "_OPTION_RE",
    "_atomic_write",
    "_contract_key",
    "_decimal",
    "_enum_text",
    "_epoch_ms_iso",
    "_fetch_option_greeks",
    "_fetch_underlying_betas",
    "_is_option_instrument",
    "_lock",
    "_normalize_beta",
    "_normalize_combined_position",
    "_normalize_event",
    "_normalize_greek",
    "_normalize_mark",
    "_now",
    "_option_terms",
    "_position_delta",
    "_read_csv",
    "_safe_market_data_error",
    "_select_transactions",
    "_text",
    "_trend_observations",
    "_value",
]
