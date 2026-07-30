"""Stock, weekly-bar, and gain/loss models used by stock-analysis endpoints."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np

from models.universe import TYPE_ETF, TYPE_MF, TYPE_STOCK

from . import trend_engine as te
from .trend_engine import (
    Daily,
    AdvancedTrendWithVolume,
    f32,
    round_float32_half_up,
)

RECENT_WEEKS_SIZE = 5
SETUP_SCORE_VERSION = "momentum-v3"

BULLISH_CONTINUATION = "BULLISH_CONTINUATION"
BEARISH_CONTINUATION = "BEARISH_CONTINUATION"
BULLISH_REVERSAL = "BULLISH_REVERSAL"
BEARISH_REVERSAL = "BEARISH_REVERSAL"
WATCH = "WATCH"
NOT_EVALUATED = "NOT_EVALUATED"
STOCK_TYPES = frozenset({TYPE_STOCK, TYPE_ETF, TYPE_MF})


def normalize_stock_type(value: object) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in STOCK_TYPES else TYPE_STOCK


def _sunday_start(d: date) -> date:
    # Python weekday(): Mon=0 .. Sun=6; weekly aggregation starts Sunday.
    return d - timedelta(days=(d.weekday() + 1) % 7)


# --------------------------------------------------------------------------- #
# Weekly                                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class Weekly:
    dailies: list[Daily]
    start_date: datetime | None = None
    end_date: datetime | None = None
    lowest_low: float = 0.0
    highest_high: float = 0.0
    avg_low: float = 0.0
    avg_high: float = 0.0
    avg_close: float = 0.0
    avg_open: float = 0.0
    avg_change: float = 0.0
    max_change: float = 0.0
    avg_volume: int = 0
    relative_momentum: float = 0.0
    relative_momentum_std: float = 0.0
    is_empty: bool = False

    @classmethod
    def empty(cls) -> "Weekly":
        w = cls(dailies=[])
        w.is_empty = True
        return w

    @classmethod
    def build(cls, dailies: list[Daily]) -> "Weekly":
        w = cls(dailies=dailies)
        w.start_date = dailies[0].date
        w.end_date = dailies[-1].date

        lowest = np.float32(np.iinfo(np.int32).max)
        highest = np.float32(np.iinfo(np.int32).min)
        for d in dailies:
            lo = np.float32(d.low)
            hi = np.float32(d.high)
            if lo < lowest:
                lowest = lo
            if hi > highest:
                highest = hi

        sum_open = np.float32(0.0)
        sum_close = np.float32(0.0)
        sum_low = np.float32(0.0)
        sum_high = np.float32(0.0)
        sum_volume = 0
        for d in dailies:
            sum_low = np.float32(sum_low + np.float32(d.low))
            sum_high = np.float32(sum_high + np.float32(d.high))
            # Plain Python int: int32 wrapping turned valid high-volume weeks
            # negative (audit P2.5 -- NVDA exceeded 2^31-1 in the local cache).
            sum_volume = sum_volume + d.volume
            sum_open = np.float32(sum_open + np.float32(d.open))
            sum_close = np.float32(sum_close + np.float32(d.close))

        n = len(dailies)
        w.lowest_low = float(lowest)
        w.highest_high = float(highest)
        w.avg_open = cls._avg_round(sum_open, n)
        w.avg_close = cls._avg_round(sum_close, n)
        w.avg_high = cls._avg_round(sum_high, n)
        w.avg_low = cls._avg_round(sum_low, n)
        w.avg_volume = (sum_volume // n) if n else 0
        w.avg_change = cls._round2(np.float32(np.float32(w.avg_high) - np.float32(w.avg_low)))
        w.max_change = cls._round2(np.float32(float(highest) - float(lowest)))
        # relativeMomentum = Math.round(avgChange * 100 / avgClose)
        rm_arg = np.float32(np.float32(np.float32(w.avg_change) * np.float32(100)) / np.float32(w.avg_close))
        w.relative_momentum = float(round_float32_half_up(float(rm_arg)))
        w.relative_momentum_std = cls._rel_momentum_std(dailies)
        return w

    @staticmethod
    def _avg_round(sum_f32, n: int) -> float:
        # Math.round(sum / n * 100) / 100f
        q = np.float32(np.float32(sum_f32) / np.float32(n))
        q = np.float32(q * np.float32(100))
        r = round_float32_half_up(float(q))
        return float(np.float32(np.float32(r) / np.float32(100)))

    @staticmethod
    def _round2(val_f32) -> float:
        v = np.float32(np.float32(val_f32) * np.float32(100))
        r = round_float32_half_up(float(v))
        return float(np.float32(np.float32(r) / np.float32(100)))

    @staticmethod
    def _rel_momentum_std(dailies: list[Daily]) -> float:
        if len(dailies) < 2:
            return 0.0
        # daily changes: float32 subtraction widened to double
        changes = [f32(dailies[i].close - dailies[i - 1].close) for i in range(1, len(dailies))]
        avg_close = sum(d.close for d in dailies) / len(dailies)
        if len(changes) == 1:
            if avg_close <= 0:
                return 0.0
            pct = f32(abs(changes[0]) * 100 / avg_close)
            return float(np.float32(np.float32(round_float32_half_up(pct * 100)) / np.float32(100)))
        mean_change = sum(changes) / len(changes)
        variance = sum((c - mean_change) ** 2 for c in changes) / len(changes)
        std_dev = f32(math.sqrt(variance))
        if avg_close > 0:
            std_dev_per = f32(f32(std_dev * 100) / avg_close)
        else:
            std_dev_per = 0.0
        v = np.float32(np.float32(std_dev_per) * np.float32(100))
        r = round_float32_half_up(float(v))
        return float(np.float32(np.float32(r) / np.float32(100)))


# --------------------------------------------------------------------------- #
# GainLossFromDate                                                             #
# --------------------------------------------------------------------------- #


@dataclass
class GainLoss:
    start_date: datetime
    start_price: float
    gain_loss: int

    @classmethod
    def build(cls, start_date: datetime, start_price: float, end_price: float) -> "GainLoss":
        gl = round_float32_half_up(f32((end_price - start_price) * 100 / start_price))
        return cls(start_date, start_price, gl)


# --------------------------------------------------------------------------- #
# Stock                                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class Stock:
    code: str
    dailies: list[Daily]
    yearly_slopes: dict[int, dict] = field(default_factory=dict)
    type: str = TYPE_STOCK

    last_trade: Daily | None = None
    recent_weeks: list[Weekly] = field(default_factory=list)
    has_all_recent_weeks: bool = False
    year_to_date: GainLoss | None = None
    mid_point_to_date: GainLoss | None = None
    five_weeks_to_date: GainLoss | None = None
    five_days_to_date: GainLoss | None = None
    atr_pct: float | None = None
    realized_volatility_expansion: float | None = None
    volume_ratio: float | None = None
    average_dollar_volume_20: float | None = None
    distance_sma20_pct: float | None = None
    rsi_change_five_day: float | None = None
    macd_histogram_change: float | None = None
    days_since_macd_cross: int | None = None
    relative_strength_spy_one_month: float | None = None
    freshness_status: str = "UNKNOWN"
    advanced_trend_with_volume: AdvancedTrendWithVolume | None = None
    strategy_report: dict | None = None

    def __post_init__(self) -> None:
        self.type = normalize_stock_type(self.type)

    @classmethod
    def build(cls, code: str, dailies: list[Daily], yearly_slopes: dict[int, dict] | None = None,
              stock_type: str = TYPE_STOCK) -> "Stock":
        s = cls(
            code=code,
            dailies=sorted(dailies, key=lambda d: d.date),
            yearly_slopes=yearly_slopes or {},
            type=stock_type,
        )
        d = s.dailies

        # Group by Sunday-start weeks.
        weeklies: list[Weekly] = []
        cur: list[Daily] = []
        prev_key = None
        for daily in d:
            key = _sunday_start(daily.date.date() if isinstance(daily.date, datetime) else daily.date)
            if key != prev_key:
                if cur:
                    weeklies.append(Weekly.build(cur))
                    cur = []
                prev_key = key
            cur.append(daily)
            s.last_trade = daily
        if cur:
            weeklies.append(Weekly.build(cur))

        if s.last_trade is None:
            s.last_trade = Daily(datetime.now(), 0.0, 0.0, 0.0, 0.0, 0)

        if len(weeklies) <= RECENT_WEEKS_SIZE:
            s.recent_weeks = list(weeklies)
            if len(weeklies) < RECENT_WEEKS_SIZE:
                for _ in range(RECENT_WEEKS_SIZE - len(weeklies)):
                    s.recent_weeks.append(Weekly.empty())
        else:
            s.has_all_recent_weeks = True
            s.recent_weeks = weeklies[len(weeklies) - RECENT_WEEKS_SIZE:]

        if len(d) > 0:
            # YTD convention (audit P1.8 fix): previous year's FINAL close ->
            # latest close. The dailies span prior + current year, so anchoring
            # at d[0] measured ~18 months, not year-to-date. Falls back to the
            # first available bar when no prior-year close exists.
            latest_year = d[-1].date.year
            current_year_start = next(
                (i for i, bar in enumerate(d) if bar.date.year == latest_year), 0)
            ytd_ref = d[current_year_start - 1] if current_year_start > 0 else d[0]
            s.year_to_date = GainLoss.build(ytd_ref.date, ytd_ref.close, s.last_trade.close)
            # Midpoint of the CURRENT year's bars (was: middle of the whole
            # two-year array, i.e. an arbitrary prior-year date).
            cy = d[current_year_start:]
            ref = cy[len(cy) // 2] if cy else d[len(d) // 2]
            s.mid_point_to_date = GainLoss.build(ref.date, ref.close, s.last_trade.close)
            ref = s.recent_weeks[0].dailies[0]
            s.five_weeks_to_date = GainLoss.build(ref.date, ref.close, s.last_trade.close)
            ref = d[-6] if len(d) >= 6 else d[0]
            s.five_days_to_date = GainLoss.build(ref.date, ref.close, s.last_trade.close)
        else:
            s.year_to_date = GainLoss.build(datetime.now(), 1, 1)
            s.mid_point_to_date = s.year_to_date
            s.five_weeks_to_date = s.year_to_date
            s.five_days_to_date = s.year_to_date

        if len(d) >= te.MIN_DATA_REQUIRED:
            s.advanced_trend_with_volume = te.analyze_trend_with_volume(d)
        s._compute_scanner_metrics()
        return s

    # -- momentum scanner metrics -------------------------------------------- #
    def _compute_scanner_metrics(self) -> None:
        """Compute self-contained metrics once from the stock's daily bars.

        Market-relative strength and freshness are applied later by the cache,
        after the benchmark and the expected data date are known.
        """
        d = self.dailies
        if not d or self.last_trade is None or self.last_trade.close <= 0:
            return
        atr = te.calc_atr(d, 14)
        if atr is not None:
            self.atr_pct = (atr / self.last_trade.close) * 100
        self.realized_volatility_expansion = te.calc_realized_volatility_expansion(d, 5, 20)
        self.volume_ratio = te.calc_volume_ratio(d, 3, 20)
        self.average_dollar_volume_20 = te.calc_average_dollar_volume(d, 20)
        if len(d) >= te.SHORT_MA_PERIOD:
            sma20 = sum(bar.close for bar in d[-te.SHORT_MA_PERIOD:]) / te.SHORT_MA_PERIOD
            if sma20 > 0:
                self.distance_sma20_pct = ((self.last_trade.close - sma20) / sma20) * 100

        rsi = te.calc_rsi(d, te.RSI_PERIOD)
        if len(rsi) >= 6:
            self.rsi_change_five_day = rsi[-1] - rsi[-6]

        ema12 = te.calc_ema(d, 12)
        ema26 = te.calc_ema(d, 26)
        macd = te.calc_macd(ema12, ema26)
        macd_signal = te.calc_macd_signal(macd, 9)
        histogram = te.calc_macd_hist(macd, macd_signal)
        if len(histogram) >= 2 and all(math.isfinite(value) for value in histogram[-2:]):
            self.macd_histogram_change = histogram[-1] - histogram[-2]
        valid_histogram = [value for value in histogram if math.isfinite(value)]
        if valid_histogram:
            current_positive = valid_histogram[-1] >= 0
            age = 0
            for value in reversed(valid_histogram[:-1]):
                if (value >= 0) != current_positive:
                    break
                age += 1
            self.days_since_macd_cross = age

    def apply_scanner_context(
        self, benchmark: "Stock | None", reference_date: datetime | None,
    ) -> None:
        """Attach benchmark-relative and freshness context after cache loading."""
        if not self.dailies or self.last_trade is None or self.last_trade.close <= 0:
            self.freshness_status = "INCOMPLETE"
            return
        if reference_date is None:
            self.freshness_status = "UNKNOWN"
        elif self.last_trade.date.date() < reference_date.date():
            self.freshness_status = "STALE"
        elif self.last_trade.date.date() > reference_date.date():
            self.freshness_status = "DATE_MISMATCH"
        else:
            self.freshness_status = "FRESH"

        self.relative_strength_spy_one_month = None
        if benchmark is None or not benchmark.dailies or len(self.dailies) < 22:
            return
        anchor = self.dailies[-22]
        benchmark_latest = next(
            (bar for bar in reversed(benchmark.dailies) if bar.date <= self.last_trade.date), None,
        )
        benchmark_anchor = next(
            (bar for bar in reversed(benchmark.dailies) if bar.date <= anchor.date), None,
        )
        if benchmark_latest is None or benchmark_anchor is None or benchmark_anchor.close <= 0:
            return
        stock_return = ((self.last_trade.close - anchor.close) / anchor.close) * 100
        benchmark_return = ((benchmark_latest.close - benchmark_anchor.close)
                            / benchmark_anchor.close) * 100
        self.relative_strength_spy_one_month = stock_return - benchmark_return

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _reversal_confirmed(self, target_direction: int) -> bool:
        """Confirm a strong turn while naming the setup for its source trend.

        ``target_direction`` is +1 for a bearish trend turning upward and -1
        for a bullish trend turning downward. Generic reversal clues alone are
        deliberately insufficient: a confirmed scanner setup also requires a
        directional price break, improving/deteriorating RSI and MACD, fresh
        data, and normal-or-better recent volume participation.
        """
        source_trend_matches = (
            self.is_bearish() if target_direction > 0 else self.is_bullish()
        )
        if (not source_trend_matches or not self.has_reversal_signal()
                or len(self.dailies) < 6
                or self.freshness_status != "FRESH"
                or (self.volume_ratio or 0) < 1.0):
            return False
        latest = self.dailies[-1]
        prior = self.dailies[-2]
        prior_five_average = sum(bar.close for bar in self.dailies[-6:-1]) / 5
        if target_direction > 0:
            price_confirmation = (
                latest.close > prior.high
                and latest.close > prior_five_average
            )
        else:
            price_confirmation = (
                latest.close < prior.low
                and latest.close < prior_five_average
            )
        return bool(
            price_confirmation
            and target_direction * (self.rsi_change_five_day or 0) > 0
            and target_direction * (self.macd_histogram_change or 0) > 0
        )

    def scanner_setup(self) -> str:
        """Return an explicit, direction-aware scanner setup."""
        if self.advanced_trend_with_volume is None:
            return NOT_EVALUATED
        if self._reversal_confirmed(1):
            return BEARISH_REVERSAL
        if self._reversal_confirmed(-1):
            return BULLISH_REVERSAL
        if self.is_bullish():
            return BULLISH_CONTINUATION
        if self.is_bearish():
            return BEARISH_CONTINUATION
        return WATCH

    def preliminary_reversal_direction(self) -> int:
        """Return the possible turn direction when evidence is not confirmed."""
        if not self.has_reversal_signal():
            return 0
        if self.is_bearish() and not self._reversal_confirmed(1):
            return 1
        if self.is_bullish() and not self._reversal_confirmed(-1):
            return -1
        return 0

    def has_preliminary_reversal_evidence(self) -> bool:
        return self.preliminary_reversal_direction() != 0

    def preliminary_reversal_label(self) -> str | None:
        direction = self.preliminary_reversal_direction()
        if direction > 0:
            return "Possible Bearish Reversal"
        if direction < 0:
            return "Possible Bullish Reversal"
        return None

    def _preliminary_reversal_penalty(self, continuation_direction: int) -> float:
        """Penalize continuation rank in proportion to contrary evidence."""
        target_direction = self.preliminary_reversal_direction()
        if target_direction == 0 or target_direction != -continuation_direction:
            return 0.0
        atv = self.advanced_trend_with_volume
        if atv is None:
            return 0.0

        # Two generic clues create the warning. Extra clues and directional
        # confirmation make the warning more damaging to the continuation case.
        penalty = 8.0 + max(0, atv.no_of_reversal_signals - 2) * 4.0
        if target_direction * (self.rsi_change_five_day or 0) > 0:
            penalty += 3.0
        if target_direction * (self.macd_histogram_change or 0) > 0:
            penalty += 3.0
        if len(self.dailies) >= 2:
            latest = self.dailies[-1]
            prior = self.dailies[-2]
            directional_break = (
                latest.close > prior.high if target_direction > 0
                else latest.close < prior.low
            )
            if directional_break:
                penalty += 2.0
        return min(penalty, 20.0)

    def _continuation_components(self, direction: int) -> dict[str, float]:
        atv = self.advanced_trend_with_volume
        if atv is None:
            return {}
        strong_direction = atv.fully_aligned
        strength_points = {"WEAK": 3.0, "MODERATE": 7.0, "STRONG": 10.0}.get(atv.strength, 0.0)
        trend = (5.0 if strong_direction else 2.0) + strength_points + atv.confidence * 10.0
        trend = self._clamp(trend, 0.0, 25.0)

        def momentum_points(raw_return: float, healthy_target: float) -> float:
            """Reward aligned momentum, then taper credit for a chased move."""
            aligned = direction * raw_return
            if aligned <= 0:
                return 0.0
            if aligned <= healthy_target:
                return aligned / healthy_target * 10.0
            if aligned <= healthy_target * 2:
                return 10.0 - ((aligned - healthy_target) / healthy_target * 3.0)
            return max(2.0, 7.0 - ((aligned - healthy_target * 2) / healthy_target * 2.0))

        five_day = float(self.five_days_to_date.gain_loss if self.five_days_to_date else 0)
        five_week = float(self.five_weeks_to_date.gain_loss if self.five_weeks_to_date else 0)
        momentum = momentum_points(five_day, 5.0) + momentum_points(five_week, 12.0)

        relative = 0.0
        if self.relative_strength_spy_one_month is not None:
            relative = self._clamp(
                direction * self.relative_strength_spy_one_month / 10.0 * 15.0,
                0.0, 15.0,
            )

        participation = 0.0
        if self.volume_ratio is not None:
            participation = self._clamp((self.volume_ratio - 0.8) / 1.2 * 15.0, 0.0, 15.0)

        rsi = atv.rsi
        if direction > 0:
            rsi_points = 7.0 if 50 <= rsi <= 70 else (3.0 if 40 <= rsi < 50 else 0.0)
            momentum_confirmed = atv.momentum > 0
        else:
            rsi_points = 7.0 if 30 <= rsi <= 50 else (3.0 if 50 < rsi <= 60 else 0.0)
            momentum_confirmed = atv.momentum < 0
        timing = rsi_points + (4.0 if momentum_confirmed else 0.0)
        if self.distance_sma20_pct is not None:
            directional_extension = direction * self.distance_sma20_pct
            if 0 <= directional_extension <= 5:
                timing += 4.0
            elif -3 <= directional_extension <= 10:
                timing += 2.0
        timing = self._clamp(timing, 0.0, 15.0)

        tradability = 0.0
        if self.average_dollar_volume_20 is not None:
            if self.average_dollar_volume_20 >= 20_000_000:
                tradability += 5.0
            elif self.average_dollar_volume_20 >= 5_000_000:
                tradability += 3.0
            else:
                tradability += 1.0
        if len(self.dailies) >= te.MIN_DATA_REQUIRED:
            tradability += 2.0
        if self.freshness_status == "FRESH":
            tradability += 3.0

        components = {
            "trendAlignment": round(trend, 1),
            "multiHorizonMomentum": round(momentum, 1),
            "relativeStrength": round(relative, 1),
            "participation": round(participation, 1),
            "entryTiming": round(timing, 1),
            "tradability": round(tradability, 1),
        }
        reversal_penalty = self._preliminary_reversal_penalty(direction)
        if reversal_penalty:
            reversal_penalty = min(reversal_penalty, sum(components.values()))
            components["preliminaryReversalPenalty"] = -round(reversal_penalty, 1)
        return components

    def _reversal_components(self, target_direction: int) -> dict[str, float]:
        atv = self.advanced_trend_with_volume
        source_trend_matches = (
            self.is_bearish() if target_direction > 0 else self.is_bullish()
        )
        if atv is None or not source_trend_matches:
            return {}
        context = 20.0 if atv.fully_aligned else 15.0
        if atv.current_trend_days >= 5:
            context = min(20.0, context + 5.0)

        trigger = 10.0 if self.has_reversal_signal() else 0.0
        trigger += 8.0 if target_direction * (self.rsi_change_five_day or 0) > 0 else 0.0
        trigger += 8.0 if target_direction * (self.macd_histogram_change or 0) > 0 else 0.0
        trigger += 4.0 if (
            self.five_days_to_date
            and target_direction * self.five_days_to_date.gain_loss > 0
        ) else 0.0

        confirmation = 0.0
        if len(self.dailies) >= 6:
            latest = self.dailies[-1]
            prior = self.dailies[-2]
            prior_five = self.dailies[-6:-1]
            prior_five_average = sum(bar.close for bar in prior_five) / 5
            if target_direction > 0:
                confirmation += 10.0 if latest.close > prior.high else 0.0
                confirmation += 10.0 if latest.close > prior_five_average else 0.0
                confirmation += 5.0 if latest.low > min(bar.low for bar in prior_five) else 0.0
            else:
                confirmation += 10.0 if latest.close < prior.low else 0.0
                confirmation += 10.0 if latest.close < prior_five_average else 0.0
                confirmation += 5.0 if latest.high < max(bar.high for bar in prior_five) else 0.0

        participation = 0.0
        if self.volume_ratio is not None:
            participation = self._clamp((self.volume_ratio - 0.8) / 1.2 * 15.0, 0.0, 15.0)

        risk = 0.0
        if self.atr_pct is not None and 1.0 <= self.atr_pct <= 8.0:
            risk += 5.0
        if self.average_dollar_volume_20 is not None and self.average_dollar_volume_20 >= 5_000_000:
            risk += 3.0
        if self.freshness_status == "FRESH":
            risk += 2.0

        context_key = "bearishContext" if target_direction > 0 else "bullishContext"
        trigger_key = "bullishTrigger" if target_direction > 0 else "bearishTrigger"
        return {
            context_key: round(context, 1),
            trigger_key: round(min(trigger, 30.0), 1),
            "priceConfirmation": round(min(confirmation, 25.0), 1),
            "participation": round(participation, 1),
            "riskAndRoom": round(risk, 1),
        }

    def setup_score_components(self) -> dict[str, float]:
        setup = self.scanner_setup()
        if setup == BULLISH_CONTINUATION:
            return self._continuation_components(1)
        if setup == BEARISH_CONTINUATION:
            return self._continuation_components(-1)
        if setup == BEARISH_REVERSAL:
            return self._reversal_components(1)
        if setup == BULLISH_REVERSAL:
            return self._reversal_components(-1)
        return {}

    def setup_score(self) -> float:
        return round(sum(self.setup_score_components().values()), 1)

    def setup_reason(self) -> str:
        setup = self.scanner_setup()
        if setup == BULLISH_CONTINUATION:
            if self.has_preliminary_reversal_evidence():
                return "Bullish trend with preliminary bearish-turn evidence; continuation score includes a reversal-risk penalty."
            return "Bullish trend; rank reflects momentum, relative strength, participation, and entry room."
        if setup == BEARISH_CONTINUATION:
            if self.has_preliminary_reversal_evidence():
                return "Bearish trend with preliminary bullish-turn evidence; continuation score includes a reversal-risk penalty."
            return "Bearish trend; rank reflects downside momentum, relative weakness, participation, and entry room."
        if setup == BEARISH_REVERSAL:
            return "Bearish trend with a confirmed upward price break, rising RSI/MACD, and participating volume."
        if setup == BULLISH_REVERSAL:
            return "Bullish trend with a confirmed downward price break, falling RSI/MACD, and participating volume."
        if setup == NOT_EVALUATED:
            return "Insufficient history for the trend engine."
        return "No continuation or confirmed reversal setup."

    def evidence_quality(self) -> str:
        if self.freshness_status in ("STALE", "DATE_MISMATCH", "INCOMPLETE"):
            return "STALE_OR_INCOMPLETE"
        required = (
            self.atr_pct,
            self.realized_volatility_expansion,
            self.volume_ratio,
            self.average_dollar_volume_20,
            self.relative_strength_spy_one_month,
        )
        if self.freshness_status != "FRESH" or any(value is None for value in required):
            return "PARTIAL"
        return "COMPLETE"

    def trigger_label(self) -> str:
        setup = self.scanner_setup()
        if setup == BEARISH_REVERSAL:
            return "Upward reversal confirmed"
        if setup == BULLISH_REVERSAL:
            return "Downward reversal confirmed"
        atv = self.advanced_trend_with_volume
        if atv is None:
            return "Not evaluated"
        warning = " · reversal warning" if self.has_preliminary_reversal_evidence() else ""
        return f"Trend {atv.current_trend_days}d{warning}"

    # -- classification ------------------------------------------------------ #
    def is_bearish(self) -> bool:
        atv = self.advanced_trend_with_volume
        return atv is not None and atv.direction == te.DOWN

    def is_bullish(self) -> bool:
        atv = self.advanced_trend_with_volume
        return atv is not None and atv.direction == te.UP

    def is_sideways(self) -> bool:
        atv = self.advanced_trend_with_volume
        return atv is not None and atv.direction == te.SIDEWAYS and self.last_trade is not None

    def is_penny(self) -> bool:
        return self.last_trade.close < 6

    def has_reversal_signal(self) -> bool:
        atv = self.advanced_trend_with_volume
        return atv is not None and atv.reversal_signal

    def signal(self) -> str:
        if self.is_bullish():
            sig = "BULLISH"
        elif self.is_bearish():
            sig = "BEARISH"
        elif self.is_sideways():
            sig = "SIDEWAYS"
        else:
            sig = "NEUTRAL"
        if self.has_reversal_signal():
            return f"{sig}-REVERSAL"
        return sig
