"""CSV readers for the strategy and wheel artifacts consumed by the API."""

from __future__ import annotations

import csv
import datetime as _dt
from pathlib import Path

from models.strategy_report import parse_strategy_report

from models.wheel import (RUN_MODE_CURRENT_CONTEXT_ONLY, WHEEL_COLUMNS,
                          WHEEL_SCHEMA_VERSION)

# --------------------------------------------------------------------------- #
# Pre-earnings scan report                                                     #
# --------------------------------------------------------------------------- #

# csv_col -> (jsonField, kind). kind: "d" primitive double (blank->0.0),
# "D" nullable Double (blank->None), "i" int-via-double (blank->0),
# "b" boolean (Boolean.parseBoolean), "s" nullable string (blank->None).
_STRATEGY_FIELDS: list[tuple[str, str, str]] = [
    ("date", "date", "s"),
    ("sma_20", "smaTwenty", "d"),
    ("sma_50", "smaFifty", "d"),
    ("rsi_14", "rsi14", "d"),
    ("macd", "macd", "d"),
    ("macd_signal", "macdSignal", "d"),
    ("macd_hist", "macdHist", "d"),
    ("bb_mid", "bbMid", "d"),
    ("bb_upper", "bbUpper", "d"),
    ("bb_lower", "bbLower", "d"),
    ("avg_vol_20", "avgVol20", "d"),
    ("avg_dollar_vol_20", "avgDollarVol20", "d"),
    ("vol_spike", "volSpike", "b"),
    ("atr_14", "atr14", "d"),
    ("atr_pct", "atrPct", "d"),
    ("event_date", "eventDate", "s"),
    ("event_type", "eventType", "s"),
    ("days_to_event", "daysToEvent", "D"),
    ("sector", "sector", "s"),
    ("rel_strength_spy", "relStrengthSpy", "D"),
    ("days_in_band", "daysInBand", "i"),
    ("market_regime", "marketRegime", "s"),
    ("regime_size_factor", "regimeSizeFactor", "d"),
    ("higher_low", "higherLow", "b"),
    ("score_trend", "scoreTrend", "d"),
    ("score_momentum", "scoreMomentum", "d"),
    ("score_extension", "scoreExtension", "d"),
    ("score_event", "scoreEvent", "d"),
    ("score_tradability", "scoreTradability", "d"),
    ("score_persistence", "scorePersistence", "d"),
    ("score_total", "scoreTotal", "d"),
    ("score_pct", "scorePct", "d"),
    ("score_shift_raw", "scoreShiftRaw", "d"),
    ("score_shift", "scoreShift", "d"),
    ("days_since_macd_cross", "daysSinceMacdCross", "D"),
    ("shift_label", "shiftLabel", "s"),
    ("signal_band", "signalBand", "s"),
    ("bucket", "bucket", "s"),
    ("reason_summary", "reasonSummary", "s"),
]

# Stable JSON field order for strategy report responses.
_STRATEGY_JSON_ORDER = [
    "date", "smaTwenty", "smaFifty", "rsi14", "macd", "macdSignal", "macdHist",
    "bbMid", "bbUpper", "bbLower", "avgVol20", "avgDollarVol20", "volSpike",
    "atr14", "atrPct", "eventDate", "eventType", "daysToEvent", "sector",
    "relStrengthSpy", "daysInBand", "marketRegime", "regimeSizeFactor",
    "higherLow", "scoreTrend", "scoreMomentum", "scoreExtension", "scoreEvent",
    "scoreTradability", "scorePersistence", "scoreTotal", "scorePct",
    "scoreShiftRaw", "scoreShift", "daysSinceMacdCross", "shiftLabel",
    "signalBand", "bucket", "reasonSummary",
]


def _parse_double(value: str | None) -> float:
    """Parse a number, returning 0.0 for blank or malformed values."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _parse_optional_double(value: str | None) -> float | None:
    """Parse an optional number, returning None for blank or malformed values."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_boolean(value: str | None) -> bool:
    """Parse a boolean: only case-insensitive ``true`` is true."""
    return value is not None and value.strip().lower() == "true"


