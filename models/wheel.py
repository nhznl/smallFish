"""Stable standard-library Wheel v2 CSV schema."""

from __future__ import annotations

DEFAULT_CUSHIONS_PCT = [2.5, 5, 7.5, 10]
WHEEL_SCHEMA_VERSION = 2
RUN_MODE_CURRENT_CONTEXT_ONLY = "CURRENT_CONTEXT_ONLY"


def cushion_key(cushion_pct: float) -> str:
    """Column-name fragment for a cushion: 2.5 -> '2_5', 5 -> '5', 10 -> '10'."""
    return f"{cushion_pct:g}".replace(".", "_")


def report_columns(cushions_pct: list[float] = DEFAULT_CUSHIONS_PCT) -> list[str]:
    """Stable, documented column order for data/wheel/{as_of}.csv."""
    cols = [
        "schema_version",
        "run_mode",
        "symbol",
        "as_of",
        "horizon_dte",
        "horizon_sessions",
        "price_as_of",
        "last_close",
        "range_5d_high",
        "range_5d_low",
        "range_5d_width_pct",
        "range_5d_close_pos",
        "range_31d_high",
        "range_31d_low",
        "range_31d_width_pct",
        "range_31d_close_pos",
        "rv7_cc",
        "rv7_park",
        "rv7_used",
        "rv21_cc",
        "rv21_park",
        "rv21_used",
        "rv37_cc",
        "rv37_park",
        "rv37_used",
        "rv_percentile_252",
        "atr14_pct",
        "avg_dollar_volume_20",
        "swing_low_20",
        "dist_sma50_pct",
        "bb_lower",
        "days_to_event",
        "score_total",
        "signal_band",
        "sector",
        "events_fetched_as_of",
        "data_quality",
        "quality_reasons",
        "expected_price_as_of",
        "price_age_sessions",
        "history_start",
        "rv_window_sessions",
        "rv_used_daily",
        "sigma_move_dollars",
        "sigma_move_pct",
    ]
    for c in cushions_pct:
        k = cushion_key(c)
        cols += [
            f"put_expiry_itm_{k}",
            f"call_expiry_itm_{k}",
            f"put_touch_{k}",
            f"call_touch_{k}",
            f"put_expiry_itm_nonoverlap_{k}",
            f"call_expiry_itm_nonoverlap_{k}",
            f"put_touch_nonoverlap_{k}",
            f"call_touch_nonoverlap_{k}",
        ]
    cols += [
        "min_cushion_20pct_itm",
        "sample_count",
        "nonoverlap_sample_count",
        "worst_min_close_pct",
        "p10_min_close_pct",
        "earnings_window_state",
    ]
    return cols


WHEEL_COLUMNS = report_columns()
