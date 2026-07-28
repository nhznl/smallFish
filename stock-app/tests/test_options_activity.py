from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app import options_activity, options_portfolio


@pytest.fixture
def activity_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SFP_OPTIONS_ACTIVITY", str(tmp_path / "activity.csv"))
    monkeypatch.setenv("SFP_OPTIONS_GROUPS", str(tmp_path / "groups.csv"))
    monkeypatch.setenv("SFP_OPTIONS_GROUP_MEMBERS", str(tmp_path / "members.csv"))
    monkeypatch.setenv("SFP_OPTIONS_POSITION_MARKS", str(tmp_path / "marks.csv"))
    monkeypatch.setenv("SFP_OPTIONS_GREEKS", str(tmp_path / "greeks.csv"))
    monkeypatch.setenv("SFP_OPTIONS_BETAS", str(tmp_path / "betas.csv"))
    monkeypatch.delenv("SFP_OPTIONS_ACTIVITY_EXCLUDED_SYMBOLS", raising=False)
    return tmp_path


def _tx(tx_id, *, symbol="ABC   260821P00050000", underlying="ABC",
        action="Sell to Open", quantity="1", net_value="99", value="100",
        instrument="Equity Option", transaction_type="Trade", sub_type=None):
    return {
        "id": tx_id,
        "executed_at": f"2026-07-{10 + tx_id:02d}T15:00:00+00:00",
        "transaction_date": f"2026-07-{10 + tx_id:02d}",
        "transaction_type": transaction_type,
        "transaction_sub_type": sub_type or action,
        "instrument_type": instrument,
        "symbol": symbol,
        "underlying_symbol": underlying,
        "action": action,
        "quantity": quantity,
        "price": "1.00",
        "value": value,
        "net_value": net_value,
        "commission": "-0.50",
        "regulatory_fees": "-0.50",
        "order_id": 1000 + tx_id,
        "description": f"{action} {symbol}",
    }


def _mark(*, symbol="ABC   260821P00050000", underlying="ABC",
          quantity="1", direction="Short", mark_price="0.40", multiplier="100"):
    return {
        "instrument_type": "Equity Option",
        "symbol": symbol,
        "underlying_symbol": underlying,
        "quantity": quantity,
        "quantity_direction": direction,
        "multiplier": multiplier,
        "mark": "40",
        "mark_price": mark_price,
        "updated_at": "2026-07-20T15:00:00+00:00",
    }


def test_credentials_read_inline_app_env_values(activity_env, monkeypatch):
    monkeypatch.delenv("SFP_TASTY_ENV_FILE", raising=False)
    monkeypatch.setenv("TT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TT_REFRESH_TOKEN", "token")
    monkeypatch.setenv("TT_ENV", "live")

    assert options_activity._credentials() == ("secret", "token", "live")


def _greek(*, event_symbol=".ABC260821P50", volatility="0.44"):
    observed = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)
    return {
        "event_symbol": event_symbol,
        "time": int(observed.timestamp() * 1000),
        "event_time": int(observed.timestamp() * 1000),
        "volatility": volatility,
        "price": "0.40", "delta": "-0.25", "gamma": "0.02",
        "theta": "-0.01", "rho": "-0.03", "vega": "0.08",
    }


def _beta(*, symbol="ABC", beta="1.25"):
    return {
        "symbol": symbol,
        "beta": beta,
        "beta_updated_at": "2026-07-19T17:00:34.617000+00:00",
    }


def test_sync_is_idempotent_auto_groups_and_marks_open_pnl(activity_env):
    provider = lambda _start, _end: ([_tx(1)], [_mark()], {"environment": "live"})
    first = options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=provider)
    second = options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=provider)

    assert first["events_inserted"] == 1
    assert first["groups_created"] == 1
    assert second["events_inserted"] == 0
    assert second["groups_created"] == 0

    snap = options_activity.snapshot("TRADING")
    assert len(snap["events"]) == 1
    group = snap["groups"][0]
    assert group["name"] == "ABC 2026"
    assert group["net_cash_flow"] == 99.0
    assert group["open_market_value"] == -40.0
    assert group["total_pnl"] == 59.0
    assert group["realized_pnl"] is None
    assert group["position_status"] == "OPEN"
    assert group["pnl_completeness"] == "INDICATIVE"
    assert group["open_positions"] == [{
        "contract_key": "ABC 260821P00050000", "quantity": -1.0,
        "option_type": "PUT", "expiry": "2026-08-21", "strike": 50.0,
        "mark_price": 0.4, "market_value": -40.0,
    }]
    assert options_activity.snapshot()["reconciliation_issues"] == []


