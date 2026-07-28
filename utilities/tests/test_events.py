from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from utilities.events import (
    EventDataError,
    FinnhubConfig,
    ensure_fresh_events,
    main,
    run_fetch,
)


def _events(symbol: str = "AAPL", event_date: str = "2026-08-01") -> pd.DataFrame:
    return pd.DataFrame([{
        "ticker": symbol,
        "event_type": "earnings",
        "event_date": event_date,
        "source": "finnhub",
    }])


def test_run_fetch_writes_current_history_and_freshness(tmp_path: Path) -> None:
    calls = []

    def fake_fetch(start: str, end: str, config: FinnhubConfig) -> pd.DataFrame:
        calls.append((start, end, config.api_key))
        return _events()

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


def test_ensure_fresh_events_reuses_recent_covered_cache_without_a_key(tmp_path: Path) -> None:
    run_fetch(
        # `NA` is a real ticker and must not be parsed as pandas' default NA token.
        "2026-07-15", 70, lambda *_: _events("NA"),
        api_key="test-key", output_root=tmp_path)

    def unexpected_fetch(*_args):
        raise AssertionError("fresh cache must not contact Finnhub")

    result = ensure_fresh_events(
        "2026-07-16", api_key=None, output_root=tmp_path,
        fetch_fn=unexpected_fetch)

    assert result.status == "fresh"
    assert result.ok is True


def test_ensure_fresh_events_refreshes_stale_cache_when_key_is_available(tmp_path: Path) -> None:
    calls = []

    def fake_fetch(start: str, end: str, config: FinnhubConfig) -> pd.DataFrame:
        calls.append((start, end, config.api_key))
        return _events("MSFT", "2026-08-15")

    result = ensure_fresh_events(
        "2026-07-16", api_key="test-key", output_root=tmp_path,
        fetch_fn=fake_fetch)

    assert result.status == "refreshed"
    assert calls == [("2026-07-16", "2026-09-24", "test-key")]
    assert pd.read_csv(tmp_path / "events.csv")["ticker"].tolist() == ["MSFT"]


def test_ensure_fresh_events_without_key_keeps_stale_cache(tmp_path: Path) -> None:
    run_fetch(
        "2026-07-01", 70, lambda *_: _events(),
        api_key="test-key", output_root=tmp_path)
    before = (tmp_path / "events.csv").read_bytes()

    result = ensure_fresh_events(
        "2026-07-16", api_key=None, output_root=tmp_path)

    assert result.status == "unavailable"
    assert result.ok is False
    assert (tmp_path / "events.csv").read_bytes() == before


def test_failed_refresh_never_replaces_last_good_calendar(tmp_path: Path) -> None:
    run_fetch(
        "2026-07-01", 70, lambda *_: _events(),
        api_key="test-key", output_root=tmp_path)
    protected = {
        path.name: path.read_bytes()
        for path in (tmp_path / "events.csv", tmp_path / "events_meta.json")
    }

    result = ensure_fresh_events(
        "2026-07-16", api_key="test-key", output_root=tmp_path,
        fetch_fn=lambda *_: pd.DataFrame(columns=[
            "ticker", "event_type", "event_date", "source"]),
    )

    assert result.status == "error"
    assert "EventDataError" in result.message
    assert {
        path.name: path.read_bytes()
        for path in (tmp_path / "events.csv", tmp_path / "events_meta.json")
    } == protected


def test_run_fetch_rejects_malformed_provider_shape(tmp_path: Path) -> None:
    with pytest.raises(EventDataError, match="missing required columns"):
        run_fetch(
            "2026-07-16", 70, lambda *_: pd.DataFrame({"symbol": ["AAPL"]}),
            api_key="test-key", output_root=tmp_path)
    assert not (tmp_path / "events.csv").exists()


def test_cli_fetch_failure_does_not_echo_provider_detail(
        tmp_path: Path, monkeypatch, capsys) -> None:
    class ProviderFailure(RuntimeError):
        pass

    def fail_fetch(*_args):
        raise ProviderFailure(
            "provider URL detail must stay private?token=configured-in-test")

    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SFP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FINNHUB_API_KEY", "configured-in-test")
    monkeypatch.setattr("utilities.events.fetch_earnings_calendar", fail_fetch)

    assert main(["--as-of", "2026-07-16"]) == 1
    output = capsys.readouterr().out
    assert "ProviderFailure" in output
    assert "provider URL detail" not in output
    log = (tmp_path / "logs" / "earnings_refresh.log").read_text(encoding="utf-8")
    assert "ProviderFailure: provider URL detail must stay private?token=***" in log
    assert "configured-in-test" not in log
