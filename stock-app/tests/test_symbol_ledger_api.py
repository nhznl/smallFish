"""The Symbol Ledger: one durable record per (brokerage, symbol).

This is the grouping replacement, so most of these tests are about what must
*not* happen: a symbol must never split into two ledgers, a period must never
close on unproven flatness, a retry must never archive twice, and a reset must
never touch a broker event.

Every scenario runs against both brokerages wherever the behavior is shared,
because the whole point is that the lifecycle no longer depends on which
provider delivered the events.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import config, options_activity
from app.brokerages import registry, store
from app.main import app
from tests.test_brokerage_adapters import (CONTRACT, _write_snaptrade,  # noqa: F401
                                           _write_tastytrade, adapter_env,
                                           write_covered_put)

client = TestClient(app)

BROKERAGE_IDS = sorted(registry.REGISTRY)
SECOND_CONTRACT = "ABC   260821C00060000"


@pytest.fixture(autouse=True)
def ledger_store(adapter_env, monkeypatch):
    """App-owned metadata and boundaries live beside the fixture artifacts."""
    monkeypatch.setenv(
        "SFP_SYMBOL_LEDGER_METADATA", str(adapter_env / "symbol_metadata.csv")
    )
    monkeypatch.setenv(
        "SFP_SYMBOL_LEDGER_ARCHIVES", str(adapter_env / "symbol_archives.csv")
    )
    monkeypatch.setenv(
        "SFP_SYMBOL_LEDGER_GL_SNAPSHOTS", str(adapter_env / "symbol_snapshots.csv")
    )
    return adapter_env


# ------------------------------------------------------------- fixtures ---

def _tastytrade_event(event_id, *, contract=CONTRACT, action="Sell to Open",
                      delta="-1", net_value="600", on="2026-07-01",
                      sub_type=None, quantity="1"):
    return {
        "id": f"tastytrade:TRADING:{event_id}", "source_transaction_id": event_id,
        "executed_at": f"{on}T16:00:00+00:00", "transaction_date": on,
        "transaction_type": "Trade", "transaction_sub_type": sub_type or action,
        "instrument_type": "Equity Option", "contract_symbol": contract,
        "underlying_symbol": "ABC", "action": action, "quantity": quantity,
        "position_delta": delta, "net_value": net_value, "option_type": "PUT",
        "expiry": "2026-08-21", "strike": "50",
    }


def _snaptrade_event(event_id, *, contract=CONTRACT, action="SELL_TO_OPEN",
                     units="-1", net_value="600", on="2026-07-01"):
    return {
        "id": event_id, "underlying_symbol": "ABC", "option_type": "PUT",
        "strike": "50", "expiry": "2026-08-21", "occ_symbol": contract,
        "action": action, "units": units, "net_value": net_value,
        "trade_date": f"{on}T16:00:00Z",
    }


def write_closed_cycle(brokerage_id: str, *, extra=()) -> None:
    """One contract opened for 600 and closed for 450: flat, reconciled, done."""
    if brokerage_id == "tastytrade":
        _write_tastytrade(activity=[
            _tastytrade_event("1"),
            _tastytrade_event("2", action="Buy to Close", delta="1",
                              net_value="-450", on="2026-07-15"),
            *extra,
        ])
    else:
        _write_snaptrade(events=[
            _snaptrade_event("a1"),
            _snaptrade_event("a2", action="BUY_TO_CLOSE", units="1",
                             net_value="-450", on="2026-07-15"),
            *extra,
        ])


def _symbols(brokerage_id, **params):
    response = client.get(f"/api/brokerages/{brokerage_id}/symbols", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _symbol(brokerage_id, symbol="ABC", **params):
    response = client.get(
        f"/api/brokerages/{brokerage_id}/symbols/{symbol}", params=params
    )
    assert response.status_code == 200, response.text
    return response.json()["symbol"]


def _archive(brokerage_id, symbol="ABC", *, version=None, request_id=None,
             note=""):
    ledger = _symbol(brokerage_id, symbol)
    payload = {
        "request_id": request_id or str(uuid.uuid4()),
        "expected_period_version": (
            version or ledger["current_period"]["period_version"]
        ),
        "note": note,
    }
    return client.post(
        f"/api/brokerages/{brokerage_id}/symbols/{symbol}/archives", json=payload
    )


# ------------------------------------------------------------- identity ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_one_ledger_per_symbol_no_matter_how_many_contracts(adapter_env,
                                                            brokerage_id):
    """Two contracts, three events, one row. Never one row per contract."""
    if brokerage_id == "tastytrade":
        _write_tastytrade(activity=[
            _tastytrade_event("1"),
            _tastytrade_event("2", contract=SECOND_CONTRACT, net_value="300",
                              on="2026-07-05"),
            _tastytrade_event("3", action="Buy to Close", delta="1",
                              net_value="-450", on="2026-07-15"),
        ])
    else:
        _write_snaptrade(events=[
            _snaptrade_event("a1"),
            _snaptrade_event("a2", contract=SECOND_CONTRACT, net_value="300",
                             on="2026-07-05"),
            _snaptrade_event("a3", action="BUY_TO_CLOSE", units="1",
                             net_value="-450", on="2026-07-15"),
        ])
    body = _symbols(brokerage_id, state="all")
    assert [item["symbol"] for item in body["items"]] == ["ABC"]
    assert body["items"][0]["current_period"]["event_count"] == 3


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_closing_one_contract_does_not_complete_the_symbol(adapter_env,
                                                           brokerage_id):
    """The second contract is still open, so the symbol is still Active."""
    if brokerage_id == "tastytrade":
        _write_tastytrade(
            positions=[{
                "instrument_type": "Equity Option", "contract_symbol": SECOND_CONTRACT,
                "underlying_symbol": "ABC", "quantity": "1", "direction": "Short",
                "signed_quantity": "-1", "multiplier": "100", "mark_price": "1.10",
                "average_open_price": "3",
            }],
            activity=[
                _tastytrade_event("1"),
                _tastytrade_event("2", action="Buy to Close", delta="1",
                                  net_value="-450", on="2026-07-15"),
                _tastytrade_event("3", contract=SECOND_CONTRACT, net_value="300",
                                  on="2026-07-05"),
            ],
        )
    else:
        _write_snaptrade(
            holdings=[{
                "asset_class": "OPTION", "symbol": SECOND_CONTRACT,
                "underlying_symbol": "ABC", "option_type": "CALL", "strike": "60",
                "expiry": "2026-08-21", "quantity": "-1", "price": "1.10",
                "cost_basis": "-300", "market_value": "-110",
            }],
            events=[
                _snaptrade_event("a1"),
                _snaptrade_event("a2", action="BUY_TO_CLOSE", units="1",
                                 net_value="-450", on="2026-07-15"),
                _snaptrade_event("a3", contract=SECOND_CONTRACT, net_value="300",
                                 on="2026-07-05"),
            ],
        )
    ledger = _symbol(brokerage_id)
    assert ledger["state"] == "ACTIVE"
    assert ledger["reset_eligible"] is False
    assert "SYMBOL_NOT_FLAT" in ledger["reset_blockers"]
    assert ledger["current_period"]["realized_pnl"] is None


def test_a_symbol_with_a_dot_survives_url_encoding(adapter_env):
    _write_tastytrade(positions=[{
        "instrument_type": "Equity", "contract_symbol": "BRK.B",
        "underlying_symbol": "BRK.B", "quantity": "10", "direction": "Long",
        "signed_quantity": "10", "multiplier": "1", "mark_price": "480",
        "average_open_price": "400",
    }])
    assert _symbol("tastytrade", "BRK.B")["symbol"] == "BRK.B"
    # Lookup is case-insensitive; the ledger is keyed by the normalized symbol.
    assert _symbol("tastytrade", "brk.b")["symbol"] == "BRK.B"


def test_an_unknown_symbol_is_a_safe_404(adapter_env):
    write_closed_cycle("tastytrade")
    response = client.get("/api/brokerages/tastytrade/symbols/NOPE")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "UNKNOWN_SYMBOL"


# ------------------------------------------------------------ lifecycle ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_a_flat_reconciled_symbol_archives_without_a_metadata_write(adapter_env,
                                                                    brokerage_id):
    write_closed_cycle(brokerage_id)
    ledger = _symbol(brokerage_id)
    assert ledger["state"] == "ARCHIVED"
    assert ledger["reconciliation_status"] == "RECONCILED"
    assert ledger["pnl_completeness"] == "COMPLETE"
    assert ledger["current_period"]["realized_pnl"] == pytest.approx(150)
    assert ledger["warnings"] == []
    # Derived, not stored: no metadata file was written to get here.
    assert not config.symbol_ledger_metadata_csv().is_file()


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_an_open_position_makes_the_symbol_active(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    ledger = _symbol(brokerage_id)
    assert ledger["state"] == "ACTIVE"
    assert ledger["pnl_completeness"] == "INDICATIVE"
    assert ledger["exposure"] == "EQUITY_AND_OPTIONS"
    assert ledger["current_period"]["open_market_value"] == pytest.approx(11925)


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_unprovable_flatness_stays_active_and_says_why(adapter_env, brokerage_id):
    """The delayed-close window: the position left the feed but its closing
    event has not posted. Presenting this as a completed archive is exactly the
    error this design exists to prevent."""
    if brokerage_id == "tastytrade":
        _write_tastytrade(activity=[_tastytrade_event("1")])
    else:
        _write_snaptrade(events=[_snaptrade_event("a1")])

    ledger = _symbol(brokerage_id)
    assert ledger["state"] == "ACTIVE"
    assert ledger["reconciliation_status"] == "UNRECONCILED"
    assert ledger["pnl_completeness"] == "UNAVAILABLE"
    assert ledger["current_period"]["total_pnl"] is None
    assert ledger["warnings"]
    assert ledger["reset_eligible"] is False
    assert set(ledger["reset_blockers"]) >= {"SYMBOL_NOT_RECONCILED", "PERIOD_INCOMPLETE"}


def test_an_unconfirmed_provider_lifecycle_blocks_completion(adapter_env):
    _write_snaptrade(events=[
        _snaptrade_event("a1"),
        _snaptrade_event("a2", action="ASSIGNMENT", units="1", net_value="0",
                         on="2026-07-15"),
    ])
    ledger = _symbol("fidelity")
    assert ledger["state"] == "ACTIVE"           # fails toward Active
    assert ledger["pnl_completeness"] == "UNAVAILABLE"
    assert any("unconfirmed" in warning.lower() for warning in ledger["warnings"])


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_state_filter_defaults_to_active(adapter_env, brokerage_id):
    write_closed_cycle(brokerage_id)
    assert _symbols(brokerage_id)["items"] == []                 # default active
    assert len(_symbols(brokerage_id, state="archived")["items"]) == 1
    assert len(_symbols(brokerage_id, state="all")["items"]) == 1
    summary = _symbols(brokerage_id, state="all")["summary"]
    assert summary["active_count"] == 0
    assert summary["archived_count"] == 1


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_options_exposure_filter_excludes_equity_only_ledgers(adapter_env,
                                                              brokerage_id):
    if brokerage_id == "tastytrade":
        _write_tastytrade(
            positions=[
                {"instrument_type": "Equity Option", "contract_symbol": CONTRACT,
                 "underlying_symbol": "ABC", "quantity": "1", "direction": "Short",
                 "signed_quantity": "-1", "multiplier": "100", "mark_price": "0.75",
                 "average_open_price": "6"},
                {"instrument_type": "Equity", "contract_symbol": "XYZ",
                 "underlying_symbol": "XYZ", "quantity": "10", "direction": "Long",
                 "signed_quantity": "10", "multiplier": "1", "mark_price": "55",
                 "average_open_price": "50"},
            ],
            activity=[_tastytrade_event("1")],
        )
    else:
        _write_snaptrade(
            holdings=[
                {"asset_class": "OPTION", "symbol": CONTRACT,
                 "underlying_symbol": "ABC", "option_type": "PUT", "strike": "50",
                 "expiry": "2026-08-21", "quantity": "-1", "price": "0.75",
                 "average_purchase_price": "6", "cost_basis": "-600",
                 "market_value": "-75"},
                {"asset_class": "STOCK", "symbol": "XYZ", "quantity": "10",
                 "price": "55", "average_purchase_price": "50",
                 "cost_basis": "500", "market_value": "550"},
            ],
            events=[_snaptrade_event("a1")],
        )

    unfiltered = _symbols(brokerage_id, state="all")
    assert [row["symbol"] for row in unfiltered["items"]] == ["ABC", "XYZ"]

    options = _symbols(brokerage_id, state="all", exposure="options")
    assert [row["symbol"] for row in options["items"]] == ["ABC"]
    assert options["summary"]["symbol_count"] == 1
    assert options["summary"]["active_count"] == 1
    assert options["summary"]["archived_count"] == 0


# ------------------------------------------------------------ cross-year ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_a_cross_year_open_and_close_are_one_period(adapter_env, brokerage_id):
    """A calendar year is a fetch window, never a ledger boundary."""
    if brokerage_id == "tastytrade":
        _write_tastytrade(activity=[
            _tastytrade_event("1", on="2025-11-21"),
            _tastytrade_event("2", action="Buy to Close", delta="1",
                              net_value="-450", on="2026-02-13"),
        ])
    else:
        _write_snaptrade(events=[
            _snaptrade_event("a1", on="2025-11-21"),
            _snaptrade_event("a2", action="BUY_TO_CLOSE", units="1",
                             net_value="-450", on="2026-02-13"),
        ])
    ledger = _symbol(brokerage_id)
    assert ledger["current_period"]["event_count"] == 2
    assert ledger["current_period"]["first_event_at"].startswith("2025-11-21")
    assert ledger["current_period"]["realized_pnl"] == pytest.approx(150)
    assert ledger["archived_period_count"] == 0


# ---------------------------------------------------------------- notes ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_notes_are_the_only_editable_field(adapter_env, brokerage_id):
    write_closed_cycle(brokerage_id)
    ok = client.patch(
        f"/api/brokerages/{brokerage_id}/symbols/ABC",
        json={"notes": "watch assignment history"},
    )
    assert ok.status_code == 200
    assert ok.json()["symbol"]["notes"] == "watch assignment history"
    assert _symbol(brokerage_id)["notes"] == "watch assignment history"

    for payload in ({"state": "ARCHIVED"}, {"symbol": "XYZ"},
                    {"lifetime_pnl": 100}, {}):
        rejected = client.patch(
            f"/api/brokerages/{brokerage_id}/symbols/ABC", json=payload
        )
        assert rejected.status_code == 422, payload
        assert rejected.json()["detail"]["code"] in {
            "UNSUPPORTED_FIELD", "NOTHING_TO_UPDATE"
        }
    # The rejected patches changed nothing.
    assert _symbol(brokerage_id)["state"] == "ARCHIVED"


def test_notes_do_not_leak_between_brokerages(adapter_env):
    write_closed_cycle("tastytrade")
    write_closed_cycle("fidelity")
    client.patch("/api/brokerages/tastytrade/symbols/ABC", json={"notes": "mine"})
    assert _symbol("tastytrade")["notes"] == "mine"
    assert _symbol("fidelity")["notes"] == ""


# --------------------------------------------------------------- events ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_events_are_immutable_history_without_group_identity(adapter_env,
                                                             brokerage_id):
    write_closed_cycle(brokerage_id)
    body = client.get(
        f"/api/brokerages/{brokerage_id}/symbols/ABC/events"
    ).json()
    assert body["total_event_count"] == 2
    assert [item["action"] for item in body["items"]] == [
        "BUY_TO_CLOSE", "SELL_TO_OPEN"      # newest first
    ]
    event = body["items"][0]
    assert event["provider_event_id"]
    assert event["net_cash_flow"] == pytest.approx(-450)
    assert event["symbol"] == "ABC"
    assert "group_id" not in event
    assert "group_name" not in event


def test_pagination_uses_an_opaque_identity_cursor(adapter_env):
    _write_tastytrade(activity=[
        _tastytrade_event(str(index), on=f"2026-07-{index:02d}")
        for index in range(1, 6)
    ])
    first = client.get(
        "/api/brokerages/tastytrade/symbols/ABC/events", params={"limit": 2}
    ).json()
    assert len(first["items"]) == 2
    assert first["has_more"] is True
    assert first["next_cursor"]

    second = client.get(
        "/api/brokerages/tastytrade/symbols/ABC/events",
        params={"limit": 2, "cursor": first["next_cursor"]},
    ).json()
    assert len(second["items"]) == 2
    ids = [item["provider_event_id"] for item in first["items"] + second["items"]]
    assert len(set(ids)) == 4               # no repeats across the boundary

    bad = client.get(
        "/api/brokerages/tastytrade/symbols/ABC/events",
        params={"cursor": "not-a-cursor"},
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "INVALID_CURSOR"


def test_a_backdated_event_does_not_shift_a_page(adapter_env):
    """An offset would skip a row here; an identity cursor does not."""
    _write_tastytrade(activity=[
        _tastytrade_event(str(index), on=f"2026-07-{index * 2:02d}")
        for index in range(1, 5)
    ])
    first = client.get(
        "/api/brokerages/tastytrade/symbols/ABC/events", params={"limit": 2}
    ).json()
    seen = {item["provider_event_id"] for item in first["items"]}

    _write_tastytrade(activity=[
        *[_tastytrade_event(str(index), on=f"2026-07-{index * 2:02d}")
          for index in range(1, 5)],
        _tastytrade_event("99", on="2026-07-01"),   # backdated, arrives late
    ])
    second = client.get(
        "/api/brokerages/tastytrade/symbols/ABC/events",
        params={"limit": 2, "cursor": first["next_cursor"]},
    ).json()
    assert not seen & {item["provider_event_id"] for item in second["items"]}


# ---------------------------------------------------------------- reset ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_reset_seals_the_period_and_leaves_broker_events_alone(adapter_env,
                                                               brokerage_id):
    write_closed_cycle(brokerage_id)
    before = client.get(
        f"/api/brokerages/{brokerage_id}/symbols/ABC/events", params={"period": "all"}
    ).json()["total_event_count"]

    created = _archive(brokerage_id, note="Reset after completed strategy")
    assert created.status_code == 201, created.text
    body = created.json()
    archive = body["archive"]
    assert archive["realized_pnl"] == pytest.approx(150)
    assert archive["event_count"] == 2
    assert archive["verification_status"] == "VERIFIED"
    assert archive["pnl_completeness"] == "COMPLETE"
    assert archive["note"] == "Reset after completed strategy"

    refreshed = body["symbol"]
    assert refreshed["current_period"]["event_count"] == 0
    assert refreshed["current_period"]["total_pnl"] == pytest.approx(0)
    assert refreshed["archived_period_count"] == 1
    assert refreshed["archived_pnl"] == pytest.approx(150)
    assert refreshed["lifetime_pnl"] == pytest.approx(150)
    assert refreshed["state"] == "ARCHIVED"

    # Broker history is untouched: every event is still readable.
    after = client.get(
        f"/api/brokerages/{brokerage_id}/symbols/ABC/events", params={"period": "all"}
    ).json()["total_event_count"]
    assert after == before == 2
    assert client.get(
        f"/api/brokerages/{brokerage_id}/symbols/ABC/events",
        params={"period": "current"},
    ).json()["total_event_count"] == 0


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_retrying_the_same_request_id_returns_the_original_archive(adapter_env,
                                                                   brokerage_id):
    write_closed_cycle(brokerage_id)
    request_id = str(uuid.uuid4())
    first = _archive(brokerage_id, request_id=request_id)
    assert first.status_code == 201

    retry = _archive(brokerage_id, request_id=request_id, version="stale-anyway")
    assert retry.status_code == 200
    assert retry.json()["archive"]["archive_id"] == first.json()["archive"]["archive_id"]
    assert _symbol(brokerage_id)["archived_period_count"] == 1


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_a_period_that_changed_since_it_was_loaded_conflicts(adapter_env,
                                                             brokerage_id):
    write_closed_cycle(brokerage_id)
    stale = _symbol(brokerage_id)["current_period"]["period_version"]

    # A sync lands between load and reset.
    write_closed_cycle(brokerage_id, extra=[
        _tastytrade_event("3", net_value="120", on="2026-07-20", delta="-1")
        if brokerage_id == "tastytrade"
        else _snaptrade_event("a3", net_value="120", units="-1", on="2026-07-20")
    ])
    assert _symbol(brokerage_id)["current_period"]["period_version"] != stale

    response = _archive(brokerage_id, version=stale)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PERIOD_CHANGED"
    assert _symbol(brokerage_id)["archived_period_count"] == 0


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_an_open_symbol_cannot_be_reset(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    response = _archive(brokerage_id)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "SYMBOL_NOT_FLAT"
    assert "ABC" in detail["message"]


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_an_unreconciled_symbol_cannot_be_reset(adapter_env, brokerage_id):
    if brokerage_id == "tastytrade":
        _write_tastytrade(activity=[_tastytrade_event("1")])
    else:
        _write_snaptrade(events=[_snaptrade_event("a1")])
    response = _archive(brokerage_id)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] in {
        "SYMBOL_NOT_FLAT", "SYMBOL_NOT_RECONCILED", "PERIOD_INCOMPLETE"
    }


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_an_empty_period_cannot_be_reset_twice(adapter_env, brokerage_id):
    write_closed_cycle(brokerage_id)
    assert _archive(brokerage_id).status_code == 201
    again = _archive(brokerage_id)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "PERIOD_EMPTY"


def test_reset_requires_a_request_id_and_an_expected_version(adapter_env):
    write_closed_cycle("tastytrade")
    version = _symbol("tastytrade")["current_period"]["period_version"]

    missing_id = client.post(
        "/api/brokerages/tastytrade/symbols/ABC/archives",
        json={"expected_period_version": version},
    )
    assert missing_id.status_code == 422
    assert missing_id.json()["detail"]["code"] == "MISSING_REQUEST_ID"

    missing_version = client.post(
        "/api/brokerages/tastytrade/symbols/ABC/archives",
        json={"request_id": str(uuid.uuid4())},
    )
    assert missing_version.status_code == 422
    assert missing_version.json()["detail"]["code"] == "MISSING_PERIOD_VERSION"
    assert _symbol("tastytrade")["archived_period_count"] == 0


# ------------------------------------------------------- after the reset ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_reopening_returns_the_same_ledger_to_active_and_keeps_the_tally(
        adapter_env, brokerage_id):
    """A new trade does not create a second ledger or an implicit reset."""
    write_closed_cycle(brokerage_id)
    assert _archive(brokerage_id).status_code == 201

    reopen = (
        _tastytrade_event("3", net_value="200", on="2026-08-01")
        if brokerage_id == "tastytrade"
        else _snaptrade_event("a3", net_value="200", on="2026-08-01")
    )
    write_closed_cycle(brokerage_id, extra=[reopen])

    body = _symbols(brokerage_id, state="all")
    assert len(body["items"]) == 1              # still exactly one ledger
    ledger = _symbol(brokerage_id)
    assert ledger["state"] == "ACTIVE"
    assert ledger["current_period"]["event_count"] == 1
    assert ledger["archived_period_count"] == 1
    assert ledger["archived_pnl"] == pytest.approx(150)


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_a_backdated_event_joins_the_period_it_belongs_to(adapter_env,
                                                          brokerage_id):
    """Provider facts stay authoritative after a reset.

    A boundary is a chronological cut, so an event that executed before it
    belongs to the sealed period on the next read — and the archive says its
    values were recomputed rather than pretending nothing moved.
    """
    write_closed_cycle(brokerage_id)
    created = _archive(brokerage_id)
    assert created.status_code == 201
    archive_id = created.json()["archive"]["archive_id"]

    late = (
        _tastytrade_event("3", net_value="-25", on="2026-07-10", delta="0")
        if brokerage_id == "tastytrade"
        else _snaptrade_event("a3", net_value="-25", units="0", on="2026-07-10")
    )
    write_closed_cycle(brokerage_id, extra=[late])

    archive = client.get(
        f"/api/brokerages/{brokerage_id}/symbols/ABC/archives/{archive_id}"
    ).json()["archive"]
    assert archive["verification_status"] == "CHANGED"
    assert archive["event_count"] == 3
    assert archive["realized_pnl"] == pytest.approx(125)     # 150 - 25
    assert archive["warnings"]

    ledger = _symbol(brokerage_id)
    assert ledger["current_period"]["event_count"] == 0      # not the current one
    assert ledger["archived_pnl"] == pytest.approx(125)
    assert client.get(
        f"/api/brokerages/{brokerage_id}/symbols/ABC/events",
        params={"period": archive_id},
    ).json()["total_event_count"] == 3


def test_archive_reads_list_and_detail(adapter_env):
    write_closed_cycle("tastytrade")
    created = _archive("tastytrade", note="first period")
    archive_id = created.json()["archive"]["archive_id"]

    listing = client.get(
        "/api/brokerages/tastytrade/symbols/ABC/archives"
    ).json()
    assert [row["archive_id"] for row in listing["items"]] == [archive_id]
    assert listing["summary"]["archived_pnl"] == pytest.approx(150)

    detail = client.get(
        f"/api/brokerages/tastytrade/symbols/ABC/archives/{archive_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["archive"]["note"] == "first period"

    missing = client.get(
        "/api/brokerages/tastytrade/symbols/ABC/archives/archive:nope"
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "UNKNOWN_ARCHIVE"


def test_a_reset_writes_a_boundary_and_never_a_broker_event(adapter_env):
    write_closed_cycle("tastytrade")
    events_before = config.options_activity_csv().read_bytes()
    assert _archive("tastytrade").status_code == 201

    assert config.options_activity_csv().read_bytes() == events_before
    boundaries = store.read_archives("tastytrade", "ABC")
    assert len(boundaries) == 1
    assert boundaries[0].event_count_at_creation == 2
    assert boundaries[0].boundary_event_id            # deterministic cut
    assert boundaries[0].event_set_hash_at_creation


# --------------------------------------------------------------- migration ---

def test_the_migration_report_reads_groups_without_changing_them(adapter_env):
    write_closed_cycle("tastytrade")
    options_activity._atomic_write(
        config.options_groups_csv(), options_activity.GROUP_HEADERS,
        [
            {"group_id": "g1", "account": "TRADING", "symbol": "ABC",
             "name": "ABC 2026", "status": "ACTIVE", "notes": "wheel campaign",
             "auto_created": "true", "created_at": "", "updated_at": ""},
            {"group_id": "g2", "account": "RETIREMENT", "symbol": "XYZ",
             "name": "XYZ 2026", "status": "ACTIVE", "notes": "note one",
             "auto_created": "true", "created_at": "", "updated_at": ""},
            {"group_id": "g3", "account": "RETIREMENT", "symbol": "XYZ",
             "name": "XYZ 2025", "status": "ARCHIVED", "notes": "note two",
             "auto_created": "true", "created_at": "", "updated_at": ""},
        ],
    )
    from app.brokerages import migration

    groups_before = config.options_groups_csv().read_bytes()
    report = migration.report()

    ready = {(row["brokerage_id"], row["symbol"]) for row in report["ready"]}
    assert ready == {("tastytrade", "ABC")}
    conflicts = {(row["brokerage_id"], row["symbol"]) for row in report["conflicts"]}
    assert conflicts == {("fidelity", "XYZ")}      # two notes, no automatic winner
    assert report["summary"]["migrates"] == ["notes"]
    assert config.options_groups_csv().read_bytes() == groups_before

    migration.migrate()
    assert _symbol("tastytrade")["notes"] == "wheel campaign"
    # The conflict was reported, not guessed at, and nothing was discarded.
    assert config.options_groups_csv().read_bytes() == groups_before
    assert {row["symbol"] for row in migration.report()["conflicts"]} == {"XYZ"}


# -------------------------------------------------------------------- sync ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_sync_takes_common_resource_names(adapter_env, brokerage_id, monkeypatch):
    calls: list[str] = []

    def fake(name):
        def command():
            calls.append(name)
            return {"events_inserted": 1, "retrieved_at": "2026-07-28T00:00:00Z"}
        return command

    entry = registry.REGISTRY[brokerage_id]
    monkeypatch.setitem(
        registry.REGISTRY, brokerage_id,
        type(entry)(
            descriptor=entry.descriptor, capabilities=entry.capabilities,
            factory=entry.factory,
            holdings_metadata_path=entry.holdings_metadata_path,
            sync_commands={name: fake(name) for name in ("HOLDINGS", "ACTIVITY")},
        ),
    )
    body = client.post(
        f"/api/brokerages/{brokerage_id}/sync", json={"resources": ["HOLDINGS"]}
    ).json()
    assert [row["resource"] for row in body["results"]] == ["HOLDINGS"]
    assert body["results"][0]["status"] == "OK"
    assert calls == ["HOLDINGS"]

    # Omitting resources requests everything configured; MARKET_DATA is absent
    # for this brokerage and says so rather than failing the request.
    everything = client.post(f"/api/brokerages/{brokerage_id}/sync", json={}).json()
    statuses = {row["resource"]: row["status"] for row in everything["results"]}
    assert statuses == {"HOLDINGS": "OK", "ACTIVITY": "OK", "MARKET_DATA": "UNSUPPORTED"}


def test_a_failing_sync_reports_the_type_and_keeps_the_detail_in_the_log(adapter_env,
                                                                         monkeypatch):
    def boom():
        raise RuntimeError("token abc123 rejected by provider")

    entry = registry.REGISTRY["tastytrade"]
    monkeypatch.setitem(
        registry.REGISTRY, "tastytrade",
        type(entry)(
            descriptor=entry.descriptor, capabilities=entry.capabilities,
            factory=entry.factory,
            holdings_metadata_path=entry.holdings_metadata_path,
            sync_commands={"HOLDINGS": boom},
        ),
    )
    body = client.post(
        "/api/brokerages/tastytrade/sync", json={"resources": ["HOLDINGS"]}
    ).json()
    assert body["results"][0]["status"] == "FAILED"
    assert "RuntimeError" in body["results"][0]["warnings"][0]
    assert "abc123" not in str(body)


def test_sync_rejects_an_unknown_resource(adapter_env):
    response = client.post(
        "/api/brokerages/tastytrade/sync", json={"resources": ["EVERYTHING"]}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_RESOURCES"


def test_sync_results_are_visible_without_a_membership_write(adapter_env):
    """A new event reaches the ledger by its underlying alone."""
    assert _symbols("tastytrade", state="all")["items"] == []
    _write_tastytrade(activity=[_tastytrade_event("1")])
    assert [item["symbol"] for item in _symbols("tastytrade", state="all")["items"]] == ["ABC"]
    assert not config.options_group_members_csv().is_file()


# ------------------------------------------------------- holdings metadata ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_holdings_metadata_patch_edits_only_the_app_owned_file(adapter_env,
                                                               brokerage_id):
    write_covered_put(brokerage_id)
    response = client.patch(
        f"/api/brokerages/{brokerage_id}/holdings/ABC/metadata",
        json={"category": "growth", "note": "core position"},
    )
    assert response.status_code == 200
    assert response.json()["metadata"]["category"] == "GROWTH"

    holding = client.get(
        f"/api/brokerages/{brokerage_id}/holdings"
    ).json()["items"][0]
    assert holding["category"] == "GROWTH"
    assert holding["note"] == "core position"

    rejected = client.patch(
        f"/api/brokerages/{brokerage_id}/holdings/ABC/metadata",
        json={"market_value": 1},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "UNSUPPORTED_FIELD"


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_gain_loss_snapshots_capture_and_replace_by_sync_date(adapter_env,
                                                              brokerage_id):
    write_covered_put(brokerage_id)
    first = client.post(
        f"/api/brokerages/{brokerage_id}/holdings/gain-loss-snapshots"
    )
    assert first.status_code == 200
    assert first.json()["holding_count"] == 1
    assert first.json()["replaced"] is False

    again = client.post(
        f"/api/brokerages/{brokerage_id}/holdings/gain-loss-snapshots"
    ).json()
    assert again["replaced"] is True
    assert again["retained_dates"] == first.json()["retained_dates"]


def test_snapshot_without_holdings_is_a_conflict_not_an_empty_capture(adapter_env):
    _write_tastytrade()
    response = client.post(
        "/api/brokerages/tastytrade/holdings/gain-loss-snapshots"
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_HOLDINGS"


# --------------------------------------------------------- no regressions ---

def test_every_legacy_brokerage_route_still_answers(adapter_env):
    write_covered_put("tastytrade")
    write_covered_put("fidelity")
    for path in ("/options", "/options/activity", "/retirement/options",
                 "/retirement/portfolio/live",
                 "/brokerage-ledgers/trading/combined",
                 "/brokerage-ledgers/retirement/combined",
                 "/brokerage-ledgers/trading/holdings",
                 "/brokerage-ledgers/retirement/holdings"):
        assert client.get(path).status_code == 200, path


def test_symbol_ledger_reads_never_call_a_provider(adapter_env, monkeypatch):
    monkeypatch.setattr(
        options_activity, "fetch_tastytrade",
        lambda *a, **k: pytest.fail("a read route called a provider"),
    )
    write_closed_cycle("tastytrade")
    _symbols("tastytrade", state="all")
    _symbol("tastytrade")
    client.get("/api/brokerages/tastytrade/symbols/ABC/events")
    client.get("/api/brokerages/tastytrade/symbols/ABC/archives")


# ------------------------------------- history that predates the fetch window ---

def _expiration_event(event_id, *, on="2026-04-17"):
    """A Tastytrade expiration: a removal with no signed position delta."""
    return {
        "id": f"tastytrade:TRADING:{event_id}", "source_transaction_id": event_id,
        "executed_at": f"{on}T20:00:00+00:00", "transaction_date": on,
        "transaction_type": "Receive Deliver", "transaction_sub_type": "Expiration",
        "instrument_type": "Equity Option", "contract_symbol": CONTRACT,
        "underlying_symbol": "ABC", "action": "Expired", "quantity": "1",
        "position_delta": "", "net_value": "0", "option_type": "PUT",
    }


def _equity_event(event_id, *, action="Sell to Close", delta="-100",
                  net_value="-900", on="2026-04-18"):
    return {
        "id": f"tastytrade:TRADING:{event_id}", "source_transaction_id": event_id,
        "executed_at": f"{on}T15:00:00+00:00", "transaction_date": on,
        "transaction_type": "Trade", "transaction_sub_type": action,
        "instrument_type": "Equity", "contract_symbol": "ABC",
        "underlying_symbol": "ABC", "action": action, "quantity": "100",
        "position_delta": delta, "net_value": net_value,
    }


def test_an_expiration_whose_opening_trade_predates_the_window_is_not_a_mismatch(
        adapter_env):
    """A contract can expire without its opening trade being in retained history.

    A removal cannot take more than is held, so with nothing open it moves the
    position by zero. Treating that as an unexplained delta reported a symbol as
    unreconciled when the broker and the ledger actually agreed — and no manual
    reconciliation could ever clear it, because there was nothing wrong.
    """
    _write_tastytrade(activity=[_expiration_event("1")])

    ledger = _symbol("tastytrade")
    assert ledger["reconciliation_status"] == "RECONCILED"
    component = ledger["components"][0]
    assert component["state"] == "FLAT"
    assert "POSITION_ACTIVITY_MISMATCH" not in component["missing"]


def test_a_share_round_trip_inside_retained_history_is_complete(adapter_env):
    """Shares bought and sold within the imported window are a whole lifecycle.

    Its executions account for the position exactly, so its cash is real and the
    symbol's total is complete. Refusing to count it merely because the shares
    are gone would hide a result this ledger genuinely has.
    """
    _write_tastytrade(activity=[
        _expiration_event("1"),
        _equity_event("2", action="Buy to Open", delta="100", net_value="-1000",
                      on="2026-04-02"),
        _equity_event("3", action="Sell to Close", delta="-100", net_value="1400",
                      on="2026-04-18"),
    ])

    ledger = _symbol("tastytrade")
    assert ledger["reconciliation_status"] == "RECONCILED"
    assert ledger["pnl_completeness"] == "COMPLETE"
    assert ledger["state"] == "ARCHIVED"
    assert ledger["warnings"] == []
    assert ledger["current_period"]["realized_pnl"] == pytest.approx(400)
    equity = next(row for row in ledger["components"] if row["instrument"] == "EQUITY")
    assert equity["cash_flow_basis"] == "BROKER_ACTIVITY"
    assert equity["state"] == "FLAT"


def test_an_unbalanced_share_lot_is_a_reconciliation_gap_a_manual_row_can_close(
        adapter_env):
    """The JOBY shape: an expired option, a share sale, and no position left.

    The sale has no matching purchase in retained history, so the ledger reads a
    short share position the broker does not have. That is a reconciliation gap
    with a stated cause and a remedy — not a permanent property of the symbol —
    and entering the missing opening trade resolves it and releases its cash.
    """
    _write_tastytrade(activity=[_expiration_event("1"), _equity_event("2")])

    before = _symbol("tastytrade")
    assert before["reconciliation_status"] == "UNRECONCILED"
    assert before["pnl_completeness"] == "UNAVAILABLE"
    assert before["state"] == "ACTIVE"          # uncertainty fails toward Active
    assert any("reconcile" in warning.lower() for warning in before["warnings"])
    assert "SYMBOL_NOT_RECONCILED" in before["reset_blockers"]

    _write_tastytrade(activity=[
        _expiration_event("1"), _equity_event("2"), _manual_equity_row(),
    ])

    after = _symbol("tastytrade")
    assert after["reconciliation_status"] == "RECONCILED"
    assert after["pnl_completeness"] == "COMPLETE"
    assert after["state"] == "ARCHIVED"
    assert after["warnings"] == []
    # -900 share sale plus the -1300 opening cost the manual row supplies.
    assert after["current_period"]["realized_pnl"] == pytest.approx(-2200)


def test_shares_still_held_are_valued_from_the_broker_not_a_partial_window(
        adapter_env):
    """A window that starts after the opening lots is missing history, not a
    disagreement with the broker: the position is real and the broker's cost
    basis still values it."""
    _write_tastytrade(
        positions=[{
            "instrument_type": "Equity", "contract_symbol": "ABC",
            "underlying_symbol": "ABC", "quantity": "300", "direction": "Long",
            "signed_quantity": "300", "multiplier": "1", "mark_price": "12",
            "average_open_price": "10",
        }],
        activity=[_equity_event("2", action="Buy to Open", delta="100",
                                net_value="-1000")],
    )
    ledger = _symbol("tastytrade")
    equity = next(row for row in ledger["components"] if row["instrument"] == "EQUITY")
    assert equity["cash_flow_basis"] == "POSITION_COST_BASIS"
    assert equity["net_cash_flow"] == pytest.approx(-3000)   # broker cost, not -1000
    assert "POSITION_ACTIVITY_MISMATCH" not in equity["missing"]
    assert "EQUITY_ACTIVITY_HISTORY" in equity["missing"]
    assert ledger["reconciliation_status"] == "RECONCILED"
    assert ledger["pnl_completeness"] == "INDICATIVE"


def test_a_manual_reconciliation_row_is_retained_and_identified(adapter_env):
    """A user's correction is a first-class event, not a hidden adjustment."""
    _write_tastytrade(activity=[
        _expiration_event("1"),
        _equity_event("2"),
        _manual_equity_row(),
    ])
    events = client.get(
        "/api/brokerages/tastytrade/symbols/ABC/events", params={"period": "all"}
    ).json()["items"]
    manual = [row for row in events if row["is_manual_reconciliation"]]
    assert len(manual) == 1
    assert manual[0]["action"] == "MANUAL_ADJUSTMENT"
    assert manual[0]["quantity_delta"] == pytest.approx(100)