def test_risk_rows_are_current_broker_positions(activity_env):
    transactions = [
        _tx(1),
        _tx(2, symbol="ABC   260821C00060000", action="Buy to Open",
            net_value="-151", value="-150"),
    ]
    marks = [
        _mark(),
        _mark(symbol="ABC   260821C00060000", direction="Long", mark_price="1.60"),
    ]
    options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (transactions, marks, {"environment": "live"}),
    )

    rows = sorted(options_activity.risk_rows(), key=lambda row: row["trade_type"])
    assert [row["trade_type"] for row in rows] == ["LONG_CALL", "SHORT_PUT"]
    assert {row["status"] for row in rows} == {"OPEN"}
    assert {row["wheel_id"] for row in rows} == {"ABC 2026"}
    assert {row["symbol"] for row in rows} == {"ABC"}
    assert {row["contract_key"] for row in rows} == {
        "ABC 260821C00060000", "ABC 260821P00050000",
    }
    assert {row["mark_price"] for row in rows} == {0.4, 1.6}
    assert all(row["mark_retrieved_at"] for row in rows)


def test_risk_rows_report_share_coverage_for_short_calls(activity_env):
    """Shares and the call against them arrive as separate broker positions."""
    transactions = [
        _tx(1, symbol="ABC", underlying="ABC", action="Buy to Open",
            quantity="100", instrument="Equity", net_value="-8000", value="-8000"),
        _tx(2, symbol="ABC   260821C00060000", action="Sell to Open",
            net_value="150", value="151"),
    ]
    marks = [
        _mark(symbol="ABC", quantity="100", direction="Long", mark_price="80",
              multiplier="1") | {"instrument_type": "Equity"},
        _mark(symbol="ABC   260821C00060000", direction="Short", mark_price="1.60"),
    ]
    options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (transactions, marks, {"environment": "live"}),
    )

    rows = {row["trade_type"]: row for row in options_activity.risk_rows()}

    assert "COVERED_CALL" in rows, "100 shares back the single short call"
    assert rows["COVERED_CALL"]["coverage"] == "COVERED"
    assert rows["COVERED_CALL"]["covered_contracts"] == 1
    assert rows["STOCK"]["qty"] == 100.0


def test_risk_rows_mark_a_short_call_without_shares_uncovered(activity_env):
    options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (
            [_tx(1, symbol="ABC   260821C00060000", action="Sell to Open")],
            [_mark(symbol="ABC   260821C00060000", direction="Short")],
            {"environment": "live"},
        ),
    )

    row = next(row for row in options_activity.risk_rows()
               if row["trade_type"] in {"SHORT_CALL", "COVERED_CALL"})

    assert row["trade_type"] == "SHORT_CALL"
    assert row["coverage"] == "UNCOVERED"
    assert row["covered_contracts"] == 0


def test_sync_persists_exact_timestamped_tastytrade_iv_and_beta(activity_env):
    report = options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (
            [_tx(1)], [_mark()],
            {"environment": "live", "greeks": [_greek()], "greeks_error": None,
             "betas": [_beta()], "betas_error": None},
        ),
    )
    rows = options_activity._read_csv(
        options_activity.config.options_greeks_csv(), options_activity.GREEKS_HEADERS
    )
    assert report["greeks_observed"] == 1
    assert report["greeks_missing"] == 0
    assert rows[0]["contract_key"] == "ABC 260821P00050000"
    assert rows[0]["streamer_symbol"] == ".ABC260821P50"
    assert rows[0]["implied_volatility"] == "0.44"
    assert rows[0]["observed_at"] == "2026-07-20T15:00:00+00:00"
    betas = options_activity._read_csv(
        options_activity.config.options_betas_csv(), options_activity.BETA_HEADERS
    )
    assert report["betas_observed"] == 1
    assert report["betas_missing"] == 0
    assert betas == [{
        "schema_version": "1", "source": "TASTYTRADE_MARKET_METRICS",
        "symbol": "ABC", "beta": "1.25",
        "beta_updated_at": "2026-07-19T17:00:34.617000+00:00",
        "retrieved_at": report["retrieved_at"],
    }]


def test_broker_position_totals_are_not_manual_ledger_pnl(activity_env):
    rows = [
        {"account": "TRADING", "status": "OPEN", "trade_type": "SHORT_PUT",
         "strike": 50.0, "qty": 2, "non_standard": False},
        {"account": "TRADING", "status": "OPEN", "trade_type": "SHORT_CALL",
         "strike": 60.0, "qty": 1, "non_standard": False},
    ]
    totals = options_portfolio._totals(rows, ["TRADING"])["combined"]
    assert totals == {
        "gross_assignment_obligation": 10_000.0,
        "open_broker_positions": 2,
        "open_short_puts": 1,
        "open_short_calls": 1,
    }


