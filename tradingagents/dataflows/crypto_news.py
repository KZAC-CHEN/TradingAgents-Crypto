"""面向加密资产的统一新闻、公告与社会情绪快照。

所有来源先转换为同一数据模型，再按分析截止日期过滤、去重和排序。单个
来源失败只会写入覆盖状态与警告，不会中断整条交易分析链路。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import requests
from parsel import Selector

from tradingagents.dataflows.binance import normalize_binance_symbol
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.crypto_project_sources import (
    build_project_reference_links,
    get_project_profile,
)

BINANCE_ANNOUNCEMENTS_URL = "https://www.binance.com/en/support/announcement"
OKX_ANNOUNCEMENTS_URL = "https://www.okx.com/help/category/announcements"
AICOIN_BASE_URL = "https://open.aicoin.com"
COINDESK_NEWS_URL = "https://data-api.coindesk.com/news/v1/article/list"
COINDESK_RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"
ROOTDATA_BASE_URL = "https://api.rootdata.com"
X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"

DEFAULT_MACRO_FEEDS = (
    ("SEC", "https://www.sec.gov/news/pressreleases.rss"),
    ("CFTC", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
)

_ASSET_ALIASES = {
    "BTC": ("btc", "bitcoin", "比特币"),
    "ETH": ("eth", "ether", "ethereum", "以太坊"),
    "BNB": ("bnb", "binance coin", "币安币"),
    "SOL": ("sol", "solana", "索拉纳"),
    "XRP": ("xrp", "ripple", "瑞波"),
    "DOGE": ("doge", "dogecoin", "狗狗币"),
    "ADA": ("ada", "cardano", "艾达币"),
    "AVAX": ("avax", "avalanche", "雪崩协议"),
    "LINK": ("link", "chainlink", "预言机"),
    "DOT": ("dot", "polkadot", "波卡"),
}

_HIGH_IMPACT_TERMS = (
    "crypto",
    "digital asset",
    "stablecoin",
    "spot etf",
    "tokenized",
    "blockchain",
    "interest rate",
    "inflation",
    "monetary policy",
)

_ROOTDATA_FALLBACK_MODES = {"official_sources", "manual_link", "disabled"}


class CryptoNewsError(RuntimeError):
    """新闻来源返回无效内容或无法完成请求。"""


@dataclass(frozen=True)
class CryptoNewsItem:
    """统一的加密新闻条目。"""

    source: str
    source_type: str
    title: str
    published_at: str
    url: str = ""
    summary: str = ""
    language: str = ""
    event_type: str = "news"
    sentiment: str = ""
    engagement: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderStatus:
    """记录某个来源在本次快照中的覆盖情况。"""

    provider: str
    state: str
    item_count: int = 0
    detail: str = ""


def _clean_text(value: Any, *, limit: int = 1200) -> str:
    """清理 HTML、空白和过长正文。"""
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = Selector(text=text).xpath("string(.)").get(default=text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _parse_datetime(value: Any) -> datetime | None:
    """将常见时间戳或日期文本转换为 UTC 时间。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(
            timezone.utc
        )
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(
            timezone.utc
        )
    except (TypeError, ValueError, OverflowError):
        pass
    for fmt in (
        "%b %d, %Y",
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M",
        "%Y年%m月%d日",
        "%d %b %Y",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _analysis_window(
    curr_date: str | date | datetime,
    lookback_days: int,
) -> tuple[datetime, datetime]:
    """返回截止到指定 UTC 时刻的新闻窗口，并兼容原有日期输入。"""
    now = datetime.now(timezone.utc)
    date_only = False
    if isinstance(curr_date, datetime):
        end = curr_date if curr_date.tzinfo else curr_date.replace(tzinfo=timezone.utc)
        end = end.astimezone(timezone.utc)
    elif isinstance(curr_date, date):
        end = datetime.combine(curr_date, datetime_time.max, tzinfo=timezone.utc)
        date_only = True
    else:
        text = str(curr_date).strip()
        try:
            day = date.fromisoformat(text)
        except ValueError:
            try:
                end = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("curr_date 必须为 ISO 日期或时间。") from exc
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            end = end.astimezone(timezone.utc)
        else:
            end = datetime.combine(day, datetime_time.max, tzinfo=timezone.utc)
            date_only = True
    if end.date() > now.date() or end > now and not date_only:
        raise ValueError("curr_date 不能晚于当前 UTC 时间。")
    if end.date() == now.date():
        end = min(end, now)
    day = end.date()
    start_day = day - timedelta(days=max(1, int(lookback_days)))
    return datetime.combine(start_day, datetime_time.min, tzinfo=timezone.utc), end


def _asset_terms(symbol: str) -> tuple[str, tuple[str, ...]]:
    """解析基础资产代码及用于新闻检索的别名。"""
    canonical = normalize_binance_symbol(symbol)
    base = canonical.removesuffix("USDT")
    aliases = _ASSET_ALIASES.get(base, (base.lower(),))
    return base, tuple(dict.fromkeys((base.lower(), *aliases)))


def _rootdata_fallback_mode(config: dict[str, Any]) -> str:
    """解析并校验无 RootData Key 时的项目基本面降级模式。"""
    mode = str(
        os.getenv("TRADINGAGENTS_ROOTDATA_FALLBACK_MODE")
        or config.get("rootdata_fallback_mode", "official_sources")
    ).strip().lower()
    if mode not in _ROOTDATA_FALLBACK_MODES:
        choices = "、".join(sorted(_ROOTDATA_FALLBACK_MODES))
        raise ValueError(f"TRADINGAGENTS_ROOTDATA_FALLBACK_MODE 必须是：{choices}。")
    return mode


def _is_relevant(item: CryptoNewsItem, terms: tuple[str, ...], *, allow_macro: bool) -> bool:
    """按完整单词或高影响主题判断条目是否与分析有关。"""
    haystack = f"{item.title} {item.summary}".lower()
    for term in terms:
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack):
            return True
    return allow_macro and any(term in haystack for term in _HIGH_IMPACT_TERMS)


