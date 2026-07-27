"""Sector-rotation snapshot reader and endpoint coverage.

The reader serves an archive the utilities module already wrote. A missing
archive is a normal empty state; anything inconsistent fails closed rather than
serving partial leadership numbers.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_SECTOR_COLUMNS = [
    "schema_version", "as_of", "symbol", "sector", "window_sessions",
    "window_start", "window_end", "total_return", "benchmark_return",
    "excess_return", "rank", "rank_of", "percentile", "prior_excess_return",
    "prior_rank", "rank_change", "rs_change", "leadership_state", "rs_trend",
    "volume_window_avg", "volume_baseline_avg", "volume_ratio", "volume_confirms",
]
_SECTOR_ROW = [
    "1", "2026-07-23", "XLV", "Health Care", "20", "2026-06-24", "2026-07-23",
    "0.0528", "0.0067", "0.0460", "2", "11", "0.9", "0.0577", "2", "0", "-0.0117",
    "LEADING", "WEAKENING", "9000000", "8000000", "1.125", "True",
]
_PAIR_COLUMNS = [
    "schema_version", "as_of", "numerator", "denominator", "window_sessions",
    "ratio_now", "ratio_prior", "ratio_change_pct", "numerator_outperforming",
]
_PAIR_ROW = ["1", "2026-07-23", "XLK", "XLV", "20", "1.5", "1.6", "-0.0625", "False"]


def _write_archive(root, *, version: int = 1, as_of: str = "2026-07-23",
                   snapshot_as_of: str | None = None,
                   pointer_sector: str | None = None) -> None:
    (root / f"{as_of}.csv").write_text(
        ",".join(_SECTOR_COLUMNS) + "\n" + ",".join(_SECTOR_ROW) + "\n", encoding="utf-8")
    (root / f"{as_of}.pairs.csv").write_text(
        ",".join(_PAIR_COLUMNS) + "\n" + ",".join(_PAIR_ROW) + "\n", encoding="utf-8")
    (root / f"{as_of}.rotation.json").write_text(json.dumps({
        "schema_name": "smallfish.sector-rotation",
        "schema_version": version,
        "as_of": snapshot_as_of or as_of,
        "session_end": as_of,
        "sessions_used": 127,
        "sessions_required": 127,
        "benchmark": "SPY",
        "included_symbols": ["XLV", "XLK"],
        "exclusions": [{"symbol": "XLRE", "reason": "missing_benchmark_sessions"}],
        "rotation_candidates": [{
            "source": "XLK", "target": "XLV", "target_sector": "Health Care",
            "windows_confirmed": 2,
            "evidence": [{"window_sessions": 20, "agrees": True,
                          "target_rank_change": 3}],
        }],
        "measurement_basis": "rotation proxy, not a measured fund flow",
        "not_validated": "descriptive market-regime context only",
    }), encoding="utf-8")
    (root / "latest.json").write_text(json.dumps({
        "schema_name": "smallfish.sector-rotation",
        "schema_version": version,
        "as_of": as_of,
        "sector_report": pointer_sector or f"{as_of}.csv",
        "pair_report": f"{as_of}.pairs.csv",
        "rotation_snapshot": f"{as_of}.rotation.json",
    }), encoding="utf-8")


def test_sector_rotation_returns_the_validated_snapshot(monkeypatch, tmp_path):
    _write_archive(tmp_path)
    monkeypatch.setenv("SFP_SECTOR_ROTATION_DIR", str(tmp_path))

    body = client.get("/sectorRotation").json()

    assert body["available"] is True
    assert body["asOf"] == "2026-07-23"
    assert body["benchmark"] == "SPY"
    assert body["windows"] == [20]
    assert body["sessionsUsed"] == 127
    row = body["sectors"][0]
    assert row["symbol"] == "XLV"
    assert row["leadershipState"] == "LEADING"
    assert row["rankChange"] == 0
    assert abs(row["excessReturn"] - 0.046) < 1e-9
    assert row["volumeConfirms"] is True
    assert body["pairs"][0]["numeratorOutperforming"] is False
    # An excluded ETF stays visible as an exclusion rather than vanishing.
    assert body["exclusions"][0]["symbol"] == "XLRE"
    # Snapshot keys are camel-cased for the Angular surface, nested ones too.
    candidate = body["rotationCandidates"][0]
    assert candidate["source"] == "XLK"
    assert candidate["targetSector"] == "Health Care"
    assert candidate["windowsConfirmed"] == 2
    assert candidate["evidence"][0]["windowSessions"] == 20
    assert candidate["evidence"][0]["targetRankChange"] == 3


def test_sector_rotation_carries_the_proxy_language_from_the_archive(monkeypatch, tmp_path):
    """The UI must not be able to state this more strongly than the archive does."""
    _write_archive(tmp_path)
    monkeypatch.setenv("SFP_SECTOR_ROTATION_DIR", str(tmp_path))

    body = client.get("/sectorRotation").json()

    assert "not a measured fund flow" in body["measurementBasis"]
    assert "descriptive" in body["notValidated"]


def _write_cache(root, symbol: str, year: int, last_session: str) -> None:
    """Minimal price-cache file whose final row carries `last_session`."""
    (root / str(year)).mkdir(parents=True, exist_ok=True)
    (root / str(year) / f"{symbol}.txt").write_text(
        f"01-02-{year},1,1,1,1,1,100\n{last_session},1,1,1,1,1,100\n", encoding="utf-8")


def test_sector_rotation_flags_a_snapshot_behind_the_price_cache(monkeypatch, tmp_path):
    """The snapshot used sessions through 07-23 but the cache now holds 07-24."""
    archive, cache_root = tmp_path / "rotation", tmp_path / "cache"
    archive.mkdir()
    _write_archive(archive)  # session_end 2026-07-23, benchmark SPY
    _write_cache(cache_root, "SPY", 2026, "07-24-2026")
    monkeypatch.setenv("SFP_SECTOR_ROTATION_DIR", str(archive))
    monkeypatch.setenv("SFP_PRICE_CACHE", str(cache_root))

    body = client.get("/sectorRotation").json()

    assert body["stale"] is True
    assert body["sessionEnd"] == "2026-07-23"
    assert body["cacheSessionEnd"] == "2026-07-24"


def test_sector_rotation_is_not_stale_when_the_cache_matches(monkeypatch, tmp_path):
    """A snapshot read on a later calendar day is not stale by itself -- a run on
    a Sunday legitimately ends on Friday's session."""
    archive, cache_root = tmp_path / "rotation", tmp_path / "cache"
    archive.mkdir()
    _write_archive(archive)
    _write_cache(cache_root, "SPY", 2026, "07-23-2026")
    monkeypatch.setenv("SFP_SECTOR_ROTATION_DIR", str(archive))
    monkeypatch.setenv("SFP_PRICE_CACHE", str(cache_root))

    body = client.get("/sectorRotation").json()

    assert body["stale"] is False
    assert body["cacheSessionEnd"] == "2026-07-23"


