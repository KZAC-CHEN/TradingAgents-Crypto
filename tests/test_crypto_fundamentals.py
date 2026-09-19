"""统一加密基本面快照的单元测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tradingagents.dataflows.crypto_fundamentals import (
    CryptoFundamentalsError,
    build_crypto_fundamentals_report,
    calculate_fundamental_ratios,
    collect_crypto_fundamentals_snapshot,
    parse_coingecko_payload,
    parse_defillama_tvl,
    parse_project_releases,
    save_crypto_fundamentals_snapshot,
)
from tradingagents.dataflows.crypto_news import CryptoNewsItem
from tradingagents.dataflows.crypto_project_sources import get_project_profile


class FakeResponse:
    """提供基本面采集器需要的最小 HTTP 响应接口。"""

    def __init__(self, *, payload=None, content=b"", status_code=200):
        self._payload = payload
        self.content = content
        self.status_code = status_code

    def json(self):
        """返回预置 JSON。"""
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class RecordingSession:
    """按 URL 返回固定响应，并记录所有请求。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        """记录请求并返回第一个匹配后缀的响应。"""
        self.calls.append((url, kwargs))
        for marker, response in self.responses.items():
            if marker in url:
                return response
        raise AssertionError(f"没有为请求配置响应：{url}")


def _historical_payload():
    """构造最小 CoinGecko 历史响应。"""
    return {
        "id": "bitcoin",
        "symbol": "btc",
        "name": "Bitcoin",
        "market_data": {
            "current_price": {"usd": 100.0},
            "market_cap": {"usd": 500.0},
            "total_volume": {"usd": 20.0},
        },
    }


def _release_feed():
    """构造包含截止日前后发布的 Atom 订阅。"""
    return b"""<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>v2.0</title><updated>2025-01-09T00:00:00Z</updated>
        <link href="https://example.com/v2" /></entry>
      <entry><title>v1.0</title><updated>2025-01-07T00:00:00Z</updated>
        <link href="https://example.com/v1" /></entry>
    </feed>"""


@pytest.mark.unit
def test_project_profiles_expose_market_and_onchain_identifiers():
    """项目登记表应成为跨来源身份映射的单一事实来源。"""
    bitcoin = get_project_profile("BTC")
    chainlink = get_project_profile("LINK")

    assert bitcoin is not None
    assert bitcoin.coingecko_id == "bitcoin"
    assert bitcoin.defillama_entity == "Bitcoin"
    assert bitcoin.defillama_entity_type == "chain"
    assert chainlink is not None
    assert chainlink.defillama_entity_type == "protocol"


@pytest.mark.unit
def test_historical_coingecko_supply_is_explicit_estimate():
    """历史接口缺少供应量时只能给出可解释的估算值。"""
    parsed = parse_coingecko_payload(
        _historical_payload(),
        historical=True,
        observed_at=datetime(2025, 1, 8, tzinfo=timezone.utc),
    )

    assert parsed["valuation"]["price_usd"] == 100.0
    assert parsed["valuation"]["fully_diluted_valuation_usd"] is None
    assert parsed["supply"]["circulating"] == 5.0
    assert parsed["supply"]["circulating_is_estimate"] is True
    assert parsed["supply"]["method"] == "market_cap_divided_by_price"
    assert parsed["supply"]["total"] is None


@pytest.mark.unit
def test_current_coingecko_payload_keeps_reported_supply():
    """当前快照应保留来源直接报告的供应量与估值。"""
    payload = {
        "id": "ethereum",
        "symbol": "eth",
        "name": "Ethereum",
        "market_cap_rank": 2,
        "genesis_date": "2015-07-30",
        "categories": ["Layer 1"],
        "market_data": {
            "current_price": {"usd": 2000},
            "market_cap": {"usd": 240_000_000_000},
            "fully_diluted_valuation": {"usd": 240_000_000_000},
            "total_volume": {"usd": 10_000_000_000},
            "circulating_supply": 120_000_000,
            "total_supply": 120_000_000,
            "max_supply": None,
            "market_cap_fdv_ratio": 1,
        },
    }

    parsed = parse_coingecko_payload(
        payload,
        historical=False,
        observed_at=datetime(2026, 9, 19, 8, tzinfo=timezone.utc),
    )

    assert parsed["valuation"]["market_cap_rank"] == 2
    assert parsed["supply"]["circulating"] == 120_000_000
    assert parsed["supply"]["circulating_is_estimate"] is False
    assert parsed["supply"]["method"] == "reported"


@pytest.mark.unit
def test_fundamental_ratios_do_not_invent_missing_denominators():
    """派生比率只在分子和分母都可用时计算。"""
    ratios = calculate_fundamental_ratios(
        {
            "volume_24h_usd": 50,
            "market_cap_usd": 1000,
            "fully_diluted_valuation_usd": None,
        },
        {"circulating": 80, "total": 100, "maximum": None},
        {"tvl_usd": 200},
    )

    assert ratios["volume_24h_to_market_cap_pct"] == 5.0
    assert ratios["market_cap_to_tvl"] == 5.0
    assert ratios["fdv_to_tvl"] is None
    assert ratios["circulating_to_total_supply_pct"] == 80.0
    assert ratios["circulating_to_max_supply_pct"] is None


