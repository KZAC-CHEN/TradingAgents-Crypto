import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Ban,
  Bot,
  BrainCircuit,
  Check,
  Circle,
  Clock3,
  Database,
  FileText,
  LoaderCircle,
  MessageSquareText,
  Play,
  RefreshCw,
  RotateCcw,
  Square,
  Wrench,
} from "lucide-react";
import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { StatusBadge } from "../components/StatusBadge";
import { useRunEvents } from "../hooks/useRunEvents";
import { formatDateTime, formatDuration, STATUS_LABELS } from "../lib/format";
import type { AnalysisRun, RunEvent } from "../types";

const TERMINAL = new Set(["succeeded", "degraded", "cancelled", "failed", "interrupted"]);
const ACTIVE = new Set(["queued", "preflight", "evidence", "running", "cancel_requested"]);

const AGENT_GROUPS = [
  { title: "分析团队", names: ["Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst"] },
  { title: "研究团队", names: ["Bull Researcher", "Bear Researcher", "Research Manager"] },
  { title: "交易", names: ["Trader"] },
  { title: "风险管理", names: ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"] },
  { title: "组合决策", names: ["Portfolio Manager"] },
];

const AGENT_LABELS: Record<string, string> = {
  "Market Analyst": "市场分析师",
  "Sentiment Analyst": "情绪分析师",
  "News Analyst": "新闻分析师",
  "Fundamentals Analyst": "基本面分析师",
  "Bull Researcher": "看多研究员",
  "Bear Researcher": "看空研究员",
  "Research Manager": "研究经理",
  Trader: "交易员",
  "Aggressive Analyst": "激进风险分析师",
  "Neutral Analyst": "中性风险分析师",
  "Conservative Analyst": "保守风险分析师",
  "Portfolio Manager": "组合经理",
};

function agentIcon(status: string) {
  if (status === "completed") return <Check size={13} />;
  if (status === "in_progress") return <LoaderCircle className="spin" size={13} />;
  return <Circle size={10} />;
}

function eventDescription(event: RunEvent): string {
  const name = typeof event.payload.name === "string" ? event.payload.name : "";
  const labels: Record<string, string> = {
    "run.queued": "任务已加入队列",
    "run.claimed": "工作线程已领取任务",
    "run.preflight": "运行配置预检通过",
    "run.preflight_result": "预检结果已生成",
    "evidence.started": "开始建立统一证据包",
    "evidence.completed": "证据包准备完成",
    "run.started": "多 Agent 分析开始",
    "progress.updated": "Agent 状态已更新",
    "llm.started": `${name || "模型"} 开始调用`,
    "llm.completed": `${name || "模型"} 调用完成`,
    "llm.failed": `${name || "模型"} 调用失败`,
    "tool.requested": `${name || "工具"} 等待执行`,
    "tool.started": `${name || "工具"} 开始执行`,
    "tool.completed": `${name || "工具"} 执行完成`,
    "tool.failed": `${name || "工具"} 执行失败`,
    "stats.updated": "调用统计已更新",
    "run.cancel_requested": "已请求在节点边界取消",
    "run.cancelled": "任务已取消",
    "run.interrupted": "任务因服务停止而中断",
    "run.resumed": "任务已重新进入队列",
    "run.failed": "任务执行失败",
    "run.degraded": "分析已完成，但部分证据不可用",
    "run.succeeded": "分析与报告已完成",
  };
  if (event.event_type === "message.created") {
    const content = String(event.payload.content || "");
    return content.length > 140 ? `${content.slice(0, 140)}…` : content || "Agent 生成新消息";
  }
  return labels[event.event_type] || event.event_type;
}

function eventIcon(type: string) {
  if (type.startsWith("llm.")) return <BrainCircuit size={14} />;
  if (type.startsWith("tool.")) return <Wrench size={14} />;
  if (type === "message.created") return <MessageSquareText size={14} />;
  if (type.startsWith("evidence.")) return <Database size={14} />;
  if (type.includes("failed") || type.includes("interrupted")) return <AlertTriangle size={14} />;
  return <Activity size={14} />;
}

function formatSeconds(value?: number): string {
  if (value === undefined) return "—";
  return value < 60 ? `${value.toFixed(1)} 秒` : `${Math.floor(value / 60)} 分 ${Math.round(value % 60)} 秒`;
}

function RunActions({ run }: { run: AnalysisRun }) {
  const queryClient = useQueryClient();
  const config = useQuery({ queryKey: ["config"], queryFn: api.getConfig });
  const cancel = useMutation({
    mutationFn: () => api.cancelRun(config.data!.csrfToken, run.run_id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["run", run.run_id] }),
  });
  const resume = useMutation({
    mutationFn: () => api.resumeRun(config.data!.csrfToken, run.run_id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["run", run.run_id] }),
  });
  const canCancel = ACTIVE.has(run.status) && run.status !== "cancel_requested";
  const canResume = run.checkpoint_available && ["failed", "interrupted", "cancelled"].includes(run.status);
  if (!canCancel && !canResume) return null;
  return (
    <div className="run-actions">
      {canCancel ? <button className="button button-danger" disabled={!config.data || cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? <LoaderCircle className="spin" size={17} /> : <Square size={15} />}取消任务</button> : null}
      {canResume ? <button className="button button-primary" disabled={!config.data || resume.isPending} onClick={() => resume.mutate()}>{resume.isPending ? <LoaderCircle className="spin" size={17} /> : <RotateCcw size={16} />}从 checkpoint 恢复</button> : null}
      {cancel.isError || resume.isError ? <span className="action-error">{cancel.error?.message || resume.error?.message}</span> : null}
    </div>
  );
}

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const query = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId),
    refetchInterval: (current) => TERMINAL.has(current.state.data?.status || "") ? false : 2_000,
  });
  const terminal = TERMINAL.has(query.data?.status || "");
  const runtime = useRunEvents(runId, !terminal);
  const visibleAgents = useMemo(() => {
    const selected = new Set(query.data?.request.analysts || []);
    const selectedNames = new Set([
      selected.has("market") && "Market Analyst",
      selected.has("social") && "Sentiment Analyst",
      selected.has("news") && "News Analyst",
      selected.has("fundamentals") && "Fundamentals Analyst",
    ].filter(Boolean));
    const selectable = ["Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst"];
    return AGENT_GROUPS.map((group) => ({
      ...group,
      names: group.names.filter((name) => !selectable.includes(name) || selectedNames.has(name)),
    }));
  }, [query.data?.request.analysts]);

  if (query.isLoading) return <div className="loading-block">正在读取任务…</div>;
  if (query.isError || !query.data) return <div className="error-banner">无法读取任务：{query.error?.message}</div>;
  const run = query.data;
  const completed = ["succeeded", "degraded"].includes(run.status);
  const percentage = runtime.totalAgents ? Math.round((runtime.completedAgents / runtime.totalAgents) * 100) : completed ? 100 : 0;
  const elapsed = formatDuration(run.started_at, run.finished_at);

  return (
    <div className="page-stack run-detail-page">
      <Link className="back-link" to="/runs"><ArrowLeft size={17} />返回任务历史</Link>
      <header className="run-header panel">
        <div className="run-header-main">
          <div className="run-symbol-icon">{run.request.symbol.slice(0, 2).toUpperCase()}</div>
          <div><p className="eyebrow">RUN #{run.run_id.slice(0, 8)}</p><h1>{run.request.symbol}</h1><span>{run.request.analysis_date} · 第 {run.attempt} 次执行 · {run.request.analysts.length} 位分析师</span></div>
        </div>
        <div className="run-header-side">
          <div className={`connection-state connection-${runtime.connection}`}><i />{runtime.connection === "live" ? "实时连接" : runtime.connection === "reconnecting" ? "正在重连" : terminal ? "事件已同步" : "正在连接"}</div>
          <StatusBadge status={run.status} />
          {terminal ? <Link className="button button-secondary-light" to={`/runs/${run.run_id}/reports`}><FileText size={16} />浏览报告与证据</Link> : null}
          <RunActions run={run} />
        </div>
      </header>

      {run.status === "queued" ? <section className="queue-banner"><Clock3 size={18} /><div><strong>任务正在排队</strong><span>{run.queue_position ? `前面还有 ${Math.max(0, run.queue_position - 1)} 个任务` : "等待工作线程领取"}</span></div></section> : null}
      {run.status === "cancel_requested" ? <section className="queue-banner neutral"><Ban size={18} /><div><strong>取消请求已发送</strong><span>任务将在当前 LangGraph 节点结束后安全停止。</span></div></section> : null}
      {run.status === "degraded" ? <section className="degraded-banner"><AlertTriangle size={21} /><div><strong>分析已降级完成 · 最终建议 {run.signal || "REVIEW"}</strong><p>部分关键证据或数据来源不可用，请先查看证据覆盖和告警，再采用报告结论。</p>{run.evidence_health.failed_sections.length ? <span>失败分区：{run.evidence_health.failed_sections.join("、")}</span> : null}</div></section> : null}
      {run.error ? <section className="failure-banner"><AlertTriangle size={21} /><div><strong>{run.status === "interrupted" ? "任务被中断" : "任务执行失败"}</strong><p>{run.error}</p>{run.checkpoint_available ? <span>已检测到 checkpoint，可以手动恢复。</span> : null}</div></section> : null}

      <section className="run-overview-grid">
        <article className="progress-card panel">
          <div className="progress-card-head"><div><p className="eyebrow">CURRENT STAGE</p><h2>{STATUS_LABELS[run.stage]}</h2></div><strong>{percentage}%</strong></div>
          <div className="progress-track"><span style={{ width: `${percentage}%` }} /></div>
          <div className="progress-meta"><span><Check size={14} />{runtime.completedAgents}/{runtime.totalAgents || "—"} Agent</span><span><Clock3 size={14} />{elapsed}</span></div>
        </article>
        <article className="runtime-stat panel"><BrainCircuit /><span>LLM 调用</span><strong>{runtime.stats.llm_calls}</strong></article>
        <article className="runtime-stat panel"><Wrench /><span>工具调用</span><strong>{runtime.stats.tool_calls}</strong></article>
        <article className="runtime-stat panel"><Play /><span>输入 Token</span><strong>{runtime.stats.tokens_in.toLocaleString()}</strong></article>
        <article className="runtime-stat panel"><FileText /><span>输出 Token</span><strong>{runtime.stats.tokens_out.toLocaleString()}</strong></article>
      </section>

      <div className="run-content-grid">
        <section className="panel timeline-panel">
          <div className="panel-heading"><div><p className="eyebrow">AGENT TIMELINE</p><h2>分析阶段</h2></div><span>{runtime.completedAgents} 个阶段已完成</span></div>
          <div className="agent-groups">
            {visibleAgents.map((group) => (
              <div className="agent-group" key={group.title}>
                <h3>{group.title}</h3>
                {group.names.map((name) => {
                  const status = runtime.agents[name] || (completed ? "completed" : "pending");
                  return <div className={`agent-row agent-${status}`} key={name}><div className="agent-state">{agentIcon(status)}</div><div><strong>{AGENT_LABELS[name] || name}</strong><span>{status === "completed" ? "已完成" : status === "in_progress" ? "正在处理" : "等待中"}</span></div><time>{formatSeconds(runtime.agentDurations[name])}</time></div>;
                })}
              </div>
            ))}
          </div>
        </section>

        <section className="panel event-panel">
          <div className="panel-heading"><div><p className="eyebrow">LIVE ACTIVITY</p><h2>运行日志</h2></div><button className="small-icon-button" onClick={() => void query.refetch()} aria-label="刷新任务详情"><RefreshCw size={15} /></button></div>
          <div className="event-list">
            {runtime.events.length ? runtime.events.slice().reverse().map((event) => <article className={`event-row event-${event.event_type.replaceAll(".", "-")}`} key={event.event_id}><div className="event-icon">{eventIcon(event.event_type)}</div><div><strong>{eventDescription(event)}</strong><span>{event.event_type} · 事件 #{event.event_id}</span></div><time>{formatDateTime(event.created_at)}</time></article>) : <div className="empty-event"><Bot size={24} /><p>等待运行事件…</p></div>}
          </div>
        </section>
      </div>
    </div>
  );
}
