"""统一加密基本面快照的 LangChain 工具封装。"""

from __future__ import annotations

import time
from collections import OrderedDict
from threading import Lock
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.crypto_fundamentals import (
    build_crypto_fundamentals_report,
    collect_crypto_fundamentals_snapshot,
)

_CACHE_TTL_SECONDS = 300.0
_CACHE_MAX_ENTRIES = 16
_REPORT_CACHE: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()
_CACHE_LOCK = Lock()


def clear_crypto_fundamentals_report_cache() -> None:
    """清空进程内基本面报告缓存，主要供测试和显式刷新使用。"""
    with _CACHE_LOCK:
        _REPORT_CACHE.clear()


def get_crypto_fundamentals_report_text(symbol: str, curr_date: str) -> str:
    """采集一次统一基本面快照，并在短时间内复用渲染结果。"""
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

    snapshot = collect_crypto_fundamentals_snapshot(normalized_symbol, analysis_date)
    report = build_crypto_fundamentals_report(snapshot)
    with _CACHE_LOCK:
        _REPORT_CACHE[cache_key] = (time.monotonic(), report)
        _REPORT_CACHE.move_to_end(cache_key)
        while len(_REPORT_CACHE) > _CACHE_MAX_ENTRIES:
            _REPORT_CACHE.popitem(last=False)
    return report


@tool
def get_crypto_fundamentals_report(
    symbol: Annotated[str, "加密资产或交易对，例如 BTC、BTC-USD、BTC/USDT"],
    curr_date: Annotated[str, "分析截止日期，格式为 YYYY-MM-DD"],
) -> str:
    """返回统一、可追溯且按分析日期截断的加密基本面报告。

    报告覆盖 CoinGecko 估值与供应量、DefiLlama 链或协议 TVL、固定公式
    派生比率和项目维护方发布记录。缺少凭证、来源失败、历史供应量估算及
    尚未覆盖的资产都会在报告中明确标注。
    """
    return get_crypto_fundamentals_report_text(symbol, curr_date)
