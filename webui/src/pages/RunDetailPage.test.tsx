import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalysisRun, RunStatus } from "../types";
import { calculateOverallProgress, RunDetailPage } from "./RunDetailPage";

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
    signal: null,
    evidence_health: { state: "ok", sections: {}, failed_sections: [], provider_issues: [], warnings: [] },
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

  it("证据阶段显示活动进度而不是静止零值", async () => {
    renderRun(run("evidence", { started_at: "2026-09-19T00:00:00Z" }));
    expect(await screen.findByText("8%")).toBeInTheDocument();
    expect(screen.getByText("证据采集中")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "分析总进度" })).toHaveAttribute(
      "aria-valuetext",
      expect.stringContaining("进度持续更新中"),
    );
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

  it("降级完成时显示最终建议和证据告警", async () => {
    renderRun(run("degraded", {
      signal: "HOLD",
      evidence_health: {
        state: "degraded",
        sections: { market: "error", news: "ok" },
        failed_sections: ["market"],
        provider_issues: [],
        warnings: ["市场快照不可用"],
      },
    }));
    expect(await screen.findByText(/最终建议 HOLD/)).toBeInTheDocument();
    expect(screen.getByText("失败分区：market")).toBeInTheDocument();
    expect(screen.getAllByText("降级完成").length).toBeGreaterThan(1);
  });
});

describe("运行总进度", () => {
  it("为预检、证据、Agent 和完成阶段计算连续里程碑", () => {
    expect(calculateOverallProgress("queued", 0, 0)).toBe(0);
    expect(calculateOverallProgress("preflight", 0, 0)).toBe(3);
    expect(calculateOverallProgress("evidence", 0, 0)).toBe(8);
    expect(calculateOverallProgress("running", 6, 12)).toBe(53);
    expect(calculateOverallProgress("succeeded", 12, 12)).toBe(100);
  });
});
