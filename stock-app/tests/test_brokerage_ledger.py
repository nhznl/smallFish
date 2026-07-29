from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app import brokerage_ledger, config, options_activity, retirement_options, snaptrade_service
from app.main import app

client = TestClient(app)


@pytest.fixture
def ledger_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SFP_TASTYTRADE_POSITIONS", str(tmp_path / "trading_positions.csv"))
    monkeypatch.setenv("SFP_OPTIONS_POSITION_MARKS", str(tmp_path / "legacy_marks.csv"))
    monkeypatch.setenv("SFP_OPTIONS_ACTIVITY", str(tmp_path / "trading_events.csv"))
    monkeypatch.setenv("SFP_OPTIONS_GROUPS", str(tmp_path / "trading_groups.csv"))
    monkeypatch.setenv("SFP_OPTIONS_GROUP_MEMBERS", str(tmp_path / "members.csv"))
    monkeypatch.setenv("SFP_SNAPTRADE_HOLDINGS", str(tmp_path / "retirement_positions.csv"))
    monkeypatch.setenv("SFP_RETIREMENT_OPTION_EVENTS", str(tmp_path / "retirement_events.csv"))
    monkeypatch.setenv("SFP_RETIREMENT_OPTION_GROUPS", str(tmp_path / "retirement_groups.csv"))
    monkeypatch.setenv("SFP_HOLDINGS_ENRICHMENT", str(tmp_path / "enrichment.csv"))
    return tmp_path


def _trading_position(*, symbol: str, underlying: str, instrument: str,
                      quantity: str, direction: str, mark: str,
                      multiplier: str, average: str) -> dict[str, str]:
    row = {header: "" for header in options_activity.COMBINED_POSITION_HEADERS}
    row.update({
        "schema_version": "1", "source": "TASTYTRADE", "account": "TRADING",
        "instrument_type": instrument, "contract_symbol": symbol,
        "contract_key": options_activity._contract_key(symbol),
        "underlying_symbol": underlying, "quantity": str(abs(float(quantity))),
        "direction": direction, "signed_quantity": quantity, "multiplier": multiplier,
        "mark": str(float(mark) * abs(float(quantity)) * float(multiplier)),
        "mark_price": mark, "average_open_price": average,
        "updated_at": "2026-07-28T16:00:00+00:00",
        "retrieved_at": "2026-07-28T16:01:00+00:00",
    })
    return row


def _trading_event(*, event_id: str, contract: str, underlying: str,
                   action: str, delta: str, net_value: str) -> dict[str, str]:
    row = {header: "" for header in options_activity.ACTIVITY_HEADERS}
    option_type, expiry, strike = options_activity._option_terms(contract)
    row.update({
        "schema_version": "1", "id": event_id, "source": "TASTYTRADE",
        "source_transaction_id": event_id, "account": "TRADING",
        "executed_at": "2026-07-01T16:00:00+00:00",
        "transaction_date": "2026-07-01", "transaction_type": "Trade",
        "instrument_type": "Equity Option", "contract_symbol": contract,
        "contract_key": options_activity._contract_key(contract),
        "underlying_symbol": underlying, "action": action, "quantity": "1",
        "position_delta": delta, "net_value": net_value,
        "option_type": option_type, "expiry": expiry, "strike": strike,
        "retrieved_at": "2026-07-28T16:01:00+00:00",
        "imported_at": "2026-07-28T16:01:00+00:00",
    })
    return row


def _holding(*, account_id: str, account: str, symbol: str, asset_class: str,
             quantity: str, price: str, cost_basis: str, market_value: str,
             underlying: str = "", option_type: str = "", strike: str = "",
             expiry: str = "") -> dict[str, str]:
    row = {header: "" for header in snaptrade_service.HOLDINGS_HEADERS}
    row.update({
        "schema_version": "1", "source": "SNAPTRADE",
        "retrieved_at": "2026-07-28T16:02:00+00:00",
        "imported_at": "2026-07-28T16:02:01+00:00",
        "account_id": account_id, "account_name": account, "institution": "Fidelity",
        "asset_class": asset_class, "symbol": symbol, "underlying_symbol": underlying,
        "option_type": option_type, "strike": strike, "expiry": expiry, "currency": "USD",
        "quantity": quantity, "price": price, "average_purchase_price": cost_basis,
        "cost_basis": cost_basis, "market_value": market_value,
        "open_pnl": str(float(market_value) - float(cost_basis)),
    })
    return row


