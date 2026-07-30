"""Phase 0/2 characterization of Fidelity sync ownership and provider call counts.

Locks the public response shape and proves each provider seam runs once per
requested resource. Phase 0 recorded the pre-fix duplicate-call defect;
Phase 2 corrected it — empty-body sync is now one call each.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import config, snaptrade_service, snaptrade_setup
from app.brokerages import registry
from app.brokerages.importers import held_option_market_data as market_data
from app.brokerages.importers import snaptrade as importer
from app.main import app
from tests.test_brokerage_adapters import adapter_env  # noqa: F401

client = TestClient(app)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "brokerage_sync"
FROZEN_NOW = "2026-07-29T16:00:00+00:00"
CONTRACT = "CLX   260918P00070000"


# ----------------------------------------------------------------- fixtures ---

@pytest.fixture
def fidelity_sync_env(adapter_env, monkeypatch):
    """Isolated ledger paths plus frozen clocks for deterministic artifacts."""
    monkeypatch.setattr(importer, "_now", lambda: FROZEN_NOW)
    monkeypatch.setattr(market_data, "_now", lambda: FROZEN_NOW)
    return adapter_env


def _account():
    return {
        "id": "acct-1",
        "name": "BrokerageLink",
        "number": "652782616",
        "institution_name": "Fidelity",
        "balance": {"total": {"amount": "184261.04", "currency": {"code": "USD"}}},
    }


def _positions_with_option():
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
                "cost_basis": "12.86",
                "currency": "USD",
            },
            {
                "instrument": {
                    "kind": "option",
                    "symbol": CONTRACT,
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
            },
        ],
        "data_freshness": {"as_of": "2026-07-23T22:10:59Z"},
    }


def _opt_activity():
    return SimpleNamespace(
        id="act-1",
        type="SELL",
        option_type="SELL_TO_OPEN",
        amount="370.34",
        units="-1",
        price="3.70",
        fee="0.66",
        trade_date="2026-07-15T04:00:00Z",
        settlement_date="2026-07-16T04:00:00Z",
        description="SELL_TO_OPEN PUT (CLX)",
        option_symbol=SimpleNamespace(
            ticker=CONTRACT,
            strike_price="70",
            expiration_date="2026-09-18",
            option_type="PUT",
            underlying_symbol=SimpleNamespace(symbol="CLX"),
        ),
    )


class _CallCounter:
    """Counts injected provider seams for one Fidelity sync characterization."""

    def __init__(self):
        self.positions = 0
        self.activities = 0
        self.betas = 0
        self.greeks = 0
        self.resource_commands: list[str] = []

    def as_dict(self) -> dict[str, int | list[str]]:
        return {
            "positions": self.positions,
            "activities": self.activities,
            "betas": self.betas,
            "greeks": self.greeks,
            "resource_commands": list(self.resource_commands),
        }


def _install_providers(monkeypatch, counter: _CallCounter, *,
                       with_option_position: bool = True) -> None:
    positions = _positions_with_option() if with_option_position else {
        "results": [_positions_with_option()["results"][0]],
        "data_freshness": {"as_of": "2026-07-23T22:10:59Z"},
    }

    def fetch_positions():
        counter.positions += 1
        return [(_account(), positions)]

    def fetch_activities(start_date, end_date, account_ids=None):
        counter.activities += 1
        return [(_account(), [_opt_activity()])]

    def fetch_betas(symbols):
        return [
            SimpleNamespace(
                symbol=symbol, beta=1.25,
                beta_updated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            for symbol in symbols
        ]

    def fetch_greeks(legs, timeout_seconds):
        from services.options_market.providers.tastytrade import occ_to_dxfeed_symbol

        return [
            SimpleNamespace(
                contract_symbol=leg["contract_symbol"],
                provider_symbol=occ_to_dxfeed_symbol(leg["contract_symbol"]),
                implied_volatility=0.5, delta=-0.2,
                gamma=0.01, theta=-0.1, rho=0.0, vega=0.1, option_price=1.25,
                event_time_ms=1784851143002, observed_at=None,
                provenance="TASTYTRADE_DXLINK",
            )
            for leg in legs
        ]

    # Default fetchers are bound at def-time; wrap the public sync entry points
    # so both the registry binding and the holdings side-effect see fakes.
    real_sync_betas = market_data.sync_betas
    real_sync_greeks = market_data.sync_greeks

    def sync_betas_counted():
        counter.betas += 1
        return real_sync_betas(fetcher=fetch_betas)

    def sync_greeks_counted():
        counter.greeks += 1
        return real_sync_greeks(fetcher=fetch_greeks)

    monkeypatch.setattr(importer, "fetch_snaptrade", fetch_positions)
    monkeypatch.setattr(importer, "fetch_activities", fetch_activities)
    monkeypatch.setattr(market_data, "sync_betas", sync_betas_counted)
    monkeypatch.setattr(market_data, "sync_greeks", sync_greeks_counted)

    # Count registry command identity without altering behavior.
    entry = registry.REGISTRY["fidelity"]
    originals = dict(entry.sync_commands)

    def wrap(name, command):
        def wrapped():
            counter.resource_commands.append(name)
            return command()
        return wrapped

    monkeypatch.setitem(
        registry.REGISTRY, "fidelity",
        type(entry)(
            descriptor=entry.descriptor,
            capabilities=entry.capabilities,
            factory=entry.factory,
            holdings_metadata_path=entry.holdings_metadata_path,
            holdings_trend_path=entry.holdings_trend_path,
            sync_commands={
                name: wrap(name, originals[name]) for name in originals
            },
        ),
    )


def _post_sync(resources=None):
    payload = {} if resources is None else {"resources": resources}
    response = client.post("/api/brokerages/fidelity/sync", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _statuses(body: dict) -> dict[str, str]:
    return {row["resource"]: row["status"] for row in body["results"]}


# ------------------------------------------------------ empty-body / all ---

def test_empty_body_fidelity_sync_public_shape_and_no_duplicate_calls(
        fidelity_sync_env, monkeypatch):
    """Angular posts `{}`. Each resource command runs once; no sibling re-entry."""
    counter = _CallCounter()
    _install_providers(monkeypatch, counter)

    body = _post_sync()

    assert body["schema_name"] == "smallfish.brokerage-sync-report"
    assert body["schema_version"] == 1
    assert body["brokerage_id"] == "fidelity"
    assert [row["resource"] for row in body["results"]] == [
        "HOLDINGS", "ACTIVITY", "MARKET_DATA",
    ]
    assert _statuses(body) == {
        "HOLDINGS": "OK", "ACTIVITY": "OK", "MARKET_DATA": "OK",
    }
    for row in body["results"]:
        assert row["warnings"] == []
        assert row["detail"] is None or isinstance(row["detail"], dict)
        if row["detail"] is not None:
            for key in row["detail"]:
                assert key in {
                    "events_received", "events_inserted", "events_updated",
                    "position_marks", "holdings", "accounts", "observed",
                    "retained", "missing", "requested", "start_date",
                    "end_date", "window", "retrieved_at", "syncDate",
                    "capturedAt", "replaced", "snapshotCount",
                    "broker_transactions_read", "option_events_selected",
                    "greeks_observed", "greeks_retained", "greeks_missing",
                    "betas_observed", "betas_retained", "betas_missing",
                }

    assert counter.resource_commands == ["HOLDINGS", "ACTIVITY", "MARKET_DATA"]
    assert counter.as_dict() == {
        "positions": 1,
        "activities": 1,
        "betas": 1,
        "greeks": 1,
        "resource_commands": ["HOLDINGS", "ACTIVITY", "MARKET_DATA"],
    }


def test_all_resources_explicit_list_matches_empty_body_order_and_counts(
        fidelity_sync_env, monkeypatch):
    counter = _CallCounter()
    _install_providers(monkeypatch, counter)

    body = _post_sync(["HOLDINGS", "ACTIVITY", "MARKET_DATA"])

    assert [row["resource"] for row in body["results"]] == [
        "HOLDINGS", "ACTIVITY", "MARKET_DATA",
    ]
    assert counter.as_dict() == {
        "positions": 1,
        "activities": 1,
        "betas": 1,
        "greeks": 1,
        "resource_commands": ["HOLDINGS", "ACTIVITY", "MARKET_DATA"],
    }


# --------------------------------------------------- resource-specific ---

def test_holdings_resource_requests_positions_once_without_siblings(
        fidelity_sync_env, monkeypatch):
    counter = _CallCounter()
    _install_providers(monkeypatch, counter)

    body = _post_sync(["HOLDINGS"])

    assert [row["resource"] for row in body["results"]] == ["HOLDINGS"]
    assert body["results"][0]["status"] == "OK"
    assert counter.as_dict() == {
        "positions": 1,
        "activities": 0,
        "betas": 0,
        "greeks": 0,
        "resource_commands": ["HOLDINGS"],
    }


def test_activity_resource_requests_activities_once_without_positions_or_market_data(
        fidelity_sync_env, monkeypatch):
    counter = _CallCounter()
    _install_providers(monkeypatch, counter)

    body = _post_sync(["ACTIVITY"])

    assert [row["resource"] for row in body["results"]] == ["ACTIVITY"]
    assert body["results"][0]["status"] == "OK"
    detail = body["results"][0]["detail"]
    assert detail["events_received"] == 1
    assert detail["events_inserted"] == 1
    assert detail["window"] == [f"{date.today().year}-01-01", date.today().isoformat()]
    assert counter.as_dict() == {
        "positions": 0,
        "activities": 1,
        "betas": 0,
        "greeks": 0,
        "resource_commands": ["ACTIVITY"],
    }


def test_market_data_resource_reads_holdings_artifact_without_snaptrade_calls(
        fidelity_sync_env, monkeypatch):
    # Seed the holdings ledger the market-data command reads; no SnapTrade call.
    path = config.snaptrade_holdings_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (FIXTURES / "expected_holdings.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    counter = _CallCounter()
    _install_providers(monkeypatch, counter)

    body = _post_sync(["MARKET_DATA"])

    assert [row["resource"] for row in body["results"]] == ["MARKET_DATA"]
    assert body["results"][0]["status"] == "OK"
    assert counter.as_dict() == {
        "positions": 0,
        "activities": 0,
        "betas": 1,
        "greeks": 1,
        "resource_commands": ["MARKET_DATA"],
    }


def test_holdings_without_option_legs_skips_market_data(
        fidelity_sync_env, monkeypatch):
    counter = _CallCounter()
    _install_providers(monkeypatch, counter, with_option_position=False)

    body = _post_sync(["HOLDINGS"])

    assert body["results"][0]["status"] == "OK"
    assert counter.as_dict() == {
        "positions": 1,
        "activities": 0,
        "betas": 0,
        "greeks": 0,
        "resource_commands": ["HOLDINGS"],
    }


def test_legacy_sync_orchestrates_each_resource_at_most_once(
        fidelity_sync_env, monkeypatch):
    counter = _CallCounter()
    _install_providers(monkeypatch, counter)

    summary = snaptrade_service.sync(provider=importer.fetch_snaptrade)

    assert summary["sync"]["positions_synced"] == 2
    assert counter.as_dict() == {
        "positions": 1,
        "activities": 1,
        "betas": 1,
        "greeks": 1,
        "resource_commands": [],
    }


# ----------------------------------------- legacy sync seam / CLI / errors ---

def test_legacy_sync_provider_injection_return_shape(fidelity_sync_env, monkeypatch):
    monkeypatch.setattr(
        importer, "sync_events",
        lambda: {"groups_reactivated": 0, "events_received": 0},
    )
    monkeypatch.setattr(market_data, "sync_market_data", lambda: {})

    summary = snaptrade_service.sync(
        provider=lambda: [(_account(), _positions_with_option())]
    )

    assert set(summary) >= {
        "holdings", "totalValue", "totalCostBasis", "totalOpenPnl",
        "totalOpenPnlPct", "byAccount", "byAssetClass", "retrievedAt",
        "source", "sync",
    }
    assert summary["source"] == "SNAPTRADE"
    assert summary["retrievedAt"] == FROZEN_NOW
    assert summary["sync"] == {
        "accounts_synced": 1,
        "positions_synced": 2,
        "added": 2,
        "changed": 0,
        "unchanged": 0,
        "removed": 0,
        "groups_reactivated": 0,
    }
    assert {h["symbol"] for h in summary["holdings"]} == {"JOBY", CONTRACT}


def test_cli_subcommand_surface_and_secret_redaction(
        fidelity_sync_env, monkeypatch, capsys, tmp_path):
    """Documented `python -m app.snaptrade_service` commands stay available."""
    env_path = tmp_path / "app.env"
    env_path.write_text(
        "SNAPTRADE_CLIENT_ID=client\n"
        "SNAPTRADE_USER_ID=\n"
        "SNAPTRADE_USER_SECRET=\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(snaptrade_setup.config, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        snaptrade_setup, "register_user",
        lambda: {"userId": "uid-secret", "userSecret": "sekrit-value"},
    )
    monkeypatch.setattr(
        snaptrade_setup, "connection_portal_url",
        lambda broker=None, custom_redirect=None: "https://example.test/portal",
    )
    monkeypatch.setattr(
        snaptrade_setup, "list_accounts",
        lambda: [{"id": "acct-1", "name": "BrokerageLink", "number": "652",
                  "institution": "Fidelity", "totalValue": 1.0}],
    )
    monkeypatch.setattr(
        snaptrade_service, "sync",
        lambda: {"source": "SNAPTRADE", "holdings": [], "sync": {"added": 0}},
    )
    monkeypatch.setattr(
        snaptrade_service, "snapshot",
        lambda: {"source": "SNAPTRADE", "holdings": [], "totalValue": 0.0},
    )

    assert snaptrade_service._main(["register"]) == 0
    assert snaptrade_service._main(["connect", "--broker", "FIDELITY"]) == 0
    assert snaptrade_service._main(["accounts"]) == 0
    assert snaptrade_service._main(["sync"]) == 0
    assert snaptrade_service._main(["snapshot"]) == 0

    output = capsys.readouterr().out
    assert "sekrit-value" not in output
    assert "uid-secret" not in output
    assert "https://example.test/portal" in output
    assert "saved securely" in output


def test_cli_validation_errors_exit_two_without_leaking_detail(
        fidelity_sync_env, monkeypatch, capsys):
    monkeypatch.setattr(
        snaptrade_setup, "register_user",
        lambda: (_ for _ in ()).throw(
            snaptrade_service.SnapTradeValidationError("missing credentials", 503)
        ),
    )
    with pytest.raises(SystemExit) as exc:
        snaptrade_service._main(["register"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "missing credentials" in err
    assert err.startswith("error: ")


def test_public_status_codes_on_setup_errors(fidelity_sync_env, monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "PERS-ABC")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "ckey")
    with pytest.raises(snaptrade_service.SnapTradeValidationError) as personal:
        snaptrade_service.register_user()
    assert personal.value.status_code == 422

    monkeypatch.delenv("SNAPTRADE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SNAPTRADE_CONSUMER_KEY", raising=False)
    with pytest.raises(snaptrade_service.SnapTradeValidationError) as missing:
        snaptrade_service.register_user()
    assert missing.value.status_code == 503


# ------------------------------------------- artifact equivalence fixtures ---

def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_holdings_and_activity_artifacts_match_golden_fixtures(
        fidelity_sync_env, monkeypatch):
    monkeypatch.setattr(
        market_data, "sync_market_data", lambda: {},
    )
    monkeypatch.setattr(
        importer, "fetch_activities",
        lambda start_date, end_date, account_ids=None: [
            (_account(), [_opt_activity()])
        ],
    )

    snaptrade_service.sync(
        provider=lambda: [(_account(), _positions_with_option())]
    )
    importer.sync_events(
        provider=lambda start_date, end_date: [(_account(), [_opt_activity()])]
    )

    holdings = _read_csv(config.snaptrade_holdings_csv())
    events = _read_csv(config.retirement_option_events_csv())
    expected_holdings = _read_csv(FIXTURES / "expected_holdings.csv")
    expected_events = _read_csv(FIXTURES / "expected_events.csv")

    assert [row.keys() for row in holdings] == [row.keys() for row in expected_holdings]
    assert holdings == expected_holdings
    assert events == expected_events


def test_beta_and_greeks_artifacts_match_golden_fixtures(
        fidelity_sync_env, monkeypatch):
    # Write holdings without invoking the live market-data side effect.
    monkeypatch.setattr(importer, "sync_events", lambda: {
        "groups_reactivated": 0,
    })
    monkeypatch.setattr(market_data, "sync_market_data", lambda: {})
    snaptrade_service.sync(
        provider=lambda: [(_account(), _positions_with_option())]
    )

    def fetch_betas(symbols):
        return [
            SimpleNamespace(
                symbol=symbol, beta=1.25,
                beta_updated_at=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
            )
            for symbol in symbols
        ]

    def fetch_greeks(legs, timeout_seconds):
        from services.options_market.providers.tastytrade import occ_to_dxfeed_symbol

        return [
            SimpleNamespace(
                contract_symbol=leg["contract_symbol"],
                provider_symbol=occ_to_dxfeed_symbol(leg["contract_symbol"]),
                implied_volatility=0.5, delta=-0.2,
                gamma=0.01, theta=-0.1, rho=0.0, vega=0.1, option_price=1.25,
                event_time_ms=1784851143002, observed_at=None,
                provenance="TASTYTRADE_DXLINK",
            )
            for leg in legs
        ]

    market_data.sync_betas(fetcher=fetch_betas)
    market_data.sync_greeks(fetcher=fetch_greeks)

    betas = _read_csv(config.retirement_option_betas_csv())
    greeks = _read_csv(config.retirement_option_greeks_csv())
    assert betas == _read_csv(FIXTURES / "expected_betas.csv")
    assert greeks == _read_csv(FIXTURES / "expected_greeks.csv")


def test_golden_fixture_manifest_lists_characterized_artifacts():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_name"] == "smallfish.brokerage-sync-characterization"
    assert set(manifest["artifacts"]) == {
        "expected_holdings.csv",
        "expected_events.csv",
        "expected_betas.csv",
        "expected_greeks.csv",
    }
