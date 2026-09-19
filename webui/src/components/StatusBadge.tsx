import type { RunStatus } from "../types";
import { STATUS_LABELS } from "../lib/format";

export function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`status-badge status-${status}`}>{STATUS_LABELS[status]}</span>;
}
