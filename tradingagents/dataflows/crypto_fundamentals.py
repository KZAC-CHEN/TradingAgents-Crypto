"""统一、可追溯且按分析日期截断的加密基本面快照。"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from tradingagents.dataflows.binance import normalize_binance_symbol
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.crypto_news import CryptoNewsItem, parse_rss
from tradingagents.dataflows.crypto_project_sources import (
    ProjectProfile,
    build_project_reference_links,
    get_project_profile,
)

COINGECKO_DEMO_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_BASE_URL = "https://pro-api.coingecko.com/api/v3"
DEFILLAMA_BASE_URL = "https://api.llama.fi"


class CryptoFundamentalsError(RuntimeError):
    """基本面来源返回无效内容或无法完成请求。"""


@dataclass(frozen=True)
class FundamentalsProviderStatus:
    """记录一个基本面来源在本次快照中的覆盖情况。"""

    provider: str
    state: str
    item_count: int = 0
    detail: str = ""


def _number(value: Any) -> float | None:
    """将有限数值转换为浮点数，无效值返回空。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _utc_timestamp(value: Any) -> datetime | None:
    """解析秒、毫秒或 ISO 时间并统一到 UTC。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _analysis_cutoff(curr_date: str | date | datetime) -> tuple[date, datetime]:
    """解析 UTC 分析截止时间，并兼容原有日期输入。"""
    now = datetime.now(timezone.utc)
    date_only = False
    if isinstance(curr_date, datetime):
        cutoff = curr_date if curr_date.tzinfo else curr_date.replace(tzinfo=timezone.utc)
        cutoff = cutoff.astimezone(timezone.utc)
    elif isinstance(curr_date, date):
        cutoff = datetime.combine(curr_date, time.max, tzinfo=timezone.utc)
        date_only = True
    else:
        text = str(curr_date).strip()
        try:
            day = date.fromisoformat(text)
        except ValueError:
            try:
                cutoff = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("curr_date 必须为 ISO 日期或时间。") from exc
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=timezone.utc)
            cutoff = cutoff.astimezone(timezone.utc)
        else:
            cutoff = datetime.combine(day, time.max, tzinfo=timezone.utc)
            date_only = True
    if cutoff.date() > now.date() or cutoff > now and not date_only:
        raise ValueError("curr_date 不能晚于当前 UTC 时间。")
    if cutoff.date() == now.date():
        cutoff = min(cutoff, now)
    return cutoff.date(), cutoff


def _request(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """执行公共只读请求，并把来源错误转换为稳定异常。"""
    request_headers = {
        "User-Agent": "TradingAgents/0.4 (+https://github.com/TauricResearch/TradingAgents)",
        "Accept": "application/json, application/atom+xml, application/xml, text/xml,*/*;q=0.8",
    }
    request_headers.update(headers or {})
    try:
        response = session.get(url, params=params, headers=request_headers, timeout=timeout)
    except requests.RequestException as exc:
        raise CryptoFundamentalsError(f"请求失败：{url}：{exc}") from exc
    if response.status_code == 429:
        raise CryptoFundamentalsError(f"来源触发限流：{url}")
    if response.status_code >= 400:
        raise CryptoFundamentalsError(f"来源返回 HTTP {response.status_code}：{url}")
    return response


def _request_json(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """读取 JSON，并拒绝无法解析的响应。"""
    response = _request(session, url, timeout=timeout, params=params, headers=headers)
    try:
        return response.json()
    except ValueError as exc:
        raise CryptoFundamentalsError(f"来源返回了无效 JSON：{url}") from exc


def _usd(mapping: Any) -> float | None:
    """从 CoinGecko 币种映射中提取美元值。"""
    return _number(mapping.get("usd")) if isinstance(mapping, dict) else None


def parse_coingecko_payload(
    payload: Any,
    *,
    historical: bool,
    observed_at: datetime,
) -> dict[str, Any]:
    """把 CoinGecko 当前或历史响应转换为统一估值与供应量结构。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("market_data"), dict):
        raise CryptoFundamentalsError("CoinGecko 响应缺少 market_data。")
    market = payload["market_data"]
    price = _usd(market.get("current_price"))
    market_cap = _usd(market.get("market_cap"))
    volume = _usd(market.get("total_volume"))
    implied_circulating = (
        market_cap / price
        if historical and market_cap is not None and price not in (None, 0.0)
        else None
    )
    rank = _number(market.get("market_cap_rank"))
    if rank is None:
        rank = _number(payload.get("market_cap_rank"))
    return {
        "identity": {
            "provider_id": str(payload.get("id") or ""),
            "provider_name": str(payload.get("name") or ""),
            "provider_symbol": str(payload.get("symbol") or "").upper(),
            "genesis_date": payload.get("genesis_date") if not historical else None,
            "hashing_algorithm": payload.get("hashing_algorithm") if not historical else None,
            "categories": list(payload.get("categories") or []) if not historical else [],
        },
        "valuation": {
            "source": "CoinGecko",
            "price_usd": price,
            "market_cap_usd": market_cap,
            "fully_diluted_valuation_usd": (
                None if historical else _usd(market.get("fully_diluted_valuation"))
            ),
            "volume_24h_usd": volume,
            "market_cap_rank": int(rank) if rank is not None else None,
            "market_cap_to_fdv_ratio": (
                None if historical else _number(market.get("market_cap_fdv_ratio"))
            ),
            "observed_at_utc": observed_at.isoformat(),
            "historical_snapshot": historical,
        },
        "supply": {
            "source": "CoinGecko",
            "circulating": (
                implied_circulating if historical else _number(market.get("circulating_supply"))
            ),
            "total": None if historical else _number(market.get("total_supply")),
            "maximum": None if historical else _number(market.get("max_supply")),
            "outstanding": None if historical else _number(market.get("outstanding_supply")),
            "circulating_is_estimate": historical and implied_circulating is not None,
            "method": (
                "market_cap_divided_by_price"
                if historical and implied_circulating is not None
                else "unavailable"
                if historical
                else "reported"
            ),
            "observed_at_utc": observed_at.isoformat(),
        },
    }


