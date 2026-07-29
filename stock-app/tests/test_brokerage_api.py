"""The additive brokerage-agnostic read APIs.

Two things are being proved. First, that one resource really is one contract:
the same request against Fidelity and Tastytrade returns the same key sets, the
same vocabulary, and the same fail-closed behavior. Second, that moving the
accounting into common projections did not change what the numbers mean — the
parity tests compare against the compatibility view that ships today.

Where the new projection is deliberately *better* than the legacy view, that is
asserted explicitly rather than papered over.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import brokerage_ledger, config, options_activity
from app.brokerages import contracts, registry
from app.brokerages.projections import components as component_projection
from app.main import app
from tests import brokerage_contract_spec as spec
from tests.test_brokerage_adapters import (CONTRACT, _write_snaptrade,  # noqa: F401
                                           _write_tastytrade, adapter_env,
                                           write_covered_put)

client = TestClient(app)

BROKERAGE_IDS = sorted(registry.REGISTRY)
RESOURCES = ("holdings", "options", "option-adjusted-basis")

#: The compatibility view keyed by portfolio; the new API by brokerage id.
LEGACY_PORTFOLIO = {"tastytrade": "trading", "fidelity": "retirement"}


def _get(brokerage_id: str, resource: str, **params):
    response = client.get(f"/api/brokerages/{brokerage_id}/{resource}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------- discovery ---

def test_catalog_discovers_both_brokerages_through_one_contract(adapter_env):
    body = client.get("/api/brokerages").json()
    assert body["schema_name"] == spec.SCHEMA_NAMES["catalog"]
    assert body["schema_version"] == spec.SCHEMA_VERSION
    entries = {entry["id"]: entry for entry in body["brokerages"]}
    assert set(entries) == spec.PUBLIC_BROKERAGE_IDS
    for brokerage_id, entry in entries.items():
        expected = spec.BROKERAGE_CATALOG[brokerage_id]
        assert entry["label"] == expected["label"]
        assert entry["institution"] == expected["institution"]
        assert entry["portfolio_role"] == expected["portfolio_role"]
        assert set(entry["capabilities"]) == spec.CAPABILITY_KEYS
        # The backend adapter is never published: `snaptrade` is not an identity.
        assert "adapter" not in entry
        assert "snaptrade" not in str(entry).lower()


def test_unknown_brokerage_is_a_safe_404(adapter_env):
    for resource in RESOURCES:
        response = client.get(f"/api/brokerages/snaptrade/{resource}")
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert set(detail) == spec.ERROR_DETAIL_KEYS
        assert detail["code"] == "UNKNOWN_BROKERAGE"


# ------------------------------------------------------- one contract, both ---

@pytest.mark.parametrize("resource", RESOURCES)
def test_each_resource_has_one_shape_across_brokerages(adapter_env, resource):
    for brokerage_id in BROKERAGE_IDS:
        write_covered_put(brokerage_id)
    bodies = {
        brokerage_id: _get(brokerage_id, resource) for brokerage_id in BROKERAGE_IDS
    }
    shapes = [set(body) for body in bodies.values()]
    assert shapes[0] == shapes[1] == set(spec.ENVELOPE_KEYS)
    for body in bodies.values():
        assert set(body["brokerage"]) == spec.BROKERAGE_DESCRIPTOR_KEYS
        assert set(body["availability"]) == spec.AVAILABILITY_KEYS
        assert set(body["as_of"]) == spec.AS_OF_KEYS
        assert spec.COVERAGE_KEYS <= set(body["coverage"])
        assert body["schema_version"] == spec.SCHEMA_VERSION
    first, second = bodies.values()
    assert set(first["summary"]) == set(second["summary"])
    assert first["items"] and second["items"]
    assert set(first["items"][0]) == set(second["items"][0])


@pytest.mark.parametrize("resource", RESOURCES)
def test_a_brokerage_with_nothing_synced_is_a_capability_state(adapter_env, resource):
    """An empty items array is a real answer. A missing artifact is not."""
    for brokerage_id in BROKERAGE_IDS:
        body = _get(brokerage_id, resource)
        assert body["items"] == []
        assert body["availability"]["status"] == "UNAVAILABLE"
        assert body["availability"]["reasons"]


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_responses_never_name_the_provider_behind_them(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    for resource in RESOURCES:
        body = str(_get(brokerage_id, resource)).lower()
        assert "snaptrade" not in body
        assert "dxlink" not in body
        assert "dxfeed" not in body


# ---------------------------------------------------------------- holdings ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_holdings_are_open_equity_with_editable_classifications(adapter_env,
                                                                brokerage_id):
    write_covered_put(brokerage_id)
    body = _get(brokerage_id, "holdings")

    assert [item["symbol"] for item in body["items"]] == ["ABC"]
    holding = body["items"][0]
    assert holding["instrument"] == "EQUITY"          # the short put is elsewhere
    assert holding["quantity"] == pytest.approx(100)
    assert holding["cost_basis"] == pytest.approx(11000)
    assert holding["cost_per_unit"] == pytest.approx(110)
    assert holding["market_value"] == pytest.approx(12000)
    assert holding["unrealized_pnl"] == pytest.approx(1000)
    assert holding["category"] == "UNCLASSIFIED"      # nothing classified yet
    assert holding["note"] == ""
    assert body["summary"]["total_market_value"] == pytest.approx(12000)
    assert body["summary"]["total_unrealized_pnl"] == pytest.approx(1000)


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_holdings_merge_each_brokerages_own_metadata_store(adapter_env, brokerage_id):
    """Classifications are per-brokerage and never leak between them."""
    write_covered_put("tastytrade")
    write_covered_put("fidelity")
    path = registry.REGISTRY[brokerage_id].holdings_metadata_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "symbol,category,industry,note,updated_at\n"
        "ABC,growth,software,watch assignment,2026-07-28T00:00:00Z\n",
        encoding="utf-8",
    )
    other = next(item for item in BROKERAGE_IDS if item != brokerage_id)

    mine = _get(brokerage_id, "holdings")["items"][0]
    theirs = _get(other, "holdings")["items"][0]
    assert mine["category"] == "GROWTH"
    assert mine["industry"] == "SOFTWARE"
    assert mine["note"] == "watch assignment"
    assert theirs["category"] == "UNCLASSIFIED"
    assert theirs["note"] == ""


# ----------------------------------------------------------------- options ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_options_return_one_item_per_exact_contract(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    body = _get(brokerage_id, "options")

    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["instrument"] == "OPTION"
    assert item["symbol"] == "ABC"
    assert item["contract_key"] == options_activity._contract_key(CONTRACT)
    assert item["option_type"] == "PUT"
    assert item["strike"] == pytest.approx(50)
    assert item["expiry"] == "2026-08-21"
    assert item["side"] == "SHORT"
    assert item["state"] == "OPEN"
    assert item["quantity"] == pytest.approx(-1)
    assert item["cash_in"] == pytest.approx(600)
    assert item["open_market_value"] == pytest.approx(-75)
    assert item["total_pnl"] == pytest.approx(525)
    assert item["realized_pnl"] is None
    assert item["pnl_completeness"] == "INDICATIVE"
    assert body["summary"]["open_contract_count"] == 1


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_options_state_filter_is_a_common_parameter(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    assert len(_get(brokerage_id, "options", state="open")["items"]) == 1
    assert _get(brokerage_id, "options", state="flat")["items"] == []


def test_two_accounts_holding_the_same_contract_stay_separate(adapter_env):
    """Merging them would imply coverage that crosses an account boundary."""
    _write_snaptrade(
        holdings=[
            {"account_id": "acct-1", "account_name": "Roth IRA",
             "asset_class": "OPTION", "symbol": CONTRACT, "underlying_symbol": "ABC",
             "option_type": "PUT", "strike": "50", "expiry": "2026-08-21",
             "quantity": "-1", "price": "0.50", "cost_basis": "-100",
             "market_value": "-50"},
            {"account_id": "acct-2", "account_name": "BrokerageLink",
             "asset_class": "OPTION", "symbol": CONTRACT, "underlying_symbol": "ABC",
             "option_type": "PUT", "strike": "50", "expiry": "2026-08-21",
             "quantity": "-1", "price": "0.50", "cost_basis": "-100",
             "market_value": "-50"},
        ],
        events=[
            {"id": f"event-{account}", "account_id": account,
             "account": "Roth IRA" if account == "acct-1" else "BrokerageLink",
             "underlying_symbol": "ABC", "option_type": "PUT", "strike": "50",
             "expiry": "2026-08-21", "occ_symbol": CONTRACT,
             "action": "SELL_TO_OPEN", "units": "-1", "net_value": "100",
             "trade_date": "2026-07-01T16:00:00Z"}
            for account in ("acct-1", "acct-2")
        ],
    )
    body = _get("fidelity", "options")
    assert len(body["items"]) == 2
    assert {item["account_id"] for item in body["items"]} == {"acct-1", "acct-2"}
    assert len({item["id"] for item in body["items"]}) == 2
    assert body["summary"]["account_count"] == 2

    narrowed = _get("fidelity", "options", account_id="acct-1")
    assert [item["account_id"] for item in narrowed["items"]] == ["acct-1"]


# ------------------------------------------------- option-adjusted basis ---

def test_option_adjusted_basis_includes_only_matched_symbols(adapter_env):
    write_covered_put("tastytrade")
    body = _get("tastytrade", "option-adjusted-basis")

    assert [item["symbol"] for item in body["items"]] == ["ABC"]
    item = body["items"][0]
    assert item["share_quantity"] == pytest.approx(100)
    assert item["equity_cost"] == pytest.approx(11000)
    assert item["option_pnl"] == pytest.approx(525)
    assert item["net_pnl"] == pytest.approx(1525)
    # (11000 - 525) / 100 — adjusted for option economics only, so the share
    # price move is not counted twice.
    assert item["adjusted_basis"]["marked_per_share"] == pytest.approx(104.75)
    assert item["adjusted_basis"]["completeness"] == "INDICATIVE"
    assert item["adjusted_basis"]["reason"] is None


def test_options_only_symbol_is_absent_from_adjusted_basis(adapter_env):
    _write_tastytrade(positions=[
        {"instrument_type": "Equity Option", "contract_symbol": CONTRACT,
         "underlying_symbol": "ABC", "quantity": "1", "direction": "Short",
         "signed_quantity": "-1", "multiplier": "100", "mark_price": "0.75",
         "average_open_price": "6"},
    ])
    assert _get("tastytrade", "option-adjusted-basis")["items"] == []
    assert _get("tastytrade", "options")["items"]      # still an option position


def test_adjusted_basis_combines_matching_symbols_across_accounts(adapter_env):
    _write_snaptrade(holdings=[
        {"account_id": "acct-1", "account_name": "Roth IRA", "asset_class": "STOCK",
         "symbol": "ABC", "quantity": "100", "price": "120", "cost_basis": "11000",
         "market_value": "12000"},
        {"account_id": "acct-2", "account_name": "BrokerageLink",
         "asset_class": "OPTION", "symbol": CONTRACT, "underlying_symbol": "ABC",
         "option_type": "PUT", "strike": "50", "expiry": "2026-08-21",
         "quantity": "-1", "price": "0.75", "cost_basis": "-600",
         "market_value": "-75"},
    ], events=[
        {"id": "activity-1", "account_id": "acct-2", "account": "BrokerageLink",
         "underlying_symbol": "ABC", "option_type": "PUT", "strike": "50",
         "expiry": "2026-08-21", "occ_symbol": CONTRACT, "action": "SELL_TO_OPEN",
         "units": "-1", "net_value": "600", "trade_date": "2026-07-01T16:00:00Z"},
    ])
    item = _get("fidelity", "option-adjusted-basis")["items"][0]
    assert item["accounts"] == ["BrokerageLink", "Roth IRA"]
    assert item["adjusted_basis"] == {
        "realized_per_share": None,
        "marked_per_share": pytest.approx(104.75),
        "completeness": "INDICATIVE",
        "reason": None,
    }


def test_adjusted_basis_combines_matching_symbols_across_trading_accounts(adapter_env):
    _write_tastytrade(
        positions=[
            {"account": "IRA", "instrument_type": "Equity", "contract_symbol": "ABC",
             "underlying_symbol": "ABC", "quantity": "100", "direction": "Long",
             "signed_quantity": "100", "multiplier": "1", "mark_price": "120",
             "average_open_price": "110"},
            {"account": "Margin", "instrument_type": "Equity Option",
             "contract_symbol": CONTRACT, "underlying_symbol": "ABC", "quantity": "1",
             "direction": "Short", "signed_quantity": "-1", "multiplier": "100",
             "mark_price": "0.75", "average_open_price": "6"},
        ],
        activity=[
            {"id": "tastytrade:Margin:1", "source_transaction_id": "1",
             "account": "Margin", "executed_at": "2026-07-01T16:00:00+00:00",
             "transaction_date": "2026-07-01", "transaction_type": "Trade",
             "transaction_sub_type": "Sell to Open", "instrument_type": "Equity Option",
             "contract_symbol": CONTRACT, "underlying_symbol": "ABC", "action": "Sell to Open",
             "quantity": "1", "position_delta": "-1", "net_value": "600",
             "fee_effect": "-1", "option_type": "PUT", "expiry": "2026-08-21",
             "strike": "50"},
        ],
    )

    item = _get("tastytrade", "option-adjusted-basis")["items"][0]
    assert item["accounts"] == ["IRA", "Margin"]
    assert item["adjusted_basis"] == {
        "realized_per_share": None,
        "marked_per_share": pytest.approx(104.75),
        "completeness": "INDICATIVE",
        "reason": None,
    }


def test_an_unconfirmed_lifecycle_blocks_adjusted_basis_without_naming_a_broker(
        adapter_env):
    _write_snaptrade(
        holdings=[
            {"asset_class": "STOCK", "symbol": "ABC", "quantity": "100",
             "price": "120", "cost_basis": "11000", "market_value": "12000"},
            {"asset_class": "OPTION", "symbol": CONTRACT, "underlying_symbol": "ABC",
             "option_type": "PUT", "strike": "50", "expiry": "2026-08-21",
             "quantity": "-1", "price": "0.75", "cost_basis": "-600",
             "market_value": "-75"},
        ],
        events=[
            {"id": "a1", "underlying_symbol": "ABC", "option_type": "PUT",
             "strike": "50", "expiry": "2026-08-21", "occ_symbol": CONTRACT,
             "action": "SELL_TO_OPEN", "units": "-1", "net_value": "600",
             "trade_date": "2026-07-01T16:00:00Z"},
            {"id": "a2", "underlying_symbol": "ABC", "option_type": "PUT",
             "strike": "50", "expiry": "2026-08-21", "occ_symbol": CONTRACT,
             "action": "ASSIGNMENT", "units": "0", "net_value": "0",
             "trade_date": "2026-07-02T16:00:00Z"},
        ],
    )
    item = _get("fidelity", "option-adjusted-basis")["items"][0]
    assert item["adjusted_basis"]["completeness"] == "UNAVAILABLE"
    assert "unconfirmed" in item["adjusted_basis"]["reason"].lower()
    assert "fidelity" not in item["adjusted_basis"]["reason"].lower()
    assert "snaptrade" not in item["adjusted_basis"]["reason"].lower()


# ------------------------------------------------------------- fail closed ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_a_missing_mark_makes_the_total_null_not_zero(adapter_env, brokerage_id):
    if brokerage_id == "tastytrade":
        _write_tastytrade(positions=[
            {"instrument_type": "Equity", "contract_symbol": "ABC",
             "underlying_symbol": "ABC", "quantity": "100", "direction": "Long",
             "signed_quantity": "100", "multiplier": "1", "mark_price": "",
             "average_open_price": "110"},
        ])
    else:
        _write_snaptrade(holdings=[
            {"asset_class": "STOCK", "symbol": "ABC", "quantity": "100",
             "price": "", "cost_basis": "11000", "market_value": ""},
        ])
    body = _get(brokerage_id, "holdings")
    item = body["items"][0]
    assert item["market_value"] is None
    assert item["unrealized_pnl"] is None
    assert item["pnl_completeness"] == "UNAVAILABLE"
    assert body["summary"]["total_market_value"] is None
    assert body["summary"]["total_unrealized_pnl"] is None
    assert {warning["code"] for warning in body["warnings"]} >= {
        component_projection.CURRENT_EQUITY_MARK
    }


# ---------------------------------------------------------------- parity ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_accounting_matches_the_compatibility_view_it_replaces(adapter_env,
                                                               brokerage_id):
    """The formulas moved; the numbers did not."""
    write_covered_put(brokerage_id)
    legacy = brokerage_ledger.snapshot(LEGACY_PORTFOLIO[brokerage_id])["symbols"][0]
    new = _get(brokerage_id, "option-adjusted-basis")["items"][0]

    assert new["symbol"] == legacy["symbol"]
    assert new["share_quantity"] == pytest.approx(legacy["share_quantity"])
    assert new["equity_cost"] == pytest.approx(legacy["equity_cost"])
    assert new["current_equity"] == pytest.approx(legacy["current_equity"])
    assert new["equity_pnl"] == pytest.approx(legacy["equity_pnl"])
    assert new["option_pnl"] == pytest.approx(legacy["option_pnl"])
    assert new["net_pnl"] == pytest.approx(legacy["net_pnl"])
    assert new["adjusted_basis"]["marked_per_share"] == pytest.approx(
        legacy["option_adjusted_basis_per_share"]
    )


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_component_vocabulary_matches_the_established_one(adapter_env, brokerage_id):
    """Reused rather than reinvented, so no second meaning for `cash_in`."""
    write_covered_put(brokerage_id)
    legacy = brokerage_ledger.snapshot(LEGACY_PORTFOLIO[brokerage_id])["symbols"][0]
    legacy_option = next(
        row for row in legacy["components"] if row["instrument"] == "OPTION"
    )
    new_option = _get(brokerage_id, "options")["items"][0]

    shared = set(legacy_option) - {"annotations"}
    assert shared <= set(new_option)
    for field in ("cash_in", "cash_out", "net_cash_flow", "open_market_value",
                  "total_pnl", "quantity", "strike"):
        assert new_option[field] == pytest.approx(legacy_option[field])
    for field in ("instrument", "side", "option_type", "state",
                  "pnl_completeness", "cash_flow_basis"):
        assert new_option[field] == legacy_option[field]


def test_an_expiration_now_resolves_instead_of_reading_as_a_mismatch(adapter_env):
    """A deliberate improvement over the compatibility view.

    A provider reports an expiration as a removal with no signed delta. The
    legacy combined view sums that as zero movement and concludes the activity
    disagrees with the broker; the common projection infers the closing sign
    from the running position, which is what lets an expired contract archive.
    """
    _write_tastytrade(activity=[
        {"id": "tastytrade:TRADING:1", "source_transaction_id": "1",
         "executed_at": "2026-07-01T16:00:00+00:00", "transaction_date": "2026-07-01",
         "transaction_type": "Trade", "transaction_sub_type": "Sell to Open",
         "instrument_type": "Equity Option", "contract_symbol": CONTRACT,
         "underlying_symbol": "ABC", "action": "Sell to Open", "quantity": "1",
         "position_delta": "-1", "net_value": "600", "option_type": "PUT"},
        {"id": "tastytrade:TRADING:2", "source_transaction_id": "2",
         "executed_at": "2026-07-24T20:00:00+00:00", "transaction_date": "2026-07-24",
         "transaction_type": "Receive Deliver", "transaction_sub_type": "Expiration",
         "instrument_type": "Equity Option", "contract_symbol": CONTRACT,
         "underlying_symbol": "ABC", "action": "Expired", "quantity": "1",
         "position_delta": "", "net_value": "0", "option_type": "PUT"},
    ])

    item = _get("tastytrade", "options")["items"][0]
    assert item["state"] == "FLAT"
    assert item["realized_pnl"] == pytest.approx(600)
    assert item["pnl_completeness"] == "COMPLETE"
    assert component_projection.POSITION_ACTIVITY_MISMATCH not in item["missing"]

    legacy = brokerage_ledger.snapshot("trading")["symbols"][0]["components"][0]
    assert legacy["pnl_completeness"] == "UNAVAILABLE"   # the behavior being replaced


def test_a_genuinely_unexplained_blank_delta_still_fails_closed(adapter_env):
    """Inference is only for lifecycle removals; it is not a general excuse."""
    _write_tastytrade(activity=[
        {"id": "tastytrade:TRADING:1", "source_transaction_id": "1",
         "executed_at": "2026-07-01T16:00:00+00:00", "transaction_date": "2026-07-01",
         "transaction_type": "Trade", "transaction_sub_type": "Sell to Open",
         "instrument_type": "Equity Option", "contract_symbol": CONTRACT,
         "underlying_symbol": "ABC", "action": "Sell to Open", "quantity": "1",
         "position_delta": "", "net_value": "600", "option_type": "PUT"},
    ])
    item = _get("tastytrade", "options")["items"][0]
    assert item["pnl_completeness"] == "UNAVAILABLE"
    assert component_projection.POSITION_ACTIVITY_MISMATCH in item["missing"]


# ------------------------------------------------- reads never call a provider ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_read_routes_consume_materialized_artifacts_only(adapter_env, brokerage_id,
                                                         monkeypatch):
    monkeypatch.setattr(
        options_activity, "fetch_tastytrade",
        lambda *a, **k: pytest.fail("a read route called a provider"),
    )
    write_covered_put(brokerage_id)
    for resource in RESOURCES:
        _get(brokerage_id, resource)


def test_coverage_reports_retained_history_not_claimed_history(adapter_env):
    write_covered_put("tastytrade")
    coverage = _get("tastytrade", "options")["coverage"]
    assert coverage["history_start"] == "2026-07-01"
    assert coverage["reached_provider_boundary"] is None
    assert contracts.PROVIDER_BOUNDARY_UNKNOWN in coverage["reasons"]
    assert coverage["status"] in spec.COVERAGE_STATUSES


def test_the_legacy_contracts_are_untouched_by_the_additive_api(adapter_env):
    write_covered_put("tastytrade")
    write_covered_put("fidelity")
    for path in ("/brokerage-ledgers/trading/combined",
                 "/brokerage-ledgers/retirement/combined",
                 "/options/activity", "/retirement/options"):
        assert client.get(path).status_code == 200
    assert config.options_activity_csv().is_file()
