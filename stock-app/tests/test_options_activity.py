from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app import config, options_activity


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


def _events(account: str | None = None) -> list[dict[str, str]]:
    """The immutable event ledger, read straight from its artifact.

    The grouped projection these tests used to read through is retired, and
    asserting on the artifact is closer to what actually has to hold anyway.
    """
    rows = options_activity._read_csv(
        config.options_activity_csv(), options_activity.ACTIVITY_HEADERS
    )
    return [row for row in rows if account is None or row["account"] == account]


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


def test_market_data_error_hides_provider_message():
    secret = "test-refresh-token-123"
    account = "account-identifier-987"

    error = options_activity._safe_market_data_error(
        RuntimeError(f"provider rejected {secret} for {account}")
    )

    assert error == (
        "RuntimeError: Tastytrade market data is unavailable; "
        "check the brokerage setup and retry the sync."
    )
    assert secret not in error
    assert account not in error


def test_tastytrade_configuration_preserves_validation_status_codes(monkeypatch):
    monkeypatch.delenv("TT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TT_REFRESH_TOKEN", raising=False)
    with pytest.raises(options_activity.ActivityValidationError) as missing:
        options_activity.fetch_tastytrade(date(2026, 1, 1), date(2026, 1, 2))
    assert missing.value.status_code == 503

    monkeypatch.setenv("TT_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("TT_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("TT_ENV", "production")
    with pytest.raises(options_activity.ActivityValidationError) as invalid:
        options_activity.fetch_tastytrade(date(2026, 1, 1), date(2026, 1, 2))
    assert invalid.value.status_code == 422


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


def test_sync_is_idempotent_and_marks_open_pnl(activity_env):
    """Grouping is retired, so the counters stay at zero; the facts still merge
    by provider id and the marked P/L still comes through."""
    provider = lambda _start, _end: ([_tx(1)], [_mark()], {"environment": "live"})
    first = options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=provider)
    second = options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=provider)

    assert first["events_inserted"] == 1
    assert second["events_inserted"] == 0
    assert first["groups_created"] == second["groups_created"] == 0

    assert len(_events("TRADING")) == 1




def test_sync_materializes_equity_only_positions_for_combined_ledger(activity_env,
                                                                     monkeypatch):
    combined = activity_env / "all_positions.csv"
    monkeypatch.setenv("SFP_TASTYTRADE_POSITIONS", str(combined))
    positions = [
        _mark(),
        _mark(symbol="EQT", underlying="EQT", quantity="20", direction="Long",
              mark_price="25", multiplier="1") | {
                  "instrument_type": "Equity", "average_open_price": "20",
              },
    ]
    options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (
            [_tx(1)], positions, {"environment": "live"}
        ),
    )
    all_positions = options_activity._read_csv(
        combined, options_activity.COMBINED_POSITION_HEADERS
    )
    legacy_marks = options_activity._read_csv(
        options_activity.config.options_position_marks_csv(), options_activity.MARK_HEADERS
    )
    assert {row["underlying_symbol"] for row in all_positions} == {"ABC", "EQT"}
    assert {row["underlying_symbol"] for row in legacy_marks} == {"ABC"}
    assert next(row for row in all_positions if row["underlying_symbol"] == "EQT")[
        "average_open_price"
    ] == "20"








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




def test_a_closing_trade_is_retained_beside_its_opening_one(activity_env):
    transactions = [
        _tx(1),
        _tx(2, action="Buy to Close", net_value="-41", value="-40"),
    ]
    options_activity.sync(
        date(2026, 1, 1), date(2026, 7, 20),
        provider=lambda _start, _end: (transactions, [], {"environment": "live"}),
    )
    # A flat symbol's realized P/L is the Symbol Ledger's answer now; what this
    # still protects is that both closing legs land as immutable facts.
    events = _events()
    assert sorted(row["source_transaction_id"] for row in events) == ["1", "2"]
    assert sum(float(row["net_value"]) for row in events) == 58.0


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

    event = next(row for row in _events()
                 if row["source_transaction_id"] == "2")
    assert event["transaction_type"] == "Receive Deliver"
    assert event["transaction_sub_type"] == "Expiration"
    assert event["description"].endswith("due to expiration.")
    assert event["action"] == "Expired"
    # Blank in the artifact: an expiration removes a position without the
    # provider stating a signed delta.
    assert event["position_delta"] == ""
    assert all(float(event[field]) == 0 for field in (
        "price", "value", "net_value", "fee_effect", "commission", "regulatory_fees",
        "clearing_fees", "proprietary_index_option_fees", "other_charge",
    ))



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
    events = _events()
    assert {row["instrument_type"] for row in events} == {"Equity Option", "Equity"}
    assert {row["transaction_sub_type"] for row in events} >= {"Assignment", "Buy to Open"}
    assert {row["source_transaction_id"] for row in events} == {"1", "2", "3", "4", "5"}





