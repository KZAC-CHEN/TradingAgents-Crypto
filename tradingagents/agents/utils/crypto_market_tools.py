from __future__ import annotations

import time
from collections import OrderedDict
from threading import Lock
from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.binance import collect_market_snapshot, normalize_binance_symbol
from tradingagents.dataflows.binance_analysis import build_deterministic_market_report
from tradingagents.dataflows.crypto_evidence import load_crypto_evidence_report

_CACHE_TTL_SECONDS = 300.0
_CACHE_MAX_ENTRIES = 16
_REPORT_CACHE: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()
_CACHE_LOCK = Lock()


def clear_crypto_market_report_cache() -> None:
    """清空进程内的币安报告缓存，主要供测试和显式刷新使用。"""
    with _CACHE_LOCK:
        _REPORT_CACHE.clear()


def _get_or_build_report(symbol: str, curr_date: str) -> str:
    """采集一次币安快照并在短时间内复用对应的确定性报告。"""
    canonical = normalize_binance_symbol(symbol)
    analysis_date = str(curr_date).strip()
    if not analysis_date:
        raise ValueError("分析日期不能为空。")

    prepared_report = load_crypto_evidence_report(canonical, analysis_date, "market")
    if prepared_report is not None:
        return prepared_report

    cache_key = (canonical, analysis_date)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _REPORT_CACHE.get(cache_key)
        if cached is not None and now - cached[0] <= _CACHE_TTL_SECONDS:
            _REPORT_CACHE.move_to_end(cache_key)
            return cached[1]

        snapshot = collect_market_snapshot(canonical, as_of=analysis_date)
        report = build_deterministic_market_report(snapshot)
        _REPORT_CACHE[cache_key] = (time.monotonic(), report)
        _REPORT_CACHE.move_to_end(cache_key)
        while len(_REPORT_CACHE) > _CACHE_MAX_ENTRIES:
            _REPORT_CACHE.popitem(last=False)
        return report


@tool
def get_crypto_market_report(
    symbol: Annotated[str, "币安交易对，也接受 BTC-USD、BTC/USDT 等常见写法"],
    curr_date: Annotated[str, "分析截止日期，格式为 YYYY-MM-DD"],
) -> str:
    """返回基于单一币安快照计算的确定性加密货币市场报告。

    报告覆盖现货与 U 本位永续合约的 4 小时和日线指标、成交量、波动率、
    资金费率、持仓量及多空数据。精确价格和指标结论应以本工具输出为准。
    """
    return _get_or_build_report(symbol, curr_date)
