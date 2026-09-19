from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import AIMessage

from tradingagents.runtime.stats import StatsCallbackHandler


def test_stats_handler_emits_timed_llm_and_tool_events():
    events = []
    handler = StatsCallbackHandler(lambda event_type, payload: events.append((event_type, payload)))
    llm_run_id = uuid4()
    tool_run_id = uuid4()

    handler.on_chat_model_start({"name": "fake-model"}, [[]], run_id=llm_run_id)
    response = SimpleNamespace(
        generations=[
            [
                SimpleNamespace(
                    message=AIMessage(
                        content="ok",
                        usage_metadata={"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
                    )
                )
            ]
        ]
    )
    handler.on_llm_end(response, run_id=llm_run_id)
    handler.on_tool_start({"name": "fake-tool"}, "{}", run_id=tool_run_id)
    handler.on_tool_end("ok", run_id=tool_run_id)

    event_by_type = dict(events)
    assert event_by_type["llm.completed"]["name"] == "fake-model"
    assert event_by_type["llm.completed"]["duration_seconds"] >= 0
    assert event_by_type["tool.completed"]["name"] == "fake-tool"
    assert event_by_type["tool.completed"]["duration_seconds"] >= 0
    assert handler.get_stats() == {
        "llm_calls": 1,
        "tool_calls": 1,
        "tokens_in": 12,
        "tokens_out": 5,
    }
