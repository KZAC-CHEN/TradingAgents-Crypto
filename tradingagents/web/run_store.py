"""Web 分析任务、事件与产物的 SQLite 持久化存储。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    """返回稳定的 UTC ISO 时间。"""
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """使用短连接管理本地任务数据库，允许 API 与工作线程并发访问。"""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    artifact_root TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    checkpoint_available INTEGER NOT NULL DEFAULT 0,
                    signal TEXT,
                    evidence_health_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_runs_created_at
                    ON runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_status
                    ON runs(status, created_at);

                CREATE TABLE IF NOT EXISTS run_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    attempt INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_run_events_lookup
                    ON run_events(run_id, event_id);

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, relative_path)
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_run
                    ON artifacts(run_id, kind, label);
                """
            )
            run_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "signal" not in run_columns:
                connection.execute("ALTER TABLE runs ADD COLUMN signal TEXT")
            if "evidence_health_json" not in run_columns:
                connection.execute(
                    "ALTER TABLE runs ADD COLUMN evidence_health_json TEXT NOT NULL DEFAULT '{}'"
                )
            artifact_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()
            }
            if "sha256" not in artifact_columns:
                connection.execute(
                    "ALTER TABLE artifacts ADD COLUMN sha256 TEXT NOT NULL DEFAULT ''"
                )

    def create_run(
        self,
        run_id: str,
        *,
        request: dict[str, Any],
        config: dict[str, Any],
        artifact_root: str | Path,
        status: str = "queued",
        stage: str = "queued",
    ) -> dict[str, Any]:
        """创建任务并返回规范化记录。"""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, status, stage, request_json, config_json, artifact_root,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    status,
                    stage,
                    json.dumps(request, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(config, ensure_ascii=False, separators=(",", ":")),
                    str(Path(artifact_root).resolve()),
                    now,
                    now,
                ),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        """读取单个任务；不存在时抛出 ``KeyError``。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._decode_run(row)

    def list_runs(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """按创建时间倒序列出任务。"""
        bounded_limit = max(1, min(int(limit), 200))
        bounded_offset = max(0, int(offset))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (bounded_limit, bounded_offset),
            ).fetchall()
        return [self._decode_run(row) for row in rows]

    def update_run(self, run_id: str, **changes: Any) -> dict[str, Any]:
        """更新允许变更的任务字段。"""
        allowed = {
            "status",
            "stage",
            "attempt",
            "checkpoint_available",
            "signal",
            "evidence_health",
            "error",
            "started_at",
            "finished_at",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"不允许更新任务字段：{sorted(unknown)}")
        if not changes:
            return self.get_run(run_id)
        normalized = dict(changes)
        if "checkpoint_available" in normalized:
            normalized["checkpoint_available"] = int(bool(normalized["checkpoint_available"]))
        if "evidence_health" in normalized:
            normalized["evidence_health_json"] = json.dumps(
                normalized.pop("evidence_health"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        normalized["updated_at"] = _utc_now()
        assignments = ", ".join(f"{name} = ?" for name in normalized)
        values = [normalized[name] for name in normalized]
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ?",  # noqa: S608
                (*values, run_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(run_id)
        return self.get_run(run_id)

    def claim_next_queued(self) -> dict[str, Any] | None:
        """原子领取创建时间最早的排队任务。"""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT run_id FROM runs
                WHERE status = 'queued'
                ORDER BY created_at ASC, run_id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            run_id = str(row["run_id"])
            connection.execute(
                """
                UPDATE runs
                SET status = 'preflight', stage = 'preflight', started_at = ?,
                    finished_at = NULL, error = NULL, updated_at = ?
                WHERE run_id = ? AND status = 'queued'
                """,
                (now, now, run_id),
            )
        return self.get_run(run_id)

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        """取消排队任务，或请求运行中的任务在节点边界停止。"""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            status = str(row["status"])
            if status == "queued":
                connection.execute(
                    """
                    UPDATE runs SET status = 'cancelled', stage = 'cancelled',
                        finished_at = ?, updated_at = ? WHERE run_id = ?
                    """,
                    (now, now, run_id),
                )
            elif status in {"preflight", "evidence", "running"}:
                connection.execute(
                    """
                    UPDATE runs SET status = 'cancel_requested', updated_at = ?
                    WHERE run_id = ?
                    """,
                    (now, run_id),
                )
            elif status != "cancel_requested":
                raise ValueError(f"状态为 {status} 的任务不能取消。")
        return self.get_run(run_id)

    def resume_run(self, run_id: str) -> dict[str, Any]:
        """把有 checkpoint 的失败、中断或取消任务重新放回队列。"""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, checkpoint_available, attempt FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            status = str(row["status"])
            if status not in {"failed", "interrupted", "cancelled"}:
                raise ValueError(f"状态为 {status} 的任务不能恢复。")
            if not bool(row["checkpoint_available"]):
                raise ValueError("任务没有可用 checkpoint，无法恢复。")
            connection.execute(
                """
                UPDATE runs
                SET status = 'queued', stage = 'queued', attempt = ?, error = NULL,
                    signal = NULL, evidence_health_json = '{}',
                    started_at = NULL, finished_at = NULL, updated_at = ?
                WHERE run_id = ?
                """,
                (int(row["attempt"]) + 1, now, run_id),
            )
        return self.get_run(run_id)

    def queue_position(self, run_id: str) -> int | None:
        """返回排队任务的一基位置；非排队任务返回 ``None``。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, created_at FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] != "queued":
                return None
            count = connection.execute(
                """
                SELECT COUNT(*) FROM runs
                WHERE status = 'queued'
                  AND (created_at < ? OR (created_at = ? AND run_id <= ?))
                """,
                (row["created_at"], row["created_at"], run_id),
            ).fetchone()[0]
        return int(count)

    def mark_active_runs_interrupted(self) -> int:
        """服务重启时把无法继续执行的进程内任务标记为中断。"""
        active = ("preflight", "evidence", "running", "cancel_requested")
        placeholders = ",".join("?" for _ in active)
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE runs
                SET status = 'interrupted', stage = 'interrupted',
                    error = COALESCE(error, 'Web 服务在任务运行期间停止。'),
                    finished_at = ?, updated_at = ?
                WHERE status IN ({placeholders})
                """,  # noqa: S608
                (now, now, *active),
            )
        return cursor.rowcount

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        """追加事件并返回包含数据库事件 ID 的记录。"""
        if attempt is None:
            attempt = int(self.get_run(run_id)["attempt"])
        created_at = _utc_now()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO run_events (
                    run_id, attempt, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, attempt, event_type, encoded, created_at),
            )
            event_id = int(cursor.lastrowid)
        return {
            "event_id": event_id,
            "run_id": run_id,
            "attempt": attempt,
            "event_type": event_type,
            "payload": payload,
            "created_at": created_at,
        }

    def list_events(
        self,
        run_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """按事件 ID 正序读取任务事件。"""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM run_events
                WHERE run_id = ? AND event_id > ?
                ORDER BY event_id ASC LIMIT ?
                """,
                (run_id, max(0, int(after_event_id)), max(1, min(int(limit), 2000))),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "run_id": row["run_id"],
                "attempt": row["attempt"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def register_artifact(
        self,
        artifact_id: str,
        run_id: str,
        *,
        kind: str,
        label: str,
        relative_path: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
    ) -> dict[str, Any]:
        """登记任务目录中的一个文件，并更新已存在的同路径记录。"""
        created_at = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, run_id, kind, label, relative_path, media_type,
                    size_bytes, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, relative_path) DO UPDATE SET
                    kind = excluded.kind,
                    label = excluded.label,
                    media_type = excluded.media_type,
                    size_bytes = excluded.size_bytes,
                    sha256 = excluded.sha256
                """,
                (
                    artifact_id,
                    run_id,
                    kind,
                    label,
                    relative_path,
                    media_type,
                    max(0, int(size_bytes)),
                    sha256,
                    created_at,
                ),
            )
        return self.get_artifact_by_path(run_id, relative_path)

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        """按不可猜测的登记 ID 读取 artifact 元数据。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return dict(row)

    def get_artifact_by_path(self, run_id: str, relative_path: str) -> dict[str, Any]:
        """按任务和规范相对路径读取 artifact 元数据。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? AND relative_path = ?",
                (run_id, relative_path),
            ).fetchone()
        if row is None:
            raise KeyError((run_id, relative_path))
        return dict(row)

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        """按类型与标签列出任务的全部登记文件。"""
        self.get_run(run_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM artifacts WHERE run_id = ?
                ORDER BY kind ASC, label ASC, relative_path ASC
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _decode_run(row: sqlite3.Row) -> dict[str, Any]:
        """把数据库行转换为 API 可序列化结构。"""
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["config"] = json.loads(result.pop("config_json"))
        result["evidence_health"] = json.loads(result.pop("evidence_health_json") or "{}")
        result["checkpoint_available"] = bool(result["checkpoint_available"])
        return result
