import { zodResolver } from "@hookform/resolvers/zod";
import { SegmentedControl } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrainCircuit, Check, ChevronRight, CircleAlert, Coins, Database, Landmark, LoaderCircle, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { api } from "../api";
import { CryptoAssetCombobox } from "../components/CryptoAssetCombobox";
import { ModelCombobox } from "../components/ModelCombobox";
import { UiSelect } from "../components/UiSelect";
import { localIsoDate, normalizeCryptoSymbol, normalizeSymbol } from "../lib/format";
import type { AnalysisRunInput, ConfigField, PreflightResult } from "../types";

const ANALYSTS = [
  { value: "market", title: "市场", description: "价格、成交量与技术指标" },
  { value: "social", title: "情绪", description: "社区讨论与市场情绪" },
  { value: "news", title: "新闻", description: "项目、监管与宏观事件" },
  { value: "fundamentals", title: "基本面", description: "财务或链上项目基本面" },
];

const LANGUAGE_OPTIONS = [
  { value: "Chinese", label: "中文" },
  { value: "English", label: "English" },
  { value: "Japanese", label: "日本語" },
];

const RESEARCH_DEPTH_OPTIONS = [
  { value: "1", label: "快速 · 1 轮辩论" },
  { value: "2", label: "标准 · 2 轮辩论" },
  { value: "3", label: "深入 · 3 轮辩论" },
];

const schema = z.object({
  symbol: z.string().trim().min(1, "请输入分析标的").max(64),
  analysis_date: z.string().min(1, "请选择分析日期"),
  analysts: z.array(z.string()).min(1, "至少选择一位分析师"),
  research_depth: z.number().int().min(1).max(3),
  checkpoint_enabled: z.boolean(),
  output_language: z.string(),
  llm_provider: z.string(),
  quick_model: z.string(),
  deep_model: z.string(),
});

type AnalysisFormValues = z.infer<typeof schema>;

function fieldByName(fields: ConfigField[], name: string): ConfigField | undefined {
  return fields.find((field) => field.name === name);
}

