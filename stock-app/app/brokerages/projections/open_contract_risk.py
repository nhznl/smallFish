"""Nearest-expiry open-option risk for the Symbol Ledger.

Stock price, DTE, short-option breakeven bands, and ITM / near-strike state are
derived once here so the list and detail envelopes stay in sync. Spot prefers a
live equity mark when shares are held; otherwise it falls back to the latest
cached daily close. Nothing here calls a network provider.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from ... import config, data_reader
from .components import Component
from .numbers import number as _number

NEAR_STRIKE_FRACTION = Decimal("0.05")
ZERO = Decimal("0")

# Ledger underliers whose daily closes live under a different cache symbol.
# Keep this tiny and explicit — futures roots are not in the equity universe.
CACHED_CLOSE_SYMBOL_ALIASES = {
    "/ESU6": "ESU26.CME",
}

STRATEGY_SHORT_CALL = "SHORT CALL"
STRATEGY_SHORT_PUT = "SHORT PUT"
STRATEGY_CALL = "CALL"
STRATEGY_PUT = "PUT"
STRATEGY_SHORT_STRANGLE = "SHORT STRANGLE"
STRATEGY_STRANGLE = "STRANGLE"
STRATEGY_SYNTHETIC_LONG = "SYNTHETIC LONG"
STRATEGY_PUT_CREDIT_SPREAD = "PUT CREDIT SPREAD"
STRATEGY_PUT_DEBIT_SPREAD = "PUT DEBIT SPREAD"
STRATEGY_CUSTOM = "CUSTOM"

SpotLookup = Callable[[str], Decimal | None]


def _parse_expiry(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _open_options(components: list[Component]) -> list[Component]:
    return [
        row for row in components
        if row.instrument == "OPTION" and row.state == "OPEN"
        and row.option_type in {"CALL", "PUT"}
    ]


def classify_open_strategy(components: list[Component]) -> str | None:
    """Name the open-option structure, or ``None`` when nothing is open."""
    options = _open_options(components)
    if not options:
        return None

    short_calls = [
        row for row in options if row.side == "SHORT" and row.option_type == "CALL"
    ]
    short_puts = [
        row for row in options if row.side == "SHORT" and row.option_type == "PUT"
    ]
    long_calls = [
        row for row in options if row.side == "LONG" and row.option_type == "CALL"
    ]
    long_puts = [
        row for row in options if row.side == "LONG" and row.option_type == "PUT"
    ]
    has_short_call = bool(short_calls)
    has_short_put = bool(short_puts)
    has_long_call = bool(long_calls)
    has_long_put = bool(long_puts)
    has_call = has_short_call or has_long_call
    has_put = has_short_put or has_long_put

    # Vertical put spreads require short + long puts only, all on one expiry.
    if has_put and not has_call and short_puts and long_puts:
        put_expiries = {
            row.expiry for row in (*short_puts, *long_puts) if row.expiry
        }
        if len(put_expiries) != 1:
            return STRATEGY_CUSTOM
        for short in short_puts:
            if short.strike is None:
                continue
            for long in long_puts:
                if long.strike is None:
                    continue
                if long.strike < short.strike:
                    return STRATEGY_PUT_CREDIT_SPREAD
                if long.strike > short.strike:
                    return STRATEGY_PUT_DEBIT_SPREAD
        return STRATEGY_CUSTOM

    if has_short_call and has_short_put and not has_long_call and not has_long_put:
        return STRATEGY_SHORT_STRANGLE
    if has_long_call and has_long_put and not has_short_call and not has_short_put:
        return STRATEGY_STRANGLE
    if has_long_call and has_short_put and not has_short_call and not has_long_put:
        return STRATEGY_SYNTHETIC_LONG

    if has_short_call and not has_short_put and not has_long_call and not has_long_put:
        return STRATEGY_SHORT_CALL
    if has_short_put and not has_short_call and not has_long_call and not has_long_put:
        return STRATEGY_SHORT_PUT
    if has_long_call and not has_long_put and not has_short_call and not has_short_put:
        return STRATEGY_CALL
    if has_long_put and not has_long_call and not has_short_call and not has_short_put:
        return STRATEGY_PUT

    return STRATEGY_CUSTOM


def _premium_per_share(component: Component) -> Decimal | None:
    if component.open_price_per_unit is not None:
        return abs(component.open_price_per_unit)
    qty = abs(component.quantity)
    multiplier = component.multiplier if component.multiplier else Decimal("100")
    if qty == 0 or multiplier == 0 or component.net_cash_flow is None:
        return None
    return abs(component.net_cash_flow) / (qty * multiplier)


def _pick_threatened_short(
    shorts: list[Component], *, option_type: str
) -> Component | None:
    matching = [
        row for row in shorts
        if row.option_type == option_type and row.strike is not None
    ]
    if not matching:
        return None
    # Lowest call / highest put is the strike most likely to be tested first.
    if option_type == "CALL":
        return min(matching, key=lambda row: (row.strike, row.id))
    return max(matching, key=lambda row: (row.strike, row.id))


def _near_strike(spot: Decimal, strike: Decimal) -> bool:
    if strike <= 0:
        return False
    return abs(spot - strike) / strike <= NEAR_STRIKE_FRACTION


def default_cached_close(symbol: str, *, as_of: date | None = None) -> Decimal | None:
    """Latest cached daily close at or before ``as_of`` (UTC today by default)."""
    today = as_of or datetime.now(timezone.utc).date()
    years = [today.year, today.year - 1]
    cache_symbol = CACHED_CLOSE_SYMBOL_ALIASES.get(symbol, symbol)
    try:
        frame = data_reader.read_prices(
            config.price_cache_root(), cache_symbol, years
        )
    except Exception:
        return None
    if frame is None or frame.empty:
        return None
    closes = frame.loc[frame["date"].dt.date <= today, "close"]
    if closes.empty:
        return None
    try:
        value = Decimal(str(closes.iloc[-1]))
    except Exception:
        return None
    return value if value > 0 else None


def resolve_spot(
    components: list[Component],
    symbol: str,
    *,
    cached_close: SpotLookup | None = None,
) -> tuple[Decimal | None, str | None]:
    for row in components:
        if (
            row.instrument == "EQUITY"
            and row.state == "OPEN"
            and row.mark_per_unit is not None
            and row.mark_per_unit > 0
        ):
            return row.mark_per_unit, "EQUITY_MARK"
    lookup = cached_close or default_cached_close
    close = lookup(symbol)
    if close is not None and close > 0:
        return close, "CACHED_CLOSE"
    return None, None


def build_open_contract_risk(
    components: list[Component],
    *,
    symbol: str,
    as_of: date | None = None,
    cached_close: SpotLookup | None = None,
) -> dict[str, Any]:
    """Additive Symbol Ledger fields for nearest open-option risk."""
    today = as_of or datetime.now(timezone.utc).date()
    open_options = [
        row for row in components
        if row.instrument == "OPTION" and row.state == "OPEN" and _parse_expiry(row.expiry)
    ]
    spot, spot_source = resolve_spot(components, symbol, cached_close=cached_close)
    strategy = classify_open_strategy(components)

    if not open_options:
        return {
            "underlying_price": _number(spot),
            "underlying_price_source": spot_source,
            "dte": None,
            "nearest_expiry": None,
            "breakeven": None,
            "strike_risk": "NONE",
            "strategy": strategy,
        }

    nearest_expiry = min(row.expiry for row in open_options if row.expiry)
    expiry_date = _parse_expiry(nearest_expiry)
    dte = (expiry_date - today).days if expiry_date is not None else None
    if dte is not None and dte < 0:
        dte = 0

    nearest_shorts = [
        row for row in open_options
        if row.expiry == nearest_expiry and row.side == "SHORT"
    ]
    short_call = _pick_threatened_short(nearest_shorts, option_type="CALL")
    short_put = _pick_threatened_short(nearest_shorts, option_type="PUT")

    breakeven: dict[str, Any] | None = None
    if spot is not None and (short_call is not None or short_put is not None):
        call_premium = _premium_per_share(short_call) if short_call else None
        put_premium = _premium_per_share(short_put) if short_put else None
        if short_call is not None and short_put is not None:
            if (
                short_call.strike is not None and call_premium is not None
                and short_put.strike is not None and put_premium is not None
            ):
                breakeven = {
                    "kind": "SHORT_STRANGLE",
                    "points": [
                        {
                            "role": "BREAKEVEN",
                            "value": _number(short_put.strike - put_premium),
                        },
                        {"role": "SPOT", "value": _number(spot)},
                        {
                            "role": "BREAKEVEN",
                            "value": _number(short_call.strike + call_premium),
                        },
                    ],
                }
        elif short_call is not None and short_call.strike is not None and call_premium is not None:
            breakeven = {
                "kind": "SHORT_CALL",
                "points": [
                    {"role": "SPOT", "value": _number(spot)},
                    {"role": "STRIKE", "value": _number(short_call.strike)},
                    {
                        "role": "BREAKEVEN",
                        "value": _number(short_call.strike + call_premium),
                    },
                ],
            }
        elif short_put is not None and short_put.strike is not None and put_premium is not None:
            breakeven = {
                "kind": "SHORT_PUT",
                "points": [
                    {
                        "role": "BREAKEVEN",
                        "value": _number(short_put.strike - put_premium),
                    },
                    {"role": "STRIKE", "value": _number(short_put.strike)},
                    {"role": "SPOT", "value": _number(spot)},
                ],
            }

    strike_risk = "NONE"
    if nearest_shorts and spot is None:
        strike_risk = "UNKNOWN"
    elif nearest_shorts and spot is not None:
        itm = False
        near = False
        for leg in (short_call, short_put):
            if leg is None or leg.strike is None:
                continue
            if leg.option_type == "CALL" and spot > leg.strike:
                itm = True
            elif leg.option_type == "PUT" and spot < leg.strike:
                itm = True
            elif _near_strike(spot, leg.strike):
                near = True
        if itm:
            strike_risk = "ITM"
        elif near:
            strike_risk = "NEAR_STRIKE"

    return {
        "underlying_price": _number(spot),
        "underlying_price_source": spot_source,
        "dte": dte,
        "nearest_expiry": nearest_expiry,
        "breakeven": breakeven,
        "strike_risk": strike_risk,
        "strategy": strategy,
    }
