"""分析报告、证据和日志的受控 artifact 索引。"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from .run_store import RunStore

_ALLOWED_SUFFIXES = {".md", ".json", ".jsonl", ".log", ".txt"}
_MEDIA_TYPES = {
    ".md": "text/markdown",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".log": "text/plain",
    ".txt": "text/plain",
}


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _artifact_id(run_id: str, relative_path: str) -> str:
    return hashlib.sha256(f"{run_id}:{relative_path}".encode()).hexdigest()[:32]


def _classify(relative_path: Path) -> tuple[str, str]:
    """根据受控任务目录结构给文件分组并生成中文标签。"""
    parts = relative_path.parts
    name = relative_path.stem.replace("_", " ")
    if relative_path.as_posix() == "reports/complete_report.md":
        return "final_report", "完整报告"
    if relative_path.as_posix() == "reports/5_portfolio/decision.md":
        return "decision_summary", "最终投资建议"
    if parts and parts[0] == "partial_reports":
        return "partial_report", f"部分报告 · {name}"
    if parts and parts[0] == "reports":
        folder = parts[1] if len(parts) > 1 else ""
        mapping = {
            "1_analysts": "分析师报告",
            "2_research": "研究团队报告",
            "3_trading": "交易计划",
            "4_risk": "风险报告",
            "5_portfolio": "组合决策",
        }
        return "report", f"{mapping.get(folder, '报告')} · {name}"
    if "crypto_evidence" in parts:
        if relative_path.name == "manifest.json":
            return "evidence_manifest", "统一加密证据清单"
        if relative_path.suffix == ".json":
            return "evidence_raw", f"原始证据 · {name}"
        return "evidence_report", f"证据报告 · {name}"
    if relative_path.name == "run_events.jsonl":
        return "run_log", "运行事件日志"
    return "other", name


def index_run_artifacts(store: RunStore, run: dict[str, Any]) -> list[dict[str, Any]]:
    """扫描服务生成的任务目录，并登记允许浏览的文本文件。"""
    root = Path(run["artifact_root"]).resolve()
    if not root.is_dir():
        return []
    indexed = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        resolved = path.resolve()
        if root not in resolved.parents:
            continue
        relative = resolved.relative_to(root)
        relative_text = relative.as_posix()
        kind, label = _classify(relative)
        media_type = _MEDIA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "text/plain"
        indexed.append(
            store.register_artifact(
                _artifact_id(str(run["run_id"]), relative_text),
                str(run["run_id"]),
                kind=kind,
                label=label,
                relative_path=relative_text,
                media_type=media_type,
                size_bytes=resolved.stat().st_size,
                sha256=_digest(resolved),
            )
        )
    return indexed


def resolve_artifact_path(store: RunStore, artifact: dict[str, Any]) -> Path:
    """只把数据库登记的相对路径解析到对应任务根目录内。"""
    run = store.get_run(str(artifact["run_id"]))
    root = Path(run["artifact_root"]).resolve()
    candidate = (root / str(artifact["relative_path"])).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise FileNotFoundError(str(artifact["artifact_id"]))
    return candidate
