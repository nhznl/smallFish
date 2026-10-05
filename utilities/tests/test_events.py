from __future__ import annotations

import json
from datetime import datetime, timezone
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

NOW = datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)


def _events(symbol: str = "AAPL", event_date: str = "2026-08-01") -> pd.DataFrame:
    return pd.DataFrame([{
        "ticker": symbol,
        "event_type": "earnings",
        "event_date": event_date,
        "source": "finnhub",
        "fiscal_year": 2026,
        "fiscal_quarter": 3,
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
    calendar = json.loads(
        (tmp_path / "market_calendar" / "earnings.json").read_text(encoding="utf-8")
    )
    assert calendar["events"] == [{
        "canonicalKey": "US:EARNINGS:AAPL:2026-Q3",
        "civilDate": "2026-08-01",
        "eventType": "EARNINGS",
        "id": "AAPL:2026-Q3",
        "referenceLabel": "2026-Q3",
        "referencePeriod": "2026-Q3",
        "symbol": "AAPL",
        "title": "AAPL earnings",
    }]
    assert calendar["schemaVersion"] == 2
    assert calendar["identityComplete"] is True
    assert calendar["identityFailureCount"] == 0
    assert calendar["coverageStart"] == "2026-07-16"
    assert calendar["coverageEnd"] == "2026-09-24"


@pytest.mark.parametrize("with_identity_columns", [True, False])
def test_run_fetch_marks_calendar_projection_incomplete_when_identity_is_missing(
    tmp_path: Path, with_identity_columns: bool,
) -> None:
    rows = [
        {
            "ticker": "AAPL",
            "event_type": "earnings",
            "event_date": "2026-08-01",
            "source": "finnhub",
            **({"fiscal_year": 2026, "fiscal_quarter": 3} if with_identity_columns else {}),
        },
        {
            "ticker": "MSFT",
            "event_type": "earnings",
            "event_date": "2026-08-02",
            "source": "finnhub",
            **({"fiscal_year": None, "fiscal_quarter": None} if with_identity_columns else {}),
        },
    ]
    run_fetch(
        "2026-07-16", 70, lambda *_: pd.DataFrame(rows),
        api_key="test-key", output_root=tmp_path,
    )

    legacy = pd.read_csv(tmp_path / "events.csv")
    assert legacy["ticker"].tolist() == ["AAPL", "MSFT"]
    calendar = json.loads(
        (tmp_path / "market_calendar" / "earnings.json").read_text(encoding="utf-8")
    )
    assert calendar["identityComplete"] is False
    assert calendar["identityFailureCount"] == (1 if with_identity_columns else 2)
    assert calendar["coverageStart"] is None
    assert calendar["coverageEnd"] is None
    assert len(calendar["events"]) == (1 if with_identity_columns else 0)


@pytest.mark.parametrize(
    ("fiscal_year", "fiscal_quarter"),
    [
        (True, 3),
        (2026.5, 3),
        ("20x6", 3),
        ("026", 3),
        (2026, 3.5),
        (2026, 5),
    ],
)
def test_run_fetch_rejects_malformed_fiscal_identity_tokens(
    tmp_path: Path, fiscal_year: object, fiscal_quarter: object,
) -> None:
    malformed = pd.DataFrame([{
        "ticker": "AAPL",
        "event_type": "earnings",
        "event_date": "2026-08-01",
        "source": "finnhub",
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
    }])

    run_fetch(
        "2026-07-16", 70, lambda *_: malformed,
        api_key="test-key", output_root=tmp_path,
    )

    calendar = json.loads(
        (tmp_path / "market_calendar" / "earnings.json").read_text(encoding="utf-8")
    )
    assert calendar["events"] == []
    assert calendar["identityComplete"] is False
    assert calendar["identityFailureCount"] == 1
    assert calendar["coverageStart"] is None
    assert calendar["coverageEnd"] is None


def test_ensure_fresh_events_reuses_recent_covered_cache_without_a_key(tmp_path: Path) -> None:
    run_fetch(
        # `NA` is a real ticker and must not be parsed as pandas' default NA token.
        "2026-07-15", 70, lambda *_: _events("NA"),
        api_key="test-key", output_root=tmp_path)

    def unexpected_fetch(*_args):
        raise AssertionError("fresh cache must not contact Finnhub")

    result = ensure_fresh_events(
        "2026-07-16", api_key=None, output_root=tmp_path,
        fetch_fn=unexpected_fetch, now=NOW)

    assert result.status == "fresh"
    assert result.ok is True


@pytest.mark.parametrize("sidecar_state", ["missing", "schema1"])
def test_ensure_fresh_events_upgrades_non_schema2_sidecar_when_key_is_available(
    tmp_path: Path, sidecar_state: str,
) -> None:
    run_fetch(
        "2026-07-15", 70, lambda *_: _events("AAPL"),
        api_key="test-key", output_root=tmp_path,
    )
    sidecar = tmp_path / "market_calendar" / "earnings.json"
    if sidecar_state == "missing":
        sidecar.unlink()
    else:
        content = json.loads(sidecar.read_text(encoding="utf-8"))
        content["schemaVersion"] = 1
        sidecar.write_text(json.dumps(content), encoding="utf-8")
    calls = []

    def fake_fetch(start: str, end: str, config: FinnhubConfig) -> pd.DataFrame:
        calls.append((start, end, config.api_key))
        return _events("MSFT")

    result = ensure_fresh_events(
        "2026-07-16", api_key="test-key", output_root=tmp_path,
        fetch_fn=fake_fetch, now=NOW,
    )

    assert result.status == "refreshed"
    assert calls == [("2026-07-16", "2026-09-24", "test-key")]
    assert json.loads(sidecar.read_text(encoding="utf-8"))["schemaVersion"] == 2


def test_ensure_fresh_events_reports_legacy_only_capability_without_key(
    tmp_path: Path,
) -> None:
    run_fetch(
        "2026-07-15", 70, lambda *_: _events(),
        api_key="test-key", output_root=tmp_path,
    )
    sidecar = tmp_path / "market_calendar" / "earnings.json"
    sidecar.unlink()

    result = ensure_fresh_events(
        "2026-07-16", api_key=None, output_root=tmp_path,
        fetch_fn=lambda *_: (_ for _ in ()).throw(AssertionError("network")),
        now=NOW,
    )

    assert result.status == "legacy_fresh"
    assert result.ok is False
    assert result.legacy_ok is True
    assert "schema-2 sidecar is missing, outdated, or identity-incomplete" in result.message
    assert "FINNHUB_API_KEY is not configured" in result.message


def test_ensure_fresh_events_rejects_mixed_same_day_artifact_generations(
    tmp_path: Path,
) -> None:
    run_fetch(
        "2026-07-16", 70, lambda *_: _events("AAPL"),
        api_key="test-key", output_root=tmp_path,
    )
    old_sidecar = (tmp_path / "market_calendar" / "earnings.json").read_bytes()
    run_fetch(
        "2026-07-16", 70, lambda *_: _events("MSFT"),
        api_key="test-key", output_root=tmp_path,
    )
    (tmp_path / "market_calendar" / "earnings.json").write_bytes(old_sidecar)
    calls = []

    def fake_fetch(*_args):
        calls.append(True)
        return _events("GOOG")

    result = ensure_fresh_events(
        "2026-07-16", api_key="test-key", output_root=tmp_path,
        fetch_fn=fake_fetch, now=NOW,
    )

    assert result.status == "refreshed"
    assert calls == [True]
    assert pd.read_csv(tmp_path / "events.csv")["ticker"].tolist() == ["GOOG"]


def test_ensure_fresh_events_does_not_report_incomplete_refresh_as_success(
    tmp_path: Path,
) -> None:
    malformed = _events()
    malformed["fiscal_year"] = 2026.5

    result = ensure_fresh_events(
        "2026-07-16", api_key="test-key", output_root=tmp_path,
        fetch_fn=lambda *_: malformed, now=NOW,
    )

    assert result.status == "calendar_incomplete"
    assert result.ok is False
    assert result.legacy_ok is True
    assert "sidecar is not complete and current" in result.message


def test_ensure_fresh_events_uses_exact_calendar_timestamp_boundary(
    tmp_path: Path,
) -> None:
    run_fetch(
        "2026-10-04", 70, lambda *_: _events("AAPL", "2026-10-20"),
        api_key="test-key", output_root=tmp_path,
    )
    exact = ensure_fresh_events(
        "2026-10-05", api_key=None, output_root=tmp_path,
        now=datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc),
    )
    expired = ensure_fresh_events(
        "2026-10-05", api_key=None, output_root=tmp_path,
        now=datetime(2026, 10, 5, 4, 0, 1, tzinfo=timezone.utc),
    )

    assert exact.status == "fresh"
    assert exact.ok is True
    assert expired.status == "legacy_fresh"
    assert expired.ok is False
    assert expired.legacy_ok is True


def test_ensure_fresh_events_refreshes_stale_cache_when_key_is_available(tmp_path: Path) -> None:
    calls = []

    def fake_fetch(start: str, end: str, config: FinnhubConfig) -> pd.DataFrame:
        calls.append((start, end, config.api_key))
        return _events("MSFT", "2026-08-15")

    result = ensure_fresh_events(
        "2026-07-16", api_key="test-key", output_root=tmp_path,
        fetch_fn=fake_fetch, now=NOW)

    assert result.status == "refreshed"
    assert calls == [("2026-07-16", "2026-09-24", "test-key")]
    assert pd.read_csv(tmp_path / "events.csv")["ticker"].tolist() == ["MSFT"]


def test_ensure_fresh_events_without_key_keeps_stale_cache(tmp_path: Path) -> None:
    run_fetch(
        "2026-07-01", 70, lambda *_: _events(),
        api_key="test-key", output_root=tmp_path)
    before = (tmp_path / "events.csv").read_bytes()

    result = ensure_fresh_events(
        "2026-07-16", api_key=None, output_root=tmp_path, now=NOW)

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
        now=NOW,
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