def test_flat_group_cash_flow_is_realized_pnl(activity_env):
    transactions = [
        _tx(1),
        _tx(2, action="Buy to Close", net_value="-41", value="-40"),
    ]
    options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (transactions, [], {"environment": "live"}),
    )
    group = options_activity.snapshot()["groups"][0]
    assert group["position_status"] == "FLAT"
    assert group["net_cash_flow"] == 58.0
    assert group["open_market_value"] == 0.0
    assert group["realized_pnl"] == 58.0
    assert group["total_pnl"] == 58.0


def test_expiration_is_recorded_as_zero_cash_expired_event(activity_env):
    opening = _tx(1, symbol="GLW   260724C00170000", underlying="GLW",
                  action="Sell to Open", net_value="210.867", value="212")
    expiration = _tx(
        2, symbol="GLW   260724C00170000", underlying="GLW",
        action="Buy to Close", quantity="1", net_value="0", value="0",
        transaction_type="Receive Deliver", sub_type="Expiration",
    )
    expiration.update({
        "price": "", "commission": "", "regulatory_fees": "",
        "clearing_fees": "", "proprietary_index_option_fees": "", "other_charge": "",
        "description": "Removal of 1 GLW 07/24/26 Call 170.00 due to expiration.",
    })

    options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 25),
        provider=lambda _start, _end: ([opening, expiration], [], {"environment": "live"}),
    )

    event = next(row for row in options_activity.snapshot()["events"]
                 if row["source_transaction_id"] == "2")
    assert event["transaction_type"] == "Receive Deliver"
    assert event["transaction_sub_type"] == "Expiration"
    assert event["description"].endswith("due to expiration.")
    assert event["action"] == "Expired"
    assert event["position_delta"] is None
    assert all(event[field] == 0 for field in (
        "price", "value", "net_value", "fee_effect", "commission", "regulatory_fees",
        "clearing_fees", "proprietary_index_option_fees", "other_charge",
    ))
    group = options_activity.snapshot()["groups"][0]
    assert group["position_status"] == "FLAT"
    assert group["realized_pnl"] == pytest.approx(210.867)


def test_assignment_imports_option_removal_and_equity_delivery(activity_env):
    option = _tx(1)
    delivery = _tx(
        2, symbol="ABC", action="Buy to Open", quantity="100", net_value="-5005",
        value="-5000", instrument="Equity", transaction_type="Receive Deliver",
    )
    assignment = _tx(
        3, action=None, quantity="1", net_value="0", value="0",
        transaction_type="Receive Deliver", sub_type="Assignment",
    )
    stock_exit = _tx(
        4, symbol="ABC", action="Sell to Close", quantity="100", net_value="5495",
        value="5500", instrument="Equity", transaction_type="Trade",
    )
    same_symbol_stock_buy = _tx(
        5, symbol="ABC", action="Buy to Open", quantity="10", net_value="-600",
        value="-600", instrument="Equity", transaction_type="Trade",
    )
    unrelated_stock_buy = _tx(
        6, symbol="XYZ", underlying="XYZ", action="Buy to Open", quantity="10",
        net_value="-600", value="-600", instrument="Equity", transaction_type="Trade",
    )
    report = options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (
            [option, delivery, assignment, stock_exit, same_symbol_stock_buy,
             unrelated_stock_buy], [],
            {"environment": "live"},
        ),
    )
    assert report["option_events_selected"] == 5
    events = options_activity.snapshot()["events"]
    assert {row["instrument_type"] for row in events} == {"Equity Option", "Equity"}
    assert {row["transaction_sub_type"] for row in events} >= {"Assignment", "Buy to Open"}
    assert {row["source_transaction_id"] for row in events} == {"1", "2", "3", "4", "5"}


def test_group_assignment_requires_same_account_and_symbol(activity_env):
    transactions = [
        _tx(1),
        _tx(2, symbol="XYZ   260821C00100000", underlying="XYZ"),
    ]
    options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (transactions, [], {"environment": "live"}),
    )
    snap = options_activity.snapshot()
    abc_group = next(row for row in snap["groups"] if row["symbol"] == "ABC")
    xyz_event = next(row for row in snap["events"] if row["underlying_symbol"] == "XYZ")
    with pytest.raises(options_activity.ActivityValidationError, match="same account and symbol"):
        options_activity.assign_event(xyz_event["id"], abc_group["group_id"])

    custom = options_activity.create_group({
        "account": "TRADING", "symbol": "XYZ", "name": "XYZ earnings repair",
    })
    assigned = options_activity.assign_event(xyz_event["id"], custom["group_id"])
    assert assigned["group_id"] == custom["group_id"]
    assert next(row for row in options_activity.snapshot()["events"]
                if row["id"] == xyz_event["id"])["group_name"] == "XYZ earnings repair"


