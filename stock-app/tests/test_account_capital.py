"""Account-capital characterization, materialization, and fail-closed reads."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app import config, options_activity
from app.brokerages import account_capital, contracts, registry
from app.brokerages.importers import snaptrade as snaptrade_importer


@pytest.fixture
def capital_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "SFP_TRADING_ACCOUNT_CAPITAL", str(tmp_path / "trading_capital.csv")
    )
    monkeypatch.setenv(
        "SFP_RETIREMENT_ACCOUNT_CAPITAL", str(tmp_path / "retirement_capital.csv")
    )
    return tmp_path


def _snap_account(*, total="184261.04"):
    balance = {} if total is None else {
        "total": {"amount": total, "currency": {"code": "USD"}}
    }
    return {
        "id": "synthetic-account",
        "name": "Synthetic account",
        "institution_name": "Synthetic institution",
        "balance": balance,
    }


def test_config_paths_are_per_ledger(capital_env, monkeypatch):
    monkeypatch.delenv("SFP_TRADING_ACCOUNT_CAPITAL")
    monkeypatch.delenv("SFP_RETIREMENT_ACCOUNT_CAPITAL")
    assert config.trading_account_capital_csv() == (
        capital_env / "ledger_trading" / "account_capital.csv"
    )
    assert config.retirement_account_capital_csv() == (
        capital_env / "ledger_retirement" / "account_capital.csv"
    )


def test_snaptrade_materializes_characterized_total_and_explicit_missing_fields(
        capital_env):
    positions = {"results": [{
        "instrument": {"kind": "mutualfund", "symbol": "CASHX"},
        "units": "2500", "price": "1", "cost_basis": "1",
        "currency": "USD", "cash_equivalent": True,
    }]}
    snaptrade_importer.sync_holdings(
        provider=lambda: [(_snap_account(), positions)],
        brokerage_id="fidelity",
    )

    [fact] = registry.resolve("fidelity").snapshot().account_capital
    assert fact.brokerage_id == "fidelity"
    assert fact.account.account_id == "synthetic-account"
    assert fact.currency == "USD"
    assert fact.net_liquidating_value == Decimal("184261.04")
    # A visible money-market position is not a provider cash-balance fact.
    assert fact.cash_balance is None
    assert fact.buying_power is None
    assert fact.maintenance_requirement is None
    assert fact.missing == (
        contracts.MISSING_CASH_BALANCE,
        contracts.MISSING_BUYING_POWER,
        contracts.MISSING_MAINTENANCE_REQUIREMENT,
    )
    assert fact.provenance.source == "FIDELITY"

    [row] = account_capital.read_facts(
        config.retirement_account_capital_csv(),
        brokerage_id="fidelity",
    )
    assert row.net_liquidating_value == Decimal("184261.04")
    csv_text = config.retirement_account_capital_csv().read_text(encoding="utf-8")
    assert "CASH_BALANCE_UNAVAILABLE" in csv_text
    assert "BUYING_POWER_UNAVAILABLE" in csv_text


def test_missing_provider_total_stays_null_with_stable_reason(capital_env):
    report = snaptrade_importer.sync_holdings(
        provider=lambda: [(_snap_account(total=None), {"results": []})],
        brokerage_id="fidelity",
    )

    [fact] = registry.resolve("fidelity").snapshot().account_capital
    assert report["capital_accounts"] == 1
    assert report["capital_accounts_with_net_liquidating_value"] == 0
    assert fact.net_liquidating_value is None
    assert contracts.MISSING_NET_LIQUIDATING_VALUE in fact.missing
    assert fact.net_liquidating_value != Decimal("0")


def test_tastytrade_sync_materializes_all_characterized_balance_fields(capital_env):
    balance = {
        "net_liquidating_value": "50250.75",
        "cash_balance": "3250.25",
        "equity_buying_power": "8000",
        "maintenance_requirement": "4200",
        "currency": "USD",
    }
    report = options_activity.sync(
        date(2026, 1, 1), date(2026, 1, 2),
        brokerage_id="tastytrade",
        provider=lambda _start, _end: (
            [], [], {"environment": "sandbox", "nickname": "Synthetic account",
                     "account_capital": balance}
        ),
    )

    [fact] = registry.resolve("tastytrade").snapshot().account_capital
    assert report["capital_accounts_with_net_liquidating_value"] == 1
    assert fact.net_liquidating_value == Decimal("50250.75")
    assert fact.cash_balance == Decimal("3250.25")
    assert fact.buying_power == Decimal("8000")
    assert fact.maintenance_requirement == Decimal("4200")
    assert fact.missing == ()


@pytest.mark.parametrize("brokerage_id", ["fidelity", "tastytrade"])
def test_missing_capital_artifact_is_empty_not_fabricated(capital_env, brokerage_id):
    snapshot = registry.resolve(brokerage_id).snapshot()
    assert snapshot.account_capital == ()
