from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tradingagents.config_store import ConfigStore
from tradingagents.runtime import AnalysisCancelled, AnalysisEvent, AnalysisRequest
from tradingagents.web.app import create_web_app
from tradingagents.web.run_manager import RunManager
from tradingagents.web.run_store import RunStore


def _request(symbol: str = "BTC-USD") -> AnalysisRequest:
    return AnalysisRequest(
        symbol=symbol,
        analysis_date=date.today().isoformat(),
        analysts=("market",),
        checkpoint_enabled=True,
        llm_provider="ollama",
        quick_model="local-fast",
        deep_model="local-deep",
    )


def _create_run(store: RunStore, tmp_path: Path, run_id: str, symbol: str = "BTC-USD"):
    request = _request(symbol)
    return store.create_run(
        run_id,
        request=request.to_dict(),
        config={
            "llm_provider": "ollama",
            "quick_think_llm": "local-fast",
            "deep_think_llm": "local-deep",
        },
        artifact_root=tmp_path / "artifacts" / run_id,
    )


def _event(run_id: str, event_type: str, payload: dict) -> AnalysisEvent:
    return AnalysisEvent(
        run_id=run_id,
        event_type=event_type,
        payload=payload,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _wait_for(store: RunStore, run_id: str, statuses: set[str], timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = store.get_run(run_id)
        if run["status"] in statuses:
            return run
        time.sleep(0.01)
    raise AssertionError(f"任务 {run_id} 未进入状态 {statuses}：{store.get_run(run_id)}")


class _SerialFactory:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0
        self.order: list[str] = []

    def __call__(self, config):
        owner = self

        class Runner:
            def run(self, request, *, artifact_root, run_id, event_sink, cancel_check):
                with owner.lock:
                    owner.active += 1
                    owner.maximum_active = max(owner.maximum_active, owner.active)
                    owner.order.append(f"start:{run_id}")
                event_sink(_event(run_id, "run.preflight", {"status": "ok"}))
                event_sink(_event(run_id, "evidence.started", {}))
                event_sink(_event(run_id, "run.started", {}))
                time.sleep(0.04)
                event_sink(_event(run_id, "progress.updated", {"completed_agents": 1}))
                root = Path(artifact_root)
                root.mkdir(parents=True, exist_ok=True)
                report = root / "report.md"
                report.write_text("ok", encoding="utf-8")
                with owner.lock:
                    owner.order.append(f"end:{run_id}")
                    owner.active -= 1
                return SimpleNamespace(signal="BUY", report_path=report)

        return Runner()


def test_run_manager_executes_two_runs_strictly_in_order(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    _create_run(store, tmp_path, "run-1")
    _create_run(store, tmp_path, "run-2")
    factory = _SerialFactory()
    manager = RunManager(
        store,
        ConfigStore(tmp_path / ".env"),
        runner_factory=factory,
        poll_interval=0.01,
    )

    manager.start()
    try:
        _wait_for(store, "run-1", {"succeeded"})
        _wait_for(store, "run-2", {"succeeded"})
    finally:
        manager.stop()

    assert factory.maximum_active == 1
    assert factory.order == ["start:run-1", "end:run-1", "start:run-2", "end:run-2"]
    assert store.get_run("run-1")["checkpoint_available"] is False


class _ResumeFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, config):
        owner = self

        class Runner:
            def run(self, request, *, artifact_root, run_id, event_sink, cancel_check):
                owner.calls += 1
                event_sink(_event(run_id, "run.started", {}))
                event_sink(_event(run_id, "progress.updated", {"completed_agents": 1}))
                if owner.calls == 1:
                    raise RuntimeError("模拟节点失败")
                root = Path(artifact_root)
                root.mkdir(parents=True, exist_ok=True)
                report = root / "report.md"
                report.write_text("resumed", encoding="utf-8")
                return SimpleNamespace(signal="HOLD", report_path=report)

        return Runner()


class _DegradedFactory:
    def __call__(self, config):
        class Runner:
            def run(self, request, *, artifact_root, run_id, event_sink, cancel_check):
                root = Path(artifact_root)
                root.mkdir(parents=True, exist_ok=True)
                report = root / "report.md"
                report.write_text("degraded", encoding="utf-8")
                return SimpleNamespace(
                    signal="HOLD",
                    report_path=report,
                    evidence_health={
                        "state": "degraded",
                        "sections": {"market": "error", "news": "ok"},
                        "failed_sections": ["market"],
                        "provider_issues": [],
                        "warnings": ["市场快照不可用"],
                    },
                )

        return Runner()


def test_evidence_failure_marks_completed_run_degraded(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    _create_run(store, tmp_path, "run-1")
    manager = RunManager(
        store,
        ConfigStore(tmp_path / ".env"),
        runner_factory=_DegradedFactory(),
        poll_interval=0.01,
    )

    manager.start()
    try:
        completed = _wait_for(store, "run-1", {"degraded"})
    finally:
        manager.stop()

    assert completed["signal"] == "HOLD"
    assert completed["evidence_health"]["failed_sections"] == ["market"]
    assert store.list_events("run-1")[-1]["event_type"] == "run.degraded"


def test_failed_run_can_resume_with_checkpoint(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    _create_run(store, tmp_path, "run-1")
    factory = _ResumeFactory()
    manager = RunManager(
        store,
        ConfigStore(tmp_path / ".env"),
        runner_factory=factory,
        poll_interval=0.01,
    )

    manager.start()
    try:
        failed = _wait_for(store, "run-1", {"failed"})
        assert failed["checkpoint_available"] is True
        resumed = store.resume_run("run-1")
        assert resumed["attempt"] == 2
        manager.wake()
        completed = _wait_for(store, "run-1", {"succeeded"})
    finally:
        manager.stop()

    assert completed["attempt"] == 2
    assert factory.calls == 2


class _InterruptFactory:
    def __call__(self, config):
        class Runner:
            def run(self, request, *, artifact_root, run_id, event_sink, cancel_check):
                event_sink(_event(run_id, "run.started", {}))
                event_sink(_event(run_id, "progress.updated", {"completed_agents": 1}))
                while not cancel_check():
                    time.sleep(0.01)
                raise AnalysisCancelled("停止")

        return Runner()


def test_manager_shutdown_marks_running_task_interrupted(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    _create_run(store, tmp_path, "run-1")
    manager = RunManager(
        store,
        ConfigStore(tmp_path / ".env"),
        runner_factory=_InterruptFactory(),
        poll_interval=0.01,
    )

    manager.start()
    _wait_for(store, "run-1", {"running"})
    manager.stop()

    run = store.get_run("run-1")
    assert run["status"] == "interrupted"
    assert run["checkpoint_available"] is True


def test_queued_cancel_and_sse_resume_from_last_event_id(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_web_app(
        env_path=tmp_path / ".env",
        database_path=tmp_path / "runs.db",
        artifacts_root=tmp_path / "artifacts",
        csrf_token="test-token",
        allowed_hosts={"testserver"},
        start_run_manager=False,
    )
    payload = {
        "symbol": "BTC-USD",
        "analysis_date": date.today().isoformat(),
        "analysts": ["market"],
        "llm_provider": "ollama",
        "quick_model": "local-fast",
        "deep_model": "local-deep",
    }
    headers = {"X-CSRF-Token": "test-token"}
    with TestClient(app, base_url="http://testserver") as client:
        preflight = client.post("/api/preflight", json=payload, headers=headers)
        created = client.post("/api/runs", json=payload, headers=headers)
        run_id = created.json()["run_id"]
        first_event = app.state.run_store.list_events(run_id)[0]
        second_event = app.state.run_store.append_event(run_id, "progress.updated", {"step": 1})
        replay = client.get(
            f"/api/runs/{run_id}/events?follow=false",
            headers={"Last-Event-ID": str(first_event["event_id"])},
        )
        cancelled = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
        listed = client.get("/api/runs")

    assert preflight.status_code == 200
    assert preflight.json()["ok"] is True
    assert preflight.json()["warnings"]
    assert created.status_code == 201
    assert replay.status_code == 200
    assert f"id: {second_event['event_id']}" in replay.text
    assert "event: progress.updated" in replay.text
    assert f"id: {first_event['event_id']}" not in replay.text
    assert cancelled.json()["status"] == "cancelled"
    assert listed.json()["items"][0]["run_id"] == run_id
