"""SnapTrade holdings normalization, materialization, and snapshot reads."""

from __future__ import annotations

import csv

import pytest

from app import config
from app.brokerages.importers import snaptrade as importer


@pytest.fixture
def holdings_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SFP_SNAPTRADE_HOLDINGS", str(tmp_path / "holdings.csv"))
    for key in (
        "SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY",
        "SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


# --------------------------------------------------------------------------- #
# fixtures shaped like real SnapTrade SDK response bodies                       #
# --------------------------------------------------------------------------- #

def _account():
    return {
        "id": "acct-1",
        "name": "BrokerageLink",
        "number": "652782616",
        "institution_name": "Fidelity",
        "balance": {"total": {"amount": "184261.04", "currency": {"code": "USD"}}},
    }


def _positions():
    """Shaped like a ``get_all_account_positions`` response body."""
    return {
        "results": [
            {
                "instrument": {
                    "kind": "stock",
                    "symbol": "JOBY",
                    "description": "Joby Aviation Inc",
                    "currency": "USD",
                },
                "units": "600",
                "price": "7.535",
                "cost_basis": "12.86",  # per share
                "currency": "USD",
            },
            {
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
                "cost_basis": "24",  # per contract
                "currency": "USD",
            },
            {
                "instrument": {
                    "kind": "mutualfund",
                    "symbol": "FDRXX",
                    "description": "Fidelity Government Cash Reserves",
                    "currency": "USD",
                },
                "units": "179865.04",
                "price": "1",
                "cost_basis": "1",
                "currency": "USD",
                "cash_equivalent": True,
            },
        ],
        "data_freshness": {"as_of": "2026-07-23T22:10:59Z"},
    }


def _provider():
    return [(_account(), _positions())]


# --------------------------------------------------------------------------- #
# config                                                                       #
# --------------------------------------------------------------------------- #

def test_config_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SFP_SNAPTRADE_HOLDINGS", raising=False)
    assert config.snaptrade_holdings_csv() == tmp_path / "ledger_retirement" / "positions.csv"
    override = tmp_path / "custom_holdings.csv"
    monkeypatch.setenv("SFP_SNAPTRADE_HOLDINGS", str(override))
    assert config.snaptrade_holdings_csv() == override


# --------------------------------------------------------------------------- #
# holdings resource / snapshot                                                  #
# --------------------------------------------------------------------------- #

def test_sync_writes_ledger_and_summary(holdings_env):
    summary = importer.sync_holdings(provider=_provider)

    by_symbol = {h["symbol"]: h for h in summary["holdings"]}
    assert set(by_symbol) == {"JOBY", "CLX   260918P00070000", "FDRXX"}

    equity = by_symbol["JOBY"]
    assert equity["assetClass"] == "STOCK"
    assert equity["quantity"] == pytest.approx(600)
    assert equity["marketValue"] == pytest.approx(4521.0)
    assert equity["costBasis"] == pytest.approx(7716.0)
    assert equity["openPnl"] == pytest.approx(-3195.0)  # mv - cost

    option = by_symbol["CLX   260918P00070000"]
    assert option["assetClass"] == "OPTION"
    assert option["underlyingSymbol"] == "CLX"
    assert option["optionType"] == "PUT"
    assert option["strike"] == pytest.approx(70.0)
    assert option["expiry"] == "2026-09-18"
    assert option["quantity"] == pytest.approx(-1)
    assert option["marketValue"] == pytest.approx(-125.0)  # -1 * 1.25 * 100
    assert option["costBasis"] == pytest.approx(-24.0)  # -1 * 24 per contract
    assert option["openPnl"] == pytest.approx(-101.0)

    cash = by_symbol["FDRXX"]
    assert cash["assetClass"] == "CASH"  # cash_equivalent money market
    assert cash["marketValue"] == pytest.approx(179865.04)

    assert summary["totalValue"] == pytest.approx(4521.0 - 125.0 + 179865.04)
    assert summary["byAssetClass"]["CASH"]["holdingCount"] == 1
    assert summary["byAccount"]["BrokerageLink"]["holdingCount"] == 3
    assert summary["sync"] == {
        "accounts_synced": 1,
        "positions_synced": 3,
        "added": 3,
        "changed": 0,
        "unchanged": 0,
        "removed": 0,
        "groups_reactivated": 0,
    }

    # Ledger persisted with schema version + immutable broker facts.
    rows = list(csv.DictReader(config.snaptrade_holdings_csv().open(encoding="utf-8")))
    assert len(rows) == 3
    assert {r["schema_version"] for r in rows} == {"1"}
    assert {r["source"] for r in rows} == {"SNAPTRADE"}
    assert all(r["imported_at"] for r in rows)


def test_missing_provider_cost_basis_stays_unknown(holdings_env):
    """Employer-plan units omit basis; absence must never become zero."""
    positions = {
        "results": [{
            "instrument": {
                "kind": "other", "symbol": "PLAN", "description": "Plan unit",
            },
            "units": "10", "price": "25", "currency": "USD",
        }],
    }

    summary = importer.sync_holdings(
        provider=lambda: [(_account(), positions)]
    )

    holding = summary["holdings"][0]
    assert holding["costBasis"] is None
    assert holding["openPnl"] is None
    assert holding["openPnlPct"] is None
    assert summary["totalValue"] == pytest.approx(250)
    assert summary["totalCostBasis"] is None
    assert summary["totalOpenPnl"] is None
    assert summary["totalOpenPnlPct"] is None

    row = next(csv.DictReader(
        config.snaptrade_holdings_csv().open(encoding="utf-8")
    ))
    assert row["average_purchase_price"] == ""
    assert row["cost_basis"] == ""
    assert row["open_pnl"] == ""
    assert row["open_pnl_pct"] == ""

    trend_rows = list(csv.DictReader(
        config.holdings_trend_csv().open(encoding="utf-8")
    ))
    assert trend_rows == []


def test_sync_reports_unchanged_and_removed_positions(holdings_env):
    importer.sync_holdings(provider=_provider)
    unchanged = importer.sync_holdings(provider=_provider)
    assert unchanged["sync"] == {
        "accounts_synced": 1,
        "positions_synced": 3,
        "added": 0,
        "changed": 0,
        "unchanged": 3,
        "removed": 0,
        "groups_reactivated": 0,
    }

    removed = importer.sync_holdings(
        provider=lambda: [(_account(), {"results": []})]
    )
    assert removed["sync"] == {
        "accounts_synced": 1,
        "positions_synced": 0,
        "added": 0,
        "changed": 0,
        "unchanged": 0,
        "removed": 3,
        "groups_reactivated": 0,
    }


def test_snapshot_round_trips_written_ledger(holdings_env):
    written = importer.sync_holdings(provider=_provider)
    read_back = importer.snapshot()
    assert read_back["totalValue"] == pytest.approx(written["totalValue"])
    assert len(read_back["holdings"]) == len(written["holdings"])


def test_snapshot_empty_when_no_ledger(holdings_env):
    summary = importer.snapshot()
    assert summary["holdings"] == []
    assert summary["totalValue"] == 0.0


def test_read_ledger_rejects_unknown_schema(holdings_env):
    path = config.snaptrade_holdings_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=importer.HOLDINGS_HEADERS)
        writer.writeheader()
        writer.writerow({"schema_version": "999", "source": "SNAPTRADE", "symbol": "X"})
    with pytest.raises(importer.SnapTradeImportError) as exc:
        importer.snapshot()
    assert exc.value.status_code == 409
