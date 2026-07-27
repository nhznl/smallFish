"""Shared contract for one row in the daily OHLCV price cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


CACHE_DATE_FORMAT = "%m-%d-%Y"
CACHE_FIELD_COUNT = 7


@dataclass(frozen=True)
class DailyPriceBar:
    """Lossless representation of ``data/{year}/{SYMBOL}.txt`` rows."""

    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_close: Decimal
    volume: int

    def __post_init__(self) -> None:
        if not isinstance(self.trade_date, date):
            raise TypeError("trade_date must be a date")
        for name in ("open", "high", "low", "close", "adjusted_close"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite non-negative value")
        if not isinstance(self.volume, int) or isinstance(self.volume, bool):
            raise TypeError("volume must be an integer")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")

    @classmethod
    def from_cache_line(cls, line: str) -> "DailyPriceBar":
        """Parse one unquoted cache row, raising ``ValueError`` when malformed."""
        if "\n" in line.rstrip("\r\n") or "\r" in line.rstrip("\r\n"):
            raise ValueError("price cache row must contain exactly one line")
        fields = line.rstrip("\r\n").split(",")
        if len(fields) != CACHE_FIELD_COUNT:
            raise ValueError(
                f"price cache row must have {CACHE_FIELD_COUNT} fields, got {len(fields)}"
            )
        try:
            trade_date = datetime.strptime(fields[0], CACHE_DATE_FORMAT).date()
            prices = tuple(Decimal(value) for value in fields[1:6])
            volume = int(fields[6])
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"malformed price cache row: {line!r}") from exc
        return cls(trade_date, *prices, volume)

    def to_cache_line(self) -> str:
        """Render the canonical seven-field cache row without a trailing newline."""
        values = (
            self.trade_date.strftime(CACHE_DATE_FORMAT),
            *(format(value, "f") for value in (
                self.open,
                self.high,
                self.low,
                self.close,
                self.adjusted_close,
            )),
            str(self.volume),
        )
        return ",".join(values)