def _coingecko_connection() -> tuple[str, dict[str, str], str]:
    """根据 Web 配置选择 CoinGecko Demo、Keyless 或 Pro 连接。"""
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    plan = os.getenv("COINGECKO_API_PLAN", "demo").strip().lower() or "demo"
    if plan not in {"demo", "pro"}:
        raise ValueError("COINGECKO_API_PLAN 必须是 demo 或 pro。")
    if plan == "pro":
        if not api_key:
            raise CryptoFundamentalsError("CoinGecko Pro 模式缺少 COINGECKO_API_KEY。")
        return COINGECKO_PRO_BASE_URL, {"x-cg-pro-api-key": api_key}, "Pro"
    headers = {"x-cg-demo-api-key": api_key} if api_key else {}
    return COINGECKO_DEMO_BASE_URL, headers, "Demo" if api_key else "Keyless"


def fetch_coingecko_fundamentals(
    profile: ProjectProfile,
    analysis_day: date,
    cutoff: datetime,
    *,
    session: requests.Session,
    timeout: float,
) -> tuple[dict[str, Any], str]:
    """读取 CoinGecko 当前或历史市场基本面，且不把当前字段写入历史日期。"""
    base_url, headers, connection_label = _coingecko_connection()
    coin_url = f"{base_url}/coins/{quote(profile.coingecko_id, safe='')}"
    historical = analysis_day < datetime.now().astimezone().date()
    if historical:
        payload = _request_json(
            session,
            f"{coin_url}/history",
            params={"date": analysis_day.isoformat(), "localization": "false"},
            headers=headers,
            timeout=timeout,
        )
        observed_at = datetime.combine(analysis_day, time.min, tzinfo=timezone.utc)
    else:
        payload = _request_json(
            session,
            coin_url,
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
            headers=headers,
            timeout=timeout,
        )
        candidate = _utc_timestamp(
            payload.get("market_data", {}).get("last_updated")
            if isinstance(payload, dict) and isinstance(payload.get("market_data"), dict)
            else None
        ) or _utc_timestamp(payload.get("last_updated") if isinstance(payload, dict) else None)
        observed_at = candidate or datetime.now(timezone.utc)
        if observed_at > cutoff:
            raise CryptoFundamentalsError("CoinGecko 数据时间晚于分析截止时间。")
    return parse_coingecko_payload(
        payload,
        historical=historical,
        observed_at=observed_at,
    ), connection_label


