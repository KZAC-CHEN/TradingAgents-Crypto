from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from tradingagents.web.app import create_web_app
from tradingagents.web.artifacts import index_run_artifacts
from tradingagents.web.run_store import RunStore


def _prepare_run(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    root = tmp_path / "artifacts" / "run-1"
    (root / "reports" / "1_analysts").mkdir(parents=True)
    (root / "reports" / "complete_report.md").write_text(
        "# 完整报告\n\n<script>alert('x')</script>", encoding="utf-8"
    )
    (root / "reports" / "1_analysts" / "market.md").write_text(
        "# 市场报告", encoding="utf-8"
    )
    evidence = root / "BTCUSDT" / "2026-09-19" / "crypto_evidence"
    evidence.mkdir(parents=True)
    (evidence / "manifest.json").write_text(
        json.dumps(
            {
                "providers": ["binance_spot"],
                "warnings": ["binance_futures: HTTP 451"],
                "sections": {"market": {"status": "degraded"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "run_events.jsonl").write_text('{"event_type":"run.succeeded"}\n', encoding="utf-8")
    run = store.create_run(
        "run-1",
        request={"symbol": "BTC-USD"},
        config={},
        artifact_root=root,
        status="succeeded",
        stage="succeeded",
    )
    return store, run, root


def test_artifact_index_records_reports_evidence_logs_and_sha256(tmp_path):
    store, run, root = _prepare_run(tmp_path)

    artifacts = index_run_artifacts(store, run)

    kinds = {artifact["kind"] for artifact in artifacts}
    assert {"final_report", "report", "evidence_manifest", "run_log"} <= kinds
    complete = next(item for item in artifacts if item["kind"] == "final_report")
    expected = hashlib.sha256((root / "reports" / "complete_report.md").read_bytes()).hexdigest()
    assert complete["sha256"] == expected
    assert complete["relative_path"] == "reports/complete_report.md"


def test_artifact_api_reads_and_downloads_only_registered_files(tmp_path):
    store, run, _root = _prepare_run(tmp_path)
    artifacts = index_run_artifacts(store, run)
    complete = next(item for item in artifacts if item["kind"] == "final_report")
    app = create_web_app(
        env_path=tmp_path / ".env",
        database_path=tmp_path / "runs.db",
        allowed_hosts={"testserver"},
        start_run_manager=False,
    )

    with TestClient(app, base_url="http://testserver") as client:
        listing = client.get("/api/runs/run-1/artifacts")
        preview = client.get(f"/api/artifacts/{complete['artifact_id']}")
        download = client.get(f"/api/artifacts/{complete['artifact_id']}/download")
        missing = client.get("/api/artifacts/not-registered")

    assert listing.status_code == 200
    assert len(listing.json()["items"]) == len(artifacts)
    assert preview.status_code == 200
    assert "<script>" in preview.json()["content"]
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert missing.status_code == 404


def test_registered_path_cannot_escape_run_directory(tmp_path):
    store, _run, root = _prepare_run(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    artifact = store.register_artifact(
        "malicious-id",
        "run-1",
        kind="other",
        label="非法路径",
        relative_path="../../../outside.txt",
        media_type="text/plain",
        size_bytes=outside.stat().st_size,
        sha256=hashlib.sha256(outside.read_bytes()).hexdigest(),
    )
    assert root not in outside.parents
    app = create_web_app(
        env_path=tmp_path / ".env",
        database_path=tmp_path / "runs.db",
        allowed_hosts={"testserver"},
        start_run_manager=False,
    )

    with TestClient(app, base_url="http://testserver") as client:
        response = client.get(f"/api/artifacts/{artifact['artifact_id']}")

    assert response.status_code == 404
    assert "private" not in response.text
