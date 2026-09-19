"""FastAPI 本地服务，统一承载配置页和后续分析任务接口。"""

from __future__ import annotations

import secrets
import threading
import webbrowser
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from tradingagents.config_store import ConfigStore, ConfigValidationError

from .run_store import RunStore

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_MAX_REQUEST_BYTES = 64 * 1024


class ConfigUpdateRequest(BaseModel):
    """配置中心允许的增量更新请求。"""

    updates: dict[str, str] = Field(default_factory=dict)
    deletes: list[str] = Field(default_factory=list)


def _default_database_path() -> Path:
    return Path.home() / ".tradingagents" / "web" / "runs.db"


def _frontend_root() -> Path:
    resource = files("tradingagents.config_ui")
    return Path(str(resource))


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
) -> FastAPI:
    """创建只为本机单用户设计的 Web 应用。"""
    allowed = set(allowed_hosts or _LOOPBACK_HOSTS)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.run_store.mark_active_runs_interrupted()
        yield

    app = FastAPI(
        title="TradingAgents Web",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.config_store = ConfigStore(env_path)
    app.state.run_store = RunStore(database_path or _default_database_path())
    app.state.csrf_token = csrf_token or secrets.token_urlsafe(32)
    app.state.frontend_root = Path(frontend_root or _frontend_root()).resolve()

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
        return {"status": "ok"}

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
        result = _config_payload(request.app)
        result["message"] = "配置已安全保存。新启动的分析任务会读取这些设置。"
        return result

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
) -> None:
    """运行单 worker 本地服务，直到用户终止进程。"""
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1 到 65535 之间。")
    app = create_web_app(env_path=env_path, database_path=database_path)
    url = f"http://127.0.0.1:{port}{initial_path}"
    print(f"TradingAgents Web 已启动：{url}")
    print(f"配置将写入：{app.state.config_store.env_path}")
    print("按 Ctrl+C 停止服务。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, workers=1, log_level="info")
