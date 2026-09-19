import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Clock3 } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { StatusBadge } from "../components/StatusBadge";

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const query = useQuery({ queryKey: ["run", runId], queryFn: () => api.getRun(runId), refetchInterval: 3_000 });
  if (query.isLoading) return <div className="loading-block">正在读取任务…</div>;
  if (query.isError || !query.data) return <div className="error-banner">无法读取任务：{query.error?.message}</div>;
  const run = query.data;
  return (
    <div className="page-stack">
      <Link className="back-link" to="/runs"><ArrowLeft size={17} />返回任务历史</Link>
      <header className="page-title-row">
        <div><p className="eyebrow">RUN #{run.run_id.slice(0, 8)}</p><h1>{run.request.symbol}</h1><p>{run.request.analysis_date} · {run.request.analysts.length} 位分析师</p></div>
        <StatusBadge status={run.status} />
      </header>
      <section className="panel coming-panel"><Clock3 size={28} /><h2>任务已经进入运行中心</h2><p>实时 Agent 时间线、Token 统计和取消/恢复操作将在下一阶段接入此页面。</p>{run.queue_position ? <span>当前队列位置：{run.queue_position}</span> : null}</section>
    </div>
  );
}