export function NewRunPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const configQuery = useQuery({ queryKey: ["config"], queryFn: api.getConfig });
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [marketType, setMarketType] = useState<"crypto" | "stock">("crypto");
  const [cryptoSelection, setCryptoSelection] = useState("BTC-USDT");
  const [stockSymbol, setStockSymbol] = useState("AAPL");
  const [customCrypto, setCustomCrypto] = useState("");
  const [advancedCrypto, setAdvancedCrypto] = useState(false);
  const fields = useMemo(
    () => configQuery.data?.groups.flatMap((group) => group.fields) ?? [],
    [configQuery.data],
  );
  const form = useForm<AnalysisFormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      symbol: "BTC-USDT",
      analysis_date: localIsoDate(),
      analysts: ANALYSTS.map((item) => item.value),
      research_depth: 1,
      checkpoint_enabled: true,
      output_language: "Chinese",
      llm_provider: "",
      quick_model: "",
      deep_model: "",
    },
  });

  useEffect(() => {
    if (!configQuery.data) return;
    form.reset({
      ...form.getValues(),
      llm_provider: fieldByName(fields, "TRADINGAGENTS_LLM_PROVIDER")?.value || "",
      quick_model: fieldByName(fields, "TRADINGAGENTS_QUICK_THINK_LLM")?.value || "",
      deep_model: fieldByName(fields, "TRADINGAGENTS_DEEP_THINK_LLM")?.value || "",
      output_language: fieldByName(fields, "TRADINGAGENTS_OUTPUT_LANGUAGE")?.value || "Chinese",
    });
  }, [configQuery.data, fields, form]);

  const symbol = form.watch("symbol");
  const assetType = marketType;
  const providerField = fieldByName(fields, "TRADINGAGENTS_LLM_PROVIDER");
  const selectedProvider = form.watch("llm_provider") || providerField?.value || "";
  const modelQuery = useQuery({
    queryKey: ["models", selectedProvider],
    queryFn: () => api.discoverModels(configQuery.data!.csrfToken, selectedProvider),
    enabled: Boolean(configQuery.data && selectedProvider),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const cryptoCatalogQuery = useQuery({
    queryKey: ["crypto-assets"],
    queryFn: () => api.discoverCryptoAssets(false),
    staleTime: 15 * 60 * 1000,
    retry: 1,
  });
  const refreshCryptoCatalog = useMutation({
    mutationFn: () => api.discoverCryptoAssets(true),
    onSuccess: (catalog) => queryClient.setQueryData(["crypto-assets"], catalog),
  });
  const selectedCryptoAsset = cryptoCatalogQuery.data?.items?.find((asset) => asset.symbol === symbol);
  const cryptoSummary = selectedCryptoAsset
    ? `${selectedCryptoAsset.nameZh || selectedCryptoAsset.nameEn || selectedCryptoAsset.baseAsset} · ${selectedCryptoAsset.baseAsset}/USDT`
    : symbol ? symbol.replace("-", "/") : "等待选择";
  const optionalWarnings = [
    ["AICOIN_ACCESS_KEY_ID", "AiCoin 中文新闻与 X 代理"],
    ["COINDESK_API_KEY", "CoinDesk Data API"],
    ["ROOTDATA_API_KEY", "RootData 项目事件"],
    ["X_BEARER_TOKEN", "原生 X API"],
  ].filter(([name]) => !fieldByName(fields, name)?.configured);

  const createMutation = useMutation({
    mutationFn: async (input: AnalysisRunInput) => {
      const token = configQuery.data?.csrfToken;
      if (!token) throw new Error("配置尚未载入，请稍后再试。");
      const check = await api.preflight(token, input);
      setPreflight(check);
      if (!check.ok) throw new Error(check.errors.join("；"));
      return api.createRun(token, input);
    },
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run.run_id}`);
    },
  });

  const submit = form.handleSubmit((values) => {
    setPreflight(null);
    createMutation.mutate({
      ...values,
      symbol: marketType === "crypto" ? normalizeCryptoSymbol(values.symbol) : normalizeSymbol(values.symbol),
      llm_provider: values.llm_provider || undefined,
      quick_model: values.quick_model || undefined,
      deep_model: values.deep_model || undefined,
      output_language: values.output_language || undefined,
    });
  });

  const switchMarket = (next: "crypto" | "stock") => {
    const current = form.getValues("symbol");
    if (marketType === "crypto") {
      if (advancedCrypto) setCustomCrypto(current);
      else setCryptoSelection(current);
    } else {
      setStockSymbol(current);
    }
    setMarketType(next);
    const nextSymbol = next === "crypto"
      ? advancedCrypto ? customCrypto : cryptoSelection
      : stockSymbol;
    form.setValue("symbol", nextSymbol, { shouldValidate: true });
  };

  const toggleAdvancedCrypto = () => {
    const current = form.getValues("symbol");
    if (advancedCrypto) {
      setCustomCrypto(current);
      setAdvancedCrypto(false);
      form.setValue("symbol", cryptoSelection, { shouldValidate: true });
      return;
    }
    const nextCustom = customCrypto || current;
    setCryptoSelection(current);
    setCustomCrypto(nextCustom);
    setAdvancedCrypto(true);
    form.setValue("symbol", nextCustom, { shouldValidate: true });
  };

  return (
    <div className="page-stack">
      <header className="page-title-row">
        <div><p className="eyebrow">NEW ANALYSIS</p><h1>新建分析</h1><p>选择标的与研究范围，任务会进入本地单任务队列。</p></div>
      </header>
      <form className="analysis-layout" onSubmit={submit}>
        <div className="form-column">
          <section className="panel form-section">
            <div className="section-number">01</div>
            <div className="section-copy"><h2>分析对象</h2><p>先选择市场，再搜索要分析的币种或输入美股代码。</p></div>
            <div className="field-grid two-columns">
              <div className="field-control field-span-two">
                <span>市场类型</span>
                <SegmentedControl
                  className="market-segmented"
                  value={marketType}
                  onChange={(value) => switchMarket(value as "crypto" | "stock")}
                  data={[
                    { value: "crypto", label: "加密资产" },
                    { value: "stock", label: "美股" },
                  ]}
                  fullWidth
                  aria-label="市场类型"
                />
              </div>
              <div className="field-control field-span-two">
                <span>{marketType === "crypto" ? advancedCrypto ? "自定义 USDT 交易对" : "加密币种" : "美股代码"}</span>
                {marketType === "crypto" ? advancedCrypto ? (
                  <div className="symbol-input">
                    <Coins size={19} />
                    <input
                      value={symbol}
                      onChange={(event) => {
                        setCustomCrypto(event.target.value);
                        form.setValue("symbol", event.target.value, { shouldValidate: true });
                      }}
                      onBlur={(event) => {
                        const normalized = normalizeCryptoSymbol(event.target.value);
                        setCustomCrypto(normalized);
                        form.setValue("symbol", normalized, { shouldValidate: true });
                      }}
                      placeholder="SUI、SUIUSDT 或 SUI-USDT"
                      autoComplete="off"
                    />
                    <b>高级</b>
                  </div>
                ) : (
                  <CryptoAssetCombobox
                    value={symbol}
                    onChange={(value) => {
                      setCryptoSelection(value);
                      form.setValue("symbol", value, { shouldValidate: true });
                    }}
                    assets={cryptoCatalogQuery.data?.items ?? []}
                    loading={cryptoCatalogQuery.isLoading}
                    error={cryptoCatalogQuery.isError ? cryptoCatalogQuery.error.message : undefined}
                  />
                ) : (
                  <div className="symbol-input">
                    <Landmark size={19} />
                    <input
                      value={symbol}
                      onChange={(event) => {
                        setStockSymbol(event.target.value);
                        form.setValue("symbol", event.target.value, { shouldValidate: true });
                      }}
                      onBlur={(event) => {
                        const normalized = normalizeSymbol(event.target.value);
                        setStockSymbol(normalized);
                        form.setValue("symbol", normalized, { shouldValidate: true });
                      }}
                      placeholder="AAPL"
                      autoComplete="off"
                    />
                    <b>美股</b>
                  </div>
                )}
                {form.formState.errors.symbol ? <small className="field-error">{form.formState.errors.symbol.message}</small> : null}
                {marketType === "crypto" ? (
                  <div className="crypto-selector-actions">
                    <button type="button" onClick={toggleAdvancedCrypto}>{advancedCrypto ? "返回币种选择" : "高级手工输入"}</button>
                    {!advancedCrypto && cryptoCatalogQuery.data?.items ? <span>{cryptoCatalogQuery.data.items.length} 个 USDT 现货币种</span> : null}
                  </div>
                ) : null}
                {marketType === "crypto" && !advancedCrypto && cryptoCatalogQuery.data?.warning ? (
                  <div className="crypto-catalog-warning">
                    <CircleAlert size={15} />
                    <span>{cryptoCatalogQuery.data.warning}，当前显示{cryptoCatalogQuery.data.source === "fallback" ? "内置热门目录" : "上次成功目录"}。</span>
                    <button type="button" disabled={refreshCryptoCatalog.isPending} onClick={() => refreshCryptoCatalog.mutate()}>
                      <RefreshCw className={refreshCryptoCatalog.isPending ? "spin" : ""} size={13} />重新获取
                    </button>
                  </div>
                ) : null}
              </div>
              <label className="field-control"><span>分析日期</span><input type="date" max={localIsoDate()} {...form.register("analysis_date")} /></label>
              <div className="field-control">
                <span>报告语言</span>
                <Controller
                  control={form.control}
                  name="output_language"
                  render={({ field }) => (
                    <UiSelect ariaLabel="报告语言" value={field.value} onChange={field.onChange} options={LANGUAGE_OPTIONS} />
                  )}
                />
              </div>
            </div>
          </section>

          <section className="panel form-section">
            <div className="section-number">02</div>
            <div className="section-copy"><h2>研究团队</h2><p>按需要组合分析师；完整团队能形成更均衡的交叉验证。</p></div>
            <div className="analyst-grid">
              {ANALYSTS.map((analyst) => (
                <label className="analyst-card" key={analyst.value}>
                  <input type="checkbox" value={analyst.value} {...form.register("analysts")} />
                  <span className="check-box"><Check size={15} /></span>
                  <strong>{analyst.title}分析师</strong><small>{analyst.description}</small>
                </label>
              ))}
            </div>
            {form.formState.errors.analysts ? <small className="field-error">{form.formState.errors.analysts.message}</small> : null}
            <div className="field-grid two-columns top-gap">
              <div className="field-control">
                <span>研究深度</span>
                <Controller
                  control={form.control}
                  name="research_depth"
                  render={({ field }) => (
                    <UiSelect
                      ariaLabel="研究深度"
                      value={String(field.value)}
                      onChange={(value) => field.onChange(Number(value))}
                      options={RESEARCH_DEPTH_OPTIONS}
                    />
                  )}
                />
              </div>
              <label className="switch-row"><input type="checkbox" {...form.register("checkpoint_enabled")} /><span className="switch" /><span><strong>启用 checkpoint</strong><small>中断后可从节点边界恢复</small></span></label>
            </div>
          </section>

          <section className="panel form-section">
            <div className="section-number">03</div>
            <div className="section-copy"><h2>模型覆盖</h2><p>留空时读取全局设置；只对当前任务生效。</p></div>
            <div className="field-grid two-columns">
              <div className="field-control field-span-two">
                <span>模型供应商</span>
                <Controller
                  control={form.control}
                  name="llm_provider"
                  render={({ field }) => (
                    <UiSelect
                      ariaLabel="模型供应商"
                      value={field.value}
                      onChange={field.onChange}
                      options={providerField?.options ?? []}
                      placeholder="使用全局设置"
                      searchable
                      clearable
                    />
                  )}
                />
              </div>
              <div className="field-control">
                <span>快速模型</span>
                <Controller
                  control={form.control}
                  name="quick_model"
                  render={({ field }) => (
                    <ModelCombobox
                      ariaLabel="快速模型"
                      value={field.value}
                      onChange={field.onChange}
                      models={modelQuery.data?.models}
                      loading={modelQuery.isLoading}
                      error={modelQuery.isError ? modelQuery.error.message : undefined}
                      placeholder="使用全局设置或选择模型"
                    />
                  )}
                />
              </div>
              <div className="field-control">
                <span>深度模型</span>
                <Controller
                  control={form.control}
                  name="deep_model"
                  render={({ field }) => (
                    <ModelCombobox
                      ariaLabel="深度模型"
                      value={field.value}
                      onChange={field.onChange}
                      models={modelQuery.data?.models}
                      loading={modelQuery.isLoading}
                      error={modelQuery.isError ? modelQuery.error.message : undefined}
                      placeholder="使用全局设置或选择模型"
                    />
                  )}
                />
              </div>
            </div>
            {selectedProvider ? <div className={`model-discovery compact ${modelQuery.data?.warning ? "warning" : ""}`}><div><strong>{modelQuery.isLoading ? "正在读取模型…" : modelQuery.data?.source === "api" ? `${modelQuery.data.models.length} 个 API 可用模型` : "使用内置模型目录"}</strong><span>{modelQuery.data?.warning || "点击输入框即可选择，也支持手工输入。"}</span></div></div> : null}
          </section>
        </div>

        <aside className="analysis-summary">
          <section className="panel sticky-panel">
            <p className="eyebrow">RUN SUMMARY</p>
            <h2>{assetType === "crypto" ? cryptoSummary : normalizeSymbol(symbol) || "等待输入"}</h2>
            <div className="summary-row"><span>市场类型</span><strong>{assetType === "crypto" ? "加密资产" : "美股"}</strong></div>
            <div className="summary-row"><span>分析师</span><strong>{form.watch("analysts")?.length || 0} 位</strong></div>
            <div className="summary-row"><span>执行方式</span><strong>本地串行队列</strong></div>
            <div className="summary-divider" />
            <div className="readiness-item ready"><BrainCircuit size={17} /><span><strong>模型配置预检</strong><small>提交前自动校验</small></span></div>
            <div className="readiness-item"><Database size={17} /><span><strong>可选数据源</strong><small>{optionalWarnings.length ? `${optionalWarnings.length} 项将降级` : "全部已配置"}</small></span></div>
            {assetType === "crypto" && optionalWarnings.length ? (
              <div className="warning-list"><CircleAlert size={16} /><div><strong>可选来源提醒</strong>{optionalWarnings.map(([, label]) => <span key={label}>{label} 未配置</span>)}</div></div>
            ) : null}
            {preflight && !preflight.ok ? <div className="error-banner">{preflight.errors.join("；")}</div> : null}
            {createMutation.isError ? <div className="error-banner">{createMutation.error.message}</div> : null}
            <button className="button button-primary button-wide" type="submit" disabled={createMutation.isPending || configQuery.isLoading}>
              {createMutation.isPending ? <LoaderCircle className="spin" size={18} /> : <ChevronRight size={18} />}
              {createMutation.isPending ? "正在预检…" : "预检并加入队列"}
            </button>
            <small className="submit-hint">任务 API 不接收任何密钥</small>
          </section>
        </aside>
      </form>
    </div>
  );
}
