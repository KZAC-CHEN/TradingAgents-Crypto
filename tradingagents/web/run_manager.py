"""Web 分析的持久化单任务队列和运行状态协调器。"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingagents.config_store import FIELD_BY_NAME, ConfigStore
from tradingagents.default_config import DEFAULT_CONFIG, _apply_env_overrides
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.runtime import (
    AnalysisCancelled,
    AnalysisEvent,
    AnalysisRequest,
    AnalysisRunner,
)

from .artifacts import index_run_artifacts
from .redaction import redact_value
from .run_store import RunStore

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"succeeded", "cancelled", "failed", "interrupted"}
RunnerFactory = Callable[[dict[str, Any]], AnalysisRunner]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_runtime_config() -> dict[str, Any]:
    """按当前环境变量重新生成任务配置副本。"""
    return _apply_env_overrides(deepcopy(DEFAULT_CONFIG))


def preflight_analysis(
    request: AnalysisRequest,
    config: dict[str, Any],
    config_store: ConfigStore,
) -> dict[str, Any]:
    """检查必需模型配置，并列出可选数据源降级告警。"""
    provider = str(request.llm_provider or config.get("llm_provider") or "").lower()
    quick_model = str(request.quick_model or config.get("quick_think_llm") or "").strip()
    deep_model = str(request.deep_model or config.get("deep_think_llm") or "").strip()
    errors: list[str] = []
    warnings: list[str] = []
    if not provider:
        errors.append("尚未配置大模型供应商。")
    if not quick_model:
        errors.append("尚未配置快速模型。")
    if not deep_model:
        errors.append("尚未配置深度模型。")
    key_name = get_api_key_env(provider) if provider else None
    if key_name and provider != "openai_compatible" and not config_store.get_value(key_name):
        errors.append(f"{provider} 缺少必需凭证 {key_name}。")

    symbol = request.symbol.upper()
    is_crypto = symbol.endswith(("-USD", "-USDT", "/USDT"))
    if is_crypto:
        optional_sources = (
            ("AICOIN_ACCESS_KEY_ID", "AiCoin 未配置，将缺少中文新闻与 X 代理信息。"),
            ("COINDESK_API_KEY", "CoinDesk API 未配置，将使用许可允许的 RSS。"),
            ("ROOTDATA_API_KEY", "RootData API 未配置，将使用公开网站与项目官方来源。"),
            ("X_BEARER_TOKEN", "X API 未配置，原生 X 数据源不会启用。"),
            ("JIN10_API_KEY", "金十未配置，将使用监管机构与央行公告。"),
        )
        for name, warning in optional_sources:
            if not config_store.get_value(name):
                warnings.append(warning)
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "resolved": {
            "llm_provider": provider,
            "quick_model": quick_model,
            "deep_model": deep_model,
            "checkpoint_enabled": request.checkpoint_enabled,
            "asset_type": "crypto" if is_crypto else "stock",
        },
    }


class RunManager:
    """用一个后台线程严格串行执行数据库中的排队任务。"""

    def __init__(
        self,
        store: RunStore,
        config_store: ConfigStore,
        *,
        runner_factory: RunnerFactory = AnalysisRunner,
        poll_interval: float = 0.1,
    ) -> None:
        self.store = store
        self.config_store = config_store
        self.runner_factory = runner_factory
        self.poll_interval = max(0.01, float(poll_interval))
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动唯一工作线程；重复调用不会创建第二个线程。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="tradingagents-web-runner",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """通知工作线程在当前节点边界停止。"""
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))

    def wake(self) -> None:
        """通知工作线程立即检查新任务。"""
        self._wake_event.set()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            run = self.store.claim_next_queued()
            if run is None:
                self._wake_event.wait(self.poll_interval)
                self._wake_event.clear()
                continue
            self._execute(run)

    def _execute(self, run: dict[str, Any]) -> None:
        run_id = str(run["run_id"])
        request = AnalysisRequest(**run["request"])
        config = dict(run["config"])
        secrets = self._secret_values()
        self._append_event(run_id, "run.claimed", {"status": "preflight"}, secrets)
        check = preflight_analysis(request, config, self.config_store)
        self._append_event(run_id, "run.preflight_result", check, secrets)
        if not check["ok"]:
            message = "；".join(check["errors"])
            self.store.update_run(
                run_id,
                status="failed",
                stage="failed",
                error=message,
                finished_at=_utc_now(),
            )
            self._append_event(run_id, "run.failed", {"error": message}, secrets)
            self._index_artifacts(run_id)
            return

        runner = self.runner_factory(config)

        def handle_event(event: AnalysisEvent) -> None:
            payload = redact_value(event.payload, secrets=secrets)
            self._sync_stage(run_id, event.event_type, request.checkpoint_enabled)
            self._append_event(run_id, event.event_type, payload, secrets)

        def should_cancel() -> bool:
            if self._stop_event.is_set():
                return True
            return self.store.get_run(run_id)["status"] == "cancel_requested"

        try:
            result = runner.run(
                request,
                artifact_root=run["artifact_root"],
                run_id=run_id,
                event_sink=handle_event,
                cancel_check=should_cancel,
            )
        except AnalysisCancelled:
            interrupted = self._stop_event.is_set()
            status = "interrupted" if interrupted else "cancelled"
            error = "Web 服务停止，任务已中断。" if interrupted else None
            self.store.update_run(
                run_id,
                status=status,
                stage=status,
                error=error,
                finished_at=_utc_now(),
            )
            self._append_event(run_id, f"run.{status}", {"status": status}, secrets)
            self._index_artifacts(run_id)
        except Exception as exc:
            logger.exception("Web 分析任务 %s 执行失败", run_id)
            message = str(redact_value(str(exc), secrets=secrets))
            checkpoint = self.store.get_run(run_id)["checkpoint_available"]
            self.store.update_run(
                run_id,
                status="failed",
                stage="failed",
                checkpoint_available=checkpoint,
                error=message,
                finished_at=_utc_now(),
            )
            self._append_event(run_id, "run.failed", {"error": message}, secrets)
            self._index_artifacts(run_id)
        else:
            self.store.update_run(
                run_id,
                status="succeeded",
                stage="succeeded",
                checkpoint_available=False,
                error=None,
                finished_at=_utc_now(),
            )
            self._append_event(
                run_id,
                "run.succeeded",
                {"signal": result.signal, "report_path": str(result.report_path)},
                secrets,
            )
            self._index_artifacts(run_id)

    def _sync_stage(self, run_id: str, event_type: str, checkpoint_enabled: bool) -> None:
        stage_by_event = {
            "run.preflight": "preflight",
            "evidence.started": "evidence",
            "evidence.completed": "evidence",
            "run.started": "running",
            "progress.updated": "running",
        }
        stage = stage_by_event.get(event_type)
        changes: dict[str, Any] = {}
        if stage:
            current = self.store.get_run(run_id)
            changes["stage"] = stage
            if current["status"] != "cancel_requested":
                changes["status"] = stage
        if event_type == "progress.updated" and checkpoint_enabled:
            changes["checkpoint_available"] = True
        if changes:
            self.store.update_run(run_id, **changes)

    def _secret_values(self) -> tuple[str, ...]:
        values = []
        for name, field in FIELD_BY_NAME.items():
            if not field.secret:
                continue
            value = self.config_store.get_value(name)
            if value:
                values.append(value)
        return tuple(values)

    def _append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        secrets: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        cleaned = redact_value(payload, secrets=secrets)
        event = self.store.append_event(run_id, event_type, cleaned)
        run = self.store.get_run(run_id)
        root = Path(run["artifact_root"]).resolve()
        root.mkdir(parents=True, exist_ok=True)
        with (root / "run_events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
        return event

    def _index_artifacts(self, run_id: str) -> None:
        """索引任务产物；索引失败不会覆盖原始任务结果。"""
        try:
            index_run_artifacts(self.store, self.store.get_run(run_id))
        except Exception:
            logger.exception("Web 分析任务 %s 的 artifact 索引失败", run_id)
