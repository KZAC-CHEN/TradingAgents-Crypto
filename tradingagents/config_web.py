"""仅监听本机的 TradingAgents Web 配置服务。"""

from __future__ import annotations

import json
import secrets
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tradingagents.config_store import ConfigStore, ConfigValidationError

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_MAX_REQUEST_BYTES = 64 * 1024
_ASSET_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


class ConfigHTTPServer(ThreadingHTTPServer):
    """保存配置存储与 CSRF 令牌的本地 HTTP 服务。"""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        store: ConfigStore,
        csrf_token: str | None = None,
    ):
        super().__init__(server_address, ConfigRequestHandler)
        self.store = store
        self.csrf_token = csrf_token or secrets.token_urlsafe(32)


class ConfigRequestHandler(BaseHTTPRequestHandler):
    """提供固定静态资源与受保护的配置接口。"""

    server: ConfigHTTPServer
    server_version = "TradingAgentsConfig/1.0"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        """只记录请求概要，避免把请求体或密钥写入日志。"""
        print(f"[配置中心] {self.address_string()} - {format % args}")

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def do_GET(self) -> None:
        if not self._host_is_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "请求主机不受信任。"})
            return
        path = urlsplit(self.path).path
        if path == "/api/config":
            payload = self.server.store.public_snapshot()
            payload["csrfToken"] = self.server.csrf_token
            payload["keylessSources"] = [
                "Binance 公告",
                "OKX 公告",
                "CoinDesk RSS",
                "SEC 公告",
                "CFTC 公告",
                "美联储公告",
            ]
            self._send_json(HTTPStatus.OK, payload)
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        asset_name = {
            "/": "index.html",
            "/assets/app.js": "app.js",
            "/assets/styles.css": "styles.css",
        }.get(path)
        if asset_name is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "页面不存在。"})
            return
        self._send_asset(asset_name)

    def do_PUT(self) -> None:
        if not self._host_is_allowed() or not self._origin_is_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "请求来源不受信任。"})
            return
        if urlsplit(self.path).path != "/api/config":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})
            return
        if not secrets.compare_digest(
            self.headers.get("X-CSRF-Token", ""), self.server.csrf_token
        ):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "安全令牌无效，请刷新页面。"})
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "请求必须使用 JSON。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > _MAX_REQUEST_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "请求内容过大。"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ConfigValidationError("请求内容必须是对象。")
            snapshot = self.server.store.apply_changes(
                payload.get("updates", {}), payload.get("deletes", [])
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "JSON 格式无效。"})
            return
        except ConfigValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except OSError:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "无法写入配置文件。"})
            return
        snapshot["csrfToken"] = self.server.csrf_token
        snapshot["message"] = "配置已安全保存。新启动的分析任务会读取这些设置。"
        snapshot["keylessSources"] = [
            "Binance 公告",
            "OKX 公告",
            "CoinDesk RSS",
            "SEC 公告",
            "CFTC 公告",
            "美联储公告",
        ]
        self._send_json(HTTPStatus.OK, snapshot)

    def do_POST(self) -> None:
        self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "不支持该请求方法。"})

    def _host_is_allowed(self) -> bool:
        host_header = self.headers.get("Host", "")
        try:
            hostname = urlsplit(f"//{host_header}").hostname
        except ValueError:
            return False
        return hostname in _LOOPBACK_HOSTS

    def _origin_is_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        return (
            parsed.scheme == "http"
            and parsed.hostname in _LOOPBACK_HOSTS
            and parsed.port == self.server.server_port
        )

    def _send_asset(self, name: str) -> None:
        try:
            content = files("tradingagents.config_ui").joinpath(name).read_bytes()
        except (FileNotFoundError, ModuleNotFoundError):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "静态资源不存在。"})
            return
        content_type = _ASSET_TYPES.get(Path(name).suffix, "application/octet-stream")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_config_server(
    port: int = 8765,
    env_path: str | Path | None = None,
    csrf_token: str | None = None,
) -> ConfigHTTPServer:
    """创建只绑定到 IPv4 回环地址的配置服务。"""
    if not 0 <= port <= 65535:
        raise ValueError("端口必须在 0 到 65535 之间。")
    return ConfigHTTPServer(
        ("127.0.0.1", port), ConfigStore(env_path), csrf_token=csrf_token
    )


def run_config_server(
    port: int = 8765,
    env_path: str | Path | None = None,
    open_browser: bool = True,
) -> None:
    """运行配置服务，直到用户按下 Ctrl+C。"""
    server = create_config_server(port=port, env_path=env_path)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"TradingAgents 配置中心已启动：{url}")
    print(f"配置将写入：{server.store.env_path}")
    print("按 Ctrl+C 停止服务。")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n配置中心已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_config_server()