def _latest_report_csv(reports_dir: Path) -> Path | None:
    """Return the lexicographically greatest report CSV filename."""
    if not reports_dir.is_dir():
        return None
    files = [p for p in reports_dir.iterdir() if p.name.endswith(".csv")]
    if not files:
        return None
    return max(files, key=lambda p: p.name)


def read_strategy_report_rows(csv_path: Path) -> list[dict]:
    """Parse a strategy report CSV into a ticker->row list of Jackson-shaped
    dicts. Mirrors StrategyReportReader exactly (naive comma split, header-name
    lookup, blank -> null for value()). Rows with a blank ticker are skipped.
    Returns (ticker, report_dict) pairs preserving file order.
    """
    out: list[dict] = []
    for row in parse_strategy_report(csv_path.read_text()):
        rec: dict = {}
        for csv_col, json_field, kind in _STRATEGY_FIELDS:
            raw = row.value(csv_col)
            if kind == "d":
                rec[json_field] = _parse_double(raw)
            elif kind == "D":
                rec[json_field] = _parse_optional_double(raw)
            elif kind == "i":
                rec[json_field] = int(_parse_double(raw))
            elif kind == "b":
                rec[json_field] = _parse_boolean(raw)
            else:  # "s"
                rec[json_field] = raw
        # Emit fields in Jackson declaration order.
        ordered = {k: rec[k] for k in _STRATEGY_JSON_ORDER}
        out.append({"ticker": row.ticker, "report": ordered})
    return out


def read_latest_strategy_report(reports_dir: Path) -> list[dict]:
    """Latest report's rows, as ``[{"ticker", "report"}, ...]`` in file order.
    Empty list if no report exists.
    """
    latest = _latest_report_csv(reports_dir)
    if latest is None:
        return []
    return read_strategy_report_rows(latest)


# --------------------------------------------------------------------------- #
# Wheel report  (/wheelCandidates)                                            #
# --------------------------------------------------------------------------- #

