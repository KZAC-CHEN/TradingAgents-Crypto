"""加密新闻统一快照的单元测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.analysts import sentiment_analyst as sentiment
from tradingagents.agents.analysts.news_analyst import _select_news_tools
from tradingagents.agents.schemas import SentimentBand, SentimentReport
from tradingagents.agents.utils import crypto_news_tools
from tradingagents.dataflows import crypto_news
from tradingagents.dataflows.crypto_news import (
    CryptoNewsItem,
    build_aicoin_signature,
    build_crypto_news_report,
    collect_crypto_news_snapshot,
    fetch_rootdata,
    parse_aicoin_payload,
    parse_coindesk_payload,
    parse_exchange_announcements,
    parse_rootdata_events,
    parse_rss,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph


@pytest.mark.unit
def test_aicoin_signature_matches_documented_algorithm():
    signature = build_aicoin_signature(
        "975988f45090561684b7d8f4e45b85c2",
        "957f23f2d6435e37d4ac21f3e9a67d45",
        nonce="2",
        timestamp=1_612_149_637,
    )

    assert signature == {
        "AccessKeyId": "975988f45090561684b7d8f4e45b85c2",
        "SignatureNonce": "2",
        "Timestamp": 1_612_149_637,
        "Signature": "M2Y0ODNlYTUwNDFiMTg5MjRmMGQxNmY1YTMyMzc1NTc5NTUzNDAzYw==",
    }


@pytest.mark.unit
def test_okx_parser_keeps_official_title_date_and_url():
    html = """
    <ul><li><a href="/help/okx-to-list-btc-pair">
      <div class="index_title__x">OKX to list BTC/EUR</div>
      <div><span>Published on Sep 18, 2026</span></div>
    </a></li></ul>
    """

    items = parse_exchange_announcements(
        html,
        exchange="okx",
        base_url="https://www.okx.com/help/category/announcements",
    )

    assert len(items) == 1
    assert items[0].title == "OKX to list BTC/EUR"
    assert items[0].published_at == "2026-09-18T00:00:00+00:00"
    assert items[0].url == "https://www.okx.com/help/okx-to-list-btc-pair"


@pytest.mark.unit
def test_rss_parser_supports_rss_and_strips_html():
    xml = """<?xml version="1.0"?>
    <rss><channel><item>
      <title>Bitcoin policy update</title>
      <description><![CDATA[<p>SEC publishes a digital asset update.</p>]]></description>
      <pubDate>Fri, 18 Sep 2026 12:00:00 GMT</pubDate>
      <link>https://example.com/item</link>
    </item></channel></rss>"""

    items = parse_rss(xml.encode("utf-8-sig"), source="SEC", source_type="macro")

    assert len(items) == 1
    assert items[0].summary == "SEC publishes a digital asset update."
    assert items[0].published_at == "2026-09-18T12:00:00+00:00"


@pytest.mark.unit
def test_vendor_payload_parsers_create_unified_items():
    aicoin = parse_aicoin_payload(
        {
            "data": {
                "list": [
                    {
                        "content": "比特币现货 ETF 出现资金流入",
                        "createtime": 1_789_721_200,
                        "shareLink": "https://example.com/cn",
                        "like_count": 9,
                    }
                ]
            }
        },
        source="AiCoin/X",
        source_type="social_proxy",
    )
    coindesk = parse_coindesk_payload(
        {
            "Data": [
                {
                    "TITLE": "Bitcoin advances after policy update",
                    "SUBTITLE": "Institutional flows improved.",
                    "PUBLISHED_ON": 1_789_721_200,
                    "URL": "https://example.com/en",
                    "LANG": "EN",
                    "SENTIMENT": "POSITIVE",
                    "SOURCE_DATA": {"NAME": "CoinDesk"},
                }
            ]
        }
    )

    assert aicoin[0].source_type == "social_proxy"
    assert aicoin[0].engagement["likes"] == 9
    assert coindesk[0].source == "CoinDesk"
    assert coindesk[0].sentiment == "POSITIVE"


@pytest.mark.unit
def test_rootdata_event_parser_uses_documented_fields():
    items = parse_rootdata_events(
        {
            "result": 200,
            "data": {
                "items": [
                    {
                        "name": "Bitcoin",
                        "type_name": "Token Unlock",
                        "hap_date": "2026-09-17",
                        "description": "Scheduled project event",
                        "site_url": "https://example.com/rootdata-event",
                    }
                ]
            },
        },
        project_name="Bitcoin",
    )

    assert items[0].title == "Bitcoin: Token Unlock"
    assert items[0].event_type == "Token Unlock"
    assert items[0].published_at == "2026-09-17T00:00:00+00:00"


@pytest.mark.unit
def test_rootdata_fetch_uses_official_search_then_event_flow(monkeypatch):
    monkeypatch.setenv("ROOTDATA_API_KEY", "root-key")
    search_response = MagicMock(status_code=200)
    search_response.json.return_value = {
        "result": 200,
        "data": [{"id": 12, "name": "Bitcoin", "type": 1, "rootdataurl": "root-url"}],
    }
    event_response = MagicMock(status_code=200)
    event_response.json.return_value = {
        "result": 200,
        "data": {
            "items": [
                {
                    "name": "Bitcoin",
                    "type_name": "Network Upgrade",
                    "hap_date": "2026-09-17",
                }
            ]
        },
    }
    session = MagicMock()
    session.post.side_effect = [search_response, event_response]

    items = fetch_rootdata(
        ("btc", "bitcoin", "比特币"),
        start=datetime(2026, 9, 11, tzinfo=timezone.utc),
        end=datetime(2026, 9, 18, 23, 59, tzinfo=timezone.utc),
        session=session,
        timeout=12,
    )

    search_call, event_call = session.post.call_args_list
    assert search_call.args[0].endswith("/open/ser_inv")
    assert search_call.kwargs["headers"]["apikey"] == "root-key"
    assert search_call.kwargs["json"] == {"query": "bitcoin"}
    assert event_call.args[0].endswith("/open/get_event")
    assert event_call.kwargs["json"]["m_id"] == 12
    assert event_call.kwargs["json"]["begin_time"] == "2026-09-11"
    assert items[0].title == "Bitcoin: Network Upgrade"


def _news_item(
    title: str,
    published_at: str,
    *,
    source: str,
    source_type: str,
    url: str,
) -> CryptoNewsItem:
    """创建快照测试条目。"""
    return CryptoNewsItem(
        source=source,
        source_type=source_type,
        title=title,
        published_at=published_at,
        url=url,
    )


@pytest.mark.unit
def test_snapshot_filters_future_items_deduplicates_and_degrades(monkeypatch):
    monkeypatch.setenv("COINDESK_API_KEY", "key")
    monkeypatch.setenv("AICOIN_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("AICOIN_ACCESS_SECRET", "secret")
    monkeypatch.setenv("ROOTDATA_API_KEY", "key")
    monkeypatch.setenv("JIN10_API_URL", "https://example.com/jin10")
    monkeypatch.setenv("JIN10_API_KEY", "key")
    monkeypatch.setenv("X_BEARER_TOKEN", "token")

    exchange_items = [
        _news_item(
            "Binance supports Bitcoin network upgrade",
            "2026-09-18T10:00:00+00:00",
            source="Binance",
            source_type="official_exchange",
            url="https://example.com/official",
        ),
        _news_item(
            "Future Bitcoin announcement",
            "2026-09-19T00:00:00+00:00",
            source="Binance",
            source_type="official_exchange",
            url="https://example.com/future",
        ),
    ]
    monkeypatch.setattr(
        crypto_news,
        "fetch_exchange_announcements",
        lambda exchange, **kwargs: exchange_items if exchange == "binance" else [],
    )
    monkeypatch.setattr(
        crypto_news,
        "fetch_aicoin",
        lambda *args, **kwargs: [
            _news_item(
                "Bitcoin X discussion",
                "2026-09-17T10:00:00+00:00",
                source="AiCoin/X",
                source_type="social_proxy",
                url="https://example.com/social",
            )
        ],
    )
    monkeypatch.setattr(
        crypto_news,
        "fetch_coindesk",
        lambda **kwargs: [
            _news_item(
                "Bitcoin duplicate",
                "2026-09-18T10:00:00+00:00",
                source="CoinDesk",
                source_type="english_news",
                url="https://example.com/official",
            )
        ],
    )
    monkeypatch.setattr(
        crypto_news,
        "fetch_rootdata",
        lambda *args, **kwargs: [
            _news_item(
                "RootData Bitcoin event",
                "2026-09-16T10:00:00+00:00",
                source="RootData",
                source_type="project_fundamentals",
                url="https://example.com/rootdata",
            )
        ],
    )
    monkeypatch.setattr(
        crypto_news,
        "fetch_configured_provider",
        lambda provider, **kwargs: [
            _news_item(
                f"{provider} Bitcoin event",
                "2026-09-16T10:00:00+00:00",
                source=provider,
                source_type=kwargs["source_type"],
                url=f"https://example.com/{provider.lower()}",
            )
        ],
    )
    monkeypatch.setattr(
        crypto_news,
        "fetch_rss",
        lambda **kwargs: (_ for _ in ()).throw(crypto_news.CryptoNewsError("暂时不可用")),
    )
    monkeypatch.setattr(
        crypto_news,
        "fetch_x",
        lambda *args, **kwargs: [
            _news_item(
                "Native X Bitcoin post",
                "2026-09-15T10:00:00+00:00",
                source="X",
                source_type="social_native",
                url="https://example.com/x",
            )
        ],
    )

    snapshot = collect_crypto_news_snapshot("BTC-USD", "2026-09-18", lookback_days=7)

    urls = {item["url"] for item in snapshot["items"]}
    assert "https://example.com/future" not in urls
    assert len([url for url in urls if url == "https://example.com/official"]) == 1
    assert {"https://example.com/rootdata", "https://example.com/jin10"} <= urls
    assert any(status["provider"] == "SEC" and status["state"] == "error" for status in snapshot["providers"])
    assert any("SEC" in warning for warning in snapshot["warnings"])


@pytest.mark.unit
def test_report_exposes_coverage_sections_and_warnings():
    snapshot = {
        "symbol": "BTCUSDT",
        "as_of_utc": "2026-09-18T23:59:59+00:00",
        "window_start_utc": "2026-09-11T00:00:00+00:00",
        "items": [
            {
                "source": "OKX",
                "source_type": "official_exchange",
                "title": "OKX Bitcoin update",
                "published_at": "2026-09-18T00:00:00+00:00",
                "url": "https://example.com/okx",
                "summary": "",
                "sentiment": "",
                "engagement": {},
            }
        ],
        "providers": [
            {"provider": "OKX", "state": "ok", "item_count": 1, "detail": ""},
            {"provider": "X API", "state": "disabled", "item_count": 0, "detail": "缺少凭证"},
        ],
        "warnings": ["Binance：官方公告页返回空正文。"],
    }

    report = build_crypto_news_report(snapshot)

    assert "## 来源覆盖" in report
    assert "## 官方交易所事件" in report
    assert "## 原生 X 信息" in report
    assert "[原文](https://example.com/okx)" in report
    assert "Binance：官方公告页返回空正文" in report


@pytest.mark.unit
def test_crypto_news_tool_reuses_cached_report(monkeypatch):
    calls = []
    crypto_news_tools.clear_crypto_news_report_cache()
    monkeypatch.setattr(
        crypto_news_tools,
        "collect_crypto_news_snapshot",
        lambda symbol, curr_date: calls.append((symbol, curr_date)) or {"symbol": symbol},
    )
    monkeypatch.setattr(
        crypto_news_tools,
        "build_crypto_news_report",
        lambda snapshot: f"report:{snapshot['symbol']}",
    )

    first = crypto_news_tools.get_crypto_news_report_text("BTC-USD", "2026-09-18")
    second = crypto_news_tools.get_crypto_news_report_text("BTC-USD", "2026-09-18")

    assert first == second == "report:BTC-USD"
    assert calls == [("BTC-USD", "2026-09-18")]


@pytest.mark.unit
def test_crypto_news_analyst_only_binds_unified_tool():
    assert [tool.name for tool in _select_news_tools("crypto")] == ["get_crypto_news_report"]
    assert [tool.name for tool in _select_news_tools("stock")] == [
        "get_news",
        "get_global_news",
        "get_macro_indicators",
        "get_prediction_markets",
    ]


@pytest.mark.unit
def test_news_toolnode_registers_crypto_report():
    nodes = TradingAgentsGraph._create_tool_nodes(None)
    assert "get_crypto_news_report" in nodes["news"].tools_by_name


@pytest.mark.unit
def test_crypto_sentiment_uses_unified_report_and_skips_stock_sources(monkeypatch):
    monkeypatch.setattr(
        sentiment,
        "get_crypto_news_report_text",
        lambda *args: "统一加密新闻证据",
    )

    def fail(*args, **kwargs):
        raise AssertionError("加密模式不应访问股票情绪来源。")

    monkeypatch.setattr(sentiment.get_news, "func", fail, raising=False)
    monkeypatch.setattr(sentiment, "fetch_stocktwits_messages", fail)
    monkeypatch.setattr(sentiment, "fetch_reddit_posts", fail)
    captured = {}
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt)
        or SentimentReport(
            overall_band=SentimentBand.NEUTRAL,
            overall_score=5.0,
            confidence="low",
            narrative="覆盖有限。",
        )
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    result = sentiment.create_sentiment_analyst(llm)(
        {
            "company_of_interest": "BTC-USD",
            "trade_date": "2026-09-18",
            "asset_type": "crypto",
            "messages": [],
        }
    )

    prompt_text = "\n".join(str(message.content) for message in captured["prompt"])
    assert "统一加密新闻证据" in prompt_text
    assert "Yahoo Finance" not in prompt_text
    assert "**Overall Sentiment:** **Neutral**" in result["sentiment_report"]
