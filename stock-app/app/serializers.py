"""Serialize stock-analysis models to the Angular API's stable JSON shape."""

from __future__ import annotations

from datetime import datetime

from .dates import market_date_iso
from .events_read import UpcomingEarnings
from .stock_model import GainLoss, SETUP_SCORE_VERSION, Stock
from .trend_engine import Daily, float32_json


def gainloss_dict(g: GainLoss) -> dict:
    return {
        "startDate": market_date_iso(g.start_date),
        "startPrice": float32_json(g.start_price),
        "gainLoss": g.gain_loss,
    }


# --------------------------------------------------------------------------- #
# Stock                                                                       #
# --------------------------------------------------------------------------- #


def trade_data_dict(stocks: list[Stock]) -> dict:
    """Build the legacy collection from the focused Stock Detail contract."""
    return {"stocks": [stock_detail_dict(s) for s in stocks]}


def _fifty_two_week_range_dict(s: Stock) -> dict[str, float | None]:
    """Return a true rolling 52-week range from the cached daily bars.

    The scanners use the same cached close as their Price column, so deriving
    the range here avoids a per-row live-quote lookup and keeps the marker
    internally consistent with the scan snapshot.
    """
    bars = s.dailies[-252:]
    if len(bars) < 252 or s.last_trade is None:
        return {
            "fiftyTwoWeekLow": None,
            "fiftyTwoWeekHigh": None,
            "fiftyTwoWeekPosition": None,
        }

    low = min(bar.low for bar in bars)
    high = max(bar.high for bar in bars)
    if high <= low:
        return {
            "fiftyTwoWeekLow": float32_json(low),
            "fiftyTwoWeekHigh": float32_json(high),
            "fiftyTwoWeekPosition": None,
        }

    position = max(0.0, min(100.0, ((s.last_trade.close - low) / (high - low)) * 100))
    return {
        "fiftyTwoWeekLow": float32_json(low),
        "fiftyTwoWeekHigh": float32_json(high),
        "fiftyTwoWeekPosition": round(position, 2),
    }


def stock_range_dict(s: Stock) -> dict[str, str | float | None]:
    """The compact cached-universe range contract for linked portfolio rows."""
    return {"code": s.code, **_fifty_two_week_range_dict(s)}


def _stock_detail_daily_dict(d: Daily) -> dict:
    return {
        "tradeDate": market_date_iso(d.date),
        "open": float32_json(d.open),
        "high": float32_json(d.high),
        "low": float32_json(d.low),
        "close": float32_json(d.close),
        "volume": d.volume,
    }


def stock_detail_dict(s: Stock, yearly_slopes: dict[int, dict] | None = None) -> dict:
    """Return exactly the cached-analysis fields rendered by Stock Detail.

    `yearly_slopes` is supplied by the caller because it is derived on demand
    per symbol rather than carried on every cached Stock.
    """
    def metric(value: float | None, decimals: int = 4) -> float | None:
        return None if value is None else round(value, decimals)

    return {
        "code": s.code,
        "type": s.type,
        "lastTradeStats": _stock_detail_daily_dict(s.last_trade) if s.last_trade else None,
        "yearToDate": gainloss_dict(s.year_to_date) if s.year_to_date else None,
        "midPointToDate": gainloss_dict(s.mid_point_to_date) if s.mid_point_to_date else None,
        "fiveWeeksToDate": gainloss_dict(s.five_weeks_to_date) if s.five_weeks_to_date else None,
        "fiveDaysToDate": gainloss_dict(s.five_days_to_date) if s.five_days_to_date else None,
        "yearlySlopes": {
            str(year): {
                "year": year,
                "weeklySlopes": {str(week): value for week, value in slope["weekly"].items()},
            }
            for year, slope in sorted((yearly_slopes or s.yearly_slopes).items())
        },
        "recentWeeks": [{
            "startDate": market_date_iso(week.start_date) if week.start_date else None,
            "endDate": market_date_iso(week.end_date) if week.end_date else None,
            "avgClose": float32_json(week.avg_close),
        } for week in s.recent_weeks],
        # Daily OHLCV back through the prior year, so the price and technical
        # charts can scope their own ranges client-side. The cache has one bar
        # per session; it does not contain intraday data.
        "dailyBars": [_stock_detail_daily_dict(bar) for bar in s.dailies],
        "atrPct": metric(s.atr_pct),
        "volumeRatio": metric(s.volume_ratio),
        "relativeStrengthSpyOneMonth": metric(s.relative_strength_spy_one_month),
        "setup": s.scanner_setup(),
        "setupScore": s.setup_score(),
        "trendRsi": s.advanced_trend_with_volume.rsi if s.advanced_trend_with_volume else None,
    }


