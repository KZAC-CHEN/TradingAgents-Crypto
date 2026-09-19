import { useEffect, useReducer, useRef } from "react";

import type { RunEvent } from "../types";

export interface RuntimeStats {
  llm_calls: number;
  tool_calls: number;
  tokens_in: number;
  tokens_out: number;
}

export interface ActiveOperation {
  name: string;
  startedAt: string;
}

export interface RunEventState {
  events: RunEvent[];
  seenEventIds: Set<number>;
  lastEventId: number;
  agents: Record<string, string>;
  agentDurations: Record<string, number>;
  completedAgents: number;
  totalAgents: number;
  elapsedSeconds: number;
  reports: Record<string, string>;
  stats: RuntimeStats;
  currentAgent: ActiveOperation | null;
  activeLlm: ActiveOperation | null;
  activeTool: ActiveOperation | null;
  lastActivityAt: string | null;
  error: string | null;
  connection: "connecting" | "live" | "reconnecting" | "closed";
}

export type RunEventAction =
  | { type: "event"; event: RunEvent }
  | { type: "connection"; connection: RunEventState["connection"] }
  | { type: "reset" };

export const initialRunEventState: RunEventState = {
  events: [],
  seenEventIds: new Set(),
  lastEventId: 0,
  agents: {},
  agentDurations: {},
  completedAgents: 0,
  totalAgents: 0,
  elapsedSeconds: 0,
  reports: {},
  stats: { llm_calls: 0, tool_calls: 0, tokens_in: 0, tokens_out: 0 },
  currentAgent: null,
  activeLlm: null,
  activeTool: null,
  lastActivityAt: null,
  error: null,
  connection: "connecting",
};

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function stringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
  );
}

function numberRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, number] => typeof entry[1] === "number"),
  );
}

export function runEventReducer(state: RunEventState, action: RunEventAction): RunEventState {
  if (action.type === "reset") return initialRunEventState;
  if (action.type === "connection") return { ...state, connection: action.connection };
  const event = action.event;
  if (state.seenEventIds.has(event.event_id)) return state;
  const seenEventIds = new Set(state.seenEventIds).add(event.event_id);
  const next: RunEventState = {
    ...state,
    seenEventIds,
    lastEventId: Math.max(state.lastEventId, event.event_id),
    events: [...state.events, event].slice(-500),
    lastActivityAt: event.created_at,
  };
  if (event.event_type === "progress.updated" || event.event_type === "run.started") {
    next.agents = { ...state.agents, ...stringRecord(event.payload.agents) };
    next.agentDurations = { ...state.agentDurations, ...numberRecord(event.payload.agent_durations) };
    next.completedAgents = numberValue(event.payload.completed_agents, state.completedAgents);
    next.totalAgents = numberValue(event.payload.total_agents, state.totalAgents);
    next.elapsedSeconds = numberValue(event.payload.elapsed_seconds, state.elapsedSeconds);
    next.reports = { ...state.reports, ...stringRecord(event.payload.reports) };
    const activeAgent = Object.entries(next.agents).find(([, status]) => status === "in_progress")?.[0];
    if (activeAgent) {
      next.currentAgent = state.currentAgent?.name === activeAgent
        ? state.currentAgent
        : { name: activeAgent, startedAt: event.created_at };
    } else if (event.event_type === "progress.updated") {
      next.currentAgent = null;
    }
  }
  if (event.event_type === "llm.started") {
    next.activeLlm = { name: String(event.payload.name || "模型"), startedAt: event.created_at };
  }
  if (event.event_type === "llm.completed" || event.event_type === "llm.failed") next.activeLlm = null;
  if (event.event_type === "tool.started") {
    next.activeTool = { name: String(event.payload.name || "工具"), startedAt: event.created_at };
  }
  if (event.event_type === "tool.completed" || event.event_type === "tool.failed") next.activeTool = null;
  if (event.event_type === "stats.updated" || event.event_type === "run.completed") {
    const source = event.event_type === "run.completed" ? event.payload.stats : event.payload;
    if (source && typeof source === "object" && !Array.isArray(source)) {
      const stats = source as Record<string, unknown>;
      next.stats = {
        llm_calls: numberValue(stats.llm_calls, state.stats.llm_calls),
        tool_calls: numberValue(stats.tool_calls, state.stats.tool_calls),
        tokens_in: numberValue(stats.tokens_in, state.stats.tokens_in),
        tokens_out: numberValue(stats.tokens_out, state.stats.tokens_out),
      };
    }
  }
  if (event.event_type === "run.failed") {
    next.error = typeof event.payload.error === "string" ? event.payload.error : "分析失败。";
  }
  if (["run.completed", "run.succeeded", "run.degraded", "run.cancelled", "run.interrupted", "run.failed"].includes(event.event_type)) {
    next.currentAgent = null;
    next.activeLlm = null;
    next.activeTool = null;
  }
  return next;
}

export const RUN_EVENT_TYPES = [
  "run.queued",
  "run.claimed",
  "run.preflight",
  "run.preflight_result",
  "evidence.started",
  "evidence.completed",
  "run.started",
  "progress.updated",
  "message.created",
  "llm.started",
  "llm.completed",
  "llm.failed",
  "tool.requested",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "stats.updated",
  "run.completed",
  "run.cancel_requested",
  "run.cancelled",
  "run.interrupted",
  "run.failed",
  "run.resumed",
  "run.degraded",
  "run.succeeded",
] as const;

export function useRunEvents(runId: string, follow = true): RunEventState {
  const [state, dispatch] = useReducer(runEventReducer, initialRunEventState);
  const cursor = useRef(0);

  useEffect(() => {
    dispatch({ type: "reset" });
    cursor.current = 0;
  }, [runId]);

  useEffect(() => {
    if (!runId) {
      dispatch({ type: "connection", connection: "closed" });
      return;
    }
    let disposed = false;
    let reconnectTimer: number | undefined;
    let retry = 0;
    let source: EventSource | undefined;

    const connect = () => {
      if (disposed) return;
      dispatch({ type: "connection", connection: retry ? "reconnecting" : "connecting" });
      source = new EventSource(`/api/runs/${runId}/events?after=${cursor.current}&follow=${follow}`);
      source.onopen = () => {
        retry = 0;
        dispatch({ type: "connection", connection: "live" });
      };
      const receive = (message: MessageEvent<string>) => {
        try {
          const event = JSON.parse(message.data) as RunEvent;
          cursor.current = Math.max(cursor.current, event.event_id);
          dispatch({ type: "event", event });
        } catch {
          // 单条损坏事件不会中断后续 SSE 进度。
        }
      };
      for (const eventType of RUN_EVENT_TYPES) source.addEventListener(eventType, receive as EventListener);
      source.onerror = () => {
        source?.close();
        if (disposed) return;
        if (!follow) {
          dispatch({ type: "connection", connection: "closed" });
          return;
        }
        retry += 1;
        dispatch({ type: "connection", connection: "reconnecting" });
        reconnectTimer = window.setTimeout(connect, Math.min(10_000, 500 * 2 ** Math.min(retry, 5)));
      };
    };
    connect();
    return () => {
      disposed = true;
      source?.close();
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
    };
  }, [follow, runId]);

  return state;
}