def test_targeted_pre_window_import_is_idempotent(activity_env):
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
    assert second["events_inserted"] == 0
    assert first["events_auto_grouped"] == 0    # grouping is retired
    imported = next(row for row in _events()
                    if row["source_transaction_id"] == "9")
    assert imported["executed_at"].startswith("2025-09-19")


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
    assert {row["underlying_symbol"] for row in _events()} == {"XYZ"}
    assert options_activity.import_broker_events([_tx(3)])["events_received"] == 0


def test_remove_symbols_cleans_events_marks_greeks_and_betas(activity_env):
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
        "marks_removed": 1,
        "greeks_removed": 1,
        "betas_removed": 1,
    }
    assert {row["underlying_symbol"] for row in _events()} == {"XYZ"}


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


def test_a_manual_row_is_added_and_removed_without_touching_broker_facts(activity_env):
    """Whether the row *clears a mismatch* is the Symbol Ledger's question now.

    What still has to hold here is the row's own lifecycle: it joins the ledger,
    it is distinguishable from an imported fact, and deleting it leaves the
    broker events untouched.
    """
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    broker_ids = {row["id"] for row in _events()}

    created = options_activity.create_manual_event({
        "account": "TRADING", "contract_key": "JOBY", "quantity": 100,
        "transaction_date": "2025-11-21", "price": "13.00",
        "net_cash": "-1300.00", "fees": "-0.50",
    })
    manual = [row for row in _events() if row["source"] == options_activity.MANUAL_SOURCE]
    assert [row["id"] for row in manual] == [created["event_id"]]

    options_activity.delete_manual_event(created["event_id"])
    assert {row["id"] for row in _events()} == broker_ids


def test_manual_events_survive_a_tastytrade_sync(activity_env):
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    created = options_activity.create_manual_event({
        "account": "TRADING", "contract_key": "JOBY", "quantity": 100,
        "transaction_date": "2025-11-21", "net_cash": "-1300.00", "fees": "-0.50",
    })
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    manual = [row for row in _events() if row["source"] == options_activity.MANUAL_SOURCE]
    assert [row["id"] for row in manual] == [created["event_id"]]


def test_manual_event_records_signed_delta_and_derived_fee_effect(activity_env):
    created = options_activity.create_manual_event({
        "account": "TRADING", "contract_key": "JOBY", "quantity": -3,
        "transaction_date": "2025-11-21", "net_cash": "-1300.00", "fees": "-0.50",
    })
    event = next(row for row in _events()
                 if row["id"] == created["event_id"])
    assert event["source"] == "MANUAL"
    assert (float(event["position_delta"]), float(event["quantity"])) == (-3.0, 3.0)
    # fee_effect must stay net_value - value: the cash-flow math downstream
    # depends on fees being counted exactly once.
    assert float(event["net_value"]) - float(event["value"]) == pytest.approx(
        float(event["fee_effect"])
    )


def test_broker_events_cannot_be_deleted_through_the_manual_path(activity_env):
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    broker_id = next(row["id"] for row in _events()
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
    event = next(row for row in _events()
                 if row["id"] == created["event_id"])
    assert event["id"] == created["event_id"]          # identity survives the edit
    assert event["source"] == "MANUAL"
    assert event["contract_key"] == "JOBY"
    assert (float(event["position_delta"]), float(event["quantity"])) == (250.0, 250.0)
    assert event["transaction_date"] == "2025-11-24"
    assert float(event["net_value"]) == pytest.approx(-3000.0)
    assert float(event["net_value"]) - float(event["value"]) == pytest.approx(
        float(event["fee_effect"])
    )
    assert event["description"] == "corrected assignment"




def test_manual_event_edit_rejects_broker_rows_and_bad_values(activity_env):
    options_activity.sync(date(2026, 1, 1), date(2026, 7, 20), provider=_joby_provider)
    broker_id = next(row["id"] for row in _events()
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


# --------------------------------------------------------------------------- #
# migration baseline: what the symbol ledger must preserve                      #
# --------------------------------------------------------------------------- #

def _dated_tx(tx_id, *, on, symbol="ABC   260821P00050000", underlying="ABC",
              action="Sell to Open", net_value="600", value="601"):
    """A broker transaction on an explicit date, for cross-year scenarios."""
    row = _tx(tx_id, symbol=symbol, underlying=underlying, action=action,
              net_value=net_value, value=value)
    row["executed_at"] = f"{on}T15:00:00+00:00"
    row["transaction_date"] = on
    return row
