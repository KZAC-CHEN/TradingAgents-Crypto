"""为单次加密分析运行建立可校验、可复用的统一证据包。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from tradingagents.dataflows.binance import collect_market_snapshot, normalize_binance_symbol
from tradingagents.dataflows.binance_analysis import build_deterministic_market_report
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.crypto_fundamentals import (
    build_crypto_fundamentals_report,
    collect_crypto_fundamentals_snapshot,
)
from tradingagents.dataflows.crypto_news import (
    build_crypto_news_report,
    collect_crypto_news_snapshot,
)
from tradingagents.dataflows.utils import safe_ticker_component

EVIDENCE_SCHEMA_VERSION = "1.0"
EVIDENCE_DIRECTORY_NAME = "crypto_evidence"
_SECTION_ORDER = ("market", "news", "fundamentals")
_SECTION_FILES = {
    "market": ("market_snapshot.json", "market_report.md"),
    "news": ("news_snapshot.json", "news_report.md"),
    "fundamentals": ("fundamentals_snapshot.json", "fundamentals_report.md"),
}
_ANALYST_SECTIONS = {
    "market": "market",
    "news": "news",
    "social": "news",
    "fundamentals": "fundamentals",
}


class CryptoEvidenceError(RuntimeError):
    """证据包缺失、损坏或与当前运行不匹配。"""


def sections_for_analysts(selected_analysts: Iterable[str]) -> tuple[str, ...]:
    """把分析师选择映射为需要预采集的证据分区。"""
    requested = {_ANALYST_SECTIONS[name] for name in selected_analysts if name in _ANALYST_SECTIONS}
    return tuple(section for section in _SECTION_ORDER if section in requested)


def crypto_evidence_directory(
    symbol: str,
    analysis_date: str,
    *,
    results_dir: str | Path | None = None,
) -> Path:
    """返回指定运行的证据包目录，并校验路径组成部分。"""
    canonical = normalize_binance_symbol(symbol)
    normalized_date = _normalize_analysis_date(analysis_date)
    root = Path(results_dir or get_config()["results_dir"])
    return root / safe_ticker_component(canonical) / normalized_date / EVIDENCE_DIRECTORY_NAME


def prepare_crypto_evidence_bundle(
    symbol: str,
    analysis_date: str,
    *,
    selected_analysts: Iterable[str],
    results_dir: str | Path | None = None,
    reuse_existing: bool = False,
) -> dict[str, Any]:
    """采集并落盘一次运行所需证据，或在恢复时严格复用原证据。"""
    requested_symbol = str(symbol).strip()
    canonical = normalize_binance_symbol(requested_symbol)
    normalized_date = _normalize_analysis_date(analysis_date)
    requested_sections = sections_for_analysts(selected_analysts)
    bundle_dir = crypto_evidence_directory(
        canonical,
        normalized_date,
        results_dir=results_dir,
    )

    if reuse_existing:
        return validate_crypto_evidence_bundle(
            bundle_dir,
            expected_symbol=canonical,
            expected_analysis_date=normalized_date,
            expected_sections=requested_sections,
        )

    bundle_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_section_files(bundle_dir, requested_sections)
    common_cutoff = datetime.combine(
        date.fromisoformat(normalized_date), time.max, tzinfo=timezone.utc
    ).isoformat()
    collected_at = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "bundle_id": str(uuid4()),
        "requested_symbol": requested_symbol,
        "symbol": canonical,
        "analysis_date": normalized_date,
        "as_of_utc": common_cutoff,
        "collected_at_utc": collected_at,
        "requested_sections": list(requested_sections),
        "sections": {},
        "providers": [],
        "warnings": [],
    }

    collectors: dict[str, tuple[Callable[..., dict[str, Any]], Callable[[dict[str, Any]], str]]] = {
        "market": (
            lambda value, day: collect_market_snapshot(value, as_of=day),
            build_deterministic_market_report,
        ),
        "news": (collect_crypto_news_snapshot, build_crypto_news_report),
        "fundamentals": (
            collect_crypto_fundamentals_snapshot,
            build_crypto_fundamentals_report,
        ),
    }

    for section in requested_sections:
        collector, renderer = collectors[section]
        entry = _collect_section(
            section,
            canonical,
            normalized_date,
            common_cutoff,
            collected_at,
            bundle_dir,
            collector,
            renderer,
        )
        manifest["sections"][section] = entry
        manifest["providers"].extend(entry["providers"])
        manifest["warnings"].extend(entry["warnings"])

    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(bundle_dir / "manifest.json", manifest)
    return validate_crypto_evidence_bundle(
        bundle_dir,
        expected_symbol=canonical,
        expected_analysis_date=normalized_date,
        expected_sections=requested_sections,
    )


def validate_crypto_evidence_bundle(
    bundle_dir: str | Path,
    *,
    expected_symbol: str | None = None,
    expected_analysis_date: str | None = None,
    expected_sections: Iterable[str] | None = None,
) -> dict[str, Any]:
    """校验清单身份、分区集合和所有文件摘要，成功时返回清单。"""
    directory = Path(bundle_dir)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise CryptoEvidenceError(f"恢复运行缺少证据包清单：{manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CryptoEvidenceError(f"证据包清单无法读取：{manifest_path}：{exc}") from exc

    if manifest.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise CryptoEvidenceError("证据包清单版本不受支持。")
    if expected_symbol is not None:
        canonical = normalize_binance_symbol(expected_symbol)
        if manifest.get("symbol") != canonical:
            raise CryptoEvidenceError("证据包交易对与当前运行不匹配。")
    if expected_analysis_date is not None:
        normalized_date = _normalize_analysis_date(expected_analysis_date)
        if manifest.get("analysis_date") != normalized_date:
            raise CryptoEvidenceError("证据包分析日期与当前运行不匹配。")
    if expected_sections is not None:
        normalized_sections = _normalize_sections(expected_sections)
        if tuple(manifest.get("requested_sections") or ()) != normalized_sections:
            raise CryptoEvidenceError("证据包分区与当前分析师选择不匹配。")

    sections = manifest.get("sections")
    if not isinstance(sections, dict):
        raise CryptoEvidenceError("证据包清单缺少有效的 sections。")
    if tuple(sections) != tuple(manifest.get("requested_sections") or ()):
        raise CryptoEvidenceError("证据包清单中的分区记录不完整。")

    for section, entry in sections.items():
        if section not in _SECTION_FILES or not isinstance(entry, dict):
            raise CryptoEvidenceError(f"证据包包含未知或无效分区：{section}")
        for kind in ("snapshot", "report"):
            filename = entry.get(f"{kind}_file")
            expected_digest = entry.get(f"{kind}_sha256")
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise CryptoEvidenceError(f"{section} 分区的 {kind} 文件名无效。")
            path = directory / filename
            if not path.is_file():
                raise CryptoEvidenceError(f"{section} 分区缺少文件：{path}")
            if not isinstance(expected_digest, str) or _sha256(path) != expected_digest:
                raise CryptoEvidenceError(f"{section} 分区的 {kind} 文件摘要校验失败。")
    return manifest


def load_crypto_evidence_report(
    symbol: str,
    analysis_date: str,
    section: str,
    *,
    results_dir: str | Path | None = None,
) -> str | None:
    """优先读取当前运行的已校验证据报告；没有证据包时返回 ``None``。"""
    if section not in _SECTION_FILES:
        raise ValueError(f"未知证据分区：{section}")
    canonical = normalize_binance_symbol(symbol)
    normalized_date = _normalize_analysis_date(analysis_date)
    bundle_dir = crypto_evidence_directory(
        canonical,
        normalized_date,
        results_dir=results_dir,
    )
    if not (bundle_dir / "manifest.json").is_file():
        return None
    manifest = validate_crypto_evidence_bundle(
        bundle_dir,
        expected_symbol=canonical,
        expected_analysis_date=normalized_date,
    )
    entry = manifest["sections"].get(section)
    if entry is None:
        return None
    try:
        return (bundle_dir / entry["report_file"]).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CryptoEvidenceError(f"无法读取 {section} 证据报告：{exc}") from exc


def _collect_section(
    section: str,
    symbol: str,
    analysis_date: str,
    common_cutoff: str,
    collected_at: str,
    bundle_dir: Path,
    collector: Callable[..., dict[str, Any]],
    renderer: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    """采集单个分区，并把失败固化为可审计的错误证据。"""
    snapshot: dict[str, Any] | None = None
    state = "ok"
    error_message = ""
    try:
        snapshot = collector(symbol, analysis_date)
        if snapshot.get("symbol") != symbol:
            raise CryptoEvidenceError(f"{section} 快照交易对不一致。")
        if snapshot.get("as_of_utc") != common_cutoff:
            raise CryptoEvidenceError(f"{section} 快照截止时间不一致。")
        report = renderer(snapshot)
    except Exception as exc:
        state = "error"
        error_message = f"{type(exc).__name__}: {exc}"
        if snapshot is None:
            snapshot = _error_snapshot(
                section,
                symbol,
                common_cutoff,
                collected_at,
                error_message,
            )
        report = _error_report(section, symbol, common_cutoff, error_message)

    snapshot_name, report_name = _SECTION_FILES[section]
    snapshot_path = bundle_dir / snapshot_name
    report_path = bundle_dir / report_name
    _atomic_write_json(snapshot_path, snapshot)
    _atomic_write_text(report_path, report)
    providers = _section_providers(section, snapshot, state, error_message)
    warnings = [str(item) for item in snapshot.get("warnings") or []]
    if error_message and error_message not in warnings:
        warnings.append(error_message)
    return {
        "state": state,
        "snapshot_file": snapshot_name,
        "snapshot_sha256": _sha256(snapshot_path),
        "report_file": report_name,
        "report_sha256": _sha256(report_path),
        "providers": providers,
        "warnings": warnings,
        "error": error_message or None,
    }


def _section_providers(
    section: str,
    snapshot: dict[str, Any],
    state: str,
    error_message: str,
) -> list[dict[str, Any]]:
    """统一不同快照的来源覆盖格式，并保留原始来源状态。"""
    if section == "market":
        provider_state = state
        if state == "ok" and snapshot.get("warnings"):
            provider_state = "degraded"
        return [
            {
                "section": section,
                "provider": snapshot.get("provider") or "binance",
                "state": provider_state,
                "detail": error_message,
            }
        ]
    providers: list[dict[str, Any]] = []
    for item in snapshot.get("providers") or []:
        normalized = dict(item)
        normalized["section"] = section
        providers.append(normalized)
    if not providers and state == "error":
        providers.append(
            {
                "section": section,
                "provider": section,
                "state": "error",
                "detail": error_message,
            }
        )
    return providers


def _error_snapshot(
    section: str,
    symbol: str,
    cutoff: str,
    collected_at: str,
    error_message: str,
) -> dict[str, Any]:
    """生成不伪造数据的分区失败快照。"""
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "section": section,
        "symbol": symbol,
        "as_of_utc": cutoff,
        "collected_at_utc": collected_at,
        "providers": [],
        "warnings": [error_message],
        "error": error_message,
    }


def _error_report(section: str, symbol: str, cutoff: str, error_message: str) -> str:
    """把分区失败写成分析代理可直接引用的确定性报告。"""
    names = {"market": "市场", "news": "新闻", "fundamentals": "基本面"}
    return (
        f"# {symbol} 加密{names[section]}证据不可用\n\n"
        f"- 截止时间（UTC）：{cutoff}\n"
        f"- 状态：采集或渲染失败\n"
        f"- 错误：{error_message}\n\n"
        "> 本分区没有可用证据，不得据此补写或猜测数据。\n"
    )


def _remove_stale_section_files(bundle_dir: Path, requested_sections: tuple[str, ...]) -> None:
    """清理本次未请求的固定分区文件，避免人工误读旧文件。"""
    requested = set(requested_sections)
    for section, filenames in _SECTION_FILES.items():
        if section in requested:
            continue
        for filename in filenames:
            (bundle_dir / filename).unlink(missing_ok=True)


def _normalize_sections(sections: Iterable[str]) -> tuple[str, ...]:
    """按固定顺序校验并规范化分区集合。"""
    supplied = tuple(sections)
    unknown = set(supplied) - set(_SECTION_ORDER)
    if unknown:
        raise CryptoEvidenceError(f"证据包包含未知分区：{sorted(unknown)}")
    selected = set(supplied)
    return tuple(section for section in _SECTION_ORDER if section in selected)


def _normalize_analysis_date(value: str) -> str:
    """校验并返回 YYYY-MM-DD 格式的分析日期。"""
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("analysis_date 必须为 YYYY-MM-DD 格式。") from exc


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """使用同目录临时文件原子写入 UTF-8 JSON。"""
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    _atomic_write_text(path, content)


def _atomic_write_text(path: Path, content: str) -> None:
    """使用唯一临时文件原子替换目标文本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    """计算文件内容的 SHA-256 十六进制摘要。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
