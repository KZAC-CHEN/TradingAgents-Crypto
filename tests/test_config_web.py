"""本地 Web 服务的配置接口、安全边界与命令入口测试。"""

from __future__ import annotations

from dotenv import dotenv_values
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import cli.main as cli_main
from tradingagents.web.app import create_web_app


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ROOTDATA_API_KEY", raising=False)
    app = create_web_app(
        env_path=tmp_path / ".env",
        database_path=tmp_path / "runs.db",
        csrf_token="test-token",
        allowed_hosts={"testserver"},
    )
    return TestClient(app, base_url="http://testserver")


def test_get_config_hides_secret_and_serves_spa_routes(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=never-return-this\n", encoding="utf-8")
    app = create_web_app(
        env_path=env_path,
        database_path=tmp_path / "runs.db",
        csrf_token="test-token",
        allowed_hosts={"testserver"},
    )
    with TestClient(app, base_url="http://testserver") as client:
        response = client.get("/api/config")
        page = client.get("/settings")

    assert response.status_code == 200
    assert response.json()["csrfToken"] == "test-token"
    assert "never-return-this" not in response.text
    assert page.status_code == 200
    assert "TradingAgents 分析中心" in page.text
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]


def test_put_requires_csrf_token(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        response = client.put(
            "/api/config",
            json={"updates": {"ROOTDATA_API_KEY": "secret"}},
        )

    assert response.status_code == 403
    assert "令牌" in response.json()["error"]
    assert not (tmp_path / ".env").exists()


def test_put_rejects_cross_origin_request(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        response = client.put(
            "/api/config",
            json={"updates": {"ROOTDATA_API_KEY": "secret"}},
            headers={
                "X-CSRF-Token": "test-token",
                "Origin": "https://attacker.example",
            },
        )

    assert response.status_code == 403
    assert "来源" in response.json()["error"]
    assert not (tmp_path / ".env").exists()


def test_put_saves_and_clear_removes_configuration(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    headers = {"X-CSRF-Token": "test-token"}
    with _client(tmp_path, monkeypatch) as client:
        saved = client.put(
            "/api/config",
            json={"updates": {"ROOTDATA_API_KEY": "root-secret"}, "deletes": []},
            headers=headers,
        )
        cleared = client.put(
            "/api/config",
            json={"updates": {}, "deletes": ["ROOTDATA_API_KEY"]},
            headers=headers,
        )

    assert saved.status_code == 200
    assert "root-secret" not in saved.text
    assert cleared.status_code == 200
    assert dotenv_values(env_path).get("ROOTDATA_API_KEY") is None
    assert "root-secret" not in cleared.text


def test_untrusted_host_is_rejected(tmp_path, monkeypatch):
    app = create_web_app(
        env_path=tmp_path / ".env",
        database_path=tmp_path / "runs.db",
        allowed_hosts={"localhost"},
    )
    with TestClient(app, base_url="http://attacker.example") as client:
        response = client.get("/api/config")

    assert response.status_code == 403
    assert "主机" in response.json()["error"]


def test_configure_command_forwards_settings_page_options(tmp_path, monkeypatch):
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


def test_web_command_forwards_server_options(tmp_path, monkeypatch):
    captured = {}

    def fake_run_web_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("tradingagents.web.run_web_server", fake_run_web_server)
    env_path = tmp_path / ".env"
    result = CliRunner().invoke(
        cli_main.app,
        ["web", "--port", "9013", "--no-browser", "--env-file", str(env_path)],
    )

    assert result.exit_code == 0
    assert captured == {
        "port": 9013,
        "env_path": str(env_path),
        "open_browser": False,
    }
