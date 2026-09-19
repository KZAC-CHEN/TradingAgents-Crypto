"""与界面无关的 TradingAgents 流式分析运行器。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from tradingagents.dataflows.crypto_evidence import summarize_crypto_evidence
from tradingagents.graph.analyst_execution import ANALYST_NODE_SPECS
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree

from .stats import StatsCallbackHandler

EventSink = Callable[["AnalysisEvent"], None]
CancelCheck = Callable[[], bool]

_ANALYST_ORDER = ("market", "social", "news", "fundamentals")
_REPORT_KEYS = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}
_FIXED_AGENTS = (
    "Bull Researcher",
    "Bear Researcher",
    "Research Manager",
    "Trader",
    "Aggressive Analyst",
    "Neutral Analyst",
    "Conservative Analyst",
    "Portfolio Manager",
)


class AnalysisCancelled(RuntimeError):
    """用户请求在安全节点边界停止分析。"""


@dataclass(frozen=True)
class AnalysisRequest:
    """一次分析任务的稳定输入。"""

    symbol: str
    analysis_date: str
    analysts: tuple[str, ...] = _ANALYST_ORDER
    research_depth: int = 1
    checkpoint_enabled: bool = True
    llm_provider: str | None = None
    quick_model: str | None = None
    deep_model: str | None = None
    output_language: str | None = None
    backend_url: str | None = None
    reasoning: dict[str, str | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip()
        if not symbol:
            raise ValueError("分析标的不能为空。")
        try:
            analysis_day = date.fromisoformat(str(self.analysis_date).strip())
        except ValueError as exc:
            raise ValueError("分析日期必须为 YYYY-MM-DD 格式。") from exc
        if analysis_day > datetime.now().astimezone().date():
            raise ValueError("分析日期不能晚于当前本地日期。")
        analysts = tuple(dict.fromkeys(self.analysts))
        unknown = set(analysts) - set(_ANALYST_ORDER)
        if not analysts:
            raise ValueError("至少选择一个分析师。")
        if unknown:
            raise ValueError(f"不支持的分析师：{sorted(unknown)}")
        if int(self.research_depth) < 1:
            raise ValueError("研究深度必须至少为 1。")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "analysis_date", analysis_day.isoformat())
        object.__setattr__(self, "analysts", analysts)
        object.__setattr__(self, "research_depth", int(self.research_depth))

    def to_dict(self) -> dict[str, Any]:
        """转换为适合存储和 API 返回的字典。"""
        return asdict(self)


@dataclass(frozen=True)
class AnalysisEvent:
    """运行器发送给 CLI、Web 或持久化层的事件。"""

    run_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class AnalysisResult:
    """成功分析的最终结果和文件位置。"""

    run_id: str
    final_state: dict[str, Any]
    signal: str
    report_path: Path
    artifact_root: Path
    stats: dict[str, Any]
    evidence_health: dict[str, Any] = field(default_factory=dict)


class AnalysisRunner:
    """执行一次 LangGraph 分析，并把界面状态转换为结构化事件。"""

    def __init__(
        self,
        base_config: dict[str, Any],
        *,
        graph_factory: Callable[..., Any] = TradingAgentsGraph,
    ) -> None:
        self.base_config = deepcopy(base_config)
        self.graph_factory = graph_factory

    def build_config(self, request: AnalysisRequest, artifact_root: Path) -> dict[str, Any]:
        """生成任务专属配置并隔离结果与 checkpoint。"""
        config = deepcopy(self.base_config)
        config["results_dir"] = str(artifact_root)
        config["data_cache_dir"] = str(artifact_root / "checkpoints")
        config["max_debate_rounds"] = request.research_depth
        config["max_risk_discuss_rounds"] = request.research_depth
        config["checkpoint_enabled"] = request.checkpoint_enabled
        overrides = {
            "llm_provider": request.llm_provider,
            "quick_think_llm": request.quick_model,
            "deep_think_llm": request.deep_model,
            "output_language": request.output_language,
            "backend_url": request.backend_url,
            "openai_reasoning_effort": request.reasoning.get("openai"),
            "google_thinking_level": request.reasoning.get("google"),
            "anthropic_effort": request.reasoning.get("anthropic"),
        }
        for key, value in overrides.items():
            if value is not None and value != "":
                config[key] = value
        return config

    def run(
        self,
        request: AnalysisRequest,
        *,
        artifact_root: str | Path,
        run_id: str | None = None,
        event_sink: EventSink | None = None,
        cancel_check: CancelCheck | None = None,
        stats_handler: StatsCallbackHandler | None = None,
        portfolio: Any = None,
    ) -> AnalysisResult:
        """同步执行分析；调用方负责把它放入工作线程。"""
        resolved_run_id = run_id or str(uuid4())
        root = Path(artifact_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        reports_dir = root / "reports"
        partial_dir = root / "partial_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        partial_dir.mkdir(parents=True, exist_ok=True)
        emit = self._emitter(resolved_run_id, event_sink)
        should_cancel = cancel_check or (lambda: False)

        config = self.build_config(request, root)
        self._check_cancel(should_cancel)
        emit("run.preflight", {"status": "ok", "asset_type": _detect_asset_type(request.symbol)})
        stats = stats_handler or StatsCallbackHandler(
            lambda event_type, payload: emit(event_type, payload)
        )
        graph = self.graph_factory(
            request.analysts,
            config=config,
            debug=True,
            callbacks=[stats],
        )
        asset_type = _detect_asset_type(request.symbol)
        initial_state = graph.create_run_state(
            request.symbol,
            request.analysis_date,
            asset_type,
            portfolio,
        )
        args = graph.propagator.get_graph_args(callbacks=[stats])
        emit("evidence.started", {"asset_type": asset_type})
        checkpoint_id = graph.begin_checkpoint(
            request.symbol,
            request.analysis_date,
            asset_type,
            portfolio,
        )
        progress = _ProgressState(request.analysts)
        trace: list[dict[str, Any]] = []
        seen_messages: set[str] = set()
        try:
            if checkpoint_id is not None:
                args.setdefault("config", {}).setdefault("configurable", {})[
                    "thread_id"
                ] = checkpoint_id
            evidence_health = summarize_crypto_evidence(
                getattr(graph, "crypto_evidence_manifest", None)
            )
            emit(
                "evidence.completed",
                {"asset_type": asset_type, "health": evidence_health},
            )
            self._check_cancel(should_cancel)
            emit("run.started", progress.payload())
            for chunk in graph.graph.stream(graph.checkpoint_input(initial_state), **args):
                self._check_cancel(should_cancel)
                normalized_chunk = dict(chunk)
                trace.append(normalized_chunk)
                _write_partial_reports(normalized_chunk, partial_dir)
                progress.apply_chunk(normalized_chunk)
                emit("progress.updated", progress.payload(normalized_chunk))
                for message in normalized_chunk.get("messages", []):
                    identity = _message_identity(message)
                    if identity in seen_messages:
                        continue
                    seen_messages.add(identity)
                    content = _message_content(message)
                    if content:
                        emit("message.created", {"role": _message_role(message), "content": content})
                    for tool_call in getattr(message, "tool_calls", None) or ():
                        name, arguments = _tool_call_parts(tool_call)
                        emit("tool.requested", {"name": name, "arguments": arguments})
            final_state: dict[str, Any] = {}
            for chunk in trace:
                final_state.update(chunk)
            if asset_type != "crypto":
                graph.record_decision(
                    request.symbol,
                    request.analysis_date,
                    final_state,
                )
            graph.clear_checkpoint_on_success(
                request.symbol,
                request.analysis_date,
                asset_type,
                portfolio,
            )
        finally:
            graph.end_checkpoint()

        signal = _extract_signal(graph, final_state)
        report_path = write_report_tree(
            final_state,
            request.symbol,
            reports_dir,
            signal=signal,
            evidence_health=evidence_health,
        )
        emit(
            "run.completed",
            {
                "signal": signal,
                "report_path": str(report_path),
                "stats": stats.get_stats(),
                "evidence_health": evidence_health,
            },
        )
        return AnalysisResult(
            run_id=resolved_run_id,
            final_state=final_state,
            signal=signal,
            report_path=report_path,
            artifact_root=root,
            stats=stats.get_stats(),
            evidence_health=evidence_health,
        )

    @staticmethod
    def _emitter(run_id: str, sink: EventSink | None):
        def emit(event_type: str, payload: dict[str, Any]) -> AnalysisEvent:
            event = AnalysisEvent(
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            if sink is not None:
                sink(event)
            return event

        return emit

    @staticmethod
    def _check_cancel(cancel_check: CancelCheck) -> None:
        if cancel_check():
            raise AnalysisCancelled("分析已由用户取消。")


class _ProgressState:
    """从累计 graph state 推导供界面显示的 Agent 状态。"""

    def __init__(self, analysts: Iterable[str]) -> None:
        self.analysts = tuple(analysts)
        self.agents = {
            ANALYST_NODE_SPECS[key].agent_node: "pending" for key in self.analysts
        }
        self.agents.update(dict.fromkeys(_FIXED_AGENTS, "pending"))
        first = ANALYST_NODE_SPECS[self.analysts[0]].agent_node
        self.agents[first] = "in_progress"
        self.reports: dict[str, str] = {}
        self._started_at = time.monotonic()
        self._agent_started_at: dict[str, float] = {first: self._started_at}
        self._agent_durations: dict[str, float] = {}

    def _set_status(self, name: str, status: str) -> None:
        """更新 Agent 状态并累计已完成阶段耗时。"""
        previous = self.agents.get(name)
        now = time.monotonic()
        if status == "in_progress" and previous != "in_progress":
            self._agent_started_at[name] = now
        if status == "completed" and previous != "completed":
            started_at = self._agent_started_at.get(name)
            if started_at is not None:
                self._agent_durations[name] = max(0.0, now - started_at)
        self.agents[name] = status

    def apply_chunk(self, chunk: dict[str, Any]) -> None:
        for index, key in enumerate(self.analysts):
            report_key = _REPORT_KEYS[key]
            if chunk.get(report_key):
                self.reports[report_key] = str(chunk[report_key])
                self._set_status(ANALYST_NODE_SPECS[key].agent_node, "completed")
                if index + 1 < len(self.analysts):
                    next_name = ANALYST_NODE_SPECS[self.analysts[index + 1]].agent_node
                    if self.agents[next_name] == "pending":
                        self._set_status(next_name, "in_progress")
        if all(
            self.reports.get(_REPORT_KEYS[key])
            for key in self.analysts
        ) and self.agents["Bull Researcher"] == "pending":
            self._set_status("Bull Researcher", "in_progress")

        debate = chunk.get("investment_debate_state") or {}
        if debate.get("bull_history"):
            self._set_status("Bull Researcher", "completed")
            self._set_status("Bear Researcher", "in_progress")
        if debate.get("bear_history"):
            self._set_status("Bear Researcher", "completed")
            self._set_status("Research Manager", "in_progress")
        if debate.get("judge_decision"):
            self._set_status("Research Manager", "completed")
            self._set_status("Trader", "in_progress")
        if chunk.get("trader_investment_plan"):
            self._set_status("Trader", "completed")
            self._set_status("Aggressive Analyst", "in_progress")

        risk = chunk.get("risk_debate_state") or {}
        risk_fields = (
            ("aggressive_history", "Aggressive Analyst", "Neutral Analyst"),
            ("neutral_history", "Neutral Analyst", "Conservative Analyst"),
            ("conservative_history", "Conservative Analyst", "Portfolio Manager"),
        )
        for field_name, completed, next_agent in risk_fields:
            if risk.get(field_name):
                self._set_status(completed, "completed")
                if self.agents[next_agent] == "pending":
                    self._set_status(next_agent, "in_progress")
        if risk.get("judge_decision"):
            for name in (
                "Aggressive Analyst",
                "Neutral Analyst",
                "Conservative Analyst",
                "Portfolio Manager",
            ):
                self._set_status(name, "completed")

        if debate:
            parts = []
            for title, field_name in (
                ("Bull Researcher", "bull_history"),
                ("Bear Researcher", "bear_history"),
                ("Research Manager", "judge_decision"),
            ):
                if debate.get(field_name):
                    parts.append(f"### {title}\n{debate[field_name]}")
            if parts:
                self.reports["investment_plan"] = "\n\n".join(parts)
        if chunk.get("trader_investment_plan"):
            self.reports["trader_investment_plan"] = str(chunk["trader_investment_plan"])
        if risk:
            parts = []
            for title, field_name in (
                ("Aggressive Analyst", "aggressive_history"),
                ("Neutral Analyst", "neutral_history"),
                ("Conservative Analyst", "conservative_history"),
                ("Portfolio Manager", "judge_decision"),
            ):
                if risk.get(field_name):
                    parts.append(f"### {title}\n{risk[field_name]}")
            if parts:
                self.reports["final_trade_decision"] = "\n\n".join(parts)

    def payload(self, chunk: dict[str, Any] | None = None) -> dict[str, Any]:
        """返回可序列化进度，并附带本轮更新的报告键。"""
        updated_reports = []
        if chunk:
            updated_reports = [key for key in _REPORT_KEYS.values() if chunk.get(key)]
        return {
            "agents": dict(self.agents),
            "completed_agents": sum(value == "completed" for value in self.agents.values()),
            "total_agents": len(self.agents),
            "reports": dict(self.reports),
            "updated_reports": updated_reports,
            "agent_durations": dict(self._agent_durations),
            "elapsed_seconds": max(0.0, time.monotonic() - self._started_at),
        }


def _detect_asset_type(symbol: str) -> str:
    """使用项目现有代码规范识别股票或加密资产。"""
    from tradingagents.dataflows.symbol_utils import normalize_symbol

    canonical = normalize_symbol(symbol)
    return "crypto" if canonical.endswith(("-USD", "-USDT", "/USDT")) else "stock"


def _write_partial_reports(chunk: dict[str, Any], target: Path) -> None:
    """在流式运行期间原子保存已经形成的报告。"""
    for key in (*_REPORT_KEYS.values(), "trader_investment_plan", "final_trade_decision"):
        content = chunk.get(key)
        if not content:
            continue
        path = target / f"{key}.md"
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(str(content), encoding="utf-8")
        temporary.replace(path)


def _message_content(message: Any) -> str:
    """提取 LangChain 消息中的文本内容。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts).strip()
    if isinstance(content, dict) and content.get("text"):
        return str(content["text"]).strip()
    return ""


