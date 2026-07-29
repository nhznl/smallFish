"""Phase 1 contract baseline for the brokerage-agnostic API migration.

Two jobs:

1. Hold the settled API decisions to their own invariants — public identities
   are institutions rather than connectors, the new routes are additive, and the
   Symbol Ledger contract carries no group identity.
2. Bind that frozen contract to the code that exists today. The accounting
   identities, completeness vocabulary, and per-resource response identity are
   asserted against the live ``/brokerage-ledgers`` responses, so a later phase
   cannot drift the two apart without a failing test.

No production behavior is exercised beyond the endpoints already shipped.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import (brokerage_ledger, config, options_activity, retirement_options,
                 snaptrade_service)
from app.brokerages import registry
from app.main import app
from tests import brokerage_contract_spec as spec
from tests.test_brokerage_ledger import (_holding, _retirement_event,
                                         _trading_event, _trading_position,
                                         ledger_env)  # noqa: F401 - fixture

client = TestClient(app)

_CONTRACT = "ABC   260821P00050000"


def _published_routes() -> set[tuple[str, str]]:
    """Every (method, path) the OpenAPI document advertises.

    The published schema — not the in-memory route table — is what an external
    caller sees, so it is the surface the compatibility freeze protects.
    """
    paths = app.openapi()["paths"]
    return {
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS"}
    }


# ------------------------------------------------------------------ identity ---

def test_public_brokerage_ids_are_institutions_not_connectors():
    """`snaptrade` is an adapter type. Fidelity is the brokerage the user sees."""
    assert set(spec.BROKERAGE_CATALOG) == set(spec.PUBLIC_BROKERAGE_IDS)
    assert "snaptrade" not in spec.PUBLIC_BROKERAGE_IDS
    for brokerage_id, entry in spec.BROKERAGE_CATALOG.items():
        assert brokerage_id == brokerage_id.lower()
        assert entry["adapter"] in spec.ADAPTER_TYPES
        assert entry["portfolio_role"] in spec.PORTFOLIO_ROLES
    # Two public identities, one shared adapter surface plus one dedicated: the
    # point of the split is that another institution can reuse SNAPTRADE later.
    assert spec.BROKERAGE_CATALOG["fidelity"]["adapter"] == "SNAPTRADE"
    assert spec.BROKERAGE_CATALOG["fidelity"]["institution"] == "FIDELITY"


def test_catalog_describes_the_portfolios_the_backend_configures_today():
    """The migration renames the identity; it does not invent new brokerages."""
    configured = {
        entry["id"]: entry["brokerage"]
        for entry in brokerage_ledger.PORTFOLIOS.values()
    }
    assert configured == {
        entry["portfolio_role"]: entry["institution"]
        for entry in spec.BROKERAGE_CATALOG.values()
    }


def test_registry_sync_disables_legacy_group_writes(monkeypatch):
    """The production sync path preserves facts without making group state."""
    calls: dict[str, object] = {}

    def trading_sync(*_args, **kwargs):
        calls["trading"] = kwargs
        return {}

    def retirement_sync(*_args, **kwargs):
        calls["retirement"] = kwargs
        return {}

    monkeypatch.setattr(options_activity, "sync", trading_sync)
    monkeypatch.setattr(retirement_options, "sync_events", retirement_sync)
    registry.REGISTRY["tastytrade"].sync_commands["ACTIVITY"]()
    registry.REGISTRY["fidelity"].sync_commands["ACTIVITY"]()

    assert calls["trading"] == {"legacy_groups": False}
    assert calls["retirement"] == {"legacy_groups": False}


def test_canonical_vocabulary_carries_no_provider_terms():
    vocabulary = (
        spec.ENVELOPE_KEYS | spec.SYMBOL_LEDGER_LIST_ITEM_KEYS
        | spec.CURRENT_PERIOD_KEYS | spec.ARCHIVE_SUMMARY_KEYS
        | spec.CAPABILITY_KEYS | set(spec.ARCHIVE_BOUNDARY_FIELDS)
    )
    for field in vocabulary:
        for term in spec.PROVIDER_ONLY_TERMS:
            assert term not in field.lower()


# -------------------------------------------------------------------- routes ---

def test_new_routes_are_additive_and_brokerage_agnostic():
    """Additive means the new surface never occupies a legacy one.

    Each phase implements more of ``NEW_ROUTES``; what must hold at every phase
    is that they live under one identity segment, name no adapter, and shadow
    nothing a current caller depends on.
    """
    legacy_paths = {path for _method, path in spec.FROZEN_LEGACY_ROUTES}
    for method, path in spec.NEW_ROUTES:
        assert path == "/api/brokerages" or path.startswith("/api/brokerages/{brokerage_id}")
        assert path not in legacy_paths, (
            f"{method} {path} would shadow a legacy contract"
        )
        for adapter in spec.ADAPTER_TYPES:
            assert adapter.lower() not in path
    # Nothing may appear on the new surface that the settled contract does not
    # describe. Phases add routes from NEW_ROUTES; they do not invent them.
    published_new = {
        entry for entry in _published_routes()
        if entry[1].startswith("/api/brokerages")
    }
    assert published_new <= set(spec.NEW_ROUTES)


def test_frozen_legacy_routes_are_all_served_today():
    """Phases 2-6 may not remove or reshape any of these."""
    published = _published_routes()
    missing = [entry for entry in spec.FROZEN_LEGACY_ROUTES if entry not in published]
    assert missing == []


# ---------------------------------------------------- symbol ledger contract ---

def test_symbol_ledger_contract_has_no_group_identity():
    assert not (spec.SYMBOL_LEDGER_LIST_ITEM_KEYS & spec.FORBIDDEN_SYMBOL_LEDGER_KEYS)
    assert not (spec.CURRENT_PERIOD_KEYS & spec.FORBIDDEN_SYMBOL_LEDGER_KEYS)
    assert not (spec.ARCHIVE_SUMMARY_KEYS & spec.FORBIDDEN_SYMBOL_LEDGER_KEYS)
    # Neither is the top-level natural key an editable field.
    assert spec.SYMBOL_PATCH_KEYS == {"notes"}
    assert "symbol" not in spec.SYMBOL_PATCH_KEYS
    assert "state" not in spec.SYMBOL_PATCH_KEYS


def test_archive_boundary_is_reproducible_and_idempotent_by_construction():
    fields = set(spec.ARCHIVE_BOUNDARY_FIELDS)
    # Verification on read needs both the creation-time hash and the P/L it
    # implied; retry safety needs the client-generated request id.
    assert {"event_set_hash_at_creation", "realized_pnl_at_creation",
            "period_version", "request_id"} <= fields
    assert "request_id" in spec.ARCHIVE_REQUEST_KEYS
    assert "expected_period_version" in spec.ARCHIVE_REQUEST_KEYS
    # The persisted header carries every boundary field plus its own version.
    persisted = set(spec.ARCHIVE_CSV_HEADERS)
    assert "schema_version" in persisted
    assert fields - persisted == {"event_count"}
    assert "event_count_at_creation" in persisted
    # Ordering is chronological by provider identity, never by import time.
    assert spec.EVENT_ORDER_KEY == ("executed_at", "provider_event_id")
    assert "imported_at" not in spec.EVENT_ORDER_KEY


def test_app_owned_metadata_persists_the_settled_natural_key():
    assert spec.SYMBOL_METADATA_CSV_HEADERS[:2] == ("brokerage_id", "symbol")
    assert spec.SYMBOL_PATCH_KEYS <= set(spec.SYMBOL_METADATA_CSV_HEADERS)


def test_the_persisted_schemas_are_exactly_the_frozen_ones():
    """The spec and the store cannot drift apart without failing here."""
    from app.brokerages import store

    assert tuple(store.ARCHIVE_HEADERS) == spec.ARCHIVE_CSV_HEADERS
    assert tuple(store.METADATA_HEADERS) == spec.SYMBOL_METADATA_CSV_HEADERS
    # Notes are the only thing a patch may touch, and the only editable column.
    assert set(store.METADATA_HEADERS) - spec.SYMBOL_PATCH_KEYS == {
        "brokerage_id", "symbol", "created_at", "updated_at"
    }


# ------------------------------------------- accounting identities, live data ---

def _write_matched_symbol() -> None:
    """One underlying with 100 shares and one open short put, in both ledgers."""
    options_activity._atomic_write(
        config.tastytrade_positions_csv(), options_activity.COMBINED_POSITION_HEADERS,
        [
            _trading_position(
                symbol="ABC", underlying="ABC", instrument="Equity", quantity="100",
                direction="Long", mark="120", multiplier="1", average="110",
            ),
            _trading_position(
                symbol=_CONTRACT, underlying="ABC", instrument="Equity Option",
                quantity="-1", direction="Short", mark="0.75", multiplier="100",
                average="6",
            ),
        ],
    )
    options_activity._atomic_write(
        config.options_activity_csv(), options_activity.ACTIVITY_HEADERS,
        [_trading_event(
            event_id="tastytrade:TRADING:1", contract=_CONTRACT, underlying="ABC",
            action="Sell to Open", delta="-1", net_value="600",
        )],
    )
    options_activity._atomic_write(config.options_groups_csv(), options_activity.GROUP_HEADERS, [])
    options_activity._atomic_write(
        config.options_group_members_csv(), options_activity.MEMBER_HEADERS, []
    )
    snaptrade_service._atomic_write(
        config.snaptrade_holdings_csv(), snaptrade_service.HOLDINGS_HEADERS,
        [
            _holding(
                account_id="acct-1", account="BrokerageLink", symbol="ABC",
                asset_class="STOCK", quantity="100", price="120",
                cost_basis="11000", market_value="12000",
            ),
            _holding(
                account_id="acct-1", account="BrokerageLink", symbol=_CONTRACT,
                asset_class="OPTION", quantity="-1", price="0.75",
                cost_basis="-600", market_value="-75", underlying="ABC",
                option_type="PUT", strike="50", expiry="2026-08-21",
            ),
        ],
    )
    retirement_options._atomic_write(
        config.retirement_option_events_csv(), retirement_options.EVENT_HEADERS,
        [_retirement_event(
            event_id="activity-1", account_id="acct-1", account="BrokerageLink",
            contract=_CONTRACT, underlying="ABC", units="-1", amount="600",
        )],
    )


@pytest.mark.parametrize("portfolio", ["trading", "retirement"])
def test_settled_pnl_identities_hold_in_the_contract_being_migrated(ledger_env, portfolio):
    """`net_cash_flow = cash_in + cash_out` and
    `total_pnl = net_cash_flow + open_market_value` are the formulas the new
    projections inherit. Prove the existing response already satisfies them."""
    _write_matched_symbol()
    row = brokerage_ledger.snapshot(portfolio)["symbols"][0]

    assert row["cash_in"] >= 0
    assert row["cash_out"] <= 0
    assert row["net_cash_flow"] == pytest.approx(row["cash_in"] + row["cash_out"])
    assert row["total_pnl"] == pytest.approx(row["net_cash_flow"] + row["open_market_value"])
    assert row["open_market_value"] == pytest.approx(
        row["equity_market_value"] + row["option_market_value"]
    )
    # A short option is a negative signed market value; a long equity is positive.
    assert row["option_market_value"] < 0
    assert row["equity_market_value"] > 0

    for component in row["components"]:
        assert component["pnl_completeness"] in spec.PNL_COMPLETENESS
        if component["state"] == "FLAT":
            assert component["open_market_value"] == pytest.approx(0)
            assert component["realized_pnl"] == pytest.approx(component["total_pnl"])
            assert component["realized_pnl"] == pytest.approx(component["net_cash_flow"])
        else:
            assert component["realized_pnl"] is None


@pytest.mark.parametrize("portfolio", ["trading", "retirement"])
def test_existing_completeness_vocabulary_is_inside_the_canonical_sets(ledger_env, portfolio):
    _write_matched_symbol()
    data = brokerage_ledger.snapshot(portfolio)

    assert data["coverage"]["closed_equity"] in spec.COVERAGE_STATUSES
    assert data["coverage"]["open_equity"] in spec.COVERAGE_STATUSES
    assert data["coverage"]["options"] in spec.COVERAGE_STATUSES
    for row in data["symbols"]:
        assert row["exposure"] in spec.EXPOSURES
        assert row["pnl_completeness"] in spec.PNL_COMPLETENESS
        assert row["adjusted_basis"]["completeness"] in spec.PNL_COMPLETENESS
        for component in row["components"]:
            assert component["pnl_completeness"] in spec.PNL_COMPLETENESS


def test_both_brokerages_already_share_one_holdings_contract(ledger_env):
    """Per-resource response identity is the acceptance criterion the new
    Holdings route inherits; the compatibility view must already satisfy it."""
    _write_matched_symbol()
    trading = client.get("/brokerage-ledgers/trading/holdings")
    retirement = client.get("/brokerage-ledgers/retirement/holdings")
    assert trading.status_code == retirement.status_code == 200
    assert set(trading.json()) == set(retirement.json())
    trading_rows = trading.json()["holdings"]
    retirement_rows = retirement.json()["holdings"]
    assert trading_rows and retirement_rows
    assert set(trading_rows[0]) == set(retirement_rows[0])


def test_unavailable_totals_are_null_rather_than_zero(ledger_env):
    """Fail-closed is part of the settled contract: a missing input may never be
    rendered as a complete number."""
    snaptrade_service._atomic_write(
        config.snaptrade_holdings_csv(), snaptrade_service.HOLDINGS_HEADERS,
        [_holding(
            account_id="acct-1", account="BrokerageLink", symbol=_CONTRACT,
            asset_class="OPTION", quantity="-1", price="0.50", cost_basis="-100",
            market_value="-50", underlying="ABC", option_type="PUT", strike="50",
            expiry="2026-08-21",
        )],
    )
    retirement_options._atomic_write(
        config.retirement_option_events_csv(), retirement_options.EVENT_HEADERS,
        [_retirement_event(
            event_id="event-1", account_id="acct-1", account="BrokerageLink",
            contract=_CONTRACT, underlying="ABC", units="-2", amount="200",
        )],
    )
    data = brokerage_ledger.snapshot("retirement")
    assert data["summary"]["total_pnl"] is None
    assert data["symbols"][0]["total_pnl"] is None
    assert Decimal(str(data["summary"]["incomplete_symbol_count"])) == 1