def test_sector_rotation_cannot_tell_staleness_without_a_cache(monkeypatch, tmp_path):
    """No cache means no claim: report not-stale rather than a false alarm."""
    archive, cache_root = tmp_path / "rotation", tmp_path / "cache"
    archive.mkdir()
    cache_root.mkdir()
    _write_archive(archive)
    monkeypatch.setenv("SFP_SECTOR_ROTATION_DIR", str(archive))
    monkeypatch.setenv("SFP_PRICE_CACHE", str(cache_root))

    body = client.get("/sectorRotation").json()

    assert body["stale"] is False
    assert body["cacheSessionEnd"] is None


def test_sector_rotation_reports_a_missing_archive_as_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("SFP_SECTOR_ROTATION_DIR", str(tmp_path))

    body = client.get("/sectorRotation").json()

    assert body["available"] is False
    assert "sector-rotation" in body["reason"]


def test_sector_rotation_rejects_an_unsupported_schema(monkeypatch, tmp_path):
    _write_archive(tmp_path, version=99)
    monkeypatch.setenv("SFP_SECTOR_ROTATION_DIR", str(tmp_path))

    response = client.get("/sectorRotation")

    assert response.status_code == 409
    assert "schema" in response.json()["detail"].lower()


def test_sector_rotation_rejects_a_snapshot_disagreeing_with_its_pointer(monkeypatch, tmp_path):
    _write_archive(tmp_path, snapshot_as_of="2026-01-02")
    monkeypatch.setenv("SFP_SECTOR_ROTATION_DIR", str(tmp_path))

    response = client.get("/sectorRotation")

    assert response.status_code == 409
    assert "disagrees" in response.json()["detail"]


def test_sector_rotation_rejects_a_pointer_escaping_the_archive(monkeypatch, tmp_path):
    _write_archive(tmp_path, pointer_sector="../secrets.csv")
    monkeypatch.setenv("SFP_SECTOR_ROTATION_DIR", str(tmp_path))

    response = client.get("/sectorRotation")

    assert response.status_code == 409
    assert "invalid" in response.json()["detail"].lower()