def _retirement_event(*, event_id: str, account_id: str, account: str,
                      contract: str, underlying: str, units: str,
                      amount: str, action: str = "SELL_TO_OPEN") -> dict[str, str]:
    row = {header: "" for header in retirement_options.EVENT_HEADERS}
    row.update({
        "schema_version": "1", "id": event_id, "source": "SNAPTRADE",
        "account_id": account_id, "account": account,
        "underlying_symbol": underlying, "option_type": "PUT", "strike": "50",
        "expiry": "2026-08-21", "occ_symbol": contract, "action": action,
        "activity_type": "TRADE", "units": units, "net_value": amount,
        "trade_date": "2026-07-01T04:00:00Z",
        "retrieved_at": "2026-07-28T16:02:00+00:00",
        "imported_at": "2026-07-28T16:02:00+00:00",
    })
    return row


def test_trading_and_retirement_share_one_contract_shape(ledger_env):
    contract = "ABC   260821P00050000"
    options_activity._atomic_write(
        config.tastytrade_positions_csv(), options_activity.COMBINED_POSITION_HEADERS,
        [
            _trading_position(
                symbol="ABC", underlying="ABC", instrument="Equity", quantity="100",
                direction="Long", mark="120", multiplier="1", average="110",
            ),
            _trading_position(
                symbol=contract, underlying="ABC", instrument="Equity Option", quantity="-1",
                direction="Short", mark="0.75", multiplier="100", average="6",
            ),
        ],
    )
    event = _trading_event(
        event_id="tastytrade:TRADING:1", contract=contract, underlying="ABC",
        action="Sell to Open", delta="-1", net_value="600",
    )
    options_activity._atomic_write(
        config.options_activity_csv(), options_activity.ACTIVITY_HEADERS, [event]
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
                account_id="acct-1", account="BrokerageLink", symbol=contract,
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
            contract=contract, underlying="ABC", units="-1", amount="600",
        )],
    )

    trading = client.get("/brokerage-ledgers/trading/combined")
    retirement = client.get("/brokerage-ledgers/retirement/combined")
    assert trading.status_code == retirement.status_code == 200
    t, r = trading.json(), retirement.json()
    assert set(t) == set(r) == {
        "schema_name", "schema_version", "portfolio", "as_of", "coverage",
        "summary", "symbols", "warnings",
    }
    assert t["schema_name"] == r["schema_name"] == "smallfish.brokerage-ledger"
    assert t["schema_version"] == r["schema_version"] == 1
    assert set(t["symbols"][0]) == set(r["symbols"][0])
    assert set(t["symbols"][0]["components"][0]) == set(r["symbols"][0]["components"][0])
    for snapshot in (t, r):
        symbol = snapshot["symbols"][0]
        assert symbol["exposure"] == "EQUITY_AND_OPTIONS"
        assert symbol["shares"] == 100
        assert symbol["cash_in"] == pytest.approx(600)
        assert symbol["cash_out"] == pytest.approx(-11000)
        assert symbol["equity_market_value"] == pytest.approx(12000)
        assert symbol["option_market_value"] == pytest.approx(-75)
        assert symbol["total_pnl"] == pytest.approx(1525)
        assert symbol["current_price_per_share"] == pytest.approx(120)
        assert symbol["share_quantity"] == pytest.approx(100)
        assert symbol["equity_cost_per_share"] == pytest.approx(110)
        assert symbol["equity_cost"] == pytest.approx(11000)
        assert symbol["current_equity"] == pytest.approx(12000)
        assert symbol["equity_pnl"] == pytest.approx(1000)
        assert symbol["equity_pnl_per_share"] == pytest.approx(10)
        assert symbol["net_credit"] == pytest.approx(600)
        assert symbol["net_debit"] == pytest.approx(0)
        assert symbol["option_pnl"] == pytest.approx(525)
        assert symbol["net_pnl"] == pytest.approx(1525)
        assert symbol["option_adjusted_basis_per_share"] == pytest.approx(104.75)
    assert t["coverage"]["closed_equity"] == "UNAVAILABLE"
    assert r["coverage"]["closed_equity"] == "UNAVAILABLE"


def test_retirement_identity_and_reconciliation_are_account_aware(ledger_env):
    contract = "ABC   260821P00050000"
    holdings = [
        _holding(
            account_id=account_id, account=account, symbol=contract,
            asset_class="OPTION", quantity="-1", price="0.50", cost_basis="-100",
            market_value="-50", underlying="ABC", option_type="PUT", strike="50",
            expiry="2026-08-21",
        )
        for account_id, account in (("acct-1", "Roth IRA"), ("acct-2", "BrokerageLink"))
    ]
    snaptrade_service._atomic_write(
        config.snaptrade_holdings_csv(), snaptrade_service.HOLDINGS_HEADERS, holdings
    )
    retirement_options._atomic_write(
        config.retirement_option_events_csv(), retirement_options.EVENT_HEADERS,
        [
            _retirement_event(
                event_id=f"event-{account_id}", account_id=account_id, account=account,
                contract=contract, underlying="ABC", units="-1", amount="100",
            )
            for account_id, account in (("acct-1", "Roth IRA"), ("acct-2", "BrokerageLink"))
        ],
    )
    data = brokerage_ledger.snapshot("retirement")
    components = data["symbols"][0]["components"]
    assert len(components) == 2
    assert len({row["id"] for row in components}) == 2
    assert {row["account_id"] for row in components} == {"acct-1", "acct-2"}
    assert data["symbols"][0]["option_market_value"] == pytest.approx(-100)
    assert data["symbols"][0]["total_pnl"] == pytest.approx(100)


