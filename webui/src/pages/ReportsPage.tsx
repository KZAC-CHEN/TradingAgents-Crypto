import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Database,
  Download,
  FileJson,
  FileText,
  Fingerprint,
  ListTree,
  ScrollText,
  Target,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";

import { api } from "../api";
import type { AnalysisRun, Artifact } from "../types";

const GROUPS = [
  { id: "decision", label: "最终建议", icon: Target },
  { id: "reports", label: "分析报告", icon: FileText },
  { id: "evidence", label: "证据包", icon: Database },
  { id: "logs", label: "运行日志", icon: ScrollText },
] as const;

type GroupId = (typeof GROUPS)[number]["id"];

function belongsToGroup(groupId: GroupId, artifact: Artifact): boolean {
  if (groupId === "decision") {
    return artifact.kind === "decision_summary"
      || artifact.relative_path === "reports/5_portfolio/decision.md";
  }
  if (groupId === "reports") return ["final_report", "report"].includes(artifact.kind) && artifact.relative_path !== "reports/5_portfolio/decision.md";
  if (groupId === "evidence") return ["evidence_manifest", "evidence_report", "evidence_raw"].includes(artifact.kind);
  return artifact.kind === "run_log";
}

const SECTION_LABELS: Record<string, string> = {
  market: "市场",
  news: "新闻",
  fundamentals: "基本面",
};

function DecisionOverview({ run }: { run: AnalysisRun }) {
  const health = run.evidence_health || { state: "ok", failed_sections: [], provider_issues: [], warnings: [] };
  const degraded = run.status === "degraded" || health.state === "degraded";
  return (
    <section className={`decision-overview panel ${degraded ? "decision-degraded" : ""}`}>
      <div className="decision-signal"><span>最终建议</span><strong>{run.signal || "REVIEW"}</strong></div>
      <div><p className="eyebrow">DECISION FIRST</p><h2>{degraded ? "结论已生成，证据覆盖存在缺口" : "统一投资结论已经生成"}</h2><p>{degraded ? "请结合失败分区和来源告警审阅建议，必要时在数据恢复后重新运行。" : "先查看组合经理的最终建议，再按需展开分析师、研究、交易和风险报告。"}</p></div>
      <div className="decision-health"><span>证据状态</span><strong>{degraded ? "降级" : "完整"}</strong>{health.failed_sections?.length ? <small>失败分区：{health.failed_sections.map((item) => SECTION_LABELS[item] || item).join("、")}</small> : null}</div>
    </section>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function JsonViewer({ content }: { content: string }) {
  let formatted = content;
  try {
    formatted = JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    // 保留原始内容，便于定位损坏的证据文件。
  }
  return <pre className="code-viewer">{formatted}</pre>;
}

function LogViewer({ content }: { content: string }) {
  const rows = content.split("\n");
  return <div className="artifact-log">{rows.filter(Boolean).map((line, index) => {
    let item: Record<string, unknown> | null = null;
    try { item = JSON.parse(line) as Record<string, unknown>; } catch { /* 显示原始日志行。 */ }
    return <article key={`${index}-${line.slice(0, 16)}`}><b>{String(item?.event_type || "log")}</b><span>{item ? JSON.stringify(item.payload) : line}</span></article>;
  })}</div>;
}

function EvidenceSummary({ artifact, content }: { artifact: Artifact; content: string }) {
  let manifest: Record<string, unknown> = {};
  try { manifest = JSON.parse(content) as Record<string, unknown>; } catch { return <JsonViewer content={content} />; }
  const providers = Array.isArray(manifest.providers) ? manifest.providers.map((item) => {
    if (typeof item === "string") return { name: item, state: "unknown", detail: "" };
    if (!item || typeof item !== "object") return { name: "未知来源", state: "unknown", detail: "" };
    const value = item as Record<string, unknown>;
    return {
      name: String(value.provider || value.name || "未知来源"),
      state: String(value.state || "unknown"),
      detail: String(value.detail || ""),
    };
  }) : [];
  const warnings = Array.isArray(manifest.warnings) ? manifest.warnings.map(String) : [];
  const sections = manifest.sections && typeof manifest.sections === "object" ? Object.entries(manifest.sections as Record<string, Record<string, unknown>>) : [];
  return (
    <div className="evidence-summary">
      <div className="evidence-metrics"><article><ListTree /><span>证据分区</span><strong>{sections.length}</strong></article><article><Database /><span>数据来源</span><strong>{providers.length}</strong></article><article><AlertTriangle /><span>降级告警</span><strong>{warnings.length}</strong></article></div>
      <section><h3>分区状态</h3><div className="coverage-list">{sections.map(([name, value]) => {
        const state = String(value.state || value.status || "unknown");
        const healthy = state === "ok";
        return <div className={healthy ? "coverage-ok" : "coverage-error"} key={name}>{healthy ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}<span><strong>{SECTION_LABELS[name] || name}</strong><small>{healthy ? "可用" : state === "error" ? "失败" : state}</small></span></div>;
      })}</div></section>
      <section><h3>参与来源</h3><div className="provider-pills">{providers.length ? providers.map((provider, index) => <span className={`provider-${provider.state}`} title={provider.detail || undefined} key={`${provider.name}-${index}`}>{provider.name}<small>{provider.state}</small></span>) : <em>清单没有记录来源</em>}</div></section>
      {warnings.length ? <section className="evidence-warnings"><h3>采集告警</h3>{warnings.map((warning) => <p key={warning}><AlertTriangle size={14} />{warning}</p>)}</section> : null}
      <section><h3>原始清单</h3><JsonViewer content={content} /></section>
      <div className="hash-row"><Fingerprint size={14} /><code>{artifact.sha256}</code></div>
    </div>
  );
}

export function ArtifactContent({ artifact, content }: { artifact: Artifact; content: string }) {
  if (artifact.kind === "evidence_manifest") return <EvidenceSummary artifact={artifact} content={content} />;
  if (artifact.kind === "run_log") return <LogViewer content={content} />;
  if (artifact.media_type === "application/json" || artifact.media_type === "application/x-ndjson") return <JsonViewer content={content} />;
  if (artifact.media_type === "text/markdown") {
    return <article className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
      a: ({ href, children, ...props }) => {
        const external = href?.startsWith("http://") || href?.startsWith("https://");
        return <a href={href} target={external ? "_blank" : undefined} rel={external ? "noopener noreferrer nofollow" : undefined} {...props}>{children}</a>;
      },
    }}>{content}</ReactMarkdown></article>;
  }
  return <pre className="code-viewer">{content}</pre>;
}

