import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowUpRight, CheckCircle2, Clock3, Plus, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { RunTable } from "../components/RunTable";

export function DashboardPage() {
  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: api.listRuns,
    refetchInterval: 5_000,
  });
  const runs = runsQuery.data?.items ?? [];
  const active = runs.filter((run) => ["preflight", "evidence", "running", "cancel_requested"].includes(run.status));
  const queued = runs.filter((run) => run.status === "queued");
  const succeeded = runs.filter((run) => run.status === "succeeded");
  const attention = runs.filter((run) => ["failed", "interrupted"].includes(run.status));

  return (
    <div className="page-stack">
      <section className="page-hero dashboard-hero">
        <div>
          <p className="eyebrow">LOCAL RESEARCH WORKSPACE</p>
          <h1>让每一次分析都有进度、证据与可追溯报告。</h1>
          <p>从统一入口启动美股或加密资产的多 Agent 研究，运行记录会持续保存在本机。</p>
          <div className="hero-actions">
            <Link className="button button-primary" to="/runs/new"><Plus size={18} />新建分析</Link>
            <Link className="button button-secondary" to="/runs">查看全部任务<ArrowUpRight size={17} /></Link>
          </div>
        </div>
        <div className="hero-orbit" aria-hidden="true">
          <div className="orbit-ring ring-one" />
          <div className="orbit-ring ring-two" />
          <div className="orbit-core">TA</div>
        </div>
      </section>

      <section className="metric-grid" aria-label="任务概览">
        <article className="metric-card"><Activity /><span>正在运行</span><strong>{active.length}</strong><small>单任务队列</small></article>
        <article className="metric-card"><Clock3 /><span>等待队列</span><strong>{queued.length}</strong><small>按创建时间执行</small></article>
        <article className="metric-card"><CheckCircle2 /><span>已完成</span><strong>{succeeded.length}</strong><small>报告可随时浏览</small></article>
        <article className="metric-card"><TriangleAlert /><span>需要处理</span><strong>{attention.length}</strong><small>失败或中断</small></article>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div><p className="eyebrow">RECENT RUNS</p><h2>最近任务</h2></div>
          <Link to="/runs">全部历史</Link>
        </div>
        {runsQuery.isLoading ? <div className="loading-block">正在读取任务…</div> : null}
        {runsQuery.isError ? <div className="error-banner">无法读取任务列表：{runsQuery.error.message}</div> : null}
        {!runsQuery.isLoading && !runsQuery.isError ? <RunTable runs={runs.slice(0, 8)} /> : null}
      </section>
    </div>
  );
}