def _tvl_rows(payload: Any, entity_type: str) -> list[tuple[datetime, float]]:
    """从 DefiLlama 链或协议响应中提取有效历史 TVL 点。"""
    raw_rows = (
        payload.get("tvl") if entity_type == "protocol" and isinstance(payload, dict) else payload
    )
    if not isinstance(raw_rows, list):
        raise CryptoFundamentalsError("DefiLlama 响应缺少历史 TVL 序列。")
    value_key = "totalLiquidityUSD" if entity_type == "protocol" else "tvl"
    rows: list[tuple[datetime, float]] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        timestamp = _utc_timestamp(row.get("date"))
        value = _number(row.get(value_key))
        if timestamp is not None and value is not None and value >= 0:
            rows.append((timestamp, value))
    rows.sort(key=lambda item: item[0])
    if not rows:
        raise CryptoFundamentalsError("DefiLlama 没有返回有效 TVL 数据点。")
    return rows


def parse_defillama_tvl(
    payload: Any,
    *,
    entity: str,
    entity_type: str,
    cutoff: datetime,
) -> dict[str, Any]:
    """选择截止时间及七天前最近的数据点，计算固定口径的 TVL 变化。"""
    if entity_type not in {"chain", "protocol"}:
        raise ValueError("entity_type 必须是 chain 或 protocol。")
    rows = _tvl_rows(payload, entity_type)
    eligible = [row for row in rows if row[0] <= cutoff]
    if not eligible:
        raise CryptoFundamentalsError("DefiLlama 在分析截止时间之前没有 TVL 数据。")
    latest_time, latest_value = eligible[-1]
    seven_day_cutoff = latest_time - timedelta(days=7)
    previous = [row for row in eligible if row[0] <= seven_day_cutoff]
    previous_time, previous_value = previous[-1] if previous else (None, None)
    change = (
        (latest_value / previous_value - 1) * 100 if previous_value not in (None, 0.0) else None
    )
    return {
        "source": "DefiLlama",
        "entity": entity,
        "entity_type": entity_type,
        "tvl_usd": latest_value,
        "tvl_change_7d_pct": change,
        "observed_at_utc": latest_time.isoformat(),
        "comparison_at_utc": previous_time.isoformat() if previous_time else None,
        "definition": "链上 DeFi 协议中被锁定的资产美元价值",
    }


def fetch_defillama_fundamentals(
    profile: ProjectProfile,
    cutoff: datetime,
    *,
    session: requests.Session,
    timeout: float,
) -> dict[str, Any]:
    """读取 DefiLlama 免费 API 的链或协议历史 TVL。"""
    entity = quote(profile.defillama_entity, safe="")
    if profile.defillama_entity_type == "chain":
        url = f"{DEFILLAMA_BASE_URL}/v2/historicalChainTvl/{entity}"
    elif profile.defillama_entity_type == "protocol":
        url = f"{DEFILLAMA_BASE_URL}/protocol/{entity}"
    else:
        raise CryptoFundamentalsError("该资产尚未登记 DefiLlama 链或协议标识。")
    payload = _request_json(session, url, timeout=timeout)
    return parse_defillama_tvl(
        payload,
        entity=profile.defillama_entity,
        entity_type=profile.defillama_entity_type,
        cutoff=cutoff,
    )


def parse_project_releases(
    items: list[CryptoNewsItem],
    *,
    start: datetime,
    cutoff: datetime,
    limit: int = 20,
) -> list[dict[str, str]]:
    """只保留窗口内且不晚于分析截止时间的项目发布。"""
    releases: list[tuple[datetime, CryptoNewsItem]] = []
    for item in items:
        published = _utc_timestamp(item.published_at)
        if published is not None and start <= published <= cutoff:
            releases.append((published, item))
    releases.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "source": item.source,
            "title": item.title,
            "published_at": published.isoformat(),
            "url": item.url,
        }
        for published, item in releases[: max(1, int(limit))]
    ]


