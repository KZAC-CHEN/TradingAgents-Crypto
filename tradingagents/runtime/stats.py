"""分析运行时共用的 LLM 与工具调用统计回调。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


class StatsCallbackHandler(BaseCallbackHandler):
    """线程安全地统计调用次数、Token，并按需发送运行事件。"""

    def __init__(self, event_sink: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._event_sink = event_sink
        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self._llm_started: dict[str, tuple[float, str]] = {}
        self._tool_started: dict[str, tuple[float, str]] = {}

    @staticmethod
    def _run_key(kwargs: dict[str, Any]) -> str:
        """从 LangChain 回调参数提取稳定的运行标识。"""
        return str(kwargs.get("run_id") or threading.get_ident())

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_sink is not None:
            self._event_sink(event_type, payload)

    @staticmethod
    def _normalized_usage(response: Any, generation: Any) -> dict[str, int]:
        """从不同供应商和 LangChain 版本中提取统一 Token 用量。"""
        candidates: list[dict[str, Any]] = []
        message = getattr(generation, "message", None)
        usage_metadata = getattr(message, "usage_metadata", None)
        if isinstance(usage_metadata, dict):
            candidates.append(usage_metadata)
        response_metadata = getattr(message, "response_metadata", None)
        if isinstance(response_metadata, dict):
            candidates.append(response_metadata)
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            candidates.append(llm_output)
        for candidate in candidates:
            nested = candidate.get("token_usage") or candidate.get("usage") or candidate
            if not isinstance(nested, dict):
                continue
            input_tokens = nested.get("input_tokens", nested.get("prompt_tokens"))
            output_tokens = nested.get("output_tokens", nested.get("completion_tokens"))
            try:
                normalized_input = int(input_tokens or 0)
                normalized_output = int(output_tokens or 0)
            except (TypeError, ValueError):
                continue
            if normalized_input or normalized_output:
                return {
                    "input_tokens": normalized_input,
                    "output_tokens": normalized_output,
                    "total_tokens": normalized_input + normalized_output,
                }
        return {}

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """记录普通 LLM 调用开始。"""
        with self._lock:
            self.llm_calls += 1
            name = serialized.get("name") or "LLM"
            self._llm_started[self._run_key(kwargs)] = (time.monotonic(), name)
        self._emit("llm.started", {"name": name})
        self._emit("stats.updated", self.get_stats())

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        """记录聊天模型调用开始。"""
        with self._lock:
            self.llm_calls += 1
            name = serialized.get("name") or "ChatModel"
            self._llm_started[self._run_key(kwargs)] = (time.monotonic(), name)
        self._emit("llm.started", {"name": name})
        self._emit("stats.updated", self.get_stats())

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """从模型响应提取 Token 统计。"""
        try:
            generation = response.generations[0][0]
        except (AttributeError, IndexError, TypeError):
            generation = None
        usage_metadata = self._normalized_usage(response, generation)
        if usage_metadata:
            with self._lock:
                self.tokens_in += usage_metadata.get("input_tokens", 0)
                self.tokens_out += usage_metadata.get("output_tokens", 0)
        with self._lock:
            started_at, name = self._llm_started.pop(
                self._run_key(kwargs), (time.monotonic(), "LLM")
            )
        self._emit(
            "llm.completed",
            {
                "name": name,
                "duration_seconds": max(0.0, time.monotonic() - started_at),
                "usage": usage_metadata or {},
            },
        )
        self._emit("stats.updated", self.get_stats())

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """记录工具调用开始，但不持久化可能过大的完整输入。"""
        with self._lock:
            self.tool_calls += 1
            name = serialized.get("name") or "tool"
            self._tool_started[self._run_key(kwargs)] = (time.monotonic(), name)
        self._emit(
            "tool.started",
            {
                "name": name,
                "input": str(input_str)[:2000],
            },
        )
        self._emit("stats.updated", self.get_stats())

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """记录工具调用结束和最新统计。"""
        with self._lock:
            started_at, name = self._tool_started.pop(
                self._run_key(kwargs), (time.monotonic(), "tool")
            )
        self._emit(
            "tool.completed",
            {
                "name": name,
                "status": "ok",
                "duration_seconds": max(0.0, time.monotonic() - started_at),
            },
        )
        self._emit("stats.updated", self.get_stats())

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """记录模型调用错误，供 Web 运行日志展示。"""
        with self._lock:
            started_at, name = self._llm_started.pop(
                self._run_key(kwargs), (time.monotonic(), "LLM")
            )
        self._emit(
            "llm.failed",
            {
                "name": name,
                "error": str(error),
                "duration_seconds": max(0.0, time.monotonic() - started_at),
            },
        )
        self._emit("stats.updated", self.get_stats())

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """记录工具调用错误，供 Web 运行日志展示。"""
        with self._lock:
            started_at, name = self._tool_started.pop(
                self._run_key(kwargs), (time.monotonic(), "tool")
            )
        self._emit(
            "tool.failed",
            {
                "name": name,
                "error": str(error),
                "duration_seconds": max(0.0, time.monotonic() - started_at),
            },
        )
        self._emit("stats.updated", self.get_stats())

    def get_stats(self) -> dict[str, Any]:
        """返回当前统计快照。"""
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            }
