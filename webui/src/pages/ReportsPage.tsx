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
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";

import { api } from "../api";
import type { Artifact } from "../types";

const GROUPS = [
  { id: "reports", label: "分析报告", icon: FileText, kinds: ["final_report", "report", "partial_report"] },
  { id: "evidence", label: "证据包", icon: Database, kinds: ["evidence_manifest", "evidence_report", "evidence_raw"] },
  { id: "logs", label: "运行日志", icon: ScrollText, kinds: ["run_log"] },
] as const;

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
  const providers = Array.isArray(manifest.providers) ? manifest.providers.map(String) : [];
  const warnings = Array.isArray(manifest.warnings) ? manifest.warnings.map(String) : [];
  const sections = manifest.sections && typeof manifest.sections === "object" ? Object.entries(manifest.sections as Record<string, Record<string, unknown>>) : [];
  return (
    <div className="evidence-summary">
      <div className="evidence-metrics"><article><ListTree /><span>证据分区</span><strong>{sections.length}</strong></article><article><Database /><span>数据来源</span><strong>{providers.length}</strong></article><article><AlertTriangle /><span>降级告警</span><strong>{warnings.length}</strong></article></div>
      <section><h3>来源覆盖</h3><div className="coverage-list">{sections.map(([name, value]) => <div key={name}><CheckCircle2 size={16} /><span><strong>{name}</strong><small>{String(value.status || "available")}</small></span></div>)}</div></section>
      <section><h3>参与来源</h3><div className="provider-pills">{providers.length ? providers.map((provider) => <span key={provider}>{provider}</span>) : <em>清单没有记录来源</em>}</div></section>
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
  const [groupId, setGroupId] = useState("reports");
  const [selectedId, setSelectedId] = useState<string>("");
  const artifacts = artifactsQuery.data?.items ?? [];
  const activeGroup = GROUPS.find((group) => group.id === groupId) ?? GROUPS[0];
  const visible = useMemo(() => artifacts.filter((item) => (activeGroup.kinds as readonly string[]).includes(item.kind)), [activeGroup, artifacts]);
  const selected = artifacts.find((item) => item.artifact_id === selectedId) ?? visible[0];
  const contentQuery = useQuery({
    queryKey: ["artifact", selected?.artifact_id],
    queryFn: () => api.getArtifact(selected!.artifact_id),
    enabled: Boolean(selected),
  });

  useEffect(() => {
    if (visible.length && !visible.some((item) => item.artifact_id === selectedId)) {
      const preferred = visible.find((item) => item.kind === "final_report") ?? visible[0];
      setSelectedId(preferred.artifact_id);
    }
  }, [selectedId, visible]);

  if (runQuery.isLoading || artifactsQuery.isLoading) return <div className="loading-block">正在建立报告索引…</div>;
  if (runQuery.isError || artifactsQuery.isError || !runQuery.data) return <div className="error-banner">无法读取报告：{runQuery.error?.message || artifactsQuery.error?.message}</div>;

  return (
    <div className="page-stack reports-page">
      <Link className="back-link" to={`/runs/${runId}`}><ArrowLeft size={17} />返回任务详情</Link>
      <header className="page-title-row">
        <div><p className="eyebrow">REPORT CENTER</p><h1>{runQuery.data.request.symbol} 报告中心</h1><p>{artifacts.length} 个已登记文件 · 所有内容来自任务专属目录</p></div>
        {selected ? <a className="button button-primary" href={`/api/artifacts/${selected.artifact_id}/download`}><Download size={17} />下载当前文件</a> : null}
      </header>
      <nav className="report-tabs">
        {GROUPS.map(({ id, label, icon: Icon, kinds }) => {
          const count = artifacts.filter((item) => (kinds as readonly string[]).includes(item.kind)).length;
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
