from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from utilities.events import FinnhubConfig, run_fetch


def test_run_fetch_writes_current_history_and_freshness(tmp_path: Path) -> None:
    calls = []

    def fake_fetch(start: str, end: str, config: FinnhubConfig) -> pd.DataFrame:
        calls.append((start, end, config.api_key))
        return pd.DataFrame([{
            "ticker": "AAPL", "event_type": "earnings",
            "event_date": "2026-08-01", "source": "finnhub",
        }])

    events, current, archived = run_fetch(
        "2026-07-16", 70, fake_fetch, api_key="test-key", output_root=tmp_path)

    assert calls == [("2026-07-16", "2026-09-24", "test-key")]
    assert list(events["fetched_as_of"]) == ["2026-07-16"]
    assert current == tmp_path / "events.csv"
    assert archived == tmp_path / "events_history" / "2026-07-16.csv"
    assert current.read_bytes() == archived.read_bytes()
    assert json.loads((tmp_path / "events_meta.json").read_text()) == {
        "events_fetched_as_of": "2026-07-16", "events_coverage_end": "2026-09-24",
    }
