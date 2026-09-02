"""Closed long equities from a holdings sync move onto Tracking as Sold Stock."""

from __future__ import annotations

import csv
from datetime import date

from fastapi.testclient import TestClient

from app import tracked_stocks
from app.brokerages import activity_sync, registry, sold_tracking
from app.brokerages.importers import snaptrade as importer
from app.main import app
from tests.test_brokerage_adapters import adapter_env  # noqa: F401
from tests.test_importer_snaptrade_holdings import _account
from tests.test_options_activity import _mark, _tx

client = TestClient(app)
TODAY = date(2026, 7, 24)


def _write_universe(root, symbols):
    path = root / "universe.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["symbol", "name", "type", "memberships", "source", "pinned", "last_seen", "sector"]
        )
        for symbol in symbols:
            writer.writerow([symbol, f"{symbol} Inc.", "STOCK", "sp500", "auto", "false",
                             "2026-07-24", "Information Technology"])


def _stock_position(symbol="JOBY", units="600"):
    return {
        "instrument": {
            "kind": "stock",
            "symbol": symbol,
            "description": f"{symbol} Inc",
            "currency": "USD",
        },
        "units": units,
        "price": "10",
        "cost_basis": "8",
        "currency": "USD",
    }


def _option_position():
    return {
        "instrument": {
            "kind": "option",
            "symbol": "CLX   260918P00070000",
            "description": "CLX 70 Put",
            "option_type": "PUT",
            "strike_price": "70",
            "expiration_date": "2026-09-18",
            "multiplier": "100",
            "underlying": {"kind": "stock", "symbol": "CLX"},
        },
        "units": "-1",
        "price": "1.25",
        "cost_basis": "24",
        "currency": "USD",
    }


def _cash_position():
    return {
        "instrument": {
            "kind": "mutualfund",
            "symbol": "FDRXX",
            "description": "Fidelity Government Cash Reserves",
            "currency": "USD",
        },
        "units": "1000",
        "price": "1",
        "cost_basis": "1",
        "currency": "USD",
        "cash_equivalent": True,
    }


def _positions(*rows):
    return {"results": list(rows), "data_freshness": {"as_of": "2026-07-23T22:10:59Z"}}


def test_wrap_preserves_shared_holdings_command_identity():
    def holdings():
        return {"holdings": 1}

    def activity():
        return {"events_inserted": 0}

    wrapped = sold_tracking.wrap_holdings_commands("fidelity", {
        "HOLDINGS": holdings,
        "ACCOUNT_CAPITAL": holdings,
        "ACTIVITY": activity,
    })
    assert wrapped["HOLDINGS"] is wrapped["ACCOUNT_CAPITAL"]
    assert wrapped["ACTIVITY"] is activity


def test_record_closed_equities_is_the_set_difference(adapter_env):
    _write_universe(adapter_env, ["AAA", "BBB", "SPY"])
    result = sold_tracking.record_closed_equities(
        {"AAA", "BBB"}, {"BBB"}, today=TODAY,
    )
    assert result["sold_tracked"] == 1
    assert result["sold_symbols"] == ["AAA"]
    payload = tracked_stocks.list_tracked(today=TODAY)
    assert payload["stocks"][0]["symbol"] == "AAA"
    assert payload["stocks"][0]["category"] == "Sold Stock"


def test_fidelity_holdings_sync_moves_closed_equity_to_sold_stock(
        adapter_env, monkeypatch):
    _write_universe(adapter_env, ["JOBY", "FDRXX", "SPY"])
    tracked_stocks.add_symbols(
        {
            "symbols": ["JOBY"],
            "category": "Tracking",
            "coverage_initiation_date": "2026-01-02",
            "notes": "watching the trend",
        },
        today=TODAY,
    )
    monkeypatch.setattr(
        importer, "fetch_snaptrade",
        lambda: [(_account(), _positions(_stock_position(), _option_position(), _cash_position()))],
    )
    first = client.post("/api/brokerages/fidelity/sync", json={"resources": ["HOLDINGS"]})
    assert first.status_code == 200, first.text
    assert first.json()["results"][0]["detail"]["sold_symbols"] == []

    monkeypatch.setattr(
        importer, "fetch_snaptrade",
        lambda: [(_account(), _positions(_option_position(), _cash_position()))],
    )
    second = client.post("/api/brokerages/fidelity/sync", json={"resources": ["HOLDINGS"]})
    assert second.status_code == 200, second.text
    detail = second.json()["results"][0]["detail"]
    assert detail["sold_symbols"] == ["JOBY"]
    assert detail["sold_updated"] == 1
    assert detail["sold_tracked"] == 0

    payload = tracked_stocks.list_tracked(today=date.today())
    row = payload["stocks"][0]
    assert row["symbol"] == "JOBY"
    assert row["category"] == "Sold Stock"
    assert row["coverage_initiation_date"] == date.today().isoformat()
    assert row["notes"] == (
        f"watching the trend updated to Sold Stock per sync on {date.today().isoformat()}"
    )
    assert {stock["symbol"] for stock in payload["stocks"]} == {"JOBY"}


