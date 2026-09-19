"""FastAPI 本地服务，统一承载配置页和后续分析任务接口。"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from tradingagents.config_store import ConfigStore, ConfigValidationError
from tradingagents.runtime import AnalysisRequest, AnalysisRunner, sanitize_runtime_config

from .artifacts import index_run_artifacts, resolve_artifact_path
from .crypto_catalog import CryptoAssetCatalog
from .model_discovery import ModelDiscoveryService
from .run_manager import (
    TERMINAL_STATUSES,
    RunManager,
    build_runtime_config,
    preflight_analysis,
)
from .run_store import RunStore
from .server_control import registered_web_server

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_ARTIFACT_PREVIEW_BYTES = 5 * 1024 * 1024


class ConfigUpdateRequest(BaseModel):
    """配置中心允许的增量更新请求。"""

    updates: dict[str, str] = Field(default_factory=dict)
    deletes: list[str] = Field(default_factory=list)


class AnalysisRunRequest(BaseModel):
    """创建或预检一次分析所需的非敏感输入。"""

    symbol: str = Field(min_length=1, max_length=64)
    analysis_date: str
    analysts: list[str] = Field(default_factory=lambda: ["market", "social", "news", "fundamentals"])
    research_depth: int = Field(default=1, ge=1, le=10)
    checkpoint_enabled: bool = True
    llm_provider: str | None = Field(default=None, max_length=64)
    quick_model: str | None = Field(default=None, max_length=256)
    deep_model: str | None = Field(default=None, max_length=256)
    output_language: str | None = Field(default=None, max_length=64)
    backend_url: str | None = Field(default=None, max_length=2048)
    reasoning: dict[str, str | None] = Field(default_factory=dict)

    def to_runtime_request(self) -> AnalysisRequest:
        """转换为与 CLI 共用的不可变请求。"""
        return AnalysisRequest(
            symbol=self.symbol,
            analysis_date=self.analysis_date,
            analysts=tuple(self.analysts),
            research_depth=self.research_depth,
            checkpoint_enabled=self.checkpoint_enabled,
            llm_provider=self.llm_provider,
            quick_model=self.quick_model,
            deep_model=self.deep_model,
            output_language=self.output_language,
            backend_url=self.backend_url,
            reasoning=self.reasoning,
        )


class ModelDiscoveryRequest(BaseModel):
    """请求后端使用已保存凭证发现供应商模型。"""

    provider: str = Field(min_length=1, max_length=64)
    refresh: bool = False


def _default_database_path() -> Path:
    return Path.home() / ".tradingagents" / "web" / "runs.db"


def _default_artifacts_root(database_path: Path) -> Path:
    return database_path.parent / "runs"


def _frontend_root() -> Path:
    return Path(str(files("tradingagents.web_ui"))) / "dist"


def _config_payload(app: FastAPI) -> dict[str, Any]:
    payload = app.state.config_store.public_snapshot()
    payload["csrfToken"] = app.state.csrf_token
    payload["keylessSources"] = [
        "Binance 公告",
        "OKX 公告",
        "CoinDesk RSS",
        "SEC 公告",
        "CFTC 公告",
        "美联储公告",
    ]
    return payload


def _require_mutation_security(request: Request) -> None:
    """校验同源写请求和 CSRF 令牌。"""
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="请求来源不受信任。") from exc
        server_port = request.url.port or (443 if request.url.scheme == "https" else 80)
        if (
            parsed.scheme != request.url.scheme
            or parsed.hostname not in _LOOPBACK_HOSTS
            or parsed.port != server_port
        ):
            raise HTTPException(status_code=403, detail="请求来源不受信任。")
    supplied = request.headers.get("x-csrf-token", "")
    if not secrets.compare_digest(supplied, request.app.state.csrf_token):
        raise HTTPException(status_code=403, detail="安全令牌无效，请刷新页面。")


def create_web_app(
    *,
    env_path: str | Path | None = None,
    database_path: str | Path | None = None,
    csrf_token: str | None = None,
    frontend_root: str | Path | None = None,
    allowed_hosts: set[str] | None = None,
    artifacts_root: str | Path | None = None,
    runner_factory: Any = AnalysisRunner,
    start_run_manager: bool = True,
    crypto_catalog: CryptoAssetCatalog | None = None,
    server_instance_id: str | None = None,
) -> FastAPI:
    """创建只为本机单用户设计的 Web 应用。"""
    allowed = set(allowed_hosts or _LOOPBACK_HOSTS)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_runs = [
            run
            for run in app.state.run_store.list_runs(limit=200)
            if run["status"] in {"preflight", "evidence", "running", "cancel_requested"}
        ]
        app.state.run_store.mark_active_runs_interrupted()
        for run in active_runs:
            app.state.run_store.append_event(
                run["run_id"],
                "run.interrupted",
                {"error": "Web 服务在任务运行期间停止。"},
            )
        if app.state.start_run_manager:
            app.state.run_manager.start()
        try:
            yield
        finally:
            if app.state.start_run_manager:
                app.state.run_manager.stop()

    app = FastAPI(
        title="TradingAgents Web",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    resolved_database = Path(database_path or _default_database_path()).resolve()
    app.state.config_store = ConfigStore(env_path)
    app.state.config_store.activate_file_values()
    app.state.run_store = RunStore(resolved_database)
    app.state.csrf_token = csrf_token or secrets.token_urlsafe(32)
    app.state.frontend_root = Path(frontend_root or _frontend_root()).resolve()
    app.state.artifacts_root = Path(
        artifacts_root or _default_artifacts_root(resolved_database)
    ).resolve()
    app.state.model_discovery = ModelDiscoveryService(app.state.config_store)
    app.state.crypto_catalog = crypto_catalog or CryptoAssetCatalog()
    app.state.run_manager = RunManager(
        app.state.run_store,
        app.state.config_store,
        runner_factory=runner_factory,
        crypto_catalog=app.state.crypto_catalog,
    )
    app.state.start_run_manager = start_run_manager
    app.state.server_instance_id = server_instance_id or str(uuid4())

    @app.middleware("http")
    async def protect_local_service(request: Request, call_next):
        host = request.url.hostname
        if host not in allowed:
            return JSONResponse(status_code=403, content={"error": "请求主机不受信任。"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "tradingagents-web",
            "instanceId": app.state.server_instance_id,
        }

    @app.get("/api/config")
    async def get_config(request: Request) -> dict[str, Any]:
        return _config_payload(request.app)

    @app.put("/api/config")
    async def update_config(payload: ConfigUpdateRequest, request: Request):
        _require_mutation_security(request)
        try:
            content_length = int(request.headers.get("content-length", "0") or 0)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length 无效。") from exc
        if content_length > _MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="请求内容过大。")
        try:
            request.app.state.config_store.apply_changes(payload.updates, payload.deletes)
        except ConfigValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="无法写入配置文件。") from exc
        request.app.state.model_discovery.clear()
        result = _config_payload(request.app)
        result["message"] = "配置已安全保存。新启动的分析任务会读取这些设置。"
        return result

    @app.post("/api/models/discover")
    async def discover_models(payload: ModelDiscoveryRequest, request: Request):
        """使用服务端保存的凭证枚举当前账号可用模型。"""
        _require_mutation_security(request)
        try:
            return await asyncio.to_thread(
                request.app.state.model_discovery.discover,
                payload.provider,
                refresh=payload.refresh,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/instruments/crypto")
    async def discover_crypto_assets(refresh: bool = False):
        """返回可搜索的币安 USDT 现货币种目录。"""
        return await asyncio.to_thread(app.state.crypto_catalog.discover, refresh=refresh)

    def parse_analysis_request(payload: AnalysisRunRequest) -> AnalysisRequest:
        try:
            return payload.to_runtime_request()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def run_payload(run: dict[str, Any]) -> dict[str, Any]:
        result = dict(run)
        result["queue_position"] = app.state.run_store.queue_position(run["run_id"])
        return result

    @app.post("/api/preflight")
    async def preflight(payload: AnalysisRunRequest, request: Request):
        _require_mutation_security(request)
        runtime_request = parse_analysis_request(payload)
        config = build_runtime_config()
        return preflight_analysis(
            runtime_request,
            config,
            request.app.state.config_store,
            request.app.state.crypto_catalog,
        )

    @app.post("/api/runs", status_code=201)
    async def create_run(payload: AnalysisRunRequest, request: Request):
        _require_mutation_security(request)
        runtime_request = parse_analysis_request(payload)
        config = build_runtime_config()
        check = preflight_analysis(
            runtime_request,
            config,
            request.app.state.config_store,
            request.app.state.crypto_catalog,
        )
        if not check["ok"]:
            raise HTTPException(status_code=422, detail="；".join(check["errors"]))
        run_id = str(uuid4())
        artifact_root = request.app.state.artifacts_root / run_id
        run = request.app.state.run_store.create_run(
            run_id,
            request=runtime_request.to_dict(),
            config=sanitize_runtime_config(config),
            artifact_root=artifact_root,
        )
        request.app.state.run_store.append_event(
            run_id,
            "run.queued",
            {"status": "queued", "warnings": check["warnings"]},
        )
        request.app.state.run_manager.wake()
        return run_payload(run)

    @app.get("/api/runs")
    async def list_runs(limit: int = 50, offset: int = 0):
        return {
            "items": [
                run_payload(run)
                for run in app.state.run_store.list_runs(limit=limit, offset=offset)
            ]
        }

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        try:
            return run_payload(app.state.run_store.get_run(run_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在。") from exc

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request):
        _require_mutation_security(request)
        try:
            run = request.app.state.run_store.request_cancel(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在。") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        event_type = "run.cancelled" if run["status"] == "cancelled" else "run.cancel_requested"
        request.app.state.run_store.append_event(run_id, event_type, {"status": run["status"]})
        request.app.state.run_manager.wake()
        return run_payload(run)

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str, request: Request):
        _require_mutation_security(request)
        try:
            run = request.app.state.run_store.resume_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在。") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        request.app.state.run_store.append_event(
            run_id,
            "run.resumed",
            {"status": "queued", "attempt": run["attempt"]},
        )
        request.app.state.run_manager.wake()
        return run_payload(run)

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(
        run_id: str,
        request: Request,
        after: int = 0,
        follow: bool = True,
    ):
        try:
            app.state.run_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在。") from exc
        header_id = request.headers.get("last-event-id", "")
        try:
            cursor = max(after, int(header_id or 0))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Last-Event-ID 无效。") from exc

        async def generate():
            nonlocal cursor
            last_heartbeat = time.monotonic()
            while True:
                if await request.is_disconnected():
                    return
                events = app.state.run_store.list_events(run_id, after_event_id=cursor)
                for event in events:
                    cursor = int(event["event_id"])
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {data}\n\n"
                if not follow:
                    return
                run = app.state.run_store.get_run(run_id)
                if run["status"] in TERMINAL_STATUSES and not events:
                    return
                if time.monotonic() - last_heartbeat >= 15:
                    last_heartbeat = time.monotonic()
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.get("/api/runs/{run_id}/artifacts")
    async def list_artifacts(run_id: str):
        try:
            run = app.state.run_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在。") from exc
        artifacts = app.state.run_store.list_artifacts(run_id)
        if Path(run["artifact_root"]).is_dir():
            artifacts = index_run_artifacts(app.state.run_store, run)
        return {"items": artifacts}

    @app.get("/api/artifacts/{artifact_id}")
    async def get_artifact(artifact_id: str):
        try:
            artifact = app.state.run_store.get_artifact(artifact_id)
            path = resolve_artifact_path(app.state.run_store, artifact)
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="文件不存在或尚未登记。") from exc
        if path.stat().st_size > _MAX_ARTIFACT_PREVIEW_BYTES:
            raise HTTPException(status_code=413, detail="文件过大，请使用下载功能。")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="该文件不能作为文本预览。") from exc
        return {"artifact": artifact, "content": content}

    @app.get("/api/artifacts/{artifact_id}/download")
    async def download_artifact(artifact_id: str):
        try:
            artifact = app.state.run_store.get_artifact(artifact_id)
            path = resolve_artifact_path(app.state.run_store, artifact)
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="文件不存在或尚未登记。") from exc
        return FileResponse(
            path,
            media_type=str(artifact["media_type"]),
            filename=path.name,
        )

    @app.api_route("/{path:path}", methods=["GET"], include_in_schema=False)
    async def serve_frontend(path: str):
        root = app.state.frontend_root
        requested = path or "index.html"
        candidate = (root / requested).resolve()
        if root not in candidate.parents and candidate != root:
            raise HTTPException(status_code=404, detail="页面不存在。")
        if candidate.is_file():
            return FileResponse(candidate)
        index_path = root / "index.html"
        if index_path.is_file():
            return FileResponse(index_path)
        return Response("TradingAgents Web 前端尚未构建。", status_code=503)

    return app


def run_web_server(
    *,
    port: int = 8765,
    env_path: str | Path | None = None,
    database_path: str | Path | None = None,
    open_browser: bool = True,
    initial_path: str = "/",
    state_path: str | Path | None = None,
) -> None:
    """运行单 worker 本地服务，直到用户终止进程。"""
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1 到 65535 之间。")
    instance_id = str(uuid4())
    with registered_web_server(
        port=port,
        instance_id=instance_id,
        state_path=state_path,
    ):
        app = create_web_app(
            env_path=env_path,
            database_path=database_path,
            server_instance_id=instance_id,
        )
        url = f"http://127.0.0.1:{port}{initial_path}"
        print(f"TradingAgents Web 已启动：{url}")
        print(f"配置将写入：{app.state.config_store.env_path}")
        print("按 Ctrl+C 停止服务，或在另一终端运行 tradingagents web --stop。")
        if open_browser:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            workers=1,
            log_level="info",
            timeout_graceful_shutdown=5,
        )
