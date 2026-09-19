"""分析运行时共用的 LLM 与工具调用统计回调。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
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

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_sink is not None:
            self._event_sink(event_type, payload)

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """记录普通 LLM 调用开始。"""
        with self._lock:
            self.llm_calls += 1
        self._emit("llm.started", {"name": serialized.get("name") or "LLM"})

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        """记录聊天模型调用开始。"""
        with self._lock:
            self.llm_calls += 1
        self._emit("llm.started", {"name": serialized.get("name") or "ChatModel"})

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """从模型响应提取 Token 统计。"""
        try:
            generation = response.generations[0][0]
        except (IndexError, TypeError):
            return
        usage_metadata = None
        if hasattr(generation, "message"):
            message = generation.message
            if isinstance(message, AIMessage) and hasattr(message, "usage_metadata"):
                usage_metadata = message.usage_metadata
        if usage_metadata:
            with self._lock:
                self.tokens_in += usage_metadata.get("input_tokens", 0)
                self.tokens_out += usage_metadata.get("output_tokens", 0)
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
        self._emit(
            "tool.started",
            {
                "name": serialized.get("name") or "tool",
                "input": str(input_str)[:2000],
            },
        )

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """记录工具调用结束和最新统计。"""
        self._emit("tool.completed", {"status": "ok"})
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
