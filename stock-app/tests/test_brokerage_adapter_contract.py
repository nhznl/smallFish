"""Contract baseline for the brokerage-agnostic API.

Two jobs:

1. Hold the settled API decisions to their own invariants — public identities
   are institutions rather than connectors, the new routes are additive, and the
   Symbol Ledger contract carries no group identity.
2. Bind the frozen accounting identities and completeness vocabulary to the
   live common API, so a later change cannot drift the two apart without a
   failing test. These originally characterized the pre-migration
   ``/brokerage-ledgers`` response; that projection is retired, and the same
   formulas are now proven directly against the surface that replaced it.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import config, options_activity, retirement_options
from app.brokerages import registry
from app.main import app
from tests import brokerage_contract_spec as spec
from tests.test_brokerage_adapters import (CONTRACT, adapter_env,  # noqa: F401
                                           write_covered_put)

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
    """The frozen catalog names an institution the registry actually serves,
    under the public id the registry actually keys it by. The legacy
    ``brokerage_ledger.PORTFOLIOS`` this compared against during migration is
    retired; the registry is now the sole source of configuration."""
    configured = {
        entry.descriptor.id: entry.descriptor.institution
        for entry in registry.REGISTRY.values()
    }
    assert configured == {
        brokerage_id: entry["institution"]
        for brokerage_id, entry in spec.BROKERAGE_CATALOG.items()
    }






def test_a_sync_creates_no_group_state(adapter_env, monkeypatch):
    """The whole write path is gone, not merely switched off.

    A flag would still be a matter of every caller remembering to opt out; a
    config function returning a path would still be an artifact a sync could
    write to. Neither exists any more, so there is nothing left to opt out of
    and nowhere left to write.
    """
    def provider(_start, _end):
        return ([], [], {"environment": "live"})

    options_activity.sync(provider=provider)

    assert "legacy_groups" not in inspect.signature(options_activity.sync).parameters
    assert "legacy_groups" not in inspect.signature(
        retirement_options.sync_events
    ).parameters
    # The mutation entry points, the group artifact paths, and the header
    # constants that described their rows are gone rather than merely
    # unreachable.
    for module, names in (
        (options_activity, ("create_group", "update_group", "assign_event",
                            "GROUP_HEADERS", "MEMBER_HEADERS")),
        (retirement_options, ("create_group", "update_group", "assign_event",
                              "GROUP_HEADERS")),
        (config, ("options_groups_csv", "options_group_members_csv",
                  "retirement_option_groups_csv")),
    ):
        for name in names:
            assert not hasattr(module, name), f"{module.__name__}.{name} still exists"


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


def test_retired_legacy_routes_are_really_gone():
    """A retirement is only real when the route stops being published.

    Retiring a contract is a deliberate, owner-approved act; this keeps the two
    lists honest, so a route cannot be quietly dropped from the frozen set and
    left half-served.
    """
    published = _published_routes()
    still_served = [
        entry for entry in spec.RETIRED_LEGACY_ROUTES if entry in published
    ]
    assert still_served == []
    assert not (set(spec.RETIRED_LEGACY_ROUTES) & set(spec.FROZEN_LEGACY_ROUTES))


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

BROKERAGE_IDS = sorted(registry.REGISTRY)


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_settled_pnl_identities_hold_in_the_live_contract(adapter_env, brokerage_id):
    """`net_cash_flow = cash_in + cash_out` and
    `total_pnl = net_cash_flow + open_market_value` are the formulas the common
    projections implement. Prove the live response satisfies them for both the
    equity and option halves of the same reference position."""
    write_covered_put(brokerage_id)
    equity = client.get(f"/api/brokerages/{brokerage_id}/holdings").json()["items"][0]
    option = client.get(f"/api/brokerages/{brokerage_id}/options").json()["items"][0]

    for component in (equity, option):
        assert component["cash_in"] >= 0
        assert component["cash_out"] <= 0
        assert component["net_cash_flow"] == pytest.approx(
            component["cash_in"] + component["cash_out"]
        )
        assert component["total_pnl"] == pytest.approx(
            component["net_cash_flow"] + component["open_market_value"]
        )
        assert component["pnl_completeness"] in spec.PNL_COMPLETENESS
        if component["state"] == "FLAT":
            assert component["open_market_value"] == pytest.approx(0)
            assert component["realized_pnl"] == pytest.approx(component["total_pnl"])
            assert component["realized_pnl"] == pytest.approx(component["net_cash_flow"])
        else:
            assert component["realized_pnl"] is None
    # A short option is a negative signed market value; a long equity is positive.
    assert option["open_market_value"] < 0
    assert equity["open_market_value"] > 0


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_existing_completeness_vocabulary_is_inside_the_canonical_sets(adapter_env,
                                                                       brokerage_id):
    write_covered_put(brokerage_id)
    for resource in ("holdings", "options", "option-adjusted-basis"):
        body = client.get(f"/api/brokerages/{brokerage_id}/{resource}").json()
        assert body["coverage"]["status"] in spec.COVERAGE_STATUSES
        assert body["summary"]["pnl_completeness"] in spec.PNL_COMPLETENESS
        for item in body["items"]:
            assert item["pnl_completeness"] in spec.PNL_COMPLETENESS
    basis = client.get(
        f"/api/brokerages/{brokerage_id}/option-adjusted-basis"
    ).json()["items"][0]
    assert basis["adjusted_basis"]["completeness"] in spec.PNL_COMPLETENESS


# A fail-closed null total — one missing mark makes the whole total unknown
# rather than partial — is asserted directly against Holdings in
# `test_brokerage_api.test_a_missing_mark_makes_the_total_null_not_zero` and
# against the Symbol Ledger throughout `test_symbol_ledger_api`. Both replaced
# the legacy `/brokerage-ledgers/{portfolio}/combined` characterization that
# used to live here.