def test_a_closed_share_lot_stays_in_the_symbols_history(adapter_env):
    """History is evidence. An event that no current component can hold — a
    closed share lot, a manual correction — is still this symbol's history and
    must remain readable, even though it cannot contribute cash."""
    _write_tastytrade(activity=[_expiration_event("1"), _equity_event("2")])

    ledger = _symbol("tastytrade")
    assert ledger["event_count_total"] == 2
    assert ledger["current_period"]["event_count"] == 2
    assert sorted(row["instrument"] for row in ledger["components"]) == [
        "EQUITY", "OPTION"
    ]

    events = client.get(
        "/api/brokerages/tastytrade/symbols/ABC/events", params={"period": "all"}
    ).json()
    assert events["total_event_count"] == 2
    assert {row["instrument"] for row in events["items"]} == {"OPTION", "EQUITY"}
    # The share sale is visible and its cash is stated even while the symbol's
    # total is unavailable, because its opening trade is not in retained history.
    sale = next(row for row in events["items"] if row["instrument"] == "EQUITY")
    assert sale["net_cash_flow"] == pytest.approx(-900)


def _manual_equity_row():
    """A user-entered correction for an opening trade the sync never delivered."""
    return {
        "id": "manual:TRADING:fix", "source": options_activity.MANUAL_SOURCE,
        "executed_at": "2025-11-21T21:00:00+00:00", "transaction_date": "2025-11-21",
        "transaction_type": "Manual Reconciliation",
        "transaction_sub_type": "Pre-window assignment",
        "instrument_type": "Equity", "contract_symbol": "ABC",
        "underlying_symbol": "ABC", "action": "Manual Adjustment",
        "quantity": "100", "position_delta": "100", "net_value": "-1300",
    }
