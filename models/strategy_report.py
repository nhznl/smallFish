"""Shared, forward-compatible contract for strategy scan report CSV rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .universe import normalize_symbol


# Current scanner output order. Readers remain header-name based so older reports
# that omit raw OHLC/indicator-input fields stay valid.
STRATEGY_REPORT_COLUMNS = (
    "date", "ticker", "open", "high", "low", "close", "adj_close", "volume",
    "sma_20", "sma_50", "rsi_14", "rsi_14_prev", "macd", "macd_signal",
    "macd_hist", "macd_hist_prev", "bb_mid", "bb_upper", "bb_lower",
    "avg_vol_20", "avg_dollar_vol_20", "vol_spike", "atr_14", "atr_pct",
    "event_date", "event_type", "days_to_event", "sector", "rel_strength_spy",
    "days_in_band", "market_regime", "regime_size_factor", "higher_low",
    "days_since_macd_cross", "score_trend", "score_momentum", "score_extension",
    "score_event", "score_tradability", "score_total", "score_persistence",
    "score_shift_raw", "score_shift", "shift_label", "score_pct", "signal_band",
    "bucket", "reason_summary",
)


@dataclass(frozen=True)
class StrategyReportRow:
    """One row addressed by named CSV columns, preserving raw field text."""

    ticker: str
    values: Mapping[str, str | None]

    def __post_init__(self) -> None:
        ticker = normalize_symbol(self.ticker)
        if not ticker:
            raise ValueError("StrategyReportRow requires a valid ticker")
        object.__setattr__(self, "ticker", ticker)

    def value(self, column: str) -> str | None:
        raw = self.values.get(column)
        return None if raw is None or raw.strip() == "" else raw

    @classmethod
    def from_parts(cls, headers: list[str], parts: list[str]) -> "StrategyReportRow | None":
        """Parse the report's simple comma-split row representation."""
        values = {
            header: (parts[index] if index < len(parts) else None)
            for index, header in enumerate(headers)
        }
        ticker = values.get("ticker")
        if ticker is None or not ticker.strip():
            return None
        try:
            return cls(ticker, values)
        except ValueError:
            return None


def parse_strategy_report(text: str) -> list[StrategyReportRow]:
    """Parse report text while retaining file order."""
    lines = text.split("\n")
    if not lines or lines[0] == "":
        return []
    headers = lines[0].split(",")
    rows: list[StrategyReportRow] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        row = StrategyReportRow.from_parts(headers, line.split(","))
        if row is not None:
            rows.append(row)
    return rows
