import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { RunTable } from "../components/RunTable";

export function RunsPage() {
  const query = useQuery({ queryKey: ["runs"], queryFn: api.listRuns, refetchInterval: 5_000 });
  return (
    <div className="page-stack">
      <header className="page-title-row">
        <div><p className="eyebrow">RUN HISTORY</p><h1>任务历史</h1><p>查看所有排队、运行和已完成的分析。</p></div>
        <Link className="button button-primary" to="/runs/new"><Plus size={18} />新建分析</Link>
      </header>
      <section className="panel">
        {query.isLoading ? <div className="loading-block">正在读取任务…</div> : null}
        {query.isError ? <div className="error-banner">{query.error.message}</div> : null}
        {query.data ? <RunTable runs={query.data.items} /> : null}
      </section>
    </div>
  );
}