# csv_col -> (jsonField, kind). kind: "s" nullable string, "d" nullable Double,
# "i" required int (blank -> 0), or "I" nullable int.
_WHEEL_FIELDS: dict[str, tuple[str, str]] = {
    "schema_version": ("schemaVersion", "i"),
    "run_mode": ("runMode", "s"),
    "symbol": ("symbol", "s"),
    "as_of": ("asOf", "s"),
    "horizon_dte": ("horizonDte", "i"),
    "horizon_sessions": ("horizonSessions", "i"),
    "price_as_of": ("priceAsOf", "s"),
    "last_close": ("lastClose", "d"),
    "range_5d_high": ("range5dHigh", "d"),
    "range_5d_low": ("range5dLow", "d"),
    "range_5d_width_pct": ("range5dWidthPct", "d"),
    "range_5d_close_pos": ("range5dClosePos", "d"),
    "range_31d_high": ("range31dHigh", "d"),
    "range_31d_low": ("range31dLow", "d"),
    "range_31d_width_pct": ("range31dWidthPct", "d"),
    "range_31d_close_pos": ("range31dClosePos", "d"),
    "rv7_cc": ("rv7Cc", "d"),
    "rv7_park": ("rv7Park", "d"),
    "rv7_used": ("rv7Used", "d"),
    "rv21_cc": ("rv21Cc", "d"),
    "rv21_park": ("rv21Park", "d"),
    "rv21_used": ("rv21Used", "d"),
    "rv37_cc": ("rv37Cc", "d"),
    "rv37_park": ("rv37Park", "d"),
    "rv37_used": ("rv37Used", "d"),
    "rv_percentile_252": ("rvPercentile252", "d"),
    "atr14_pct": ("atr14Pct", "d"),
    "avg_dollar_volume_20": ("avgDollarVolume20", "d"),
    "swing_low_20": ("swingLow20", "d"),
    "dist_sma50_pct": ("distSma50Pct", "d"),
    "bb_lower": ("bbLower", "d"),
    "days_to_event": ("daysToEvent", "d"),
    "score_total": ("scoreTotal", "d"),
    "signal_band": ("signalBand", "s"),
    "sector": ("sector", "s"),
    "events_fetched_as_of": ("eventsFetchedAsOf", "s"),
    "data_quality": ("dataQuality", "s"),
    "quality_reasons": ("qualityReasons", "s"),
    "expected_price_as_of": ("expectedPriceAsOf", "s"),
    "price_age_sessions": ("priceAgeSessions", "i"),
    "history_start": ("historyStart", "s"),
    "rv_window_sessions": ("rvWindowSessions", "i"),
    "rv_used_daily": ("rvUsedDaily", "d"),
    "sigma_move_dollars": ("sigmaMoveDollars", "d"),
    "sigma_move_pct": ("sigmaMovePct", "d"),
    "put_expiry_itm_2_5": ("putExpiryItm25", "d"),
    "call_expiry_itm_2_5": ("callExpiryItm25", "d"),
    "put_touch_2_5": ("putTouch25", "d"),
    "call_touch_2_5": ("callTouch25", "d"),
    "put_expiry_itm_nonoverlap_2_5": ("putExpiryItmNonoverlap25", "d"),
    "call_expiry_itm_nonoverlap_2_5": ("callExpiryItmNonoverlap25", "d"),
    "put_touch_nonoverlap_2_5": ("putTouchNonoverlap25", "d"),
    "call_touch_nonoverlap_2_5": ("callTouchNonoverlap25", "d"),
    "put_expiry_itm_5": ("putExpiryItm5", "d"),
    "call_expiry_itm_5": ("callExpiryItm5", "d"),
    "put_touch_5": ("putTouch5", "d"),
    "call_touch_5": ("callTouch5", "d"),
    "put_expiry_itm_nonoverlap_5": ("putExpiryItmNonoverlap5", "d"),
    "call_expiry_itm_nonoverlap_5": ("callExpiryItmNonoverlap5", "d"),
    "put_touch_nonoverlap_5": ("putTouchNonoverlap5", "d"),
    "call_touch_nonoverlap_5": ("callTouchNonoverlap5", "d"),
    "put_expiry_itm_7_5": ("putExpiryItm75", "d"),
    "call_expiry_itm_7_5": ("callExpiryItm75", "d"),
    "put_touch_7_5": ("putTouch75", "d"),
    "call_touch_7_5": ("callTouch75", "d"),
    "put_expiry_itm_nonoverlap_7_5": ("putExpiryItmNonoverlap75", "d"),
    "call_expiry_itm_nonoverlap_7_5": ("callExpiryItmNonoverlap75", "d"),
    "put_touch_nonoverlap_7_5": ("putTouchNonoverlap75", "d"),
    "call_touch_nonoverlap_7_5": ("callTouchNonoverlap75", "d"),
    "put_expiry_itm_10": ("putExpiryItm10", "d"),
    "call_expiry_itm_10": ("callExpiryItm10", "d"),
    "put_touch_10": ("putTouch10", "d"),
    "call_touch_10": ("callTouch10", "d"),
    "put_expiry_itm_nonoverlap_10": ("putExpiryItmNonoverlap10", "d"),
    "call_expiry_itm_nonoverlap_10": ("callExpiryItmNonoverlap10", "d"),
    "put_touch_nonoverlap_10": ("putTouchNonoverlap10", "d"),
    "call_touch_nonoverlap_10": ("callTouchNonoverlap10", "d"),
    "min_cushion_20pct_itm": ("minCushion20pctItm", "s"),
    "sample_count": ("sampleCount", "i"),
    "nonoverlap_sample_count": ("nonoverlapSampleCount", "I"),
    "worst_min_close_pct": ("worstMinClosePct", "d"),
    "p10_min_close_pct": ("p10MinClosePct", "d"),
    "earnings_window_state": ("earningsWindowState", "s"),
}