def test_partial_quantity_reduction_is_not_a_sale(adapter_env, monkeypatch):
    _write_universe(adapter_env, ["JOBY", "SPY"])
    monkeypatch.setattr(
        importer, "fetch_snaptrade",
        lambda: [(_account(), _positions(_stock_position("JOBY", "600")))],
    )
    client.post("/api/brokerages/fidelity/sync", json={"resources": ["HOLDINGS"]})
    monkeypatch.setattr(
        importer, "fetch_snaptrade",
        lambda: [(_account(), _positions(_stock_position("JOBY", "100")))],
    )
    body = client.post("/api/brokerages/fidelity/sync", json={"resources": ["HOLDINGS"]})
    assert body.json()["results"][0]["detail"]["sold_symbols"] == []
    assert tracked_stocks.list_tracked()["stocks"] == []


def test_tastytrade_holdings_sync_moves_closed_equity_to_sold_stock(
        adapter_env, monkeypatch):
    _write_universe(adapter_env, ["EQT", "SPY"])

    def with_equity(_start, _end):
        return (
            [_tx(1)],
            [
                _mark(),
                _mark(symbol="EQT", underlying="EQT", quantity="20", direction="Long",
                      mark_price="25", multiplier="1") | {
                          "instrument_type": "Equity", "average_open_price": "20",
                      },
            ],
            {"environment": "live"},
        )

    def without_equity(_start, _end):
        return ([_tx(1)], [_mark()], {"environment": "live"})

    monkeypatch.setattr(activity_sync, "fetch_tastytrade", with_equity)
    first = client.post("/api/brokerages/tastytrade/sync", json={"resources": ["HOLDINGS"]})
    assert first.status_code == 200, first.text
    assert first.json()["results"][0]["detail"].get("sold_symbols") == []

    monkeypatch.setattr(activity_sync, "fetch_tastytrade", without_equity)
    second = client.post("/api/brokerages/tastytrade/sync", json={"resources": ["HOLDINGS"]})
    assert second.status_code == 200, second.text
    detail = second.json()["results"][0]["detail"]
    assert detail["sold_symbols"] == ["EQT"]
    assert detail["sold_tracked"] == 1

    row = tracked_stocks.list_tracked()["stocks"][0]
    assert row["symbol"] == "EQT"
    assert row["category"] == "Sold Stock"
    assert row["notes"] == ""


def test_sold_tracking_failure_does_not_fail_holdings_sync(adapter_env, monkeypatch):
    def ok():
        return {"holdings": 3}

    entry = registry.REGISTRY["fidelity"]
    monkeypatch.setitem(
        registry.REGISTRY, "fidelity",
        type(entry)(
            descriptor=entry.descriptor, capabilities=entry.capabilities,
            factory=entry.factory,
            holdings_metadata_path=entry.holdings_metadata_path,
            sync_commands={"HOLDINGS": ok},
        ),
    )
    monkeypatch.setattr(
        sold_tracking, "record_closed_equities",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    body = client.post(
        "/api/brokerages/fidelity/sync", json={"resources": ["HOLDINGS"]}
    ).json()
    assert body["results"][0]["status"] == "OK"
    assert body["results"][0]["detail"]["holdings"] == 3


def test_a_failed_current_read_does_not_treat_every_holding_as_sold(
        adapter_env, monkeypatch):
    _write_universe(adapter_env, ["AAA", "SPY"])
    calls = {"n": 0}

    def flaky(_brokerage_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"AAA"}
        raise RuntimeError("positions unreadable")

    def ok():
        return {"holdings": 1}

    entry = registry.REGISTRY["fidelity"]
    monkeypatch.setitem(
        registry.REGISTRY, "fidelity",
        type(entry)(
            descriptor=entry.descriptor, capabilities=entry.capabilities,
            factory=entry.factory,
            holdings_metadata_path=entry.holdings_metadata_path,
            sync_commands={"HOLDINGS": ok},
        ),
    )
    monkeypatch.setattr(sold_tracking, "open_long_equity_symbols", flaky)
    body = client.post(
        "/api/brokerages/fidelity/sync", json={"resources": ["HOLDINGS"]}
    ).json()
    assert body["results"][0]["status"] == "OK"
    assert tracked_stocks.list_tracked()["stocks"] == []