def _collect_development(
    profile: ProjectProfile,
    *,
    session: requests.Session,
    cutoff: datetime,
    lookback_days: int,
    timeout: float,
    statuses: list[FundamentalsProviderStatus],
    warnings: list[str],
) -> dict[str, Any]:
    """聚合项目维护方发布订阅，并按截止时间过滤。"""
    start = datetime.combine(
        cutoff.date() - timedelta(days=max(1, int(lookback_days))),
        time.min,
        tzinfo=timezone.utc,
    )
    releases: list[dict[str, str]] = []
    for feed in profile.feeds:
        try:
            response = _request(session, feed.url, timeout=timeout)
            parsed = parse_rss(
                response.content,
                source=feed.name,
                source_type="project_fundamentals",
            )
            selected = parse_project_releases(parsed, start=start, cutoff=cutoff)
            releases.extend(selected)
            statuses.append(
                FundamentalsProviderStatus(feed.name, "ok", len(selected), "项目维护方发布订阅")
            )
        except Exception as exc:
            message = str(exc)
            statuses.append(FundamentalsProviderStatus(feed.name, "error", detail=message))
            warnings.append(f"{feed.name} 采集失败：{message}")
    releases.sort(key=lambda item: item["published_at"], reverse=True)
    releases = releases[:20]
    return {
        "lookback_days": max(1, int(lookback_days)),
        "release_count": len(releases),
        "latest_release": releases[0] if releases else None,
        "releases": releases,
    }


def calculate_fundamental_ratios(
    valuation: dict[str, Any] | None,
    supply: dict[str, Any] | None,
    network_or_protocol: dict[str, Any] | None,
) -> dict[str, float | None]:
    """用同一快照中的字段计算固定口径的估值、流动性和供应量比率。"""
    valuation = valuation or {}
    supply = supply or {}
    network_or_protocol = network_or_protocol or {}

    def percentage(numerator: Any, denominator: Any) -> float | None:
        numerator_value = _number(numerator)
        denominator_value = _number(denominator)
        if numerator_value is None or denominator_value in (None, 0.0):
            return None
        return numerator_value / denominator_value * 100

    def multiple(numerator: Any, denominator: Any) -> float | None:
        value = percentage(numerator, denominator)
        return value / 100 if value is not None else None

    return {
        "volume_24h_to_market_cap_pct": percentage(
            valuation.get("volume_24h_usd"), valuation.get("market_cap_usd")
        ),
        "market_cap_to_tvl": multiple(
            valuation.get("market_cap_usd"), network_or_protocol.get("tvl_usd")
        ),
        "fdv_to_tvl": multiple(
            valuation.get("fully_diluted_valuation_usd"),
            network_or_protocol.get("tvl_usd"),
        ),
        "circulating_to_total_supply_pct": percentage(
            supply.get("circulating"), supply.get("total")
        ),
        "circulating_to_max_supply_pct": percentage(
            supply.get("circulating"), supply.get("maximum")
        ),
    }