export function ReportsPage() {
  const { runId = "" } = useParams();
  const runQuery = useQuery({ queryKey: ["run", runId], queryFn: () => api.getRun(runId) });
  const artifactsQuery = useQuery({ queryKey: ["artifacts", runId], queryFn: () => api.listArtifacts(runId) });
  const [groupId, setGroupId] = useState<GroupId>("decision");
  const [selectedId, setSelectedId] = useState<string>("");
  const artifacts = artifactsQuery.data?.items ?? [];
  const activeGroup = GROUPS.find((group) => group.id === groupId) ?? GROUPS[0];
  const visible = useMemo(() => artifacts.filter((item) => belongsToGroup(activeGroup.id, item)), [activeGroup.id, artifacts]);
  const selected = visible.find((item) => item.artifact_id === selectedId) ?? visible[0];
  const contentQuery = useQuery({
    queryKey: ["artifact", selected?.artifact_id],
    queryFn: () => api.getArtifact(selected!.artifact_id),
    enabled: Boolean(selected),
  });

  useEffect(() => {
    if (!visible.length && artifacts.length) {
      const fallback = GROUPS.find((group) => artifacts.some((item) => belongsToGroup(group.id, item)));
      if (fallback && fallback.id !== groupId) setGroupId(fallback.id);
      return;
    }
    if (visible.length && !visible.some((item) => item.artifact_id === selectedId)) {
      const preferred = visible.find((item) => item.kind === "final_report") ?? visible[0];
      setSelectedId(preferred.artifact_id);
    }
  }, [artifacts, groupId, selectedId, visible]);

  if (runQuery.isLoading || artifactsQuery.isLoading) return <div className="loading-block">正在建立报告索引…</div>;
  if (runQuery.isError || artifactsQuery.isError || !runQuery.data) return <div className="error-banner">无法读取报告：{runQuery.error?.message || artifactsQuery.error?.message}</div>;

  return (
    <div className="page-stack reports-page">
      <Link className="back-link" to={`/runs/${runId}`}><ArrowLeft size={17} />返回任务详情</Link>
      <header className="page-title-row">
        <div><p className="eyebrow">REPORT CENTER</p><h1>{runQuery.data.request.symbol} 报告中心</h1><p>{artifacts.filter((item) => item.kind !== "partial_report").length} 个正式文件 · 运行草稿已从报告索引隐藏</p></div>
        {selected ? <a className="button button-primary" href={`/api/artifacts/${selected.artifact_id}/download`}><Download size={17} />下载当前文件</a> : null}
      </header>
      <DecisionOverview run={runQuery.data} />
      <nav className="report-tabs">
        {GROUPS.map(({ id, label, icon: Icon }) => {
          const count = artifacts.filter((item) => belongsToGroup(id, item)).length;
          return <button key={id} className={id === groupId ? "active" : ""} onClick={() => setGroupId(id)}><Icon size={17} /><span>{label}</span><b>{count}</b></button>;
        })}
      </nav>
      <div className="report-layout">
        <aside className="panel artifact-index">
          <div className="artifact-index-head"><strong>{activeGroup.label}</strong><span>{visible.length} 个文件</span></div>
          {visible.length ? visible.map((item) => <button key={item.artifact_id} className={item.artifact_id === selected?.artifact_id ? "active" : ""} onClick={() => setSelectedId(item.artifact_id)}>{item.media_type.includes("json") ? <FileJson size={16} /> : <FileText size={16} />}<span><strong>{item.label}</strong><small>{formatBytes(item.size_bytes)} · {item.relative_path}</small></span></button>) : <div className="artifact-empty">这个分类还没有文件。</div>}
        </aside>
        <section className="panel artifact-viewer">
          {selected ? <><header><div><p className="eyebrow">{selected.kind.replaceAll("_", " ")}</p><h2>{selected.label}</h2><span>{selected.relative_path}</span></div><div className="artifact-sha"><Fingerprint size={14} /><span>SHA-256</span><code>{selected.sha256.slice(0, 16)}…</code></div></header>{contentQuery.isLoading ? <div className="loading-block">正在读取文件…</div> : null}{contentQuery.isError ? <div className="error-banner">{contentQuery.error.message}</div> : null}{contentQuery.data ? <ArtifactContent artifact={selected} content={contentQuery.data.content} /> : null}</> : <div className="empty-state"><FileText size={28} /><h3>没有可预览的文件</h3></div>}
        </section>
      </div>
    </div>
  );
}