def strategy_stock_dict(s: Stock) -> dict:
    """Return the fields rendered by the Strategy table and details drawer."""
    return {
        "code": s.code,
        "type": s.type,
        "lastTradeStats": {
            "close": float32_json(s.last_trade.close),
        } if s.last_trade else None,
        **_fifty_two_week_range_dict(s),
        "signal": s.signal(),
        "strategyReport": s.strategy_report,
    }


def momentum_stock_dict(s: Stock, earnings: UpcomingEarnings | None = None) -> dict:
    """Return only fields rendered by the scanner table and evidence drawer.

    `earnings` is the shared upcoming-earnings calendar, passed in by the router
    so the whole response is joined against one reading of that artifact. Both
    earnings fields are null when no upcoming event is cached for the symbol.
    """
    def metric(value: float | None, decimals: int = 4) -> float | None:
        return None if value is None else round(value, decimals)

    atv = s.advanced_trend_with_volume
    next_earnings = earnings.next_date(s.code) if earnings else None
    crossover = s.ema14_over_20_cross
    if s.freshness_status != "FRESH":
        crossover = crossover.unavailable()
    return {
        "code": s.code,
        "type": s.type,
        "lastTradeStats": {
            "tradeDate": market_date_iso(s.last_trade.date),
            "close": float32_json(s.last_trade.close),
        } if s.last_trade else None,
        **_fifty_two_week_range_dict(s),
        "yearToDate": gainloss_dict(s.year_to_date) if s.year_to_date else None,
        "midPointToDate": gainloss_dict(s.mid_point_to_date) if s.mid_point_to_date else None,
        "fiveWeeksToDate": gainloss_dict(s.five_weeks_to_date) if s.five_weeks_to_date else None,
        "fiveDaysToDate": gainloss_dict(s.five_days_to_date) if s.five_days_to_date else None,
        "recentWeeks": [{
            "endDate": market_date_iso(week.end_date) if week.end_date else None,
            "avgClose": float32_json(week.avg_close),
            "avgChange": float32_json(week.avg_change),
            "avgVolume": week.avg_volume,
            # Sessions actually behind the week's statistics. A holiday-shortened
            # or partially cached week yields a return volatility computed from
            # fewer daily changes, which the drawer marks as incomplete.
            "sessionCount": len(week.dailies),
            "relativeMomentum": float32_json(week.relative_momentum),
            "relativeMomentumStd": float32_json(week.relative_momentum_std),
        } for week in s.recent_weeks],
        "atrPct": metric(s.atr_pct),
        "realizedVolatilityExpansion": metric(s.realized_volatility_expansion),
        "volumeRatio": metric(s.volume_ratio),
        "averageDollarVolume20": metric(s.average_dollar_volume_20, 2),
        "distanceSma20Pct": metric(s.distance_sma20_pct),
        "rsiChangeFiveDay": metric(s.rsi_change_five_day),
        "macdHistogramChange": metric(s.macd_histogram_change),
        "daysSinceMacdCross": s.days_since_macd_cross,
        "relativeStrengthSpyOneMonth": metric(s.relative_strength_spy_one_month),
        "freshnessStatus": s.freshness_status,
        "evidenceQuality": s.evidence_quality(),
        "setup": s.scanner_setup(),
        "setupScore": s.setup_score(),
        "ema14Over20Cross": {
            "status": crossover.status,
            "sessionsAgo": crossover.sessions_ago,
            "asOfDate": crossover.as_of_date.isoformat() if crossover.as_of_date else None,
        },
        "setupScoreVersion": SETUP_SCORE_VERSION,
        "setupScoreComponents": s.setup_score_components(),
        "setupReason": s.setup_reason(),
        "triggerLabel": s.trigger_label(),
        "nextEarningsDate": market_date_iso(
            datetime(next_earnings.year, next_earnings.month, next_earnings.day)
        ) if next_earnings else None,
        "daysToEarnings": earnings.days_until(s.code) if earnings else None,
        "preliminaryReversal": s.has_preliminary_reversal_evidence(),
        "preliminaryReversalLabel": s.preliminary_reversal_label(),
        "advancedTrendWithVolume": {
            "direction": atv.direction,
            "strength": atv.strength,
            "rsi": atv.rsi,
            "currentTrendDays": atv.current_trend_days,
        } if atv else None,
    }
