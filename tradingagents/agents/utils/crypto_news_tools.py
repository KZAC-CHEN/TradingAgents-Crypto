"""加密新闻统一快照的 LangChain 工具封装。"""

from __future__ import annotations

import time
from collections import OrderedDict
from threading import Lock
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.crypto_news import (
    build_crypto_news_report,
    collect_crypto_news_snapshot,
)

_CACHE_TTL_SECONDS = 300.0
_CACHE_MAX_ENTRIES = 16
_REPORT_CACHE: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()
_CACHE_LOCK = Lock()


def clear_crypto_news_report_cache() -> None:
    """清空进程内新闻报告缓存，主要供测试和显式刷新使用。"""
    with _CACHE_LOCK:
        _REPORT_CACHE.clear()


def get_crypto_news_report_text(symbol: str, curr_date: str) -> str:
    """采集一次统一新闻快照，并在短时间内复用渲染结果。"""
    normalized_symbol = str(symbol).strip().upper()
    analysis_date = str(curr_date).strip()
    if not normalized_symbol:
        raise ValueError("加密资产代码不能为空。")
    if not analysis_date:
        raise ValueError("分析日期不能为空。")

    cache_key = (normalized_symbol, analysis_date)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _REPORT_CACHE.get(cache_key)
        if cached is not None and now - cached[0] <= _CACHE_TTL_SECONDS:
            _REPORT_CACHE.move_to_end(cache_key)
            return cached[1]

    snapshot = collect_crypto_news_snapshot(normalized_symbol, analysis_date)
    report = build_crypto_news_report(snapshot)
    with _CACHE_LOCK:
        _REPORT_CACHE[cache_key] = (time.monotonic(), report)
        _REPORT_CACHE.move_to_end(cache_key)
        while len(_REPORT_CACHE) > _CACHE_MAX_ENTRIES:
            _REPORT_CACHE.popitem(last=False)
    return report


@tool
def get_crypto_news_report(
    symbol: Annotated[str, "加密资产或交易对，例如 BTC、BTC-USD、BTC/USDT"],
    curr_date: Annotated[str, "分析截止日期，格式为 YYYY-MM-DD"],
) -> str:
    """返回统一、可追溯且严格按分析日期截断的加密新闻与事件报告。

    来源包括 Binance、OKX、AiCoin、CoinDesk、RootData、官方宏观公告和可选
    X API。缺少凭证或来源失败时，报告会显示覆盖状态而不是伪造内容。
    """
    return get_crypto_news_report_text(symbol, curr_date)
