"""The settled brokerage-agnostic API contract, frozen as test data.

This module is the machine-readable form of the **Settled API decisions** in
``docs/BROKERAGE_REFACTOR_PLAN.md``. It contains no assertions and imports
nothing from ``app``: later phases import these constants so a route, a
projection, and a characterization test all check the *same* vocabulary instead
of each restating it.

Only values the plan states literally are recorded here. Anything the plan
leaves to implementation is deliberately absent — freezing a guess would create
friction later without protecting anything today.
"""

from __future__ import annotations

# --------------------------------------------------------------- identity ---

# Public brokerage identities. These name a configured institution, never an
# SDK or aggregation connector.
PUBLIC_BROKERAGE_IDS = frozenset({"fidelity", "tastytrade"})

# Backend adapter types selected by the registry. These are not public identities
# and must never appear in a URL path or an Angular contract.
ADAPTER_TYPES = frozenset({"SNAPTRADE", "TASTYTRADE"})

PORTFOLIO_ROLES = frozenset({"RETIREMENT", "TRADING"})

# id -> the descriptor the catalog must publish for it.
BROKERAGE_CATALOG = {
    "tastytrade": {
        "label": "Tastytrade",
        "institution": "TASTYTRADE",
        "portfolio_role": "TRADING",
        "adapter": "TASTYTRADE",
    },
    "fidelity": {
        "label": "Fidelity",
        "institution": "FIDELITY",
        "portfolio_role": "RETIREMENT",
        "adapter": "SNAPTRADE",
    },
}

CAPABILITY_KEYS = frozenset({
    "holdings", "options", "option_adjusted_basis", "activity", "sync",
})

# ---------------------------------------------------------------- envelope ---

ENVELOPE_KEYS = frozenset({
    "schema_name", "schema_version", "brokerage", "availability", "as_of",
    "coverage", "summary", "items", "warnings",
})
BROKERAGE_DESCRIPTOR_KEYS = frozenset({
    "id", "label", "institution", "portfolio_role",
})
AVAILABILITY_KEYS = frozenset({"status", "reasons"})
AS_OF_KEYS = frozenset({"positions", "activity", "market"})
COVERAGE_KEYS = frozenset({"status", "history_start", "reasons"})

SCHEMA_NAMES = {
    "catalog": "smallfish.brokerage-catalog",
    "holdings": "smallfish.brokerage-holdings",
    "symbol_ledger_list": "smallfish.symbol-ledger-list",
    # Retained during migration; its component vocabulary is reused rather than
    # replaced by a second set of cash-flow signs.
    "legacy_combined": "smallfish.brokerage-ledger",
}
SCHEMA_VERSION = 1

# ---------------------------------------------------------------- lifecycle ---

LEDGER_STATES = frozenset({"ACTIVE", "ARCHIVED"})
PNL_COMPLETENESS = frozenset({"COMPLETE", "INDICATIVE", "UNAVAILABLE"})
COVERAGE_STATUSES = frozenset({"COMPLETE", "INDICATIVE", "UNAVAILABLE"})
RECONCILIATION_STATUSES = frozenset({"RECONCILED", "UNRECONCILED"})
ARCHIVE_VERIFICATION_STATUSES = frozenset({"VERIFIED", "CHANGED"})
EXPOSURES = frozenset({"EQUITY", "OPTIONS", "EQUITY_AND_OPTIONS"})

# A period's chronological boundary. Import time and calendar date alone are
# explicitly rejected, because a backdated event must land in the archived
# period on the next read.
EVENT_ORDER_KEY = ("executed_at", "provider_event_id")

# ------------------------------------------------------ symbol ledger shape ---

SYMBOL_LEDGER_LIST_ITEM_KEYS = frozenset({
    "symbol", "state", "reconciliation_status", "pnl_completeness", "accounts",
    "exposure", "current_period", "archived_period_count", "archived_pnl",
    "lifetime_pnl", "notes", "warnings",
})
CURRENT_PERIOD_KEYS = frozenset({
    "period_version", "started_at", "event_count", "first_event_at",
    "last_event_at", "net_cash_flow", "open_market_value", "total_pnl",
    "realized_pnl",
})
SYMBOL_LEDGER_LIST_SUMMARY_KEYS = frozenset({
    "symbol_count", "active_count", "archived_count", "needs_review_count",
    "lifetime_pnl",
})

# Group identity disappears from the new surfaces entirely: an event belongs to
# a symbol, so there is nothing for a membership field to express.
FORBIDDEN_SYMBOL_LEDGER_KEYS = frozenset({"group_id", "group_name"})

