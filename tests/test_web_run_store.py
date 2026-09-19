"""Web 任务 SQLite 存储的持久化与恢复测试。"""

from __future__ import annotations

import pytest

from tradingagents.web.run_store import RunStore


@pytest.mark.unit
def test_run_store_round_trips_runs_and_events(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    created = store.create_run(
        "run-1",
        request={"symbol": "BTC-USD"},
        config={"llm_provider": "openai"},
        artifact_root=tmp_path / "artifacts" / "run-1",
    )
    event = store.append_event("run-1", "run.created", {"status": "queued"})

    assert created["status"] == "queued"
    assert created["request"] == {"symbol": "BTC-USD"}
    assert created["config"] == {"llm_provider": "openai"}
    assert created["signal"] is None
    assert created["evidence_health"] == {}
    assert event["event_id"] == 1
    assert store.list_events("run-1")[0]["payload"] == {"status": "queued"}
    assert store.list_runs()[0]["run_id"] == "run-1"


@pytest.mark.unit
def test_active_runs_are_marked_interrupted_after_restart(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    for run_id, status in (
        ("queued", "queued"),
        ("running", "running"),
        ("done", "succeeded"),
    ):
        store.create_run(
            run_id,
            request={},
            config={},
            artifact_root=tmp_path / run_id,
            status=status,
            stage=status,
        )

    assert store.mark_active_runs_interrupted() == 1
    assert store.get_run("queued")["status"] == "queued"
    assert store.get_run("running")["status"] == "interrupted"
    assert store.get_run("done")["status"] == "succeeded"


@pytest.mark.unit
def test_run_store_rejects_unknown_updates(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    store.create_run("run-1", request={}, config={}, artifact_root=tmp_path / "run-1")

    with pytest.raises(ValueError, match="不允许更新"):
        store.update_run("run-1", request_json="unsafe")


@pytest.mark.unit
def test_run_store_persists_signal_and_evidence_health(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    store.create_run("run-1", request={}, config={}, artifact_root=tmp_path / "run-1")

    updated = store.update_run(
        "run-1",
        signal="HOLD",
        evidence_health={
            "state": "degraded",
            "sections": {"market": "error"},
            "failed_sections": ["market"],
            "provider_issues": [],
            "warnings": ["市场不可用"],
        },
    )

    assert updated["signal"] == "HOLD"
    assert updated["evidence_health"]["failed_sections"] == ["market"]
