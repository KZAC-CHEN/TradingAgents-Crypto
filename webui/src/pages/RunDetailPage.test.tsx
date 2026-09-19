import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalysisRun, RunStatus } from "../types";
import { RunDetailPage } from "./RunDetailPage";

class MockEventSource {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public readonly url: string) {
    window.setTimeout(() => this.onopen?.(), 0);
  }
  addEventListener() {}
  close() {}
}

function run(status: RunStatus, overrides: Partial<AnalysisRun> = {}): AnalysisRun {
  return {
    run_id: "run-1",
    status,
    stage: status,
    request: {
      symbol: "BTC-USD",
      analysis_date: "2026-09-19",
      analysts: ["market", "news"],
      research_depth: 1,
      checkpoint_enabled: true,
    },
    config: {},
    artifact_root: "local",
    attempt: 1,
    checkpoint_available: false,
    queue_position: null,
    error: null,
    created_at: "2026-09-19T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-09-19T00:00:00Z",
    ...overrides,
  };
}

function renderRun(current: AnalysisRun) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.endsWith("/api/config")
      ? { csrfToken: "token", groups: [], keylessSources: [], configuredSecrets: 0, totalSecrets: 0, envPath: ".env" }
      : current;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/runs/run-1"]}>
        <Routes><Route path="/runs/:runId" element={<RunDetailPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("任务详情状态", () => {
  beforeEach(() => vi.stubGlobal("EventSource", MockEventSource));
  afterEach(() => vi.unstubAllGlobals());

  it("显示排队位置和取消操作", async () => {
    renderRun(run("queued", { queue_position: 2 }));
    expect(await screen.findByText("任务正在排队")).toBeInTheDocument();
    expect(screen.getByText("前面还有 1 个任务")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "取消任务" })).toBeEnabled();
  });

  it("失败且有 checkpoint 时显示恢复操作", async () => {
    renderRun(run("failed", { checkpoint_available: true, error: "模拟失败" }));
    expect(await screen.findByText("任务执行失败")).toBeInTheDocument();
    expect(screen.getByText("模拟失败")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "从 checkpoint 恢复" })).toBeEnabled();
  });

  it("完成状态显示百分之百进度", async () => {
    renderRun(run("succeeded", { started_at: "2026-09-19T00:00:00Z", finished_at: "2026-09-19T00:01:00Z" }));
    expect(await screen.findByText("100%")).toBeInTheDocument();
    expect(screen.getAllByText("已完成").length).toBeGreaterThan(1);
  });
});