def _within_window(item: CryptoNewsItem, start: datetime, end: datetime) -> bool:
    """仅保留有可信时间且位于分析窗口内的条目。"""
    published = _parse_datetime(item.published_at)
    return published is not None and start <= published <= end


def _request(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> requests.Response:
    """执行带统一请求头和错误处理的只读请求。"""
    request_headers = {
        "User-Agent": "TradingAgents/0.4 (+https://github.com/TauricResearch/TradingAgents)",
        "Accept": "application/json, application/xml, text/xml, text/html;q=0.9,*/*;q=0.8",
    }
    request_headers.update(headers or {})
    try:
        response = session.get(url, params=params, headers=request_headers, timeout=timeout)
    except requests.RequestException as exc:
        raise CryptoNewsError(f"请求失败：{url}：{exc}") from exc
    if response.status_code == 429:
        raise CryptoNewsError(f"来源触发限流：{url}")
    if response.status_code >= 400:
        raise CryptoNewsError(f"来源返回 HTTP {response.status_code}：{url}")
    return response


def _post_json(
    session: requests.Session,
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float,
) -> Any:
    """向授权数据源发送 JSON 请求并校验响应。"""
    request_headers = {
        "User-Agent": "TradingAgents/0.4 (+https://github.com/TauricResearch/TradingAgents)",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    request_headers.update(headers or {})
    try:
        response = session.post(url, json=payload, headers=request_headers, timeout=timeout)
    except requests.RequestException as exc:
        raise CryptoNewsError(f"请求失败：{url}：{exc}") from exc
    if response.status_code == 429:
        raise CryptoNewsError(f"来源触发限流：{url}")
    if response.status_code >= 400:
        raise CryptoNewsError(f"来源返回 HTTP {response.status_code}：{url}")
    try:
        data = response.json()
    except ValueError as exc:
        raise CryptoNewsError(f"来源返回无效 JSON：{url}") from exc
    if isinstance(data, dict) and data.get("result") not in (None, 200, "200"):
        raise CryptoNewsError(f"来源返回业务错误 {data.get('result')}：{url}")
    return data


def _item(
    *,
    source: str,
    source_type: str,
    title: Any,
    published_at: Any,
    url: Any = "",
    summary: Any = "",
    language: str = "",
    event_type: str = "news",
    sentiment: Any = "",
    engagement: dict[str, int] | None = None,
) -> CryptoNewsItem | None:
    """校验并创建一个统一条目。"""
    clean_title = _clean_text(title, limit=400)
    parsed = _parse_datetime(published_at)
    if not clean_title or parsed is None:
        return None
    return CryptoNewsItem(
        source=source,
        source_type=source_type,
        title=clean_title,
        published_at=parsed.isoformat(),
        url=str(url or "").strip(),
        summary=_clean_text(summary),
        language=language,
        event_type=event_type,
        sentiment=_clean_text(sentiment, limit=60),
        engagement=engagement or {},
    )


def parse_exchange_announcements(
    html_text: str,
    *,
    exchange: str,
    base_url: str,
) -> list[CryptoNewsItem]:
    """解析 Binance 或 OKX 官方公告列表页。"""
    selector = Selector(text=html_text)
    if exchange.lower() == "okx":
        links = selector.xpath(
            "//a[starts-with(@href, '/help/') and not(contains(@href, '/category/')) "
            "and not(contains(@href, '/section/'))]"
        )
        date_pattern = re.compile(r"Published on\s+(.+)$", re.IGNORECASE)
    elif exchange.lower() == "binance":
        links = selector.xpath("//a[contains(@href, '/support/announcement/detail/')]")
        date_pattern = re.compile(r"(20\d{2}-\d{2}-\d{2})")
    else:
        raise ValueError("exchange 仅支持 binance 或 okx。")

    items: list[CryptoNewsItem] = []
    for link in links:
        title = link.css("div[class*='title']::text").get() or " ".join(link.xpath(".//text()").getall())
        block_text = _clean_text(" ".join(link.xpath(".//text()").getall()))
        match = date_pattern.search(block_text)
        if not match:
            parent_text = _clean_text(
                " ".join(link.xpath("../descendant-or-self::*//text()").getall())
            )
            match = date_pattern.search(parent_text)
        created = match.group(1).strip() if match else None
        if exchange.lower() == "okx" and created:
            title = re.sub(r"\s*Published on\s+.+$", "", str(title), flags=re.IGNORECASE)
        parsed = _item(
            source=exchange.upper(),
            source_type="official_exchange",
            title=title,
            published_at=created,
            url=urljoin(base_url, link.attrib.get("href", "")),
            language="en",
            event_type="exchange_announcement",
        )
        if parsed:
            items.append(parsed)
    return _deduplicate(items)


def fetch_exchange_announcements(
    exchange: str,
    *,
    session: requests.Session,
    timeout: float,
) -> list[CryptoNewsItem]:
    """读取交易所官方公告页面。"""
    url = BINANCE_ANNOUNCEMENTS_URL if exchange.lower() == "binance" else OKX_ANNOUNCEMENTS_URL
    response = _request(session, url, timeout=timeout)
    if not response.text.strip():
        raise CryptoNewsError(f"{exchange.upper()} 官方公告页返回空正文。")
    items = parse_exchange_announcements(response.text, exchange=exchange, base_url=url)
    if not items:
        raise CryptoNewsError(f"未能从 {exchange.upper()} 官方公告页解析出带日期的条目。")
    return items


def build_aicoin_signature(
    access_key_id: str,
    access_secret: str,
    *,
    nonce: str,
    timestamp: int,
) -> dict[str, str | int]:
    """按照 AiCoin 文档生成 SHA1 HMAC 签名参数。"""
    signing = f"AccessKeyId={access_key_id}&SignatureNonce={nonce}&Timestamp={timestamp}"
    digest_hex = hmac.new(
        access_secret.encode("utf-8"), signing.encode("utf-8"), hashlib.sha1
    ).hexdigest()
    signature = base64.b64encode(digest_hex.encode("ascii")).decode("ascii")
    return {
        "AccessKeyId": access_key_id,
        "SignatureNonce": nonce,
        "Timestamp": timestamp,
        "Signature": signature,
    }


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    """从不同供应商常见的包裹结构中提取列表。"""
    current = payload
    for key in ("data", "Data"):
        if isinstance(current, dict) and key in current:
            current = current[key]
            break
    if isinstance(current, dict):
        for key in ("list", "items", "rows", "data", "Data"):
            if isinstance(current.get(key), list):
                current = current[key]
                break
    return [row for row in current if isinstance(row, dict)] if isinstance(current, list) else []


def parse_aicoin_payload(payload: Any, *, source: str, source_type: str) -> list[CryptoNewsItem]:
    """解析 AiCoin 新闻快讯或 X 代理响应。"""
    items: list[CryptoNewsItem] = []
    for row in _payload_rows(payload):
        engagement = {}
        for source_key, target_key in (
            ("like_count", "likes"),
            ("reply_count", "replies"),
            ("retweet_count", "reposts"),
            ("view_count", "views"),
            ("commentCount", "comments"),
        ):
            try:
                engagement[target_key] = int(row.get(source_key) or 0)
            except (TypeError, ValueError):
                continue
        parsed = _item(
            source=source,
            source_type=source_type,
            title=(
                row.get("transTitle")
                or row.get("title")
                or row.get("transContent")
                or row.get("content")
                or row.get("description")
            ),
            summary=(
                row.get("transContent")
                or row.get("translated_content")
                or row.get("content")
                or row.get("describe")
                or row.get("description")
            ),
            published_at=(
                row.get("timestamp")
                or row.get("createtime")
                or row.get("createTime")
                or row.get("create_time")
                or row.get("publishedAt")
            ),
            url=row.get("shareLink") or row.get("url") or row.get("link"),
            language=str(row.get("language") or "zh"),
            event_type="social" if source_type == "social_proxy" else "news",
            sentiment=row.get("sentiment"),
            engagement=engagement,
        )
        if parsed:
            items.append(parsed)
    return items


def fetch_aicoin(
    terms: tuple[str, ...],
    *,
    session: requests.Session,
    timeout: float,
) -> list[CryptoNewsItem]:
    """读取 AiCoin 中文快讯和其 X 信息代理。"""
    access_key_id = os.getenv("AICOIN_ACCESS_KEY_ID", "").strip()
    access_secret = os.getenv("AICOIN_ACCESS_SECRET", "").strip()
    if not access_key_id or not access_secret:
        raise CryptoNewsError("缺少 AICOIN_ACCESS_KEY_ID 或 AICOIN_ACCESS_SECRET。")
    signed = build_aicoin_signature(
        access_key_id,
        access_secret,
        nonce=uuid.uuid4().hex,
        timestamp=int(time.time()),
    )
    flash_response = _request(
        session,
        f"{AICOIN_BASE_URL}/api/v2/content/flashList",
        params={**signed, "language": "cn"},
        timeout=timeout,
    )
    try:
        flash_payload = flash_response.json()
    except ValueError as exc:
        raise CryptoNewsError("AiCoin 快讯返回了无效 JSON。") from exc
    items = parse_aicoin_payload(flash_payload, source="AiCoin", source_type="chinese_news")

    signed = build_aicoin_signature(
        access_key_id,
        access_secret,
        nonce=uuid.uuid4().hex,
        timestamp=int(time.time()),
    )
    x_response = _request(
        session,
        f"{AICOIN_BASE_URL}/api/upgrade/v2/content/twitter/search",
        params={**signed, "keyword": terms[0], "page_size": 30, "language": "cn"},
        timeout=timeout,
    )
    try:
        x_payload = x_response.json()
    except ValueError as exc:
        raise CryptoNewsError("AiCoin X 代理返回了无效 JSON。") from exc
    items.extend(parse_aicoin_payload(x_payload, source="AiCoin/X", source_type="social_proxy"))
    return items


def parse_coindesk_payload(payload: Any) -> list[CryptoNewsItem]:
    """解析 CoinDesk Data 新闻接口响应。"""
    items: list[CryptoNewsItem] = []
    for row in _payload_rows(payload):
        source_data = row.get("SOURCE_DATA") if isinstance(row.get("SOURCE_DATA"), dict) else {}
        parsed = _item(
            source=str(source_data.get("NAME") or "CoinDesk Data"),
            source_type="english_news",
            title=row.get("TITLE"),
            summary=row.get("SUBTITLE") or row.get("BODY"),
            published_at=row.get("PUBLISHED_ON"),
            url=row.get("URL"),
            language=str(row.get("LANG") or "en"),
            sentiment=row.get("SENTIMENT"),
        )
        if parsed:
            items.append(parsed)
    return items


def fetch_coindesk(
    *,
    session: requests.Session,
    timeout: float,
    limit: int,
    end: datetime,
) -> list[CryptoNewsItem]:
    """使用 CoinDesk Data API 读取英文新闻。"""
    api_key = os.getenv("COINDESK_API_KEY", "").strip()
    if not api_key:
        raise CryptoNewsError("缺少 COINDESK_API_KEY。")
    response = _request(
        session,
        COINDESK_NEWS_URL,
        params={
            "api_key": api_key,
            "lang": "EN",
            "limit": min(max(int(limit), 1), 100),
            "to_ts": int(end.timestamp()),
        },
        timeout=timeout,
    )
    try:
        return parse_coindesk_payload(response.json())
    except ValueError as exc:
        raise CryptoNewsError("CoinDesk Data 返回了无效 JSON。") from exc


def parse_rss(
    xml_text: str | bytes,
    *,
    source: str,
    source_type: str,
) -> list[CryptoNewsItem]:
    """解析 RSS 2.0 或 Atom 订阅。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise CryptoNewsError(f"{source} RSS 不是有效 XML。") from exc

    items: list[CryptoNewsItem] = []
    entries = root.findall(".//item")
    if not entries:
        entries = root.findall(".//{*}entry")
    for entry in entries:
        def first_text(*names: str, current_entry=entry) -> str:
            for name in names:
                node = current_entry.find(name)
                if node is None:
                    node = current_entry.find(f"{{*}}{name}")
                if node is not None and node.text:
                    return node.text
            return ""

        link = first_text("link")
        if not link:
            link_node = entry.find("{*}link")
            link = link_node.attrib.get("href", "") if link_node is not None else ""
        parsed = _item(
            source=source,
            source_type=source_type,
            title=first_text("title"),
            summary=first_text("description", "summary", "content"),
            published_at=first_text("pubDate", "published", "updated", "date"),
            url=link,
            language="en",
            event_type=(
                "macro"
                if source_type == "macro"
                else "project_update"
                if source_type == "project_fundamentals"
                else "news"
            ),
        )
        if parsed:
            items.append(parsed)
    return items


def fetch_rss(
    url: str,
    *,
    source: str,
    source_type: str,
    session: requests.Session,
    timeout: float,
) -> list[CryptoNewsItem]:
    """读取并解析一个明确配置或官方公布的 RSS。"""
    response = _request(session, url, timeout=timeout)
    # 直接解析原始字节，让 XML 声明和 BOM 决定编码，避免 requests 猜错字符集。
    return parse_rss(response.content, source=source, source_type=source_type)


def _parse_flexible_project_payload(
    payload: Any,
    *,
    source: str,
    source_type: str,
) -> list[CryptoNewsItem]:
    """解析授权项目数据接口的常见字段。"""
    items: list[CryptoNewsItem] = []
    for row in _payload_rows(payload):
        parsed = _item(
            source=source,
            source_type=source_type,
            title=row.get("title") or row.get("name") or row.get("event"),
            summary=row.get("summary") or row.get("description") or row.get("content"),
            published_at=(
                row.get("published_at")
                or row.get("publishedAt")
                or row.get("timestamp")
                or row.get("createTime")
                or row.get("date")
            ),
            url=row.get("url") or row.get("link"),
            language=str(row.get("language") or "en"),
            event_type=str(
                row.get("event_type")
                or row.get("type")
                or ("macro" if source_type == "macro" else "project_event")
            ),
        )
        if parsed:
            items.append(parsed)
    return items


def parse_rootdata_events(
    payload: Any,
    *,
    project_name: str,
    project_url: str = "",
) -> list[CryptoNewsItem]:
    """解析 RootData 官方项目事件响应。"""
    rows = _payload_rows(payload)
    items: list[CryptoNewsItem] = []
    for row in rows:
        event_name = row.get("type_name") or row.get("event_type") or "Project event"
        row_project = row.get("name") or project_name
        parsed = _item(
            source="RootData",
            source_type="project_fundamentals",
            title=f"{row_project}: {event_name}",
            summary=row.get("description"),
            published_at=row.get("hap_date") or row.get("start_date") or row.get("end_date"),
            url=row.get("site_url") or project_url,
            language="en",
            event_type=str(event_name),
        )
        if parsed:
            items.append(parsed)
    return items


def fetch_rootdata(
    terms: tuple[str, ...],
    *,
    start: datetime,
    end: datetime,
    session: requests.Session,
    timeout: float,
) -> list[CryptoNewsItem]:
    """按 RootData 官方流程解析项目并读取其事件。"""
    api_key = os.getenv("ROOTDATA_API_KEY", "").strip()
    if not api_key:
        raise CryptoNewsError("缺少 ROOTDATA_API_KEY。")
    headers = {"apikey": api_key, "language": "en"}
    query = terms[1] if len(terms) > 1 else terms[0]
    search_payload = _post_json(
        session,
        f"{ROOTDATA_BASE_URL}/open/ser_inv",
        payload={"query": query},
        headers=headers,
        timeout=timeout,
    )
    projects = _payload_rows(search_payload)
    projects = [row for row in projects if row.get("type") in (None, 1, "1")]
    if not projects:
        raise CryptoNewsError(f"RootData 未找到项目：{query}。")
    project = projects[0]
    project_id = project.get("id") or project.get("project_id")
    if project_id is None:
        raise CryptoNewsError("RootData 项目搜索结果缺少项目 ID。")
    event_payload = _post_json(
        session,
        f"{ROOTDATA_BASE_URL}/open/get_event",
        payload={
            "page": 1,
            "page_size": 100,
            "m_id": project_id,
            "begin_time": start.date().isoformat(),
            "end_time": end.date().isoformat(),
        },
        headers=headers,
        timeout=timeout,
    )
    return parse_rootdata_events(
        event_payload,
        project_name=str(project.get("name") or query),
        project_url=str(project.get("rootdataurl") or ""),
    )


def fetch_configured_provider(
    *,
    provider: str,
    url_env: str,
    key_env: str,
    source_type: str,
    symbol: str,
    session: requests.Session,
    timeout: float,
) -> list[CryptoNewsItem]:
    """调用需要授权且端点由用户提供的项目或宏观来源。"""
    url = os.getenv(url_env, "").strip()
    api_key = os.getenv(key_env, "").strip()
    if not url or not api_key:
        raise CryptoNewsError(f"缺少 {url_env} 或 {key_env}。")
    response = _request(
        session,
        url,
        params={"symbol": symbol},
        headers={"Authorization": f"Bearer {api_key}", "X-API-Key": api_key},
        timeout=timeout,
    )
    try:
        return _parse_flexible_project_payload(
            response.json(), source=provider, source_type=source_type
        )
    except ValueError as exc:
        raise CryptoNewsError(f"{provider} 返回了无效 JSON。") from exc


def fetch_x(
    terms: tuple[str, ...],
    *,
    session: requests.Session,
    timeout: float,
) -> list[CryptoNewsItem]:
    """在配置 Bearer Token 后调用原生 X 最近搜索接口。"""
    token = os.getenv("X_BEARER_TOKEN", "").strip()
    if not token:
        raise CryptoNewsError("缺少 X_BEARER_TOKEN。")
    query_terms = " OR ".join(f'"{term}"' for term in terms)
    response = _request(
        session,
        X_RECENT_SEARCH_URL,
        params={
            "query": f"({query_terms}) -is:retweet",
            "max_results": 50,
            "tweet.fields": "created_at,public_metrics,lang,author_id",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    try:
        rows = response.json().get("data", [])
    except (ValueError, AttributeError) as exc:
        raise CryptoNewsError("X API 返回了无效 JSON。") from exc
    items: list[CryptoNewsItem] = []
    for row in rows if isinstance(rows, list) else []:
        metrics = row.get("public_metrics") if isinstance(row.get("public_metrics"), dict) else {}
        parsed = _item(
            source="X",
            source_type="social_native",
            title=row.get("text"),
            summary=row.get("text"),
            published_at=row.get("created_at"),
            url=f"https://x.com/i/web/status/{row.get('id')}" if row.get("id") else "",
            language=str(row.get("lang") or ""),
            event_type="social",
            engagement={
                "likes": int(metrics.get("like_count") or 0),
                "replies": int(metrics.get("reply_count") or 0),
                "reposts": int(metrics.get("retweet_count") or 0),
            },
        )
        if parsed:
            items.append(parsed)
    return items


def _deduplicate(items: list[CryptoNewsItem]) -> list[CryptoNewsItem]:
    """优先按链接、其次按规范化标题去重。"""
    seen: set[str] = set()
    result: list[CryptoNewsItem] = []
    for item in items:
        normalized_title = re.sub(r"\W+", "", item.title.lower())
        key = item.url.rstrip("/").lower() or normalized_title
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def collect_crypto_news_snapshot(
    symbol: str,
    curr_date: str | date | datetime,
    *,
    session: requests.Session | None = None,
    lookback_days: int | None = None,
    limit: int | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """采集统一快照，并严格排除分析日之后发布的信息。"""
    config = get_config()
    days = int(lookback_days or config.get("crypto_news_lookback_days", 7))
    item_limit = int(limit or config.get("crypto_news_article_limit", 50))
    request_timeout = float(timeout or config.get("crypto_news_timeout", 12.0))
    rootdata_fallback_mode = _rootdata_fallback_mode(config)
    start, end = _analysis_window(curr_date, days)
    base, terms = _asset_terms(symbol)
    client = session or requests.Session()

    all_items: list[CryptoNewsItem] = []
    statuses: list[ProviderStatus] = []
    warnings: list[str] = []

    def run(
        provider: str,
        fetcher: Callable[[], list[CryptoNewsItem]],
        *,
        optional: bool = False,
        allow_macro: bool = False,
        assume_relevant: bool = False,
    ) -> bool:
        try:
            fetched = fetcher()
        except CryptoNewsError as exc:
            state = "disabled" if optional and str(exc).startswith("缺少") else "error"
            statuses.append(ProviderStatus(provider, state, detail=str(exc)))
            if state == "error":
                warnings.append(f"{provider}：{exc}")
            return False
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            statuses.append(ProviderStatus(provider, "error", detail=str(exc)))
            warnings.append(f"{provider}：{exc}")
            return False
        filtered = [
            item
            for item in fetched
            if _within_window(item, start, end)
            and (assume_relevant or _is_relevant(item, terms, allow_macro=allow_macro))
        ]
        statuses.append(ProviderStatus(provider, "ok", item_count=len(filtered)))
        all_items.extend(filtered)
        return True

    run(
        "Binance",
        lambda: fetch_exchange_announcements("binance", session=client, timeout=request_timeout),
    )
    run(
        "OKX",
        lambda: fetch_exchange_announcements("okx", session=client, timeout=request_timeout),
    )
    run(
        "AiCoin",
        lambda: fetch_aicoin(terms, session=client, timeout=request_timeout),
        optional=True,
    )

    coindesk_available = False
    if os.getenv("COINDESK_API_KEY", "").strip():
        coindesk_available = run(
            "CoinDesk Data",
            lambda: fetch_coindesk(
                session=client,
                timeout=request_timeout,
                limit=item_limit,
                end=end,
            ),
        )
    else:
        statuses.append(
            ProviderStatus("CoinDesk Data", "disabled", detail="缺少 COINDESK_API_KEY，已使用官方 RSS。")
        )
    if not coindesk_available:
        run(
            "CoinDesk RSS",
            lambda: fetch_rss(
                COINDESK_RSS_URL,
                source="CoinDesk RSS",
                source_type="english_news",
                session=client,
                timeout=request_timeout,
            ),
        )

    rootdata_api_key = os.getenv("ROOTDATA_API_KEY", "").strip()
    project_references: list[dict[str, str]] = []
    if rootdata_api_key:
        run(
            "RootData",
            lambda: fetch_rootdata(
                terms,
                start=start,
                end=end,
                session=client,
                timeout=request_timeout,
            ),
        )
        project_references = build_project_reference_links(
            base,
            include_official=True,
            include_rootdata=True,
        )
    elif rootdata_fallback_mode == "official_sources":
        statuses.append(
            ProviderStatus(
                "RootData",
                "disabled",
                detail=(
                    "缺少 ROOTDATA_API_KEY；已改用项目维护方公开来源，"
                    "未自动抓取 RootData 网站。"
                ),
            )
        )
        profile = get_project_profile(base)
        if profile is None:
            statuses.append(
                ProviderStatus(
                    "项目官方来源",
                    "disabled",
                    detail=f"尚未登记 {base} 的项目官方来源。",
                )
            )
        else:
            for feed in profile.feeds:
                run(
                    feed.name,
                    lambda feed=feed: fetch_rss(
                        feed.url,
                        source=feed.name,
                        source_type="project_fundamentals",
                        session=client,
                        timeout=request_timeout,
                    ),
                    assume_relevant=True,
                )
        project_references = build_project_reference_links(
            base,
            include_official=True,
            include_rootdata=True,
        )
    elif rootdata_fallback_mode == "manual_link":
        statuses.append(
            ProviderStatus(
                "RootData",
                "disabled",
                detail="缺少 ROOTDATA_API_KEY；仅提供 RootData 人工核对链接。",
            )
        )
        project_references = build_project_reference_links(
            base,
            include_official=False,
            include_rootdata=True,
        )
    else:
        statuses.append(
            ProviderStatus(
                "RootData",
                "disabled",
                detail="缺少 ROOTDATA_API_KEY，项目基本面降级已禁用。",
            )
        )
    run(
        "Jin10",
        lambda: fetch_configured_provider(
            provider="Jin10",
            url_env="JIN10_API_URL",
            key_env="JIN10_API_KEY",
            source_type="macro",
            symbol=base,
            session=client,
            timeout=request_timeout,
        ),
        optional=True,
        allow_macro=True,
    )
    for source, url in DEFAULT_MACRO_FEEDS:
        run(
            source,
            lambda source=source, url=url: fetch_rss(
                url,
                source=source,
                source_type="macro",
                session=client,
                timeout=request_timeout,
            ),
            allow_macro=True,
        )
    run(
        "X API",
        lambda: fetch_x(terms, session=client, timeout=request_timeout),
        optional=True,
    )

    unique = _deduplicate(all_items)
    unique.sort(key=lambda item: item.published_at, reverse=True)
    unique = unique[: max(1, item_limit)]
    return {
        "schema_version": "1.0",
        "symbol": normalize_binance_symbol(symbol),
        "base_asset": base,
        "as_of_utc": end.isoformat(),
        "window_start_utc": start.isoformat(),
        "lookback_days": days,
        "items": [asdict(item) for item in unique],
        "providers": [asdict(status) for status in statuses],
        "project_references": project_references,
        "warnings": warnings,
    }


_SECTION_TITLES = {
    "official_exchange": "官方交易所事件",
    "chinese_news": "中文新闻",
    "english_news": "英文新闻",
    "project_fundamentals": "项目基本面事件",
    "macro": "宏观与监管公告",
    "social_proxy": "AiCoin 的 X 信息代理",
    "social_native": "原生 X 信息",
}


def build_crypto_news_report(snapshot: dict[str, Any]) -> str:
    """将统一快照渲染为供分析代理引用的确定性 Markdown 报告。"""
    lines = [
        f"# {snapshot['symbol']} 加密新闻与事件快照",
        "",
        f"- 截止时间（UTC）：{snapshot['as_of_utc']}",
        f"- 回溯起点（UTC）：{snapshot['window_start_utc']}",
        f"- 纳入条目：{len(snapshot.get('items', []))}",
        "",
        "## 来源覆盖",
        "",
        "| 来源 | 状态 | 条目数 | 说明 |",
        "|---|---:|---:|---|",
    ]
    state_labels = {"ok": "可用", "disabled": "未配置", "error": "失败"}
    for status in snapshot.get("providers", []):
        detail = str(status.get("detail") or "").replace("|", "\\|")
        lines.append(
            f"| {status['provider']} | {state_labels.get(status['state'], status['state'])} | "
            f"{status.get('item_count', 0)} | {detail} |"
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in snapshot.get("items", []):
        grouped.setdefault(item.get("source_type", "other"), []).append(item)
    for source_type, title in _SECTION_TITLES.items():
        lines.extend(["", f"## {title}", ""])
        entries = grouped.get(source_type, [])
        if not entries:
            lines.append("本时间窗口内没有可验证条目。")
            continue
        for entry in entries:
            link = f"（[原文]({entry['url']})）" if entry.get("url") else ""
            lines.append(
                f"- **{entry['published_at']} · {entry['source']}**：{entry['title']}{link}"
            )
            if entry.get("summary") and entry["summary"] != entry["title"]:
                lines.append(f"  - 摘要：{entry['summary']}")
            if entry.get("event_type") not in (None, "", "news"):
                lines.append(f"  - 事件类型：{entry['event_type']}")
            if entry.get("sentiment"):
                lines.append(f"  - 供应商情绪标签：{entry['sentiment']}")
            if entry.get("engagement"):
                metrics = "，".join(f"{key}={value}" for key, value in entry["engagement"].items())
                lines.append(f"  - 互动：{metrics}")

    if snapshot.get("project_references"):
        lines.extend(
            [
                "",
                "## 项目人工核对入口",
                "",
                "> 以下链接未由系统自动读取，不属于本次快照证据；用于人工复核项目资料。",
                "",
            ]
        )
        for reference in snapshot["project_references"]:
            note = f" — {reference['note']}" if reference.get("note") else ""
            lines.append(f"- [{reference['label']}]({reference['url']}){note}")

    if snapshot.get("warnings"):
        lines.extend(["", "## 采集警告", ""])
        lines.extend(f"- {warning}" for warning in snapshot["warnings"])
    lines.extend(
        [
            "",
            "> 本报告只陈述截至分析日可取得的来源数据。来源未配置、失败或无相关条目均会明确显示，不应据此推断没有事件发生。",
        ]
    )
    return "\n".join(lines)
