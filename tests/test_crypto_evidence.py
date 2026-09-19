"""运行级统一加密证据包的单元测试。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

import tradingagents.dataflows.crypto_evidence as evidence
from tradingagents.dataflows.binance import BinanceEndpoints, collect_market_snapshot

_FROZEN_NOW = datetime(2026, 9, 19, 8, 12, 18, 123456, tzinfo=timezone.utc)
_CURRENT_CUTOFF = "2026-09-19T08:12:18.123000+00:00"


def _install_fake_sources(monkeypatch, calls: dict[str, int]) -> None:
    """安装不访问网络的三个确定性来源。"""

    monkeypatch.setattr(evidence, "_utc_now", lambda: _FROZEN_NOW)

    def market(symbol: str, *, as_of: str):
        calls["market"] += 1
        return {
            "schema_version": 1,
            "provider": "binance",
            "symbol": symbol,
            "as_of_utc": as_of,
            "warnings": [],
        }

    def news(symbol: str, cutoff: str):
        calls["news"] += 1
        return {
            "schema_version": "1.0",
            "symbol": symbol,
            "as_of_utc": cutoff,
            "providers": [{"provider": "CoinDesk RSS", "state": "ok", "item_count": 2}],
            "warnings": [],
        }

    def fundamentals(symbol: str, cutoff: str):
        calls["fundamentals"] += 1
        return {
            "schema_version": "1.0",
            "symbol": symbol,
            "as_of_utc": cutoff,
            "providers": [{"provider": "CoinGecko", "state": "ok", "item_count": 1}],
            "warnings": ["历史供应量为估算值"],
        }

    monkeypatch.setattr(evidence, "collect_market_snapshot", market)
    monkeypatch.setattr(evidence, "collect_crypto_news_snapshot", news)
    monkeypatch.setattr(evidence, "collect_crypto_fundamentals_snapshot", fundamentals)
    monkeypatch.setattr(
        evidence,
        "build_deterministic_market_report",
        lambda snapshot: f"市场报告 {snapshot['symbol']}\n",
    )
    monkeypatch.setattr(
        evidence,
        "build_crypto_news_report",
        lambda snapshot: f"新闻报告 {snapshot['symbol']}\n",
    )
    monkeypatch.setattr(
        evidence,
        "build_crypto_fundamentals_report",
        lambda snapshot: f"基本面报告 {snapshot['symbol']}\n",
    )


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.unit
def test_prepare_bundle_collects_each_requested_section_once(tmp_path, monkeypatch):
    calls = {"market": 0, "news": 0, "fundamentals": 0}
    _install_fake_sources(monkeypatch, calls)

    manifest = evidence.prepare_crypto_evidence_bundle(
        "BTC-USD",
        "2026-09-19",
        selected_analysts=("market", "social", "news", "fundamentals"),
        results_dir=tmp_path,
    )

    assert calls == {"market": 1, "news": 1, "fundamentals": 1}
    assert manifest["symbol"] == "BTCUSDT"
    assert manifest["requested_sections"] == ["market", "news", "fundamentals"]
    assert manifest["as_of_utc"] == _CURRENT_CUTOFF
    assert manifest["warnings"] == ["历史供应量为估算值"]
    bundle_dir = tmp_path / "BTCUSDT" / "2026-09-19" / "crypto_evidence"
    on_disk = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == manifest
    for entry in manifest["sections"].values():
        assert _sha256(bundle_dir / entry["snapshot_file"]) == entry["snapshot_sha256"]
        assert _sha256(bundle_dir / entry["report_file"]) == entry["report_sha256"]


@pytest.mark.unit
def test_social_analyst_reuses_news_section_and_skips_unselected_sources(tmp_path, monkeypatch):
    calls = {"market": 0, "news": 0, "fundamentals": 0}
    _install_fake_sources(monkeypatch, calls)

    manifest = evidence.prepare_crypto_evidence_bundle(
        "ETH/USDT",
        "2026-09-19",
        selected_analysts=("market", "social"),
        results_dir=tmp_path,
    )

    assert calls == {"market": 1, "news": 1, "fundamentals": 0}
    assert manifest["requested_sections"] == ["market", "news"]
    bundle_dir = tmp_path / "ETHUSDT" / "2026-09-19" / "crypto_evidence"
    assert not (bundle_dir / "fundamentals_snapshot.json").exists()


@pytest.mark.unit
def test_resume_validates_and_reuses_bundle_without_collecting(tmp_path, monkeypatch):
    calls = {"market": 0, "news": 0, "fundamentals": 0}
    _install_fake_sources(monkeypatch, calls)
    first = evidence.prepare_crypto_evidence_bundle(
        "BTC",
        "2026-09-19",
        selected_analysts=("market", "news"),
        results_dir=tmp_path,
    )

    monkeypatch.setattr(
        evidence,
        "collect_market_snapshot",
        lambda *args, **kwargs: pytest.fail("恢复运行不应重新采集市场数据"),
    )
    monkeypatch.setattr(
        evidence,
        "collect_crypto_news_snapshot",
        lambda *args, **kwargs: pytest.fail("恢复运行不应重新采集新闻数据"),
    )
    resumed = evidence.prepare_crypto_evidence_bundle(
        "BTCUSDT",
        "2026-09-19",
        selected_analysts=("market", "news"),
        results_dir=tmp_path,
        reuse_existing=True,
    )

    assert resumed["bundle_id"] == first["bundle_id"]
    assert (
        evidence.load_crypto_evidence_report(
            "BTC-USD", "2026-09-19", "market", results_dir=tmp_path
        )
        == "市场报告 BTCUSDT\n"
    )


@pytest.mark.unit
def test_resume_rejects_tampered_evidence(tmp_path, monkeypatch):
    calls = {"market": 0, "news": 0, "fundamentals": 0}
    _install_fake_sources(monkeypatch, calls)
    evidence.prepare_crypto_evidence_bundle(
        "BTC",
        "2026-09-19",
        selected_analysts=("market",),
        results_dir=tmp_path,
    )
    report = tmp_path / "BTCUSDT" / "2026-09-19" / "crypto_evidence" / "market_report.md"
    report.write_text("被修改的报告\n", encoding="utf-8")

    with pytest.raises(evidence.CryptoEvidenceError, match="摘要校验失败"):
        evidence.prepare_crypto_evidence_bundle(
            "BTC",
            "2026-09-19",
            selected_analysts=("market",),
            results_dir=tmp_path,
            reuse_existing=True,
        )


@pytest.mark.unit
def test_section_failure_is_persisted_without_aborting_other_sections(tmp_path, monkeypatch):
    calls = {"market": 0, "news": 0, "fundamentals": 0}
    _install_fake_sources(monkeypatch, calls)

    def broken_news(symbol: str, day: str):
        calls["news"] += 1
        raise RuntimeError("新闻网关不可用")

    monkeypatch.setattr(evidence, "collect_crypto_news_snapshot", broken_news)
    manifest = evidence.prepare_crypto_evidence_bundle(
        "BTC",
        "2026-09-19",
        selected_analysts=("market", "news", "fundamentals"),
        results_dir=tmp_path,
    )

    assert manifest["sections"]["market"]["state"] == "ok"
    assert manifest["sections"]["news"]["state"] == "error"
    assert manifest["sections"]["fundamentals"]["state"] == "ok"
    report = evidence.load_crypto_evidence_report("BTC", "2026-09-19", "news", results_dir=tmp_path)
    assert "新闻证据不可用" in report
    assert "新闻网关不可用" in report


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.text = ""

    def json(self):
        return self.payload


class _FakeSession:
    def __init__(self, candle_rows):
        self.candle_rows = candle_rows

    def get(self, url, params, timeout):
        if url.endswith("/klines"):
            return _FakeResponse(self.candle_rows)
        return _FakeResponse([])


def _kline(open_ms: int, close_ms: int, close: str) -> list[object]:
    return [
        open_ms,
        "100",
        "105",
        "99",
        close,
        "12",
        close_ms,
        "1200",
        20,
        "6",
        "600",
    ]


@pytest.mark.unit
def test_bundle_accepts_real_market_collector_millisecond_contract(tmp_path, monkeypatch):
    """真实市场采集器的毫秒截止时间应通过证据包校验并排除未收盘 K 线。"""
    monkeypatch.setattr(evidence, "_utc_now", lambda: _FROZEN_NOW)
    cutoff_ms = int(_FROZEN_NOW.timestamp() * 1000)
    session = _FakeSession(
        [
            _kline(cutoff_ms - 10_000, cutoff_ms - 1_000, "102"),
            _kline(cutoff_ms - 1_000, cutoff_ms + 1_000, "999"),
        ]
    )
    endpoints = BinanceEndpoints(spot="https://spot.test", futures="https://future.test")

    def market(symbol: str, *, as_of: str):
        return collect_market_snapshot(
            symbol,
            as_of=as_of,
            session=session,
            endpoints=endpoints,
        )

    monkeypatch.setattr(evidence, "collect_market_snapshot", market)
    monkeypatch.setattr(
        evidence,
        "build_deterministic_market_report",
        lambda snapshot: f"市场报告 {snapshot['symbol']}\n",
    )

    manifest = evidence.prepare_crypto_evidence_bundle(
        "BTC-USD",
        "2026-09-19",
        selected_analysts=("market",),
        results_dir=tmp_path,
    )

    assert manifest["sections"]["market"]["state"] == "ok"
    snapshot_path = tmp_path / "BTCUSDT" / "2026-09-19" / "crypto_evidence" / "market_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["as_of_utc"] == _CURRENT_CUTOFF
    assert [row["Close"] for row in snapshot["timeframes"]["4h"]["spot"]["candles"]] == [102.0]


@pytest.mark.unit
def test_cutoff_comparison_uses_utc_millisecond_precision():
    assert evidence._same_cutoff(
        "2026-09-18T23:59:59.999999+00:00",
        "2026-09-18T23:59:59.999000+00:00",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("module_name", "entrypoint", "collector_name", "section"),
    [
        (
            "tradingagents.agents.utils.crypto_market_tools",
            "_get_or_build_report",
            "collect_market_snapshot",
            "market",
        ),
        (
            "tradingagents.agents.utils.crypto_news_tools",
            "get_crypto_news_report_text",
            "collect_crypto_news_snapshot",
            "news",
        ),
        (
            "tradingagents.agents.utils.crypto_fundamentals_tools",
            "get_crypto_fundamentals_report_text",
            "collect_crypto_fundamentals_snapshot",
            "fundamentals",
        ),
    ],
)
def test_agent_tools_prefer_run_evidence(
    monkeypatch,
    module_name,
    entrypoint,
    collector_name,
    section,
):
    module = __import__(module_name, fromlist=[entrypoint])
    monkeypatch.setattr(
        module,
        "load_crypto_evidence_report",
        lambda symbol, day, requested_section: f"证据包报告:{requested_section}",
    )
    monkeypatch.setattr(
        module,
        collector_name,
        lambda *args, **kwargs: pytest.fail("存在证据包时不应访问来源"),
    )

    result = getattr(module, entrypoint)("BTC-USD", "2026-09-19")

    assert result == f"证据包报告:{section}"