@pytest.mark.unit
def test_defillama_parser_obeys_cutoff_and_calculates_seven_day_change():
    """TVL 只能使用截止时间之前的数据点。"""
    payload = [
        {"date": 1_735_689_600, "tvl": 100.0},
        {"date": 1_736_294_400, "tvl": 200.0},
        {"date": 1_736_380_800, "tvl": 999.0},
    ]
    parsed = parse_defillama_tvl(
        payload,
        entity="Ethereum",
        entity_type="chain",
        cutoff=datetime(2025, 1, 8, 23, 59, tzinfo=timezone.utc),
    )

    assert parsed["tvl_usd"] == 200.0
    assert parsed["tvl_change_7d_pct"] == 100.0
    assert parsed["observed_at_utc"] == "2025-01-08T00:00:00+00:00"


@pytest.mark.unit
def test_project_release_filter_excludes_future_entries():
    """开发发布必须同时满足回看窗口和分析截止时间。"""
    items = [
        CryptoNewsItem("Core", "project_fundamentals", "future", "2025-01-09T00:00:00Z"),
        CryptoNewsItem("Core", "project_fundamentals", "kept", "2025-01-07T00:00:00Z"),
        CryptoNewsItem("Core", "project_fundamentals", "old", "2024-01-01T00:00:00Z"),
    ]
    selected = parse_project_releases(
        items,
        start=datetime(2024, 12, 1, tzinfo=timezone.utc),
        cutoff=datetime(2025, 1, 8, 23, 59, tzinfo=timezone.utc),
    )

    assert [item["title"] for item in selected] == ["kept"]


@pytest.mark.unit
def test_historical_collection_never_requests_current_coingecko_data(monkeypatch):
    """历史分析不得通过当前接口把未来供应量带入过去。"""
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    monkeypatch.setenv("COINGECKO_API_PLAN", "demo")
    session = RecordingSession(
        {
            "/history": FakeResponse(payload=_historical_payload()),
            "historicalChainTvl/Bitcoin": FakeResponse(
                payload=[
                    {"date": 1_735_689_600, "tvl": 100.0},
                    {"date": 1_736_294_400, "tvl": 110.0},
                ]
            ),
            "releases.atom": FakeResponse(content=_release_feed()),
        }
    )

    snapshot = collect_crypto_fundamentals_snapshot(
        "BTC-USD",
        "2025-01-08",
        session=session,
        development_lookback_days=30,
    )

    urls = [url for url, _ in session.calls]
    assert any(url.endswith("/coins/bitcoin/history") for url in urls)
    assert not any(url.endswith("/coins/bitcoin") for url in urls)
    assert snapshot["symbol"] == "BTCUSDT"
    assert snapshot["supply"]["circulating_is_estimate"] is True
    assert snapshot["network_or_protocol"]["tvl_usd"] == 110.0
    assert snapshot["development"]["release_count"] == 1
    assert snapshot["development"]["releases"][0]["title"] == "v1.0"
    assert all(
        release["published_at"] <= snapshot["as_of_utc"]
        for release in snapshot["development"]["releases"]
    )


@pytest.mark.unit
def test_source_failures_are_visible_without_aborting_snapshot(monkeypatch):
    """单个来源失败应进入覆盖状态，而不是伪造空数据或中断快照。"""
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    monkeypatch.setenv("COINGECKO_API_PLAN", "demo")
    session = RecordingSession(
        {
            "/history": FakeResponse(status_code=429),
            "historicalChainTvl/Bitcoin": FakeResponse(payload=[]),
            "releases.atom": FakeResponse(content=_release_feed()),
        }
    )

    snapshot = collect_crypto_fundamentals_snapshot(
        "BTC",
        "2025-01-08",
        session=session,
    )

    status = {item["provider"]: item["state"] for item in snapshot["providers"]}
    assert status["CoinGecko"] == "error"
    assert status["DefiLlama"] == "error"
    assert status["Bitcoin Core Releases"] == "ok"
    assert snapshot["valuation"] is None
    assert len(snapshot["warnings"]) >= 2


@pytest.mark.unit
def test_report_and_json_preserve_provenance(tmp_path, monkeypatch):
    """报告和落盘快照应保留来源覆盖、估算口径与中文文本。"""
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    monkeypatch.setenv("COINGECKO_API_PLAN", "demo")
    session = RecordingSession(
        {
            "/history": FakeResponse(payload=_historical_payload()),
            "historicalChainTvl/Bitcoin": FakeResponse(
                payload=[
                    {"date": 1_735_689_600, "tvl": 100.0},
                    {"date": 1_736_294_400, "tvl": 110.0},
                ]
            ),
            "releases.atom": FakeResponse(content=_release_feed()),
        }
    )
    snapshot = collect_crypto_fundamentals_snapshot(
        "BTC",
        "2025-01-08",
        session=session,
        development_lookback_days=30,
    )

    report = build_crypto_fundamentals_report(snapshot)
    target = save_crypto_fundamentals_snapshot(snapshot, tmp_path / "fundamentals.json")
    restored = json.loads(target.read_text(encoding="utf-8"))

    assert "加密基本面快照" in report
    assert "market_cap_divided_by_price" in report
    assert "CoinGecko" in report
    assert "DefiLlama" in report
    assert restored["identity"]["project_name"] == "Bitcoin"


@pytest.mark.unit
def test_invalid_defillama_payload_is_rejected():
    """缺少历史序列的响应不能被当作零 TVL。"""
    with pytest.raises(CryptoFundamentalsError, match="历史 TVL"):
        parse_defillama_tvl(
            {},
            entity="Ethereum",
            entity_type="protocol",
            cutoff=datetime(2025, 1, 8, tzinfo=timezone.utc),
        )
