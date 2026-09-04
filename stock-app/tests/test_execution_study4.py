"""Study 4 execution control-plane tests. Injected fakes only; no sockets."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.execution.broker import ExecutionBroker
from app.execution.ledger import ExecutionLedger
from app.execution.service import ExecutionService
from app.execution.settings import ExecutionSettings, load_settings
from app.main import app
from models.study4_live import CycleState, OrderIntentState

NY = ZoneInfo("America/New_York")
client = TestClient(app)
CUTOFF = datetime(2026, 9, 3, 16, 5, tzinfo=NY)
OPEN = datetime(2026, 9, 4, 9, 31, tzinfo=NY)


def _settings(tmp_path: Path, **overrides) -> ExecutionSettings:
    values = dict(
        mode="sandbox",
        production_enabled=False,
        kill_switch=False,
        account_alias="study4-sandbox",
        account_fingerprint="abc123",
        confirmation_secret="secret",
        target_bucket=10000.0,
        production_cap=None,
    )
    values.update(overrides)
    return ExecutionSettings(**values)


def _artifact(session="2026-09-03", cutoff=True):
    items = [
        {
            "priority": 1, "kind": "stock_entry", "symbol": "AAA", "side": "buy",
            "deltaShares": 10, "limitPrice": 10.3, "timeInForce": "IOC",
            "orderType": "Limit", "reservationCash": 103.0, "rank": 1,
        }
    ] if cutoff else []
    return {
        "artifactHash": "hash-1",
        "session": session,
        "isCutoff": cutoff,
        "isExecutionSession": not cutoff,
        "executionSession": "2026-09-04",
        "cutoffSession": "2026-09-03",
        "scanRows": [{"ticker": "AAA", "state": "selected", "shares": 10, "limit_price": 10.3}],
        "planItems": items,
        "holdings": {"cash": 10000, "spy_shares": 0, "positions": []},
        "positionDecisions": [],
        "marketRegime": "RISK_ON",
        "protocolId": "pre-earnings-post-event-weekly-batch-risk-on-v1",
    }


class FakeBroker:
    def __init__(self):
        self.submitted = []
        self.lookups = []

    def snapshot(self):
        return {
            "is_closed": False,
            "is_frozen": False,
            "cash": 10000.0,
            "normalized_positions": [],
            "live_orders": (),
        }

    def dry_run(self, request):
        return {
            "ok": True, "time_in_force": "IOC", "errors": [], "warnings": [],
            "broker_order_id": None, "status": "Routed",
        }

    def submit(self, request):
        self.submitted.append(request)
        return {
            "ok": True, "time_in_force": "IOC", "errors": [], "warnings": [],
            "broker_order_id": "99", "status": "Filled",
            "external_identifier": request.external_identifier,
        }

    def lookup(self, external_identifier):
        self.lookups.append(external_identifier)
        return (SimpleNamespace(broker_order_id="99", external_identifier=external_identifier),)


def _service(tmp_path: Path, artifact=None, broker=None, clock=None) -> ExecutionService:
    settings = _settings(tmp_path)
    ledger = ExecutionLedger(tmp_path / "study4.sqlite")
    payload = artifact or _artifact()

    def evaluator(**kwargs):
        return payload

    return ExecutionService(
        settings=settings,
        ledger=ledger,
        broker=broker or FakeBroker(),
        evaluator=evaluator,
        clock=clock or (lambda: CUTOFF),
    )


def _arm(service: ExecutionService) -> None:
    service.capital_reset(target_bucket=10000, active_bucket=10000)
    service.finalize("2026-09-03")
    token = service.preflight()["confirmationChallenge"]
    service.confirm(token)
    service.clock = lambda: OPEN


def test_status_is_inert_when_execution_mode_is_off():
    body = client.get("/api/execution/study4/status").json()
    assert body["configured"] is False
    assert body["submissionsAllowed"] is False
    assert body["configuredTargetBucket"] is None
    assert "EXPLORATORY" in body["exploratoryWarning"]
    requirements = {item["id"]: item for item in body["setupRequirements"]}
    assert requirements["execution_mode"]["ready"] is False
    assert "SFP_STUDY4_EXECUTION_MODE" in requirements["execution_mode"]["action"]
    assert all(isinstance(item["ready"], bool) for item in body["setupRequirements"])


def test_status_distinguishes_configured_target_from_recommended_pilot_cap(tmp_path: Path):
    service = ExecutionService(
        settings=_settings(tmp_path, target_bucket=50000),
        ledger=ExecutionLedger(tmp_path / "status.sqlite"),
        broker=FakeBroker(),
        evaluator=lambda **kwargs: _artifact(),
    )

    body = service.status()

    assert body["configuredTargetBucket"] == 50000
    assert body["recommendedPilotCap"] == 5000


def test_scan_finalize_preflight_confirm_and_execute(tmp_path: Path):
    broker = FakeBroker()
    service = _service(tmp_path, broker=broker)
    _arm(service)
    opened = service.execute()
    assert broker.submitted
    assert opened["results"][0]["state"] in {
        OrderIntentState.ACKNOWLEDGED.value, OrderIntentState.FILLED.value,
    }


def test_duplicate_submit_is_blocked_and_unknown_looks_up_external_id(tmp_path: Path):
    class BoomBroker(FakeBroker):
        def submit(self, request):
            raise TimeoutError("network")

    broker = BoomBroker()
    service = _service(tmp_path, broker=broker)
    _arm(service)
    result = service.execute()
    assert result["results"][0]["state"] == OrderIntentState.ACKNOWLEDGED.value
    assert broker.lookups == [broker.lookups[0]]
    with pytest.raises(Exception):
        service.execute()


def test_missed_open_does_not_trade_later(tmp_path: Path):
    service = _service(tmp_path)
    service.capital_reset(target_bucket=10000, active_bucket=10000)
    service.finalize("2026-09-03")
    token = service.preflight()["confirmationChallenge"]
    service.confirm(token)
    service.clock = lambda: datetime(2026, 9, 4, 15, 0, tzinfo=NY)
    with pytest.raises(Exception) as exc:
        service.execute()
    assert "missed" in str(exc.value).lower() or "window" in str(exc.value).lower() or "passed" in str(exc.value).lower()
    assert service.ledger.current_cycle()["state"] in {CycleState.ARMED.value, CycleState.MISSED.value}


def test_kill_switch_blocks_new_submissions(tmp_path: Path):
    service = _service(tmp_path)
    _arm(service)
    service.kill_switch(True)
    with pytest.raises(Exception):
        service.execute()


def test_production_defaults_disabled(tmp_path: Path):
    settings = _settings(tmp_path, mode="production", production_enabled=False, production_cap=10000)
    service = ExecutionService(
        settings=settings,
        ledger=ExecutionLedger(tmp_path / "p.sqlite"),
        broker=FakeBroker(),
        evaluator=lambda **k: _artifact(),
    )
    with pytest.raises(Exception):
        service.finalize("2026-09-03")


def test_confirmation_secret_is_required_before_a_plan_can_advance(tmp_path: Path):
    settings = _settings(tmp_path, confirmation_secret="")
    service = ExecutionService(
        settings=settings,
        ledger=ExecutionLedger(tmp_path / "no-secret.sqlite"),
        broker=FakeBroker(),
        evaluator=lambda **kwargs: _artifact(),
    )

    assert service.status()["submissionsAllowed"] is False
    with pytest.raises(Exception, match="confirmation HMAC secret"):
        service.finalize("2026-09-03")


def test_ioc_dry_run_failure_does_not_downgrade_to_day(tmp_path: Path):
    class DayBroker(FakeBroker):
        def dry_run(self, request):
            return {"ok": True, "time_in_force": "Day", "errors": []}

    service = _service(tmp_path, broker=DayBroker())
    service.capital_reset(target_bucket=10000, active_bucket=10000)
    service.finalize("2026-09-03")
    with pytest.raises(Exception) as exc:
        service.preflight()
    assert "IOC" in str(exc.value)


def test_rejected_exit_dry_run_blocks_the_entire_preflight(tmp_path: Path):
    artifact = _artifact()
    artifact["planItems"] = [{
        "priority": 1, "kind": "stock_exit", "symbol": "AAA", "side": "sell",
        "deltaShares": -10, "limitPrice": None, "timeInForce": "Day",
        "orderType": "Market",
    }]

    class RejectBroker(FakeBroker):
        def dry_run(self, request):
            return {"ok": False, "time_in_force": "Day", "errors": ["rejected"]}

    service = _service(tmp_path, artifact=artifact, broker=RejectBroker())
    service.capital_reset(target_bucket=10000, active_bucket=10000)
    service.finalize("2026-09-03")

    with pytest.raises(Exception, match="dry-run rejected"):
        service.preflight()
    assert service.ledger.current_cycle()["state"] == CycleState.PLAN_FINALIZED.value


def test_preflight_revalidates_the_immutable_plan_payload(tmp_path: Path):
    artifact = _artifact()
    artifact["planItems"][0]["timeInForce"] = "Day"
    service = _service(tmp_path, artifact=artifact)
    service.capital_reset(target_bucket=10000, active_bucket=10000)
    service.finalize("2026-09-03")

    with pytest.raises(Exception, match="Stock entries must be IOC"):
        service.preflight()


def test_residual_spy_waits_for_terminal_ioc_then_buys_whole_shares(tmp_path: Path):
    artifact = _artifact()
    artifact["planItems"].append({
        "priority": 2, "kind": "spy_residual", "symbol": "SPY", "side": "buy",
        "deltaShares": None, "limitPrice": None, "timeInForce": "DAY",
        "orderType": "Market", "modeledCostPerShare": 0.0, "formula": "floor(cash/spy)",
    })

    class MarkedBroker(FakeBroker):
        def snapshot(self):
            body = super().snapshot()
            body["spy_mark"] = 100.0
            body["cash"] = 250.0
            return body

    broker = MarkedBroker()
    service = _service(tmp_path, artifact=artifact, broker=broker)
    _arm(service)
    opened = service.execute()
    symbols = [req.symbol for req in broker.submitted]
    assert "AAA" in symbols
    assert "SPY" in symbols
    spy_order = next(req for req in broker.submitted if req.symbol == "SPY")
    assert spy_order.quantity == 2
    assert spy_order.side == "buy"
    assert opened["results"][-1]["state"] in {
        OrderIntentState.ACKNOWLEDGED.value, OrderIntentState.FILLED.value,
    }


def test_unfilled_ioc_is_final_and_never_chased(tmp_path: Path):
    class MissBroker(FakeBroker):
        def submit(self, request):
            self.submitted.append(request)
            return {
                "ok": True, "time_in_force": "IOC", "errors": [], "warnings": [],
                "broker_order_id": "1", "status": "Cancelled",
                "external_identifier": request.external_identifier,
            }

    service = _service(tmp_path, broker=MissBroker())
    _arm(service)
    result = service.execute()
    assert result["results"][0]["state"] == OrderIntentState.IOC_UNFILLED.value
    with pytest.raises(Exception):
        service.execute()


def test_reconciliation_flags_unknown_holdings(tmp_path: Path):
    class DriftBroker(FakeBroker):
        def snapshot(self):
            return {
                "is_closed": False, "is_frozen": False, "cash": 10000.0,
                "normalized_positions": [{"symbol": "ZZZ", "quantity": "5", "instrument_type": "Equity"}],
                "live_orders": (),
            }

    service = _service(tmp_path, broker=DriftBroker())
    service.capital_reset(target_bucket=10000, active_bucket=10000)
    service.finalize("2026-09-03")
    result = service.reconcile()
    assert result["ok"] is False
    assert "unknown_holding" in result["codes"]


def test_incomplete_marks_make_performance_unavailable(tmp_path: Path):
    artifact = _artifact()
    artifact["holdings"] = {"cash": 1000, "spy_shares": 3, "positions": []}
    artifact["spyClose"] = None
    service = _service(tmp_path, artifact=artifact)
    service.capital_reset(target_bucket=10000, active_bucket=10000)
    service.finalize("2026-09-03")
    series = service.performance_series()
    assert series["unavailableDates"]
    assert series["series"][0]["complete"] is False
    assert series["series"][0]["strategyEquity"] is None


def test_capital_reset_is_recorded_as_a_cash_flow_instruction(tmp_path: Path):
    service = _service(tmp_path)
    service.capital_reset(target_bucket=20000, active_bucket=10000)
    flows = service.ledger.cash_flows()
    assert flows[0]["kind"] == "capital_reset"
    assert "investment return" not in flows[0]["note"].lower() or "not an investment return" in flows[0]["note"].lower()


def test_setup_requirements_are_presence_only():
    secret = "sandbox-secret-must-not-leak"
    token = "sandbox-token-must-not-leak"
    settings = load_settings(environ={
        "SFP_STUDY4_EXECUTION_MODE": "sandbox",
        "SFP_STUDY4_ACCOUNT_FINGERPRINT": "abc123",
        "SFP_STUDY4_CONFIRMATION_SECRET": "confirm-secret-must-not-leak",
    })
    items = settings.setup_requirements(
        capital_ready=False,
        environ={
            "SFP_STUDY4_SANDBOX_TT_CLIENT_SECRET": secret,
            "SFP_STUDY4_SANDBOX_TT_REFRESH_TOKEN": token,
        },
    )
    blob = str(items)
    assert secret not in blob
    assert token not in blob
    assert "confirm-secret-must-not-leak" not in blob
    by_id = {item["id"]: item for item in items}
    assert by_id["execution_mode"]["ready"] is True
    assert by_id["execution_credentials"]["ready"] is True
    assert by_id["target_capital"]["ready"] is False
    assert by_id["confirmation_secret"]["ready"] is True


def test_reset_wipes_local_stats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    leftover = artifacts / "cycle.json"
    leftover.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("app.execution.service.artifacts_dir", lambda: artifacts)
    service = _service(tmp_path)
    service.capital_reset(target_bucket=10000, active_bucket=10000)
    service.finalize("2026-09-03")
    assert service.ledger.current_cycle() is not None
    with pytest.raises(ValueError, match="RESET"):
        service.reset_ledger("wipe")
    assert service.ledger.current_cycle() is not None
    result = service.reset_ledger("RESET")
    assert result["ok"] is True
    assert service.ledger.current_cycle() is None
    assert service.ledger.latest_capital() is None
    assert leftover.exists() is False


def test_http_reset_isolated_from_default_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ledger_path = tmp_path / "http-reset.sqlite"
    artifacts = tmp_path / "http-artifacts"
    artifacts.mkdir()
    leftover = artifacts / "cycle.json"
    leftover.write_text("{}", encoding="utf-8")
    ledger = ExecutionLedger(ledger_path)
    ledger.add_capital_version(
        target_bucket=10000,
        active_bucket=10000,
        production_cap=None,
        cash_flow_status="scheduled",
        approved_by="owner",
    )
    monkeypatch.setenv("SFP_STUDY4_LEDGER", str(ledger_path))
    monkeypatch.setenv("SFP_STUDY4_ARTIFACTS", str(artifacts))

    refused = client.post("/api/execution/study4/reset", json={"confirmation": "wipe"})
    assert refused.status_code == 400
    assert ledger.latest_capital() is not None
    assert leftover.exists() is True

    response = client.post("/api/execution/study4/reset", json={"confirmation": "RESET"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert ledger.latest_capital() is None
    assert leftover.exists() is False
