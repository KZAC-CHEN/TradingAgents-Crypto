import { describe, expect, it } from "vitest";

import type { RunEvent } from "../types";
import { initialRunEventState, runEventReducer } from "./useRunEvents";

function event(eventId: number, eventType: string, payload: Record<string, unknown>): RunEvent {
  return {
    event_id: eventId,
    run_id: "run-1",
    attempt: 1,
    event_type: eventType,
    payload,
    created_at: "2026-09-19T00:00:00Z",
  };
}

describe("实时事件 reducer", () => {
  it("合并 Agent 进度、统计和报告", () => {
    const progressed = runEventReducer(initialRunEventState, {
      type: "event",
      event: event(1, "progress.updated", {
        agents: { "Market Analyst": "completed" },
        completed_agents: 1,
        total_agents: 9,
        reports: { market_report: "市场报告" },
        agent_durations: { "Market Analyst": 1.5 },
      }),
    });
    const withStats = runEventReducer(progressed, {
      type: "event",
      event: event(2, "stats.updated", { llm_calls: 2, tool_calls: 3, tokens_in: 100, tokens_out: 50 }),
    });
    expect(withStats.completedAgents).toBe(1);
    expect(withStats.reports.market_report).toBe("市场报告");
    expect(withStats.agentDurations["Market Analyst"]).toBe(1.5);
    expect(withStats.stats).toEqual({ llm_calls: 2, tool_calls: 3, tokens_in: 100, tokens_out: 50 });
    expect(withStats.lastActivityAt).toBe("2026-09-19T00:00:00Z");
  });

  it("按事件 ID 去重以支持断线补发", () => {
    const first = event(8, "run.started", { total_agents: 9 });
    const state = runEventReducer(initialRunEventState, { type: "event", event: first });
    const duplicate = runEventReducer(state, { type: "event", event: first });
    expect(duplicate).toBe(state);
    expect(duplicate.events).toHaveLength(1);
    expect(duplicate.lastEventId).toBe(8);
  });

  it("跟踪当前 Agent、模型和工具活动", () => {
    const agent = runEventReducer(initialRunEventState, {
      type: "event",
      event: event(1, "progress.updated", {
        agents: { "Market Analyst": "in_progress" },
        completed_agents: 0,
        total_agents: 9,
      }),
    });
    const llm = runEventReducer(agent, {
      type: "event",
      event: event(2, "llm.started", { name: "deepseek-v4-pro" }),
    });
    const tool = runEventReducer(llm, {
      type: "event",
      event: event(3, "tool.started", { name: "get_crypto_market_report" }),
    });

    expect(tool.currentAgent?.name).toBe("Market Analyst");
    expect(tool.activeLlm?.name).toBe("deepseek-v4-pro");
    expect(tool.activeTool?.name).toBe("get_crypto_market_report");

    const completed = runEventReducer(tool, {
      type: "event",
      event: event(4, "run.completed", { stats: {} }),
    });
    expect(completed.currentAgent).toBeNull();
    expect(completed.activeLlm).toBeNull();
    expect(completed.activeTool).toBeNull();
  });
});
