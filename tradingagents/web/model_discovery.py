"""从已配置的大模型接口发现当前账号可用的模型。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from tradingagents.config_store import ConfigStore
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS
from tradingagents.llm_clients.openai_client import OPENAI_COMPATIBLE_PROVIDERS

_DEFAULT_TIMEOUT_SECONDS = 12.0
_CACHE_TTL_SECONDS = 300.0
_OPENAI_BASE_URL = "https://api.openai.com/v1"
_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
_GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_NON_CHAT_MARKERS = (
    "audio",
    "babbage",
    "computer-use",
    "dall-e",
    "davinci",
    "embedding",
    "imagen",
    "moderation",
    "realtime",
    "sora",
    "speech",
    "transcribe",
    "tts",
    "veo",
    "whisper",
)


def _catalog_models(provider: str, store: ConfigStore) -> list[dict[str, str]]:
    """读取内置目录，并始终保留用户已经配置的模型。"""
    labels: dict[str, str] = {}
    for options in MODEL_OPTIONS.get(provider, {}).values():
        for label, model_id in options:
            if model_id != "custom":
                labels.setdefault(model_id, label)
    if store.get_value("TRADINGAGENTS_LLM_PROVIDER").lower() == provider:
        configured = (
            store.get_value("TRADINGAGENTS_QUICK_THINK_LLM"),
            store.get_value("TRADINGAGENTS_DEEP_THINK_LLM"),
        )
        for model_id in configured:
            if model_id:
                labels.setdefault(model_id, model_id)
    return [
        {"id": model_id, "label": label}
        for model_id, label in sorted(labels.items(), key=lambda item: item[0].lower())
    ]


def _is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _NON_CHAT_MARKERS)


def _parse_openai_models(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("模型接口返回格式无法识别。")
    models: dict[str, str] = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id or not _is_chat_model(model_id):
            continue
        label = str(item.get("display_name") or item.get("name") or model_id).strip()
        models[model_id] = label or model_id
    return [
        {"id": model_id, "label": label}
        for model_id, label in sorted(models.items(), key=lambda item: item[0].lower())
    ]


def _parse_google_models(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Gemini 模型接口返回格式无法识别。")
    models: dict[str, str] = {}
    for item in payload["models"]:
        if not isinstance(item, dict):
            continue
        methods = item.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        model_id = str(item.get("baseModelId") or item.get("name") or "").strip()
        if model_id.startswith("models/"):
            model_id = model_id.removeprefix("models/")
        if not model_id or not _is_chat_model(model_id):
            continue
        label = str(item.get("displayName") or model_id).strip()
        models[model_id] = label or model_id
    return [
        {"id": model_id, "label": label}
        for model_id, label in sorted(models.items(), key=lambda item: item[0].lower())
    ]


def _parse_ollama_models(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Ollama 模型接口返回格式无法识别。")
    models: dict[str, str] = {}
    for item in payload["models"]:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("model") or item.get("name") or "").strip()
        if model_id:
            models[model_id] = model_id
    return [
        {"id": model_id, "label": label}
        for model_id, label in sorted(models.items(), key=lambda item: item[0].lower())
    ]


class ModelDiscoveryService:
    """通过官方模型列表接口发现模型，并为短时间重复请求提供缓存。"""

    def __init__(
        self,
        store: ConfigStore,
        *,
        session: requests.Session | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        cache_ttl: float = _CACHE_TTL_SECONDS,
    ) -> None:
        self.store = store
        self.session = session or requests.Session()
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def clear(self) -> None:
        """配置保存后清空缓存，使新凭证立即生效。"""
        with self._lock:
            self._cache.clear()

    def discover(self, provider: str, *, refresh: bool = False) -> dict[str, Any]:
        """返回 API 实际可用模型；失败时安全降级到内置目录。"""
        normalized = provider.strip().lower()
        if normalized not in set(OPENAI_COMPATIBLE_PROVIDERS) | {
            "anthropic",
            "google",
            "azure",
            "bedrock",
        }:
            raise ValueError("不支持的模型供应商。")
        cache_key = self._cache_key(normalized)
        with self._lock:
            cached = self._cache.get(cache_key)
            if not refresh and cached and cached[0] > time.monotonic():
                return dict(cached[1])

        fallback = _catalog_models(normalized, self.store)
        try:
            models = self._fetch(normalized)
            if not models:
                raise ValueError("模型接口没有返回可用于文本分析的模型。")
            result = self._result(normalized, models, "api", None)
        except (requests.RequestException, ValueError) as exc:
            result = self._result(
                normalized,
                fallback,
                "catalog",
                self._safe_warning(normalized, exc),
            )
        with self._lock:
            self._cache[cache_key] = (time.monotonic() + self.cache_ttl, result)
        return dict(result)

    def _cache_key(self, provider: str) -> str:
        backend = self._backend_url(provider) or ""
        return f"{provider}:{backend}"

    def _backend_url(self, provider: str) -> str | None:
        selected_provider = self.store.get_value("TRADINGAGENTS_LLM_PROVIDER").lower()
        configured_backend = self.store.get_value("TRADINGAGENTS_LLM_BACKEND_URL")
        if configured_backend and (provider == selected_provider or provider == "openai_compatible"):
            return configured_backend.rstrip("/")
        spec = OPENAI_COMPATIBLE_PROVIDERS.get(provider)
        if spec and spec.base_url_env:
            configured = self.store.get_value(spec.base_url_env)
            if configured:
                return configured.rstrip("/")
        if provider == "openai":
            return _OPENAI_BASE_URL
        if spec and spec.base_url:
            return spec.base_url.rstrip("/")
        return None

    def _api_key(self, provider: str) -> str:
        key_name = get_api_key_env(provider)
        return self.store.get_value(key_name) if key_name else ""

    def _fetch(self, provider: str) -> list[dict[str, str]]:
        if provider == "anthropic":
            key = self._required_key(provider)
            response = self.session.get(
                _ANTHROPIC_MODELS_URL,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                params={"limit": 1000},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return _parse_openai_models(response.json())
        if provider == "google":
            key = self._required_key(provider)
            response = self.session.get(
                _GOOGLE_MODELS_URL,
                headers={"x-goog-api-key": key},
                params={"pageSize": 1000},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return _parse_google_models(response.json())
        if provider in {"azure", "bedrock"}:
            raise ValueError("该供应商不能通过当前数据面凭证安全枚举模型。")

        base_url = self._backend_url(provider)
        if not base_url:
            raise ValueError("尚未配置模型接口地址。")
        headers: dict[str, str] = {}
        key = self._api_key(provider)
        spec = OPENAI_COMPATIBLE_PROVIDERS[provider]
        if not key and not spec.key_optional:
            self._required_key(provider)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if provider == "ollama":
            origin = base_url.removesuffix("/v1")
            response = self.session.get(
                f"{origin}/api/tags",
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return _parse_ollama_models(response.json())
        response = self.session.get(
            f"{base_url}/models",
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return _parse_openai_models(response.json())

    def _required_key(self, provider: str) -> str:
        key_name = get_api_key_env(provider)
        key = self._api_key(provider)
        if not key:
            raise ValueError(f"缺少 {key_name or 'API Key'}，请先在设置页保存凭证。")
        return key

    @staticmethod
    def _safe_warning(provider: str, exc: Exception) -> str:
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            status = exc.response.status_code
            if status in {401, 403}:
                return f"{provider} 模型接口拒绝了凭证，请检查 API Key。"
            return f"{provider} 模型接口返回 HTTP {status}，已显示内置目录。"
        if isinstance(exc, requests.Timeout):
            return f"{provider} 模型接口请求超时，已显示内置目录。"
        if isinstance(exc, requests.RequestException):
            return f"无法连接 {provider} 模型接口，已显示内置目录。"
        return str(exc)

    @staticmethod
    def _result(
        provider: str,
        models: list[dict[str, str]],
        source: str,
        warning: str | None,
    ) -> dict[str, Any]:
        return {
            "provider": provider,
            "models": models,
            "source": source,
            "warning": warning,
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
        }
