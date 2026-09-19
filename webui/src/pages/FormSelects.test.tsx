import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnalysisRun, ConfigPayload } from "../types";
import { NewRunPage } from "./NewRunPage";
import { SettingsPage } from "./SettingsPage";

const config: ConfigPayload = {
  csrfToken: "test-token",
  configuredSecrets: 1,
  totalSecrets: 1,
  envPath: ".env",
  keylessSources: ["Binance"],
  groups: [
    {
      id: "runtime",
      title: "模型与运行",
      description: "配置分析模型。",
      fields: [
        {
          name: "TRADINGAGENTS_LLM_PROVIDER",
          label: "模型供应商",
          inputType: "select",
          secret: false,
          description: "选择兼容的模型服务。",
          placeholder: "",
          configured: true,
          value: "deepseek",
          options: [
            { value: "deepseek", label: "DeepSeek" },
            { value: "openai", label: "OpenAI" },
          ],
        },
        {
          name: "TRADINGAGENTS_QUICK_THINK_LLM",
          label: "快速模型",
          inputType: "text",
          secret: false,
          description: "快速任务使用的模型。",
          placeholder: "输入模型 ID",
          configured: true,
          value: "deepseek-chat",
        },
        {
          name: "TRADINGAGENTS_DEEP_THINK_LLM",
          label: "深度模型",
          inputType: "text",
          secret: false,
          description: "深度推理使用的模型。",
          placeholder: "输入模型 ID",
          configured: true,
          value: "deepseek-reasoner",
        },
        {
          name: "TRADINGAGENTS_OUTPUT_LANGUAGE",
          label: "报告语言",
          inputType: "select",
          secret: false,
          description: "报告输出语言。",
          placeholder: "",
          configured: true,
          value: "Chinese",
          options: [
            { value: "Chinese", label: "中文" },
            { value: "English", label: "English" },
          ],
        },
      ],
    },
  ],
};

const completedRun: AnalysisRun = {
  run_id: "run-test",
  status: "queued",
  stage: "queued",
  request: {
    symbol: "BTC-USD",
    analysis_date: "2026-09-19",
    analysts: ["market", "social", "news", "fundamentals"],
    research_depth: 2,
    checkpoint_enabled: true,
  },
  config: {},
  artifact_root: "local",
  attempt: 1,
  checkpoint_available: false,
  signal: null,
  evidence_health: { state: "ok", sections: {}, failed_sections: [], provider_issues: [], warnings: [] },
  queue_position: 1,
  error: null,
  created_at: "2026-09-19T00:00:00Z",
  started_at: null,
  finished_at: null,
  updated_at: "2026-09-19T00:00:00Z",
};

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function renderWithClient(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </MantineProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Mantine 表单接入", () => {
  it("新建分析提交数字研究深度和模型覆盖", async () => {
    const user = userEvent.setup();
    const requests: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/config")) return jsonResponse(config);
      if (url.includes("/api/instruments/crypto")) {
        return jsonResponse({
          items: [
            {
              symbol: "BTC-USDT",
              exchangeSymbol: "BTCUSDT",
              baseAsset: "BTC",
              quoteAsset: "USDT",
              nameZh: "比特币",
              nameEn: "Bitcoin",
              aliases: ["XBT"],
              featured: true,
            },
          ],
          source: "binance",
          warning: null,
          fetchedAt: "2026-09-19T00:00:00Z",
        });
      }
      if (url.endsWith("/api/models/discover")) {
        return jsonResponse({
          provider: "deepseek",
          models: [
            { id: "deepseek-chat", label: "DeepSeek Chat" },
            { id: "deepseek-reasoner", label: "DeepSeek Reasoner" },
          ],
          source: "api",
          warning: null,
          fetchedAt: "2026-09-19T00:00:00Z",
        });
      }
      const request = JSON.parse(String(init?.body)) as Record<string, unknown>;
      requests.push(request);
      if (url.endsWith("/api/preflight")) {
        return jsonResponse({
          ok: true,
          errors: [],
          warnings: [],
          resolved: {
            llm_provider: "deepseek",
            quick_model: String(request.quick_model),
            deep_model: String(request.deep_model),
            checkpoint_enabled: true,
            asset_type: "crypto",
          },
        });
      }
      return jsonResponse(completedRun);
    }));

    renderWithClient(
      <MemoryRouter initialEntries={["/runs/new"]}>
        <Routes>
          <Route path="/runs/new" element={<NewRunPage />} />
          <Route path="/runs/:runId" element={<div>任务已创建</div>} />
        </Routes>
      </MemoryRouter>,
    );

    const depth = await screen.findByRole("combobox", { name: "研究深度" });
    await user.click(depth);
    await user.click(screen.getByText("标准 · 2 轮辩论"));
    const quickModel = screen.getByRole("combobox", { name: "快速模型" });
    await waitFor(() => expect(quickModel).toHaveValue("deepseek-chat"));
    await user.clear(quickModel);
    await user.type(quickModel, "proxy-fast-model");
    await user.click(screen.getByRole("button", { name: "预检并加入队列" }));

    expect(await screen.findByText("任务已创建")).toBeInTheDocument();
    expect(requests[0]).toMatchObject({
      symbol: "BTC-USDT",
      research_depth: 2,
      llm_provider: "deepseek",
      quick_model: "proxy-fast-model",
      deep_model: "deepseek-reasoner",
    });
    expect(typeof requests[0].research_depth).toBe("number");
  });

  it("支持切换美股并把高级币种输入标准化", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/config")) return jsonResponse(config);
      if (url.includes("/api/instruments/crypto")) {
        return jsonResponse({
          items: [],
          source: "fallback",
          warning: "offline",
          fetchedAt: "2026-09-19T00:00:00Z",
        });
      }
      return jsonResponse({
        provider: "deepseek",
        models: [],
        source: "catalog",
        warning: null,
        fetchedAt: "2026-09-19T00:00:00Z",
      });
    }));

    renderWithClient(<MemoryRouter><NewRunPage /></MemoryRouter>);
    expect(await screen.findByRole("combobox", { name: "加密币种" })).toHaveValue("BTC/USDT");

    await user.click(screen.getByText("美股"));
    const stock = screen.getByPlaceholderText("AAPL");
    await user.clear(stock);
    await user.type(stock, "nvda");
    await user.click(screen.getByText("加密资产"));
    await user.click(screen.getByRole("button", { name: "高级手工输入" }));
    const crypto = screen.getByPlaceholderText("SUI、SUIUSDT 或 SUI-USDT");
    await user.clear(crypto);
    await user.type(crypto, "sui");
    await user.tab();

    expect(crypto).toHaveValue("SUI-USDT");
    await user.click(screen.getByText("美股"));
    expect(screen.getByPlaceholderText("AAPL")).toHaveValue("NVDA");
  });

  it("设置页选择新供应商后显示待保存状态", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/config")) return jsonResponse(config);
      return jsonResponse({
        provider: "deepseek",
        models: [],
        source: "catalog",
        warning: "使用内置目录",
        fetchedAt: "2026-09-19T00:00:00Z",
      });
    }));

    renderWithClient(<MemoryRouter><SettingsPage /></MemoryRouter>);
    const provider = await screen.findByRole("combobox", { name: "模型供应商" });
    expect(provider).toHaveValue("DeepSeek");
    await user.click(provider);
    await user.click(screen.getByText("OpenAI"));

    expect(provider).toHaveValue("OpenAI");
    expect(screen.getByText("1 项待保存")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存配置" })).toBeEnabled();
  });
});
