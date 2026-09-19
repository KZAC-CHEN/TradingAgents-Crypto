from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingagents.runtime import (
    AnalysisCancelled,
    AnalysisRequest,
    AnalysisRunner,
    sanitize_runtime_config,
)


@dataclass
class _Message:
    content: str
    tool_calls: list[dict]


class _FakeStream:
    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks

    def stream(self, initial_state, **kwargs):
        yield from self.chunks


class _FakeGraph:
    last_instance = None

    def __init__(self, analysts, *, config, debug, callbacks) -> None:
        self.analysts = analysts
        self.config = config
        self.callbacks = callbacks
        self.graph = _FakeStream(
            [
                {
                    "market_report": "市场报告",
                    "messages": [
                        _Message(
                            "已完成市场分析",
                            [{"name": "get_market_data", "args": {"symbol": "BTC-USD"}}],
                        )
                    ],
                },
                {
                    "investment_debate_state": {
                        "bull_history": "看多观点",
                        "bear_history": "看空观点",
                        "judge_decision": "研究结论",
                    },
                    "trader_investment_plan": "交易计划",
                },
                {
                    "risk_debate_state": {
                        "aggressive_history": "激进意见",
                        "neutral_history": "中性意见",
                        "conservative_history": "保守意见",
                        "judge_decision": "最终决策",
                    },
                    "final_trade_decision": "最终决策",
                },
            ]
        )
        self.propagator = SimpleNamespace(
            create_initial_state=lambda *args, **kwargs: {"symbol": args[0]},
            get_graph_args=lambda **kwargs: {"stream_mode": "values"},
        )
        self.signal_processor = SimpleNamespace(process_signal=lambda decision: "BUY")
        self.clear_calls = []
        self.record_calls = []
        self.end_calls = 0
        _FakeGraph.last_instance = self

    def create_run_state(self, symbol, analysis_date, asset_type="stock", portfolio=None):
        return {"symbol": symbol, "portfolio": portfolio}

    def begin_checkpoint(self, symbol, analysis_date, asset_type, portfolio=None):
        return "checkpoint-1"

    def checkpoint_input(self, initial_state):
        return initial_state

    def clear_checkpoint_on_success(self, *args):
        self.clear_calls.append(args)

    def record_decision(self, *args):
        self.record_calls.append(args)

    def end_checkpoint(self):
        self.end_calls += 1


def test_analysis_request_validates_input() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        AnalysisRequest(symbol="", analysis_date="2026-09-19")
    with pytest.raises(ValueError, match="不支持"):
        AnalysisRequest(symbol="AAPL", analysis_date="2026-09-19", analysts=("other",))
    future_year = datetime.now().year + 1
    with pytest.raises(ValueError, match="不能晚于"):
        AnalysisRequest(symbol="AAPL", analysis_date=f"{future_year}-01-01")


def test_runner_emits_events_and_writes_reports(tmp_path: Path) -> None:
    events = []
    request = AnalysisRequest(
        symbol="BTC-USD",
        analysis_date="2026-09-19",
        analysts=("market",),
        research_depth=2,
        output_language="简体中文",
    )
    runner = AnalysisRunner(
        {"results_dir": "shared", "api_key": "secret"},
        graph_factory=_FakeGraph,
    )

    result = runner.run(
        request,
        artifact_root=tmp_path / "run-1",
        run_id="run-1",
        event_sink=events.append,
    )

    graph = _FakeGraph.last_instance
    assert result.run_id == "run-1"
    assert result.signal == "BUY"
    assert result.report_path.exists()
    assert (tmp_path / "run-1" / "partial_reports" / "market_report.md").read_text(
        encoding="utf-8"
    ) == "市场报告"
    assert (tmp_path / "run-1" / "reports" / "1_analysts" / "market.md").exists()
    assert graph.config["results_dir"] == str((tmp_path / "run-1").resolve())
    assert graph.config["data_cache_dir"].endswith("checkpoints")
    assert graph.config["max_debate_rounds"] == 2
    assert graph.clear_calls == [("BTC-USD", "2026-09-19", "crypto", None)]
    assert graph.record_calls == []
    assert graph.end_calls == 1
    event_types = [event.event_type for event in events]
    assert event_types[:3] == ["run.preflight", "evidence.started", "evidence.completed"]
    assert "tool.requested" in event_types
    assert event_types[-1] == "run.completed"
    progress = [event.payload for event in events if event.event_type == "progress.updated"]
    assert progress[-1]["completed_agents"] >= 5
    assert progress[-1]["reports"]["final_trade_decision"]


def test_runner_preserves_checkpoint_when_cancelled(tmp_path: Path) -> None:
    checks = iter([False, False, True])
    runner = AnalysisRunner({}, graph_factory=_FakeGraph)
    request = AnalysisRequest(
        symbol="AAPL",
        analysis_date="2026-09-19",
        analysts=("market",),
    )

    with pytest.raises(AnalysisCancelled):
        runner.run(
            request,
            artifact_root=tmp_path / "cancelled",
            cancel_check=lambda: next(checks, True),
        )

    graph = _FakeGraph.last_instance
    assert graph.clear_calls == []
    assert graph.end_calls == 1


def test_sanitize_runtime_config_removes_secrets() -> None:
    sanitized = sanitize_runtime_config(
        {
            "llm_provider": "openai",
            "api_key": "hidden",
            "access_token": "hidden",
            "password": "hidden",
            "results_dir": Path("results"),
        }
    )

    assert sanitized == {"llm_provider": "openai", "results_dir": "results"}