def test_targeted_pre_window_import_is_idempotent_and_joins_existing_group(activity_env):
    options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: ([_tx(1)], [_mark()], {"environment": "live"}),
    )
    old = _tx(
        9, action="Buy to Open", quantity="1", value="-50", net_value="-51"
    )
    old["executed_at"] = "2025-09-19T15:00:00+00:00"
    old["transaction_date"] = "2025-09-19"

    first = options_activity.import_broker_events([old])
    second = options_activity.import_broker_events([old])
    assert first["events_inserted"] == 1
    assert first["events_auto_grouped"] == 1
    assert second["events_inserted"] == 0
    assert len(options_activity.snapshot()["groups"]) == 1
    imported = next(row for row in options_activity.snapshot()["events"]
                    if row["source_transaction_id"] == "9")
    assert imported["group_name"] == "ABC 2026"


def test_excluded_symbols_are_not_synced_or_imported(activity_env, monkeypatch):
    monkeypatch.setenv("SFP_OPTIONS_ACTIVITY_EXCLUDED_SYMBOLS", "abc, JOBY")
    report = options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (
            [_tx(1), _tx(2, symbol="XYZ   260821C00100000", underlying="XYZ")],
            [_mark(), _mark(symbol="XYZ   260821C00100000", underlying="XYZ")],
            {"environment": "live"},
        ),
    )
    assert report["option_events_selected"] == 1
    assert {row["underlying_symbol"] for row in options_activity.snapshot()["events"]} == {"XYZ"}
    assert options_activity.import_broker_events([_tx(3)])["events_received"] == 0


def test_remove_symbols_cleans_events_groups_memberships_marks_greeks_and_betas(activity_env):
    options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (
            [_tx(1), _tx(2, symbol="XYZ   260821C00100000", underlying="XYZ")],
            [_mark(), _mark(symbol="XYZ   260821C00100000", underlying="XYZ")],
            {"environment": "live", "greeks": [_greek()],
             "betas": [_beta(), _beta(symbol="XYZ", beta="0.8")]},
        ),
    )
    result = options_activity.remove_symbols({"abc"})
    assert result == {
        "events_removed": 1,
        "groups_removed": 1,
        "memberships_removed": 1,
        "marks_removed": 1,
        "greeks_removed": 1,
        "betas_removed": 1,
    }
    snap = options_activity.snapshot()
    assert {row["underlying_symbol"] for row in snap["events"]} == {"XYZ"}
    assert {row["symbol"] for row in snap["groups"]} == {"XYZ"}
    assert snap["reconciliation_issues"] == []


def _joby_provider(_start, _end):
    """A ledger whose imported history is missing the opening assignment: the
    equity events sum to -100 shares while the broker reports the account flat.

    The expired option leg is what pulls the equity executions into the options
    ledger at all (see `_select_transactions`) and nets to no position itself.
    """
    return (
        [
            _tx(1, symbol="JOBY  260417C00016000", underlying="JOBY",
                instrument="Equity Option", transaction_type="Receive Deliver",
                sub_type="Expiration", action="Sell to Close"),
            _tx(2, symbol="JOBY", underlying="JOBY", instrument="Equity",
                action="Sell to Close", quantity="100", value="-900", net_value="-900"),
        ],
        [],
        {"environment": "live"},
    )


def test_manual_event_resolves_a_position_mismatch(activity_env):
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    issue = options_activity.snapshot()["reconciliation_issues"][0]
    assert issue["contract_key"] == "JOBY"
    assert (issue["activity_quantity"], issue["broker_quantity"]) == (-100.0, 0.0)

    created = options_activity.create_manual_event({
        "account": "TRADING", "contract_key": "JOBY", "quantity": 100,
        "transaction_date": "2025-11-21", "price": "13.00",
        "net_cash": "-1300.00", "fees": "-0.50",
    })
    snap = options_activity.snapshot()
    assert snap["reconciliation_issues"] == []
    assert [row["id"] for row in snap["manual_events"]] == [created["event_id"]]

    options_activity.delete_manual_event(created["event_id"])
    restored = options_activity.snapshot()
    assert restored["manual_events"] == []
    assert restored["reconciliation_issues"][0]["activity_quantity"] == -100.0


