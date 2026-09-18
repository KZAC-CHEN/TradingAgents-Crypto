"""本地 Web 配置服务的接口与安全边界测试。"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dotenv import dotenv_values
from typer.testing import CliRunner

import cli.main as cli_main
from tradingagents.config_web import create_config_server


@contextmanager
def running_server(env_path):
    """在随机回环端口启动服务，并在测试结束后可靠关闭。"""
    server = create_config_server(port=0, env_path=env_path, csrf_token="test-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def fetch_json(url, *, method="GET", payload=None, headers=None):
    """发送 JSON 请求并返回状态码与响应体。"""
    request_headers = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_get_config_hides_secret_and_serves_frontend(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=never-return-this\n", encoding="utf-8")
    with running_server(env_path) as server:
        base_url = f"http://127.0.0.1:{server.server_port}"
        status, payload = fetch_json(f"{base_url}/api/config")
        with urlopen(f"{base_url}/", timeout=3) as response:
            html = response.read().decode("utf-8")
            content_security_policy = response.headers["Content-Security-Policy"]

    assert status == 200
    assert payload["csrfToken"] == "test-token"
    assert "never-return-this" not in repr(payload)
    assert "TradingAgents 配置中心" in html
    assert "default-src 'self'" in content_security_policy


def test_put_requires_csrf_token(tmp_path):
    env_path = tmp_path / ".env"
    with running_server(env_path) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/config"
        status, payload = fetch_json(
            url,
            method="PUT",
            payload={"updates": {"ROOTDATA_API_KEY": "secret"}},
        )

    assert status == 403
    assert "令牌" in payload["error"]
    assert not env_path.exists()


def test_put_rejects_cross_origin_request(tmp_path):
    env_path = tmp_path / ".env"
    with running_server(env_path) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/config"
        status, payload = fetch_json(
            url,
            method="PUT",
            payload={"updates": {"ROOTDATA_API_KEY": "secret"}},
            headers={"X-CSRF-Token": "test-token", "Origin": "https://attacker.example"},
        )

    assert status == 403
    assert "来源" in payload["error"]
    assert not env_path.exists()


def test_put_saves_and_clear_removes_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("ROOTDATA_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    with running_server(env_path) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/config"
        headers = {"X-CSRF-Token": "test-token"}
        save_status, save_payload = fetch_json(
            url,
            method="PUT",
            payload={"updates": {"ROOTDATA_API_KEY": "root-secret"}, "deletes": []},
            headers=headers,
        )
        clear_status, clear_payload = fetch_json(
            url,
            method="PUT",
            payload={"updates": {}, "deletes": ["ROOTDATA_API_KEY"]},
            headers=headers,
        )

    assert save_status == 200
    assert "root-secret" not in repr(save_payload)
    assert clear_status == 200
    assert dotenv_values(env_path).get("ROOTDATA_API_KEY") is None
    assert "root-secret" not in repr(clear_payload)


def test_configure_command_forwards_local_server_options(tmp_path, monkeypatch):
    """CLI 子命令应把端口、文件与浏览器选项传给本地服务。"""
    captured = {}

    def fake_run_config_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("tradingagents.config_web.run_config_server", fake_run_config_server)
    env_path = tmp_path / ".env"

    result = CliRunner().invoke(
        cli_main.app,
        ["configure", "--port", "9012", "--no-browser", "--env-file", str(env_path)],
    )

    assert result.exit_code == 0
    assert captured == {
        "port": 9012,
        "env_path": str(env_path),
        "open_browser": False,
    }
