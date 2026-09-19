import { ArrowRight, Clock3 } from "lucide-react";
import { Link } from "react-router-dom";

import { detectAssetType, formatDateTime } from "../lib/format";
import type { AnalysisRun } from "../types";
import { StatusBadge } from "./StatusBadge";

export function RunTable({ runs }: { runs: AnalysisRun[] }) {
  if (!runs.length) {
    return (
      <div className="empty-state">
        <div className="empty-icon"><Clock3 size={22} /></div>
        <h3>还没有分析任务</h3>
        <p>创建第一个任务后，实时进度和报告会出现在这里。</p>
        <Link className="button button-primary" to="/runs/new">新建分析</Link>
      </div>
    );
  }
  return (
    <div className="run-table-wrap">
      <table className="run-table">
        <thead>
          <tr><th>标的</th><th>市场</th><th>状态</th><th>分析日期</th><th>创建时间</th><th /></tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.run_id}>
              <td><strong>{run.request.symbol}</strong><small>#{run.run_id.slice(0, 8)}</small></td>
              <td><span className="asset-chip">{detectAssetType(run.request.symbol) === "crypto" ? "加密" : "美股"}</span></td>
              <td><StatusBadge status={run.status} /></td>
              <td>{run.request.analysis_date}</td>
              <td>{formatDateTime(run.created_at)}</td>
              <td><Link className="table-link" to={`/runs/${run.run_id}`} aria-label={`查看 ${run.request.symbol}`}><ArrowRight size={18} /></Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