def test_manual_events_survive_a_tastytrade_sync(activity_env):
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    created = options_activity.create_manual_event({
        "account": "TRADING", "contract_key": "JOBY", "quantity": 100,
        "transaction_date": "2025-11-21", "net_cash": "-1300.00", "fees": "-0.50",
    })
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    snap = options_activity.snapshot()
    assert [row["id"] for row in snap["manual_events"]] == [created["event_id"]]
    assert snap["reconciliation_issues"] == []


def test_manual_event_records_signed_delta_and_derived_fee_effect(activity_env):
    created = options_activity.create_manual_event({
        "account": "TRADING", "contract_key": "JOBY", "quantity": -3,
        "transaction_date": "2025-11-21", "net_cash": "-1300.00", "fees": "-0.50",
    })
    event = next(row for row in options_activity.snapshot()["events"]
                 if row["id"] == created["event_id"])
    assert event["source"] == "MANUAL"
    assert (event["position_delta"], event["quantity"]) == (-3.0, 3.0)
    # fee_effect must stay net_value - value so group P/L math is unchanged.
    assert event["net_value"] - event["value"] == pytest.approx(event["fee_effect"])


def test_broker_events_cannot_be_deleted_through_the_manual_path(activity_env):
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    broker_id = next(row["id"] for row in options_activity.snapshot()["events"]
                     if row["source"] == "TASTYTRADE")
    with pytest.raises(options_activity.ActivityValidationError):
        options_activity.delete_manual_event(broker_id)


def test_manual_event_rejects_zero_quantity_and_bad_date(activity_env):
    with pytest.raises(options_activity.ActivityValidationError):
        options_activity.create_manual_event({
            "contract_key": "JOBY", "quantity": 0, "transaction_date": "2025-11-21"})
    with pytest.raises(options_activity.ActivityValidationError):
        options_activity.create_manual_event({
            "contract_key": "JOBY", "quantity": 100, "transaction_date": "11/21/2025"})


def test_manual_event_edit_updates_values_and_keeps_identity(activity_env):
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    created = options_activity.create_manual_event({
        "account": "TRADING", "contract_key": "JOBY", "quantity": 100,
        "transaction_date": "2025-11-21", "net_cash": "-1300.00", "fees": "-0.50",
    })
    options_activity.update_manual_event(created["event_id"], {
        "quantity": 250, "transaction_date": "2025-11-24",
        "price": "12.00", "net_cash": "-3000.00", "fees": "-1.25",
        "description": "corrected assignment",
    })
    event = next(row for row in options_activity.snapshot()["events"]
                 if row["id"] == created["event_id"])
    assert event["id"] == created["event_id"]          # identity survives the edit
    assert event["source"] == "MANUAL"
    assert event["contract_key"] == "JOBY"
    assert (event["position_delta"], event["quantity"]) == (250.0, 250.0)
    assert event["transaction_date"] == "2025-11-24"
    assert event["net_value"] == pytest.approx(-3000.0)
    assert event["net_value"] - event["value"] == pytest.approx(event["fee_effect"])
    assert event["description"] == "corrected assignment"


def test_manual_event_edit_keeps_its_group_membership(activity_env):
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    group_id = options_activity.snapshot()["groups"][0]["group_id"]
    created = options_activity.create_manual_event({
        "account": "TRADING", "contract_key": "JOBY", "quantity": 100,
        "transaction_date": "2025-11-21", "net_cash": "-1300.00", "fees": "-0.50",
        "group_id": group_id,
    })
    options_activity.update_manual_event(created["event_id"], {
        "quantity": 100, "transaction_date": "2025-11-24", "net_cash": "-1300.00", "fees": "-0.50",
    })
    event = next(row for row in options_activity.snapshot()["events"]
                 if row["id"] == created["event_id"])
    assert event["group_id"] == group_id


def test_manual_event_edit_rejects_broker_rows_and_bad_values(activity_env):
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    broker_id = next(row["id"] for row in options_activity.snapshot()["events"]
                     if row["source"] == "TASTYTRADE")
    with pytest.raises(options_activity.ActivityValidationError):
        options_activity.update_manual_event(broker_id, {
            "quantity": 1, "transaction_date": "2025-11-21"})
    created = options_activity.create_manual_event({
        "account": "TRADING", "contract_key": "JOBY", "quantity": 100,
        "transaction_date": "2025-11-21", "net_cash": "0", "fees": "0",
    })
    with pytest.raises(options_activity.ActivityValidationError):
        options_activity.update_manual_event(created["event_id"], {
            "quantity": 0, "transaction_date": "2025-11-21"})
