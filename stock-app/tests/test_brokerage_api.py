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

from app import config, options_activity
from app.brokerages import contracts, registry, trend
from app.brokerages.projections import components as component_projection
from app.brokerages.projections import holdings as holdings_projection
from app.main import app
from tests import brokerage_contract_spec as spec
from tests.test_brokerage_adapters import (CONTRACT, _write_snaptrade,  # noqa: F401
                                           _write_tastytrade, adapter_env,
                                           write_covered_put)

client = TestClient(app)

BROKERAGE_IDS = sorted(registry.REGISTRY)
RESOURCES = ("holdings", "options", "option-adjusted-basis")


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


def test_fidelity_holdings_keep_missing_cost_basis_unknown(adapter_env):
    _write_snaptrade(holdings=[{
        "asset_class": "OTHER", "symbol": "PLAN", "quantity": "10",
        "price": "25", "average_purchase_price": "", "cost_basis": "",
        "market_value": "250", "open_pnl": "", "open_pnl_pct": "",
    }])
    options_activity._atomic_write(
        config.symbol_ledger_gain_loss_snapshots_csv(),
        holdings_projection.SNAPSHOT_HEADERS,
        [{
            "brokerage_id": "fidelity", "sync_date": "2026-07-27",
            "retrieved_at": "2026-07-27T20:00:00Z",
            "captured_at": "2026-07-27T20:05:00Z",
            "account_id": "acct-1", "account": "BrokerageLink",
            "symbol": "PLAN", "gain_loss_pct": "0",
        }],
    )

    body = _get("fidelity", "holdings")
    holding = body["items"][0]
    assert holding["cost_basis"] is None
    assert holding["cost_per_unit"] is None
    assert holding["unrealized_pnl"] is None
    assert holding["unrealized_pnl_pct"] is None
    assert holding["gain_loss_snapshots"] == {}
    assert holding["pnl_completeness"] == "UNAVAILABLE"
    assert body["summary"]["total_cost_basis"] is None
    assert body["summary"]["total_unrealized_pnl"] is None
    assert body["summary"]["total_unrealized_pnl_pct"] is None
    assert component_projection.EQUITY_COST_BASIS in holding["missing"]
    assert component_projection.EQUITY_COST_BASIS in {
        warning["code"] for warning in body["warnings"]
    }

    saved = client.patch(
        "/api/brokerages/fidelity/holdings/PLAN/metadata",
        json={"account_id": "acct-1", "cost_per_unit": 25},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["metadata"]["cost_basis_mode"] == "PER_UNIT"

    overridden = _get("fidelity", "holdings")
    holding = overridden["items"][0]
    assert holding["cost_basis"] == pytest.approx(250)
    assert holding["cost_per_unit"] == pytest.approx(25)
    assert holding["cost_basis_source"] == "USER_OVERRIDE"
    assert holding["cost_basis_override_mode"] == "PER_UNIT"
    assert holding["cash_flow_basis"] == "USER_COST_BASIS"
    assert holding["unrealized_pnl"] == pytest.approx(0)
    assert holding["pnl_completeness"] == "INDICATIVE"
    assert holding["gain_loss_snapshots"] == {}
    assert overridden["summary"]["total_cost_basis"] == pytest.approx(250)
    assert overridden["summary"]["total_unrealized_pnl"] == pytest.approx(0)
    assert component_projection.EQUITY_COST_BASIS not in {
        warning["code"] for warning in overridden["warnings"]
    }

    # A brokerage sync rewrites only immutable position artifacts. The
    # account-specific per-unit basis remains in effect afterward and follows
    # the newly reported quantity.
    _write_snaptrade(holdings=[{
        "asset_class": "OTHER", "symbol": "PLAN", "quantity": "12",
        "price": "25", "average_purchase_price": "", "cost_basis": "",
        "market_value": "300", "open_pnl": "", "open_pnl_pct": "",
    }])
    after_sync = _get("fidelity", "holdings")["items"][0]
    assert after_sync["cost_basis"] == pytest.approx(300)
    assert after_sync["cost_per_unit"] == pytest.approx(25)
    assert after_sync["cost_basis_source"] == "USER_OVERRIDE"

    # If a later sync finally supplies broker basis, immutable provider facts
    # take precedence without deleting the user's fallback metadata.
    _write_snaptrade(holdings=[{
        "asset_class": "OTHER", "symbol": "PLAN", "quantity": "12",
        "price": "25", "average_purchase_price": "27.5", "cost_basis": "330",
        "market_value": "300", "open_pnl": "-30", "open_pnl_pct": "-9.09",
    }])
    broker_basis = _get("fidelity", "holdings")["items"][0]
    assert broker_basis["cost_basis"] == pytest.approx(330)
    assert broker_basis["cost_per_unit"] == pytest.approx(27.5)
    assert broker_basis["cost_basis_source"] == "BROKER"
    assert broker_basis["cost_basis_override_mode"] is None


def test_manual_cost_basis_cannot_replace_broker_basis(adapter_env):
    write_covered_put("fidelity")
    response = client.patch(
        "/api/brokerages/fidelity/holdings/ABC/metadata",
        json={"account_id": "acct-1", "cost_basis": 1},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "COST_BASIS_AVAILABLE"


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


def _write_sold_out_lot(brokerage_id: str) -> None:
    """A share lot that is no longer held, beside one that still is.

    Tastytrade imports the executions, so its closed lot arrives as activity and
    genuinely produced a flat holdings row before this rule existed. SnapTrade
    imports no equity activity and drops a zeroed position at the adapter, so it
    cannot reach that state today; its case pins the shared contract rather than
    reproducing the defect.
    """
    if brokerage_id == "tastytrade":
        _write_tastytrade(
            positions=[
                {"instrument_type": "Equity", "contract_symbol": "XYZ",
                 "underlying_symbol": "XYZ", "quantity": "50", "direction": "Long",
                 "signed_quantity": "50", "multiplier": "1", "mark_price": "20",
                 "average_open_price": "18"},
            ],
            activity=[
                {"id": "tastytrade:TRADING:1", "source_transaction_id": "1",
                 "executed_at": "2026-02-01T16:00:00+00:00",
                 "transaction_date": "2026-02-01", "transaction_type": "Trade",
                 "transaction_sub_type": "Buy to Open", "instrument_type": "Equity",
                 "contract_symbol": "ABC", "underlying_symbol": "ABC",
                 "action": "Buy to Open", "quantity": "100",
                 "position_delta": "100", "net_value": "-11000", "fee_effect": "0"},
                {"id": "tastytrade:TRADING:2", "source_transaction_id": "2",
                 "executed_at": "2026-03-01T16:00:00+00:00",
                 "transaction_date": "2026-03-01", "transaction_type": "Trade",
                 "transaction_sub_type": "Sell to Close", "instrument_type": "Equity",
                 "contract_symbol": "ABC", "underlying_symbol": "ABC",
                 "action": "Sell to Close", "quantity": "100",
                 "position_delta": "-100", "net_value": "12000", "fee_effect": "0"},
            ],
        )
    else:
        _write_snaptrade(holdings=[
            {"asset_class": "STOCK", "symbol": "XYZ", "quantity": "50",
             "price": "20", "average_purchase_price": "18", "cost_basis": "900",
             "market_value": "1000"},
            {"asset_class": "STOCK", "symbol": "ABC", "quantity": "0",
             "price": "120", "average_purchase_price": "110", "cost_basis": "0",
             "market_value": "0"},
        ])


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_holdings_exclude_a_lot_that_is_no_longer_held(adapter_env, brokerage_id):
    """Holdings answers "what do I hold", not "what have I ever held".

    The component projection keeps a sold lot so the Symbol Ledger can report
    its realized cash. Listing it here would show a zero-quantity row and, worse,
    count realized profit as unrealized and subtract it from invested capital.
    """
    _write_sold_out_lot(brokerage_id)
    body = _get(brokerage_id, "holdings")

    assert [item["symbol"] for item in body["items"]] == ["XYZ"]
    assert {item["state"] for item in body["items"]} == {"OPEN"}
    assert body["summary"]["holding_count"] == 1
    assert body["summary"]["total_cost_basis"] == pytest.approx(900)
    assert body["summary"]["total_market_value"] == pytest.approx(1000)
    assert body["summary"]["total_unrealized_pnl"] == pytest.approx(100)


def test_account_value_counts_cash_and_matches_the_legacy_total(adapter_env):
    """Cash-equivalents are holdings and also count toward account value.

    Options stay out of both figures. Proving Holdings and account value agree
    on the cash+equity total keeps the risk cash limit honest.
    """
    _write_snaptrade(holdings=[
        {"asset_class": "STOCK", "symbol": "ABC", "quantity": "100",
         "price": "120", "average_purchase_price": "110", "cost_basis": "11000",
         "market_value": "12000"},
        {"asset_class": "CASH", "symbol": "USD", "quantity": "2500",
         "price": "1", "cost_basis": "2500", "market_value": "2500"},
        {"asset_class": "OPTION", "symbol": CONTRACT, "underlying_symbol": "ABC",
         "option_type": "PUT", "strike": "50", "expiry": "2026-08-21",
         "quantity": "-1", "price": "0.75", "cost_basis": "-600",
         "market_value": "-75"},
    ])
    body = _get("fidelity", "holdings")

    assert [item["symbol"] for item in body["items"]] == ["ABC", "USD"]
    cash = next(item for item in body["items"] if item["symbol"] == "USD")
    assert cash["instrument"] == "CASH"
    assert cash["category"] == "CASH"
    assert cash["market_value"] == pytest.approx(2500)
    assert body["summary"]["total_market_value"] == pytest.approx(14500)
    # Equity 12000 + cash 2500. Options are excluded from both.
    assert body["summary"]["total_account_value"] == pytest.approx(14500)


def test_account_value_is_unknown_when_a_position_has_no_mark(adapter_env):
    """A cash limit built on a partial total would read as a real allowance."""
    _write_snaptrade(holdings=[
        {"asset_class": "STOCK", "symbol": "ABC", "quantity": "100",
         "price": "120", "cost_basis": "11000", "market_value": "12000"},
        {"asset_class": "CASH", "symbol": "USD", "quantity": "2500",
         "price": "1", "cost_basis": "2500", "market_value": ""},
    ])
    assert _get("fidelity", "holdings")["summary"]["total_account_value"] is None


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_holdings_report_each_position_share_and_the_portfolio_return(
        adapter_env, brokerage_id):
    _write_sold_out_lot(brokerage_id)   # one held lot: 900 invested, 1000 marked
    body = _get(brokerage_id, "holdings")

    assert body["items"][0]["pct_of_total"] == pytest.approx(100)
    assert body["summary"]["total_unrealized_pnl_pct"] == pytest.approx(
        100 / 900 * 100
    )


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_position_share_blanks_when_the_portfolio_total_is_unknown(
        adapter_env, brokerage_id):
    """A share of an unknown total is not a number we may invent.

    Legacy divided by whatever it could add up, so one unmarked holding silently
    rebased every other row's % Portfolio on a partial denominator.
    """
    if brokerage_id == "tastytrade":
        _write_tastytrade(positions=[
            {"instrument_type": "Equity", "contract_symbol": "XYZ",
             "underlying_symbol": "XYZ", "quantity": "50", "direction": "Long",
             "signed_quantity": "50", "multiplier": "1", "mark_price": "20",
             "average_open_price": "18"},
            {"instrument_type": "Equity", "contract_symbol": "ABC",
             "underlying_symbol": "ABC", "quantity": "10", "direction": "Long",
             "signed_quantity": "10", "multiplier": "1", "mark_price": "",
             "average_open_price": "110"},
        ])
    else:
        _write_snaptrade(holdings=[
            {"asset_class": "STOCK", "symbol": "XYZ", "quantity": "50",
             "price": "20", "average_purchase_price": "18", "cost_basis": "900",
             "market_value": "1000"},
            {"asset_class": "STOCK", "symbol": "ABC", "quantity": "10",
             "price": "", "average_purchase_price": "110", "cost_basis": "1100",
             "market_value": ""},
        ])
    body = _get(brokerage_id, "holdings")

    assert body["summary"]["total_market_value"] is None
    assert body["summary"]["total_unrealized_pnl_pct"] is None
    assert [item["pct_of_total"] for item in body["items"]] == [None, None]


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_gain_loss_snapshot_records_only_held_lots(adapter_env, brokerage_id):
    """A sold lot has no current gain to snapshot; recording one would put a
    meaningless percentage into the comparison columns forever."""
    _write_sold_out_lot(brokerage_id)
    response = client.post(f"/api/brokerages/{brokerage_id}/holdings/gain-loss-snapshots")
    assert response.status_code == 200, response.text
    assert response.json()["holding_count"] == 1

    rows = holdings_projection.read_snapshots(brokerage_id)
    assert {row["symbol"] for row in rows} == {"XYZ"}


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_capturing_twice_for_one_sync_date_replaces_it(adapter_env, brokerage_id):
    """Two partial captures of one date would compare a row against itself at
    two different moments; the date is replaced wholesale instead."""
    _write_sold_out_lot(brokerage_id)
    url = f"/api/brokerages/{brokerage_id}/holdings/gain-loss-snapshots"
    first = client.post(url).json()
    second = client.post(url).json()

    assert first["replaced"] is False
    assert second["replaced"] is True
    assert second["sync_date"] == first["sync_date"]
    assert second["retained_dates"] == [first["sync_date"]]


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_only_the_three_newest_capture_dates_are_retained(adapter_env, brokerage_id):
    """Three dates is enough to see a trend; more turns a comparison column
    into an unbounded archive."""
    _write_sold_out_lot(brokerage_id)
    path = config.symbol_ledger_gain_loss_snapshots_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "".join(
        f"{brokerage_id},2026-07-{day},2026-07-{day}T20:00:00Z,"
        f"2026-07-{day}T20:05:00Z,acct,Acct,XYZ,5.0\n"
        for day in ("01", "08", "15")
    )
    path.write_text(
        ",".join(holdings_projection.SNAPSHOT_HEADERS) + "\n" + rows,
        encoding="utf-8",
    )
    captured = client.post(
        f"/api/brokerages/{brokerage_id}/holdings/gain-loss-snapshots"
    ).json()

    assert len(captured["retained_dates"]) == 3
    assert "2026-07-01" not in captured["retained_dates"]   # oldest aged out
    assert captured["sync_date"] in captured["retained_dates"]


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_capturing_with_nothing_synced_is_a_safe_refusal(adapter_env, brokerage_id):
    response = client.post(
        f"/api/brokerages/{brokerage_id}/holdings/gain-loss-snapshots"
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "NO_HOLDINGS"
    assert "sync" in detail["message"].lower()


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_editing_one_classification_field_preserves_the_others(adapter_env,
                                                               brokerage_id):
    write_covered_put(brokerage_id)
    base = f"/api/brokerages/{brokerage_id}/holdings/ABC/metadata"
    client.patch(base, json={"category": "growth", "industry": "aviation",
                             "note": "original note"})
    client.patch(base, json={"note": "revised note"})

    holding = _get(brokerage_id, "holdings")["items"][0]
    assert holding["note"] == "revised note"
    assert holding["category"] == "GROWTH"        # untouched by the second edit
    assert holding["industry"] == "AVIATION"


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_a_metadata_edit_that_cannot_be_honored_is_rejected(adapter_env,
                                                            brokerage_id):
    write_covered_put(brokerage_id)
    base = f"/api/brokerages/{brokerage_id}/holdings"
    cases = {
        f"{base}/%20%20/metadata": ({"note": "x"}, "INVALID_SYMBOL"),
        f"{base}/ABC/metadata": ({}, "NOTHING_TO_UPDATE"),
    }
    for url, (payload, code) in cases.items():
        response = client.patch(url, json=payload)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == code

    typed = client.patch(f"{base}/ABC/metadata", json={"category": 42})
    assert typed.status_code == 422
    assert typed.json()["detail"]["code"] == "INVALID_FIELD"

    unknown = client.patch(f"{base}/ABC/metadata", json={"lifecycle": "CLOSED"})
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "UNSUPPORTED_FIELD"


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_captured_percentages_come_back_as_comparison_columns(adapter_env,
                                                              brokerage_id):
    _write_sold_out_lot(brokerage_id)
    captured = client.post(
        f"/api/brokerages/{brokerage_id}/holdings/gain-loss-snapshots"
    ).json()
    body = _get(brokerage_id, "holdings")

    catalog = body["summary"]["gain_loss_snapshots"]
    assert [entry["sync_date"] for entry in catalog] == [captured["sync_date"]]
    held = body["items"][0]
    assert held["gain_loss_snapshots"] == {
        captured["sync_date"]: pytest.approx(100 / 900 * 100)
    }


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_holdings_carry_the_declining_trend_the_sync_recorded(adapter_env,
                                                              brokerage_id):
    """Both brokerages already write this state, with the same columns and the
    same (account, symbol) key, so the projection reads it through the registry
    rather than learning which brokerage it is rendering."""
    _write_sold_out_lot(brokerage_id)
    entry = registry.REGISTRY[brokerage_id]
    account = _get(brokerage_id, "holdings")["items"][0]["account_id"]
    path = entry.holdings_trend_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        ",".join(trend.HEADERS) + "\n"
        f"{account},Legacy,XYZ,22.5,2026-06-01T20:00:00Z,11.1,"
        "2026-07-28T20:00:00Z,true,22.5,2026-06-01T20:00:00Z,11.1,50.6,"
        "2026-07-28T20:00:00Z\n",
        encoding="utf-8",
    )

    recorded = _get(brokerage_id, "holdings")["items"][0]["trend"]
    assert recorded["alert"] is True
    assert recorded["direction"] == "GAIN"       # the lot is up 100 on 900
    assert recorded["peak_pct"] == pytest.approx(22.5)
    assert recorded["from_pct"] == pytest.approx(22.5)
    assert recorded["to_pct"] == pytest.approx(11.1)
    assert recorded["drop_pct"] == pytest.approx(50.6)
    assert recorded["alert_at"] == "2026-07-28T20:00:00Z"


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_a_holding_with_no_recorded_trend_does_not_alert(adapter_env, brokerage_id):
    _write_sold_out_lot(brokerage_id)
    recorded = _get(brokerage_id, "holdings")["items"][0]["trend"]
    assert recorded["alert"] is False
    assert recorded["drop_pct"] is None
    assert recorded["alert_at"] is None




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
def test_adjusted_basis_accounting_for_the_reference_position(adapter_env,
                                                              brokerage_id):
    """100 shares at a 110 cost and a 120 mark, one short put opened for a 600
    credit and marked at 0.75 -- the same economic position both brokerage
    artifact families express. These figures were proven identical to the
    legacy compatibility view before that view was retired; pinned directly
    now that there is nothing left to compare against."""
    write_covered_put(brokerage_id)
    item = _get(brokerage_id, "option-adjusted-basis")["items"][0]

    assert item["symbol"] == "ABC"
    assert item["share_quantity"] == pytest.approx(100)
    assert item["equity_cost"] == pytest.approx(11000)
    assert item["current_equity"] == pytest.approx(12000)
    assert item["equity_pnl"] == pytest.approx(1000)
    assert item["option_pnl"] == pytest.approx(525)
    assert item["net_pnl"] == pytest.approx(1525)
    assert item["adjusted_basis"]["marked_per_share"] == pytest.approx(104.75)


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_option_component_accounting_for_the_reference_position(adapter_env,
                                                                 brokerage_id):
    """The short put half of the same reference position. Pinned directly for
    the same reason as the adjusted-basis figures above."""
    write_covered_put(brokerage_id)
    option = _get(brokerage_id, "options")["items"][0]

    assert option["instrument"] == "OPTION"
    assert option["side"] == "SHORT"
    assert option["option_type"] == "PUT"
    assert option["state"] == "OPEN"
    assert option["pnl_completeness"] == "INDICATIVE"
    assert option["cash_flow_basis"] == "BROKER_ACTIVITY"
    assert option["cash_in"] == pytest.approx(600)
    assert option["cash_out"] == pytest.approx(0)
    assert option["net_cash_flow"] == pytest.approx(600)
    assert option["open_market_value"] == pytest.approx(-75)
    assert option["total_pnl"] == pytest.approx(525)
    assert option["quantity"] == pytest.approx(-1)
    assert option["strike"] == pytest.approx(50)


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
    # The behavior this replaced -- reading the same expiration as
    # UNAVAILABLE / POSITION_ACTIVITY_MISMATCH -- lived in the legacy combined
    # projection, retired with the rest of the compatibility surface.


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


def test_the_additive_api_does_not_disturb_the_activity_artifact(adapter_env):
    """What this protected -- several now-retired legacy routes staying live
    beside the new API -- no longer applies; they are gone on purpose. What
    still matters is that reading the new API is not destructive to the
    artifact underneath it."""
    write_covered_put("tastytrade")
    write_covered_put("fidelity")
    for resource in RESOURCES:
        for brokerage_id in BROKERAGE_IDS:
            _get(brokerage_id, resource)
    assert config.options_activity_csv().is_file()