def collect_crypto_fundamentals_snapshot(
    symbol: str,
    curr_date: str | date | datetime,
    *,
    session: requests.Session | None = None,
    timeout: float | None = None,
    development_lookback_days: int | None = None,
) -> dict[str, Any]:
    """建立统一基本面快照；单一来源失败不会中断其余来源。"""
    config = get_config()
    request_timeout = float(timeout or config.get("crypto_fundamentals_timeout", 12.0))
    lookback_days = int(
        development_lookback_days or config.get("crypto_development_lookback_days", 90)
    )
    analysis_day, cutoff = _analysis_cutoff(curr_date)
    canonical = normalize_binance_symbol(symbol)
    base_asset = canonical.removesuffix("USDT")
    profile = get_project_profile(base_asset)
    client = session or requests.Session()
    statuses: list[FundamentalsProviderStatus] = []
    warnings: list[str] = []
    collected_at = datetime.now(timezone.utc)

    identity: dict[str, Any] = {
        "base_asset": base_asset,
        "project_name": profile.project_name if profile else base_asset,
        "coingecko_id": profile.coingecko_id if profile else "",
        "defillama_entity": profile.defillama_entity if profile else "",
        "defillama_entity_type": profile.defillama_entity_type if profile else "",
    }
    valuation: dict[str, Any] | None = None
    supply: dict[str, Any] | None = None
    network_or_protocol: dict[str, Any] | None = None
    development = {
        "lookback_days": lookback_days,
        "release_count": 0,
        "latest_release": None,
        "releases": [],
    }

    if profile is None:
        detail = f"尚未登记 {base_asset} 的基本面标识；不会按代码猜测项目。"
        statuses.extend(
            [
                FundamentalsProviderStatus("CoinGecko", "disabled", detail=detail),
                FundamentalsProviderStatus("DefiLlama", "disabled", detail=detail),
                FundamentalsProviderStatus("项目发布订阅", "disabled", detail=detail),
            ]
        )
        warnings.append(detail)
    else:
        try:
            coingecko, connection_label = fetch_coingecko_fundamentals(
                profile,
                analysis_day,
                cutoff,
                session=client,
                timeout=request_timeout,
            )
            identity.update(coingecko["identity"])
            valuation = coingecko["valuation"]
            supply = coingecko["supply"]
            statuses.append(
                FundamentalsProviderStatus(
                    "CoinGecko",
                    "ok",
                    1,
                    f"{connection_label}；{'历史日快照' if valuation['historical_snapshot'] else '当前快照'}",
                )
            )
            if supply["circulating_is_estimate"]:
                warnings.append(
                    "CoinGecko 历史接口不提供同日供应量；流通量为市值除以价格的估算，"
                    "总供应量、最大供应量和完全稀释估值保持为空。"
                )
        except Exception as exc:
            message = str(exc)
            statuses.append(FundamentalsProviderStatus("CoinGecko", "error", detail=message))
            warnings.append(f"CoinGecko 采集失败：{message}")

        if profile.defillama_entity and profile.defillama_entity_type:
            try:
                network_or_protocol = fetch_defillama_fundamentals(
                    profile,
                    cutoff,
                    session=client,
                    timeout=request_timeout,
                )
                statuses.append(
                    FundamentalsProviderStatus(
                        "DefiLlama",
                        "ok",
                        1,
                        f"免费 API；{profile.defillama_entity_type}={profile.defillama_entity}",
                    )
                )
            except Exception as exc:
                message = str(exc)
                statuses.append(FundamentalsProviderStatus("DefiLlama", "error", detail=message))
                warnings.append(f"DefiLlama 采集失败：{message}")
        else:
            detail = f"尚未登记 {base_asset} 的 DefiLlama 链或协议标识。"
            statuses.append(FundamentalsProviderStatus("DefiLlama", "disabled", detail=detail))
            warnings.append(detail)

        development = _collect_development(
            profile,
            session=client,
            cutoff=cutoff,
            lookback_days=lookback_days,
            timeout=request_timeout,
            statuses=statuses,
            warnings=warnings,
        )

    derived_metrics = calculate_fundamental_ratios(
        valuation,
        supply,
        network_or_protocol,
    )
    return {
        "schema_version": "1.0",
        "symbol": canonical,
        "base_asset": base_asset,
        "as_of_utc": cutoff.isoformat(),
        "collected_at_utc": collected_at.isoformat(),
        "identity": identity,
        "valuation": valuation,
        "supply": supply,
        "network_or_protocol": network_or_protocol,
        "derived_metrics": derived_metrics,
        "development": development,
        "providers": [asdict(status) for status in statuses],
        "official_references": build_project_reference_links(
            base_asset,
            include_official=True,
            include_rootdata=False,
        ),
        "warnings": warnings,
    }


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    """按报告口径格式化有限数值。"""
    number = _number(value)
    return "—" if number is None else f"{number:,.{digits}f}{suffix}"


def _escape(value: Any) -> str:
    """转义 Markdown 表格分隔符。"""
    return str(value or "").replace("|", "\\|")


