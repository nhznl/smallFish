"""Tests for the live scan's date-consistency events filter
(scan._apply_date_consistency)."""

import pandas as pd
import pytest

from studies.pre_earnings_momentum.scan import _apply_date_consistency


def _realized_csv(tmp_path, mapping):
    rows = [{"ticker": t, "event_date": d.strftime("%Y-%m-%d")}
            for t, dates in mapping.items() for d in dates]
    pd.DataFrame(rows).to_csv(tmp_path / "earnings_history.csv", index=False)


def quarterly(start, n, step=91):
    first = pd.Timestamp(start)
    return [first + pd.Timedelta(days=step * i) for i in range(n)]


def _events(tickers):
    return pd.DataFrame({
        "ticker": tickers,
        "event_date": [pd.Timestamp("2026-09-01")] * len(tickers),
        "event_type": ["earnings"] * len(tickers),
    })


STRATEGY = {"date_consistency": {"enabled": True}}
AS_OF = pd.Timestamp("2026-07-18")


class TestApplyDateConsistency:
    def test_disabled_passes_everything_through(self, tmp_path):
        events = _events(["GOOD", "BAD"])
        out, fields = _apply_date_consistency(events, {}, AS_OF, tmp_path)
        assert len(out) == 2 and fields == {}

    def test_missing_history_file_is_flagged_not_silent(self, tmp_path):
        events = _events(["GOOD"])
        out, fields = _apply_date_consistency(events, STRATEGY, AS_OF, tmp_path)
        assert len(out) == 1
        assert fields == {"date_consistency_unavailable": True}

    def test_nan_ticker_rows_do_not_crash(self, tmp_path):
        good = quarterly("2024-01-15", 10)
        _realized_csv(tmp_path, {"GOOD": good})
        events = _events(["GOOD"])
        events.loc[len(events)] = {"ticker": float("nan"),
                                   "event_date": pd.Timestamp("2026-09-01"),
                                   "event_type": "earnings"}
        out, fields = _apply_date_consistency(events, STRATEGY, AS_OF, tmp_path)
        assert list(out["ticker"]) == ["GOOD"]
        assert fields["date_consistency_excluded"] == 0  # NaN is not a symbol

    def test_irregular_and_unknown_tickers_are_dropped(self, tmp_path):
        good = quarterly("2024-01-15", 10)
        erratic = quarterly("2024-02-01", 10)
        erratic[-1] += pd.Timedelta(days=20)
        _realized_csv(tmp_path, {"GOOD": good, "BAD": erratic})
        events = _events(["GOOD", "BAD", "NOHIST"])
        out, fields = _apply_date_consistency(events, STRATEGY, AS_OF, tmp_path)
        assert list(out["ticker"]) == ["GOOD"]
        assert fields["date_consistency_excluded"] == 2
        assert fields["date_consistency_excluded_symbols"] == ["BAD", "NOHIST"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
