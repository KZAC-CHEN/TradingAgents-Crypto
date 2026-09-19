from __future__ import annotations

import requests
from fastapi.testclient import TestClient

from tradingagents.config_store import ConfigStore
from tradingagents.web.app import create_web_app
from tradingagents.web.model_discovery import ModelDiscoveryService


class _Response:
    def __init__(self, payload, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class _Session:
    def __init__(self, response: _Response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_openai_discovery_uses_saved_key_and_filters_non_chat_models(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = ConfigStore(tmp_path / ".env")
    store.apply_changes({"OPENAI_API_KEY": "secret-key"})
    session = _Session(
        _Response(
            {
                "data": [
                    {"id": "gpt-5.6", "owned_by": "openai"},
                    {"id": "text-embedding-3-small", "owned_by": "openai"},
                ]
            }
        )
    )
    service = ModelDiscoveryService(store, session=session)

    result = service.discover("openai")

    assert result["source"] == "api"
    assert result["models"] == [{"id": "gpt-5.6", "label": "gpt-5.6"}]
    url, kwargs = session.calls[0]
    assert url == "https://api.openai.com/v1/models"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in repr(result)


def test_google_discovery_keeps_only_generate_content_models(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    store = ConfigStore(tmp_path / ".env")
    store.apply_changes({"GOOGLE_API_KEY": "google-secret"})
    session = _Session(
        _Response(
            {
                "models": [
                    {
                        "name": "models/gemini-chat",
                        "displayName": "Gemini Chat",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/gemini-embedding",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            }
        )
    )

    result = ModelDiscoveryService(store, session=session).discover("google")

    assert result["models"] == [{"id": "gemini-chat", "label": "Gemini Chat"}]
    _url, kwargs = session.calls[0]
    assert kwargs["headers"]["x-goog-api-key"] == "google-secret"


def test_ollama_discovery_uses_native_tags_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    store = ConfigStore(tmp_path / ".env")
    store.apply_changes({"OLLAMA_BASE_URL": "http://localhost:11434/v1"})
    session = _Session(_Response({"models": [{"name": "qwen3:8b"}]}))

    result = ModelDiscoveryService(store, session=session).discover("ollama")

    assert result["models"] == [{"id": "qwen3:8b", "label": "qwen3:8b"}]
    assert session.calls[0][0] == "http://localhost:11434/api/tags"


def test_missing_key_falls_back_to_catalog_without_exposing_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    store = ConfigStore(tmp_path / ".env")
    session = _Session(_Response({"data": []}))

    result = ModelDiscoveryService(store, session=session).discover("deepseek")

    assert result["source"] == "catalog"
    assert result["models"]
    assert "DEEPSEEK_API_KEY" in result["warning"]
    assert session.calls == []


def test_model_discovery_api_requires_csrf_and_returns_service_result(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_web_app(
        env_path=tmp_path / ".env",
        database_path=tmp_path / "runs.db",
        csrf_token="test-token",
        allowed_hosts={"testserver"},
        start_run_manager=False,
    )

    class StubDiscovery:
        def discover(self, provider, *, refresh=False):
            return {
                "provider": provider,
                "models": [{"id": "model-a", "label": "Model A"}],
                "source": "api",
                "warning": None,
                "fetchedAt": "2026-09-19T00:00:00+00:00",
                "refresh": refresh,
            }

        def clear(self):
            pass

    app.state.model_discovery = StubDiscovery()
    with TestClient(app, base_url="http://testserver") as client:
        denied = client.post("/api/models/discover", json={"provider": "openai"})
        allowed = client.post(
            "/api/models/discover",
            json={"provider": "openai", "refresh": True},
            headers={"X-CSRF-Token": "test-token"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["models"][0]["id"] == "model-a"
    assert allowed.json()["refresh"] is True
