"""Tests for scan, Wheel, and option-quote command endpoints.

The real endpoints shell ``./commands.sh scan|wheel`` (a full scan/wheel run) and
reload the cache. Here subprocess.run and cache.reload are mocked so the tests are
deterministic + offline and assert the response SHAPE + tail-lines semantics.
"""

from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from app.main import app
from app.routers import run_jobs

client = TestClient(app)


class _FakeProc:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def test_scan_job_ok_shape(monkeypatch):
    reloaded = {"n": 0}
    monkeypatch.setattr(run_jobs.cache, "reload", lambda: reloaded.__setitem__("n", reloaded["n"] + 1))
    twenty_lines = "\n".join(f"line{i}" for i in range(20))
    monkeypatch.setattr(run_jobs.subprocess, "run", lambda *a, **k: _FakeProc(0, twenty_lines))

    body = run_jobs._run_command("scan")
    assert set(body) == {"status", "exitCode", "durationMs", "output"}
    assert body["status"] == "ok"
    assert body["exitCode"] == 0
    assert isinstance(body["durationMs"], int)
    # last 12 lines only
    assert body["output"].split("\n") == [f"line{i}" for i in range(8, 20)]
    assert reloaded["n"] == 1  # cache reloaded on exit 0


def test_scan_job_error_no_reload(monkeypatch):
    reloaded = {"n": 0}
    monkeypatch.setattr(run_jobs.cache, "reload", lambda: reloaded.__setitem__("n", reloaded["n"] + 1))
    monkeypatch.setattr(run_jobs.subprocess, "run", lambda *a, **k: _FakeProc(1, "boom"))

    body = run_jobs._run_command("scan")
    assert body["status"] == "error"
    assert body["exitCode"] == 1
    assert body["output"] == "boom"
    assert reloaded["n"] == 0  # non-zero exit -> no reload


def test_run_wheel_timeout(monkeypatch):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="./commands.sh wheel", timeout=300)

    monkeypatch.setattr(run_jobs.subprocess, "run", _raise)
    body = client.get("/runWheel").json()
    assert body["status"] == "timeout"
    assert body["message"] == "Wheel scan did not finish within 5 minutes."
    assert body["earningsRefresh"]["status"] == "timeout"


def test_run_wheel_checks_earnings_before_scanning(monkeypatch):
    calls = []

    def _run(args, **kwargs):
        job = args[4]
        calls.append(job)
        output = ("Upcoming earnings calendar is fresh."
                  if job == "ensure-events" else "Wheel complete")
        return _FakeProc(0, output)

    monkeypatch.setattr(run_jobs.subprocess, "run", _run)
    monkeypatch.setattr(run_jobs.cache, "reload", lambda: None)

    body = client.get("/runWheel").json()

    assert calls == ["ensure-events", "wheel"]
    assert body["status"] == "ok"
    assert body["earningsRefresh"]["status"] == "ok"
    assert "warning" not in body


def test_run_wheel_continues_with_visible_warning_when_refresh_is_unavailable(monkeypatch):
    calls = []

    def _run(args, **kwargs):
        job = args[4]
        calls.append(job)
        if job == "ensure-events":
            return _FakeProc(3, "Upcoming earnings calendar is stale and no key is configured.")
        return _FakeProc(0, "Wheel complete")

    monkeypatch.setattr(run_jobs.subprocess, "run", _run)
    monkeypatch.setattr(run_jobs.cache, "reload", lambda: None)

    body = client.get("/runWheel").json()

    assert calls == ["ensure-events", "wheel"]
    assert body["status"] == "ok"
    assert body["earningsRefresh"]["status"] == "error"
    assert "Unknown (stale)" in body["warning"]


