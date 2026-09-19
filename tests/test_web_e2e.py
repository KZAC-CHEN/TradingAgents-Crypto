from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from tradingagents.runtime import AnalysisEvent, AnalysisResult
from tradingagents.web.app import create_web_app


def _event(run_id: str, event_type: str, payload: dict) -> AnalysisEvent:
    return AnalysisEvent(
        run_id=run_id,
        event_type=event_type,
        payload=payload,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


class _EndToEndRunnerFactory:
    def __call__(self, config):
        class Runner:
            def run(self, request, *, artifact_root, run_id, event_sink, cancel_check):
                root = Path(artifact_root)
                reports = root / "reports"
                evidence = root / request.symbol.replace("-", "") / request.analysis_date / "crypto_evidence"
                reports.mkdir(parents=True, exist_ok=True)
                evidence.mkdir(parents=True, exist_ok=True)

                event_sink(_event(run_id, "run.preflight", {"status": "ok"}))
                event_sink(_event(run_id, "evidence.started", {"symbol": request.symbol}))
                (evidence / "manifest.json").write_text(
                    json.dumps(
                        {
                            "providers": ["binance_spot", "binance_futures"],
                            "warnings": [],
                            "sections": {"market": {"status": "complete"}},
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                event_sink(_event(run_id, "evidence.completed", {"warnings": []}))
                event_sink(_event(run_id, "run.started", {"total_agents": 1}))
                event_sink(
                    _event(
                        run_id,
                        "llm.completed",
                        {"input_tokens": 120, "output_tokens": 36, "duration_seconds": 0.1},
                    )
                )
                event_sink(
                    _event(
                        run_id,
                        "tool.completed",
                        {"tool": "get_crypto_market_report", "duration_seconds": 0.1},
                    )
                )
                event_sink(_event(run_id, "progress.updated", {"completed_agents": 1}))
                report = reports / "complete_report.md"
                report.write_text("# BTC-USD 完整分析\n\n最终信号：HOLD", encoding="utf-8")
                return AnalysisResult(
                    run_id=run_id,
                    final_state={"final_trade_decision": "HOLD"},
                    signal="HOLD",
                    report_path=report,
                    artifact_root=root,
                    stats={"llm_calls": 1, "tool_calls": 1},
                )

        return Runner()


def _wait_for_completion(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        run = response.json()
        if run["status"] == "succeeded":
            return run
        time.sleep(0.01)
    raise AssertionError("模拟 Web 分析未在限定时间内完成。")


def test_web_analysis_survives_restart_with_events_and_artifacts(tmp_path):
    database = tmp_path / "runs.db"
    artifacts_root = tmp_path / "runs"
    env_path = tmp_path / ".env"
    headers = {"X-CSRF-Token": "test-token"}
    payload = {
        "symbol": "BTC-USD",
        "analysis_date": date.today().isoformat(),
        "analysts": ["market"],
        "llm_provider": "ollama",
        "quick_model": "local-fast",
        "deep_model": "local-deep",
    }
    app = create_web_app(
        env_path=env_path,
        database_path=database,
        artifacts_root=artifacts_root,
        csrf_token="test-token",
        allowed_hosts={"testserver"},
        runner_factory=_EndToEndRunnerFactory(),
    )

    with TestClient(app, base_url="http://testserver") as client:
        created = client.post("/api/runs", json=payload, headers=headers)
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        completed = _wait_for_completion(client, run_id)
        events = client.get(f"/api/runs/{run_id}/events?follow=false")
        artifacts = client.get(f"/api/runs/{run_id}/artifacts")

        assert completed["stage"] == "succeeded"
        assert "event: llm.completed" in events.text
        assert "event: tool.completed" in events.text
        assert "event: run.succeeded" in events.text
        items = artifacts.json()["items"]
        assert {item["kind"] for item in items} >= {
            "final_report",
            "evidence_manifest",
            "run_log",
        }
        report = next(item for item in items if item["kind"] == "final_report")
        preview = client.get(f"/api/artifacts/{report['artifact_id']}")
        assert "最终信号：HOLD" in preview.json()["content"]

    restarted = create_web_app(
        env_path=env_path,
        database_path=database,
        artifacts_root=artifacts_root,
        csrf_token="test-token",
        allowed_hosts={"testserver"},
        start_run_manager=False,
    )
    with TestClient(restarted, base_url="http://testserver") as client:
        restored = client.get(f"/api/runs/{run_id}")
        history = client.get("/api/runs")
        artifacts = client.get(f"/api/runs/{run_id}/artifacts")

    assert restored.json()["status"] == "succeeded"
    assert history.json()["items"][0]["run_id"] == run_id
    assert len(artifacts.json()["items"]) >= 3