ARCHIVE_SUMMARY_KEYS = frozenset({
    "archive_id", "symbol", "period_started_at", "period_ended_at",
    "event_count", "realized_pnl", "pnl_completeness", "verification_status",
    "created_at", "note", "warnings",
})
ARCHIVE_BOUNDARY_FIELDS = (
    "archive_id", "brokerage_id", "symbol", "period_started_at",
    "period_ended_at", "first_event_at", "last_event_at", "event_count",
    "realized_pnl_at_creation", "event_set_hash_at_creation", "period_version",
    "request_id", "note", "created_at",
)
# ``boundary_event_id`` is not in the plan's suggested field list, but the plan
# requires the boundary to be deterministic when several events share a
# timestamp — which a date alone cannot be. The ordered boundary event identity
# is what makes the cut reproducible.
ARCHIVE_CSV_HEADERS = (
    "schema_version", "archive_id", "brokerage_id", "symbol",
    "period_started_at", "period_ended_at", "first_event_at", "last_event_at",
    "boundary_event_id", "event_count_at_creation", "realized_pnl_at_creation",
    "event_set_hash_at_creation", "period_version", "request_id", "note",
    "created_at",
)
SYMBOL_METADATA_CSV_HEADERS = (
    "brokerage_id", "symbol", "notes", "created_at", "updated_at",
)
ARCHIVE_REQUEST_KEYS = frozenset({"request_id", "expected_period_version", "note"})

# Version 1 accepts only app-owned metadata. Broker facts, P/L, account
# membership, and archive boundaries are not patchable.
SYMBOL_PATCH_KEYS = frozenset({"notes"})

# ------------------------------------------------------------------- errors ---

ERROR_DETAIL_KEYS = frozenset({"code", "message"})
ARCHIVE_FAILURE_STATUSES = frozenset({404, 409, 422})

# ------------------------------------------------------------------ adapters ---

ADAPTER_PROTOCOL_METHODS = (
    "descriptor", "capabilities", "positions", "activity", "market_observations",
)
SYNC_RESOURCES = frozenset({"HOLDINGS", "ACTIVITY", "MARKET_DATA"})

# -------------------------------------------------------------- new routes ---

# Route templates in FastAPI form. Additive: none of these may collide with a
# route the application already serves.
NEW_ROUTES = (
    ("GET", "/api/brokerages"),
    ("POST", "/api/brokerages/{brokerage_id}/sync"),
    ("GET", "/api/brokerages/{brokerage_id}/holdings"),
    ("PATCH", "/api/brokerages/{brokerage_id}/holdings/{symbol}/metadata"),
    ("POST", "/api/brokerages/{brokerage_id}/holdings/gain-loss-snapshots"),
    ("GET", "/api/brokerages/{brokerage_id}/options"),
    ("GET", "/api/brokerages/{brokerage_id}/option-adjusted-basis"),
    ("GET", "/api/brokerages/{brokerage_id}/symbols"),
    ("GET", "/api/brokerages/{brokerage_id}/symbols/{symbol}"),
    ("PATCH", "/api/brokerages/{brokerage_id}/symbols/{symbol}"),
    ("GET", "/api/brokerages/{brokerage_id}/symbols/{symbol}/events"),
    ("GET", "/api/brokerages/{brokerage_id}/symbols/{symbol}/archives"),
    ("GET", "/api/brokerages/{brokerage_id}/symbols/{symbol}/archives/{archive_id}"),
    ("POST", "/api/brokerages/{brokerage_id}/symbols/{symbol}/archives"),
)

# Existing contracts that must keep working until the Phase 7 compatibility
# audit deliberately retires them. Nothing here may be removed or reshaped by
# Phases 2-6. Paths are written as they appear in the published OpenAPI
# document, which is what an external caller actually sees.
#
# Entries leave this list only by that deliberate audit, recorded in
# RETIRED_LEGACY_ROUTES below — never because a phase found one inconvenient.
FROZEN_LEGACY_ROUTES = (
    ("GET", "/options"),
    ("GET", "/options/activity"),
    ("POST", "/options/activity/sync"),
    ("POST", "/options/groups"),
    ("PUT", "/options/groups/{group_id}"),
    ("PUT", "/options/activity/{event_id}/group"),
    ("POST", "/options/activity/manual"),
    ("PUT", "/options/activity/manual/{event_id}"),
    ("DELETE", "/options/activity/manual/{event_id}"),
    ("GET", "/retirement/portfolio/live"),
    ("POST", "/retirement/holdings/sync"),
    ("POST", "/retirement/holdings/gain-loss-snapshots"),
    ("PUT", "/retirement/enrichment/{symbol}"),
    ("GET", "/retirement/options"),
    ("POST", "/retirement/options/groups"),
    ("PUT", "/retirement/options/groups/{symbol}"),
    ("PUT", "/retirement/options/activity/{event_id}/group"),
    ("GET", "/brokerage-ledgers/{portfolio}/combined"),
)

# Retired by the post-phase cleanup, after the owner confirmed these are not
# externally consumable and a consumer sweep proved nothing calls them. They are
# listed rather than deleted so a later reader can tell a deliberate retirement
# from an accidental regression, and so the suite asserts they are really gone.
#
# Holdings moved to `/api/brokerages/{brokerage_id}/holdings`, which carries the
# editable classifications, captured gain/loss percentages, and declining-trend
# state these used to be the only source of.
RETIRED_LEGACY_ROUTES = (
    ("GET", "/brokerage-ledgers/{portfolio}/holdings"),
    ("PUT", "/brokerage-ledgers/{portfolio}/holdings/{symbol}/enrichment"),
    ("POST", "/brokerage-ledgers/{portfolio}/holdings/gain-loss-snapshots"),
)

# Provider vocabulary that must be converted inside an adapter and must never
# reach a common projection, a public route path, or an Angular contract.
PROVIDER_ONLY_TERMS = ("snaptrade", "tastytrade", "dxfeed", "dxlink", "occ")