def _message_identity(message: Any) -> str:
    """为累计状态中的消息生成稳定去重标识。"""
    message_id = getattr(message, "id", None)
    if message_id:
        return f"id:{message_id}"
    tool_calls = getattr(message, "tool_calls", None) or ()
    return f"value:{type(message).__name__}:{_message_content(message)}:{tool_calls!r}"


def _message_role(message: Any) -> str:
    """返回稳定的消息角色标签。"""
    name = type(message).__name__.lower()
    if "human" in name:
        return "user"
    if "tool" in name:
        return "tool"
    if "ai" in name:
        return "agent"
    return "system"


def _tool_call_parts(tool_call: Any) -> tuple[str, dict[str, Any]]:
    """把字典或 LangChain 工具调用统一为名称与参数。"""
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "tool"), dict(tool_call.get("args") or {})
    return str(getattr(tool_call, "name", "tool")), dict(getattr(tool_call, "args", {}) or {})


def _extract_signal(graph: Any, final_state: dict[str, Any]) -> str:
    """从最终组合决策提取评级，失败时返回 REVIEW。"""
    decision = str(final_state.get("final_trade_decision") or "")
    if not decision:
        risk = final_state.get("risk_debate_state") or {}
        decision = str(risk.get("judge_decision") or "")
    if not decision:
        return "REVIEW"
    try:
        if hasattr(graph, "process_signal"):
            return str(graph.process_signal(decision))
        return str(graph.signal_processor.process_signal(decision))
    except Exception:
        return "REVIEW"


def sanitize_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    """返回不含任何密钥或令牌字段的运行配置快照。"""
    result: dict[str, Any] = {}
    for key, value in config.items():
        lowered = key.lower()
        if any(marker in lowered for marker in ("key", "secret", "token", "password")):
            continue
        try:
            json.dumps(value)
        except TypeError:
            result[key] = str(value)
        else:
            result[key] = value
    return result