def test_required_earnings_job_fails_before_scan_when_refresh_is_unavailable(monkeypatch):
    calls = []

    def _run(args, **kwargs):
        calls.append(args[4])
        return _FakeProc(
            3,
            "Upcoming earnings calendar is stale or missing and FINNHUB_API_KEY is not configured.",
        )

    monkeypatch.setattr(run_jobs.subprocess, "run", _run)

    body = run_jobs._run_earnings_dependent_command("scan", require_fresh=True)

    assert calls == ["ensure-events"]
    assert body["status"] == "error"
    assert "FINNHUB_API_KEY" in body["message"]
    assert body["earningsRefresh"]["status"] == "error"


def _capture_chains(monkeypatch) -> dict:
    """Record the argv a /runChains call would execute."""
    called: dict = {}

    def _run(args, **kwargs):
        called["args"] = args
        return _FakeProc(0, "Tastytrade quote collection: COMPLETE (4/4 contracts)")

    monkeypatch.setattr(run_jobs.subprocess, "run", _run)
    monkeypatch.setattr(run_jobs.cache, "reload", lambda: None)
    return called


def test_run_chains_dispatches_prospective_quote_collection(monkeypatch):
    called = _capture_chains(monkeypatch)

    body = client.get("/runChains").json()

    assert body["status"] == "ok"
    # Arguments are positional parameters, never interpolated into the script.
    assert called["args"][:3] == ["/bin/bash", "-lc", 'exec ./commands.sh "$@"']
    assert called["args"][4:] == ["chains"]
    assert "COMPLETE" in body["output"]


def test_run_chains_forwards_the_requested_collection_scope(monkeypatch):
    called = _capture_chains(monkeypatch)

    body = client.get(
        "/runChains", params={"horizonDte": 37, "symbols": "aapl,msft", "minOtmPct": 5}
    ).json()

    assert body["status"] == "ok"
    assert called["args"][4:] == [
        "chains", "--horizon-dte", "37", "--symbols", "AAPL,MSFT",
        "--min-otm-pct", "5",
    ]


def test_run_chains_scope_arguments_cannot_extend_the_command(monkeypatch):
    """A shell metacharacter is rejected as a symbol, not passed through."""
    called = _capture_chains(monkeypatch)

    r = client.get("/runChains", params={"symbols": "AAPL; rm -rf /"})

    assert r.status_code == 400
    assert "Invalid symbol" in r.json()["detail"]
    assert "args" not in called, "an invalid scope must never reach the shell"


def test_run_chains_rejects_out_of_range_scope_values(monkeypatch):
    _capture_chains(monkeypatch)

    assert client.get("/runChains", params={"horizonDte": 0}).status_code == 400
    assert client.get("/runChains", params={"minOtmPct": 100}).status_code == 400
    assert client.get("/runChains", params={"minOtmPct": -1}).status_code == 400
    assert client.get("/runChains", params={"symbols": " , "}).status_code == 400


def test_run_sector_rotation_does_not_rebuild_the_stock_cache(monkeypatch):
    """The snapshot is served from its own archive, so a full universe rebuild
    would be pure cost."""
    called: dict = {}
    reloaded = {"n": 0}

    def _run(args, **kwargs):
        called["args"] = args
        return _FakeProc(0, "Sector rotation as of 2026-07-26")

    monkeypatch.setattr(run_jobs.subprocess, "run", _run)
    monkeypatch.setattr(run_jobs.cache, "reload",
                        lambda: reloaded.__setitem__("n", reloaded["n"] + 1))

    body = client.get("/runSectorRotation").json()

    assert body["status"] == "ok"
    assert called["args"][4:] == ["sector-rotation"]
    assert reloaded["n"] == 0
    assert "Sector rotation" in body["output"]


def test_scan_job_launch_failure(monkeypatch):
    def _raise(*a, **k):
        raise OSError("bash not found")

    monkeypatch.setattr(run_jobs.subprocess, "run", _raise)
    body = run_jobs._run_command("scan")
    assert body["status"] == "error"
    assert body["message"] == "The job could not be started."
    assert "bash not found" not in body["message"]
    assert set(body) == {"status", "message"}