def build_crypto_fundamentals_report(snapshot: dict[str, Any]) -> str:
    """把统一快照渲染为固定格式的中文 Markdown 报告。"""
    identity = snapshot.get("identity") or {}
    valuation = snapshot.get("valuation") or {}
    supply = snapshot.get("supply") or {}
    onchain = snapshot.get("network_or_protocol") or {}
    ratios = snapshot.get("derived_metrics") or {}
    development = snapshot.get("development") or {}
    lines = [
        f"# {snapshot['symbol']} 加密基本面快照",
        "",
        f"- 项目：{identity.get('project_name') or snapshot.get('base_asset')}",
        f"- 截止时间（UTC）：{snapshot.get('as_of_utc')}",
        f"- 采集时间（UTC）：{snapshot.get('collected_at_utc')}",
        "- 生成方式：来源数据直接归一化与固定公式计算，不调用大模型",
        "",
        "## 估值",
        "",
        "| 指标 | 数值 | 观测时间 |",
        "|---|---:|---|",
        f"| 价格 | {_fmt(valuation.get('price_usd'), 8)} USD | {valuation.get('observed_at_utc') or '—'} |",
        f"| 市值 | {_fmt(valuation.get('market_cap_usd'))} USD | {valuation.get('observed_at_utc') or '—'} |",
        f"| 完全稀释估值 | {_fmt(valuation.get('fully_diluted_valuation_usd'))} USD | {valuation.get('observed_at_utc') or '—'} |",
        f"| 24 小时成交额 | {_fmt(valuation.get('volume_24h_usd'))} USD | {valuation.get('observed_at_utc') or '—'} |",
        f"| 市值排名 | {_fmt(valuation.get('market_cap_rank'), 0)} | {valuation.get('observed_at_utc') or '—'} |",
        "",
        "## 供应量",
        "",
        "| 指标 | 数值 | 口径 |",
        "|---|---:|---|",
        f"| 流通量 | {_fmt(supply.get('circulating'))} | {_escape(supply.get('method') or 'unavailable')} |",
        f"| 总供应量 | {_fmt(supply.get('total'))} | 来源报告值 |",
        f"| 最大供应量 | {_fmt(supply.get('maximum'))} | 来源报告值 |",
        f"| Outstanding Supply | {_fmt(supply.get('outstanding'))} | 来源报告值 |",
        "",
        "## 链上或协议指标",
        "",
        "| 实体 | 类型 | TVL | 7 天变化 | 观测时间 |",
        "|---|---|---:|---:|---|",
    ]
    if onchain:
        lines.append(
            f"| {_escape(onchain.get('entity'))} | {_escape(onchain.get('entity_type'))} | "
            f"{_fmt(onchain.get('tvl_usd'))} USD | {_fmt(onchain.get('tvl_change_7d_pct'), suffix='%')} | "
            f"{onchain.get('observed_at_utc') or '—'} |"
        )
    else:
        lines.append("| — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## 派生比率",
            "",
            "| 指标 | 数值 |",
            "|---|---:|",
            f"| 24 小时成交额 / 市值 | {_fmt(ratios.get('volume_24h_to_market_cap_pct'), suffix='%')} |",
            f"| 市值 / TVL | {_fmt(ratios.get('market_cap_to_tvl'), 4)} |",
            f"| 完全稀释估值 / TVL | {_fmt(ratios.get('fdv_to_tvl'), 4)} |",
            f"| 流通量 / 总供应量 | {_fmt(ratios.get('circulating_to_total_supply_pct'), suffix='%')} |",
            f"| 流通量 / 最大供应量 | {_fmt(ratios.get('circulating_to_max_supply_pct'), suffix='%')} |",
        ]
    )

    lines.extend(
        [
            "",
            "## 开发发布",
            "",
            f"- 回看天数：{development.get('lookback_days', 0)}",
            f"- 发布数量：{development.get('release_count', 0)}",
        ]
    )
    releases = development.get("releases") or []
    if releases:
        for release in releases:
            link = f"（[原文]({release['url']})）" if release.get("url") else ""
            lines.append(
                f"- **{release['published_at']} · {_escape(release['source'])}**："
                f"{release['title']}{link}"
            )
    else:
        lines.append("- 本时间窗口内没有可验证的项目发布。")

    lines.extend(
        [
            "",
            "## 来源覆盖",
            "",
            "| 来源 | 状态 | 数据点 | 说明 |",
            "|---|---|---:|---|",
        ]
    )
    labels = {"ok": "可用", "disabled": "未覆盖", "error": "失败"}
    for status in snapshot.get("providers", []):
        lines.append(
            f"| {_escape(status.get('provider'))} | {labels.get(status.get('state'), status.get('state'))} | "
            f"{status.get('item_count', 0)} | {_escape(status.get('detail'))} |"
        )

    lines.extend(["", "## 数据完整性", ""])
    warnings = snapshot.get("warnings") or []
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- 本次快照未记录来源降级或口径警告。")
    lines.extend(
        [
            "",
            "> TVL 表示相关链或协议中的 DeFi 锁仓价值，不等同于项目收入、现金流或代币内在价值。本报告不构成投资建议。",
        ]
    )
    return "\n".join(lines) + "\n"


def save_crypto_fundamentals_snapshot(snapshot: dict[str, Any], path: str | Path) -> Path:
    """把统一快照以 UTF-8 JSON 原子写入磁盘。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def save_crypto_fundamentals_report(report: str, path: str | Path) -> Path:
    """把 Markdown 报告以 UTF-8 编码原子写入磁盘。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(report, encoding="utf-8")
    temporary.replace(target)
    return target
