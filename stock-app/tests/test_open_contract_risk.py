"""Nearest-expiry open-option risk for the Symbol Ledger."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.brokerages.projections.components import Component
from app.brokerages.projections.open_contract_risk import (
    build_open_contract_risk,
    classify_open_strategy,
)


def _component(**overrides) -> Component:
    base = dict(
        id="tastytrade:TRADING:OPTION:x",
        account_id="TRADING",
        account="TRADING",
        instrument="OPTION",
        symbol="ABC",
        side="SHORT",
        option_type="PUT",
        state="OPEN",
        quantity=Decimal("-1"),
        strike=Decimal("50"),
        expiry="2026-08-21",
        cash_in=Decimal("600"),
        cash_out=Decimal("0"),
        net_cash_flow=Decimal("600"),
        mark_per_unit=Decimal("0.75"),
        mark_observed_at=None,
        open_price_per_unit=Decimal("6"),
        multiplier=Decimal("100"),
        open_market_value=Decimal("-75"),
        realized_pnl=None,
        total_pnl=Decimal("525"),
        pnl_completeness="INDICATIVE",
        cash_flow_basis="BROKER_ACTIVITY",
        open_leg_count=1,
        event_count=1,
        contract_key="ABC   260821P00050000",
        provenance={},
        missing=(),
    )
    base.update(overrides)
    return Component(**base)


def _equity(**overrides) -> Component:
    defaults = dict(
        id="tastytrade:TRADING:EQUITY:ABC",
        instrument="EQUITY",
        side="LONG",
        option_type=None,
        quantity=Decimal("100"),
        strike=None,
        expiry=None,
        cash_in=Decimal("0"),
        cash_out=Decimal("-10000"),
        net_cash_flow=Decimal("-10000"),
        mark_per_unit=Decimal("48"),
        open_price_per_unit=Decimal("100"),
        multiplier=Decimal("1"),
        open_market_value=Decimal("4800"),
        contract_key=None,
    )
    defaults.update(overrides)
    return _component(**defaults)


AS_OF = date(2026, 7, 30)


def test_short_put_band_and_near_strike():
    # Spot 51 vs $50 strike is OTM but within 5% → NEAR_STRIKE; BE = 50 - 6 = 44.
    risk = build_open_contract_risk(
        [_component()], symbol="ABC", as_of=AS_OF,
        cached_close=lambda _symbol: Decimal("51"),
    )
    assert risk["underlying_price"] == 51.0
    assert risk["underlying_price_source"] == "CACHED_CLOSE"
    assert risk["dte"] == 22
    assert risk["nearest_expiry"] == "2026-08-21"
    assert risk["strike_risk"] == "NEAR_STRIKE"
    assert risk["breakeven"]["kind"] == "SHORT_PUT"
    assert [point["role"] for point in risk["breakeven"]["points"]] == [
        "BREAKEVEN", "STRIKE", "SPOT",
    ]
    assert [point["value"] for point in risk["breakeven"]["points"]] == [
        44.0, 50.0, 51.0,
    ]


def test_short_call_band_and_itm():
    risk = build_open_contract_risk(
        [_component(
            option_type="CALL",
            strike=Decimal("60"),
            open_price_per_unit=Decimal("3"),
            net_cash_flow=Decimal("300"),
            cash_in=Decimal("300"),
            contract_key="ABC   260821C00060000",
        )],
        symbol="ABC", as_of=AS_OF,
        cached_close=lambda _symbol: Decimal("62"),
    )
    assert risk["strike_risk"] == "ITM"
    assert risk["breakeven"]["kind"] == "SHORT_CALL"
    assert [point["value"] for point in risk["breakeven"]["points"]] == [
        62.0, 60.0, 63.0,
    ]


def test_short_strangle_band():
    risk = build_open_contract_risk(
        [
            _component(
                id="put",
                option_type="PUT",
                strike=Decimal("50"),
                open_price_per_unit=Decimal("2"),
                net_cash_flow=Decimal("200"),
                cash_in=Decimal("200"),
            ),
            _component(
                id="call",
                option_type="CALL",
                strike=Decimal("60"),
                open_price_per_unit=Decimal("1.5"),
                net_cash_flow=Decimal("150"),
                cash_in=Decimal("150"),
                contract_key="ABC   260821C00060000",
            ),
        ],
        symbol="ABC", as_of=AS_OF,
        cached_close=lambda _symbol: Decimal("55"),
    )
    assert risk["strike_risk"] == "NONE"
    assert risk["breakeven"]["kind"] == "SHORT_STRANGLE"
    assert [point["value"] for point in risk["breakeven"]["points"]] == [
        48.0, 55.0, 61.5,
    ]


def test_equity_mark_preferred_over_cached_close():
    risk = build_open_contract_risk(
        [_equity(mark_per_unit=Decimal("51")), _component()],
        symbol="ABC", as_of=AS_OF,
        cached_close=lambda _symbol: Decimal("999"),
    )
    assert risk["underlying_price"] == 51.0
    assert risk["underlying_price_source"] == "EQUITY_MARK"
    assert risk["strike_risk"] == "NEAR_STRIKE"


def test_missing_spot_is_unknown_for_open_shorts():
    risk = build_open_contract_risk(
        [_component()], symbol="ABC", as_of=AS_OF,
        cached_close=lambda _symbol: None,
    )
    assert risk["underlying_price"] is None
    assert risk["strike_risk"] == "UNKNOWN"
    assert risk["breakeven"] is None


def test_cached_close_alias_maps_es_futures_root(monkeypatch, tmp_path):
    from app.brokerages.projections import open_contract_risk as risk_mod

    year_dir = tmp_path / "2026"
    year_dir.mkdir()
    # normalize_symbol("ESU26.CME") -> ESU26-CME
    (year_dir / "ESU26-CME.txt").write_text(
        "07-30-2026,7479.5,7498.0,7468.25,7496.0,7496.0,26522\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(risk_mod.config, "price_cache_root", lambda: tmp_path)

    close = risk_mod.default_cached_close("/ESU6", as_of=date(2026, 7, 30))
    assert close == Decimal("7496.0")

    risk = build_open_contract_risk(
        [_component(symbol="/ESU6")],
        symbol="/ESU6", as_of=date(2026, 7, 30),
    )
    assert risk["underlying_price"] == 7496.0
    assert risk["underlying_price_source"] == "CACHED_CLOSE"


def test_nearest_expiry_among_multiple_contracts():
    risk = build_open_contract_risk(
        [
            _component(expiry="2026-09-18", id="later"),
            _component(expiry="2026-08-07", id="sooner", strike=Decimal("45")),
        ],
        symbol="ABC", as_of=AS_OF,
        cached_close=lambda _symbol: Decimal("40"),
    )
    assert risk["nearest_expiry"] == "2026-08-07"
    assert risk["dte"] == 8
    # Spot 40 vs put 45 is ITM.
    assert risk["strike_risk"] == "ITM"


def test_premium_falls_back_to_net_cash_flow():
    risk = build_open_contract_risk(
        [_component(open_price_per_unit=None, net_cash_flow=Decimal("500"),
                    cash_in=Decimal("500"))],
        symbol="ABC", as_of=AS_OF,
        cached_close=lambda _symbol: Decimal("52"),
    )
    assert risk["breakeven"]["points"][0]["value"] == 45.0  # 50 - 5


def test_long_only_has_dte_but_no_breakeven_or_risk_color():
    risk = build_open_contract_risk(
        [_component(side="LONG", quantity=Decimal("1"), cash_in=Decimal("0"),
                    cash_out=Decimal("-400"), net_cash_flow=Decimal("-400"),
                    open_price_per_unit=Decimal("4"))],
        symbol="ABC", as_of=AS_OF,
        cached_close=lambda _symbol: Decimal("48"),
    )
    assert risk["dte"] == 22
    assert risk["breakeven"] is None
    assert risk["strike_risk"] == "NONE"


def test_picks_most_threatened_short_when_multiple_same_type():
    risk = build_open_contract_risk(
        [
            _component(
                id="far-put",
                strike=Decimal("40"),
                open_price_per_unit=Decimal("1"),
                contract_key="ABC   260821P00040000",
            ),
            _component(
                id="near-put",
                strike=Decimal("49"),
                open_price_per_unit=Decimal("1"),
                contract_key="ABC   260821P00049000",
            ),
        ],
        symbol="ABC", as_of=AS_OF,
        cached_close=lambda _symbol: Decimal("50"),
    )
    # Highest put strike (49) is most threatened; within 5% of spot 50.
    assert risk["strike_risk"] == "NEAR_STRIKE"
    assert risk["breakeven"]["points"][1]["value"] == 49.0


def test_strategy_labels_from_open_contracts():
    assert classify_open_strategy([_component(option_type="CALL")]) == "SHORT CALL"
    assert classify_open_strategy([_component(option_type="PUT")]) == "SHORT PUT"
    assert classify_open_strategy([
        _component(side="LONG", quantity=Decimal("1"), option_type="CALL"),
    ]) == "CALL"
    assert classify_open_strategy([
        _component(side="LONG", quantity=Decimal("1"), option_type="PUT"),
    ]) == "PUT"
    assert classify_open_strategy([
        _component(id="sc", option_type="CALL", strike=Decimal("60")),
        _component(id="sp", option_type="PUT", strike=Decimal("50")),
    ]) == "SHORT STRANGLE"
    assert classify_open_strategy([
        _component(id="lc", side="LONG", quantity=Decimal("1"), option_type="CALL"),
        _component(id="lp", side="LONG", quantity=Decimal("1"), option_type="PUT"),
    ]) == "STRANGLE"
    assert classify_open_strategy([
        _component(id="lc", side="LONG", quantity=Decimal("1"), option_type="CALL"),
        _component(id="sp", option_type="PUT"),
    ]) == "SYNTHETIC LONG"
    assert classify_open_strategy([
        _component(id="sp", option_type="PUT", strike=Decimal("50")),
        _component(
            id="lp", side="LONG", quantity=Decimal("1"), option_type="PUT",
            strike=Decimal("45"),
        ),
    ]) == "PUT CREDIT SPREAD"
    assert classify_open_strategy([
        _component(id="lp", side="LONG", quantity=Decimal("1"), option_type="PUT",
                    strike=Decimal("50")),
        _component(id="sp", option_type="PUT", strike=Decimal("45")),
    ]) == "PUT DEBIT SPREAD"
    assert classify_open_strategy([
        _component(id="sp", option_type="PUT", strike=Decimal("50"),
                    expiry="2026-08-21"),
        _component(
            id="lp", side="LONG", quantity=Decimal("1"), option_type="PUT",
            strike=Decimal("45"), expiry="2026-09-18",
        ),
    ]) == "CUSTOM"
    assert classify_open_strategy([
        _component(id="sc", option_type="CALL"),
        _component(id="lp", side="LONG", quantity=Decimal("1"), option_type="PUT"),
    ]) == "CUSTOM"
    assert classify_open_strategy([_equity()]) is None
    assert build_open_contract_risk(
        [_component()], symbol="ABC", as_of=AS_OF,
        cached_close=lambda _symbol: Decimal("51"),
    )["strategy"] == "SHORT PUT"