# Stable JSON field order for wheel report rows.
_WHEEL_JSON_ORDER = [_WHEEL_FIELDS[c][0] for c in WHEEL_COLUMNS]


def _wheel_str(raw: str | None) -> str | None:
    """WheelReportReader.str: trim; empty -> None."""
    if raw is None:
        return None
    v = raw.strip()
    return None if v == "" else v


def _wheel_dbl(raw: str | None) -> float | None:
    """WheelReportReader.dbl: trim; blank/unparseable -> None."""
    if raw is None:
        return None
    v = raw.strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _wheel_int(raw: str | None) -> int:
    """WheelReportReader.intVal: trim; blank -> 0; tolerates '5' or '5.0'."""
    if raw is None:
        return 0
    v = raw.strip()
    if v == "":
        return 0
    try:
        return int(v)
    except ValueError:
        try:
            return round(float(v))
        except ValueError:
            return 0


def _wheel_optional_int(raw: str | None) -> int | None:
    """Nullable integer: blank/unparseable -> None; accepts '5' or '5.0'."""
    if raw is None or raw.strip() == "":
        return None
    try:
        return round(float(raw.strip()))
    except ValueError:
        return None


def _latest_dated_csv(directory: Path, today: str | None = None) -> Path | None:
    """WheelReportReader.latestDatedCsv: greatest YYYY-MM-DD.csv filename whose
    stem is <= today. None if the directory is missing/empty."""
    if not directory.is_dir():
        return None
    files = [p for p in directory.iterdir() if p.name.endswith(".csv")]
    if not files:
        return None
    today = today or _dt.date.today().isoformat()

    def stem(name: str) -> str:
        dot = name.rfind(".")
        return name if dot < 0 else name[:dot]

    eligible = [p for p in files if stem(p.name) <= today]
    if not eligible:
        return None
    return max(eligible, key=lambda p: p.name)


def read_wheel_report_rows(csv_path: Path) -> list[dict]:
    """Parse a wheel report CSV into a list of Jackson-shaped ``wheel`` dicts,
    in file order. Mirrors WheelReportReader.parseReport (RFC-4180 parse,
    header-name lookup, trimmed cells)."""
    out: list[dict] = []
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return out
        idx = {h.strip(): i for i, h in enumerate(headers)}
        legacy_schema = "schema_version" not in idx

        def cell(cols: list[str], name: str) -> str | None:
            i = idx.get(name)
            if i is None or i >= len(cols):
                return None
            return cols[i]

        for cols in reader:
            if not cols or all(c.strip() == "" for c in cols):
                continue
            row: dict = {}
            for csv_col, (json_field, kind) in _WHEEL_FIELDS.items():
                raw = cell(cols, csv_col)
                if kind == "s":
                    row[json_field] = _wheel_str(raw)
                elif kind == "i":
                    row[json_field] = _wheel_int(raw)
                elif kind == "I":
                    row[json_field] = _wheel_optional_int(raw)
                else:  # "d"
                    row[json_field] = _wheel_dbl(raw)
            if legacy_schema:
                row["schemaVersion"] = 1
                row["runMode"] = RUN_MODE_CURRENT_CONTEXT_ONLY
            version = row["schemaVersion"]
            if version not in {1, WHEEL_SCHEMA_VERSION}:
                raise ValueError(f"unsupported wheel schema version: {version}")
            if not row["runMode"]:
                row["runMode"] = RUN_MODE_CURRENT_CONTEXT_ONLY
            ordered = {k: row[k] for k in _WHEEL_JSON_ORDER}
            out.append(ordered)
    return out


def read_latest_wheel_report(wheel_dir: Path, today: str | None = None) -> list[dict]:
    """Latest wheel report's ``wheel`` dicts (every symbol x horizon), file
    order. Empty list if none."""
    latest = _latest_dated_csv(wheel_dir, today=today)
    if latest is None:
        return []
    return read_wheel_report_rows(latest)
