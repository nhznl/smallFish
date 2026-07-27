"""Immutable latest option-quote archive reader and endpoint coverage."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _write_latest(root, *, version: int = 3, pointer_report: str | None = None,
                  collection_scope: dict | None = None) -> None:
    run_id = "20260725T043926199394Z"
    run = root / "runs" / run_id
    run.mkdir(parents=True)
    columns = [
        "schema_version", "contract_id", "provider_contract_symbol", "symbol",
        "contract_quality", "as_of", "requested_dte", "expiry", "actual_dte",
        "dte_deviation", "side", "strike", "moneyness", "analysis_view",
        "strategy_role", "bid", "ask", "mid", "open_interest", "volume",
        "spread_abs", "spread_pct", "quote_source", "quote_provider_status",
        "quote_streamer_symbol", "bid_timestamp", "ask_timestamp",
        "quote_event_timestamp", "bid_size", "ask_size", "retrieved_at",
        "market_session", "quote_age_seconds", "quote_quality",
        "quote_quality_reasons", "liquidity_ok", "gate_reason", "entry_eligible",
        "entry_reason",
    ]
    values = [
        str(version), "TASTY:ABC", "ABC260731P00100000", "ABC", "OK", "2026-07-24",
        "7", "2026-07-31", "7", "0", "PUT", "100", "OTM", "ENTRY", "CSP_ENTRY",
        "1.20", "1.40", "1.30", "450", "25", "0.2", "0.15", "TASTYTRADE_DXLINK",
        "RECEIVED", ".ABC260731P100", "2026-07-24T15:00:00+00:00",
        "2026-07-24T15:00:00+00:00", "2026-07-24T15:00:00+00:00", "10", "12",
        "2026-07-24T15:00:05+00:00", "RTH", "5", "OK", "", "True", "", "True", "",
    ]
    (run / "premiums.csv").write_text(",".join(columns) + "\n" + ",".join(values) + "\n", encoding="utf-8")
    meta = {
        "run_id": run_id, "schema_name": "smallfish.option-premium", "schema_version": version,
        "as_of": "2026-07-24", "generated_at_utc": "2026-07-24T15:00:05+00:00",
        "quote_provider": {"source": "TASTYTRADE_DXLINK", "status": "COMPLETE"},
    }
    if collection_scope is not None:
        meta["collection_scope"] = collection_scope
    (run / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (root / "latest.json").write_text(json.dumps({
        "run_id": run_id, "schema_name": "smallfish.option-premium", "schema_version": version,
        "immutable_report": pointer_report or f"runs/{run_id}/premiums.csv",
        "immutable_meta": f"runs/{run_id}/run_meta.json",
    }), encoding="utf-8")


def test_option_quotes_returns_validated_v3_latest(monkeypatch, tmp_path):
    _write_latest(tmp_path)
    monkeypatch.setenv("SFP_PREMIUMS_DIR", str(tmp_path))

    response = client.get("/optionQuotes")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["runId"] == "20260725T043926199394Z"
    assert body["summary"] == {
        "contracts": 1, "symbols": 1, "entryViewContracts": 1,
        "rollExitViewContracts": 0, "entryEligibleContracts": 1,
        "quoteQualityCounts": {"OK": 1},
        "quoteSourceCounts": {"TASTYTRADE_DXLINK": 1},
        "providerStatusCounts": {"RECEIVED": 1},
    }
    assert body["rows"][0]["requestedDte"] == 7
    assert body["rows"][0]["entryEligible"] is True


def test_option_quotes_exposes_the_recorded_collection_scope(monkeypatch, tmp_path):
    """A narrowed archive must announce its scope so it is never read as a full sweep."""
    _write_latest(tmp_path, collection_scope={
        "scoped": True,
        "configured_dtes": [7, 37],
        "requested_dtes": [37],
        "symbols": ["AAPL"],
        "symbol_count": 1,
        "min_otm_pct": 0.05,
        "min_otm_applies_to": "ENTRY",
        "limit": None,
        "symbols_without_entry_strikes": ["KO"],
    })
    monkeypatch.setenv("SFP_PREMIUMS_DIR", str(tmp_path))

    body = client.get("/optionQuotes").json()

    assert body["collectionScope"] == {
        "scoped": True,
        "configuredDtes": [7, 37],
        "requestedDtes": [37],
        "symbols": ["AAPL"],
        "symbolCount": 1,
        "minOtmPct": 0.05,
        "minOtmAppliesTo": "ENTRY",
        "limit": None,
        "symbolsWithoutEntryStrikes": ["KO"],
    }


def test_option_quotes_scope_is_null_for_pre_scope_archives(monkeypatch, tmp_path):
    """An older archive must not be reported as either scoped or full."""
    _write_latest(tmp_path)
    monkeypatch.setenv("SFP_PREMIUMS_DIR", str(tmp_path))

    assert client.get("/optionQuotes").json()["collectionScope"] is None


def test_option_quotes_reports_missing_latest_pointer(monkeypatch, tmp_path):
    monkeypatch.setenv("SFP_PREMIUMS_DIR", str(tmp_path))

    response = client.get("/optionQuotes")

    assert response.status_code == 200
    assert response.json()["available"] is False


def test_option_quotes_rejects_unsupported_schema(monkeypatch, tmp_path):
    _write_latest(tmp_path, version=2)
    monkeypatch.setenv("SFP_PREMIUMS_DIR", str(tmp_path))

    response = client.get("/optionQuotes")

    assert response.status_code == 409
    assert "schema v3" in response.json()["detail"]


def test_option_quotes_rejects_untrusted_latest_pointer(monkeypatch, tmp_path):
    _write_latest(tmp_path, pointer_report="../../outside.csv")
    monkeypatch.setenv("SFP_PREMIUMS_DIR", str(tmp_path))

    response = client.get("/optionQuotes")

    assert response.status_code == 409
    assert "pointer" in response.json()["detail"]