def test_reconciliation_mismatch_fails_closed_without_partial_portfolio_total(ledger_env):
    contract = "ABC   260821P00050000"
    snaptrade_service._atomic_write(
        config.snaptrade_holdings_csv(), snaptrade_service.HOLDINGS_HEADERS,
        [_holding(
            account_id="acct-1", account="BrokerageLink", symbol=contract,
            asset_class="OPTION", quantity="-1", price="0.50", cost_basis="-100",
            market_value="-50", underlying="ABC", option_type="PUT", strike="50",
            expiry="2026-08-21",
        )],
    )
    retirement_options._atomic_write(
        config.retirement_option_events_csv(), retirement_options.EVENT_HEADERS,
        [_retirement_event(
            event_id="event-1", account_id="acct-1", account="BrokerageLink",
            contract=contract, underlying="ABC", units="-2", amount="200",
        )],
    )
    data = brokerage_ledger.snapshot("retirement")
    symbol = data["symbols"][0]
    component = symbol["components"][0]
    assert component["pnl_completeness"] == "UNAVAILABLE"
    assert component["net_cash_flow"] is None
    assert component["total_pnl"] is None
    assert symbol["total_pnl"] is None
    assert symbol["option_pnl"] is None
    assert symbol["net_pnl"] is None
    assert symbol["option_adjusted_basis_per_share"] == 0
    assert data["summary"]["total_pnl"] is None
    assert data["summary"]["incomplete_symbol_count"] == 1
    assert {warning["code"] for warning in data["warnings"]} >= {
        "POSITION_ACTIVITY_MISMATCH"
    }


def test_main_row_keeps_equity_and_option_blocks_empty_when_not_applicable(ledger_env):
    options_activity._atomic_write(
        config.tastytrade_positions_csv(), options_activity.COMBINED_POSITION_HEADERS,
        [_trading_position(
            symbol="SHARES", underlying="SHARES", instrument="Equity", quantity="20",
            direction="Long", mark="12", multiplier="1", average="10",
        )],
    )
    options_activity._atomic_write(
        config.options_activity_csv(), options_activity.ACTIVITY_HEADERS,
        [_trading_event(
            event_id="tastytrade:TRADING:flat", contract="OPT 260821C00010000",
            underlying="OPT", action="Sell to Open", delta="-1", net_value="150",
        ), _trading_event(
            event_id="tastytrade:TRADING:close", contract="OPT 260821C00010000",
            underlying="OPT", action="Buy to Close", delta="1", net_value="-50",
        )],
    )
    options_activity._atomic_write(config.options_groups_csv(), options_activity.GROUP_HEADERS, [])
    options_activity._atomic_write(
        config.options_group_members_csv(), options_activity.MEMBER_HEADERS, []
    )

    rows = {row["symbol"]: row for row in brokerage_ledger.snapshot("trading")["symbols"]}
    equity = rows["SHARES"]
    assert equity["exposure"] == "EQUITY"
    assert equity["share_quantity"] == 20
    assert equity["equity_cost"] == pytest.approx(200)
    assert equity["equity_pnl"] == pytest.approx(40)
    assert equity["net_credit"] is None
    assert equity["net_debit"] is None
    assert equity["option_pnl"] is None
    assert equity["net_pnl"] == pytest.approx(40)
    assert equity["option_adjusted_basis_per_share"] == pytest.approx(10)

    option = rows["OPT"]
    assert option["exposure"] == "OPTIONS"
    assert option["current_price_per_share"] is None
    assert option["share_quantity"] is None
    assert option["equity_cost_per_share"] is None
    assert option["equity_cost"] is None
    assert option["current_equity"] is None
    assert option["equity_pnl"] is None
    assert option["equity_pnl_per_share"] is None
    assert option["net_credit"] == pytest.approx(150)
    assert option["net_debit"] == pytest.approx(-50)
    assert option["option_pnl"] == pytest.approx(100)
    assert option["net_pnl"] == pytest.approx(100)
    assert option["option_adjusted_basis_per_share"] == 0


def test_unknown_portfolio_is_404(ledger_env):
    response = client.get("/brokerage-ledgers/taxable/combined")
    assert response.status_code == 404
