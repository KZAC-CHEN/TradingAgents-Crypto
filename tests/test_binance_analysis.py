"""币安快照技术指标和确定性报告测试。"""

from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.dataflows.binance_analysis import (
    BinanceSnapshotValidationError,
    analyze_market_snapshot,
    build_deterministic_market_report,
    calculate_indicators,
    save_deterministic_market_report,
)


def _candles(count: int, *, offset: float = 0.0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        close = 100.0 + offset + index * 0.35 + math.sin(index / 4) * 2
        opened = close - math.sin(index / 3)
        rows.append(
            {
                "Open time": (start + timedelta(hours=4 * index)).isoformat(),
                "Close time": (start + timedelta(hours=4 * (index + 1))).isoformat(),
                "Open": opened,
                "High": max(opened, close) + 1.5,
                "Low": min(opened, close) - 1.25,
                "Close": close,
                "Volume": 1_000 + index * 5,
                "Quote volume": 100_000 + index * 500,
                "Trades": 500 + index,
                "Taker buy volume": 520 + index,
                "Taker buy quote volume": 52_000 + index * 50,
            }
        )
    return rows


def _snapshot(count: int = 220):
    spot = _candles(count)
    perpetual = _candles(count, offset=0.5)
    return {
        "schema_version": 1,
        "provider": "binance",
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "as_of_utc": "2026-09-18T08:00:00+00:00",
        "as_of_beijing": "2026-09-18T16:00:00+08:00",
        "collected_at_utc": "2026-09-18T08:00:01+00:00",
        "timeframes": {
            interval: {
                "spot": {"candles": copy.deepcopy(spot)},
                "perpetual": {"candles": copy.deepcopy(perpetual)},
            }
            for interval in ("4h", "1d")
        },
        "derivatives": {
            "funding_rate": [
                {"funding_rate": value, "mark_price": 177.0, "timestamp": f"t{index}"}
                for index, value in enumerate((0.0001, 0.0002, 0.0003))
            ],
            "open_interest_4h": [
                {
                    "sumOpenInterest": 1_000 + index,
                    "sumOpenInterestValue": 1_000_000 + index * 10_000,
                    "timestamp": f"o{index}",
                }
                for index in range(8)
            ],
            "global_long_short_ratio_4h": [
                {
                    "longShortRatio": 1.2,
                    "longAccount": 0.545,
                    "shortAccount": 0.455,
                    "timestamp": "l1",
                }
            ],
            "taker_buy_sell_ratio_4h": [
                {"buySellRatio": 1.1, "buyVol": 550, "sellVol": 500, "timestamp": "v1"}
            ],
        },
        "warnings": [],
    }


@pytest.mark.unit
def test_calculate_indicators_returns_full_long_window():
    values, warnings = calculate_indicators(
        {"candles": _candles(220)}, interval="4h", market="spot"
    )

    assert warnings == []
    assert values["candle_count"] == 220
    assert values["ema10"] is not None
    assert values["sma50"] is not None
    assert values["sma200"] is not None
    assert 0 <= values["rsi14"] <= 100
    assert values["atr14"] > 0
    assert values["trend"] in {"多头排列", "中期偏强"}


@pytest.mark.unit
def test_short_history_marks_unavailable_indicators():
    values, warnings = calculate_indicators(
        {"candles": _candles(8)}, interval="4h", market="spot"
    )

    assert values["ema10"] is None
    assert values["sma200"] is None
    assert values["trend"] == "样本不足"
    assert len(warnings) == 1
    assert "只有 8 根" in warnings[0]
    assert "SMA200" in warnings[0]


@pytest.mark.unit
def test_snapshot_analysis_is_deterministic_and_does_not_mutate_input():
    snapshot = _snapshot()
    original = copy.deepcopy(snapshot)

    first = analyze_market_snapshot(snapshot)
    second = analyze_market_snapshot(snapshot)

    assert first == second
    assert snapshot == original
    assert first["perpetual_basis_pct"]["4h"] > 0
    assert first["derivatives"]["latest_funding_rate"] == 0.0003
    assert first["derivatives"]["open_interest_change_24h_pct"] > 0


@pytest.mark.unit
def test_report_contains_grounded_tables_and_no_nan():
    report = build_deterministic_market_report(_snapshot())

    assert "# BTCUSDT 确定性市场报告" in report
    assert "4 小时" in report
    assert "U 本位永续" in report
    assert "未平仓量 24h 变化" in report
    assert "不调用大模型" in report
    assert "0.030000%" in report
    assert "布林上轨" in report
    assert "已收盘 K 线数量" in report
    assert "| nan |" not in report.lower()


@pytest.mark.unit
def test_missing_perpetual_market_is_explicit():
    snapshot = _snapshot()
    snapshot["timeframes"]["4h"]["perpetual"] = None
    snapshot["timeframes"]["1d"]["perpetual"] = None
    snapshot["warnings"] = ["合约接口不可用。"]

    analysis = analyze_market_snapshot(snapshot)
    report = build_deterministic_market_report(snapshot)

    assert analysis["markets"]["4h"]["perpetual"] is None
    assert analysis["perpetual_basis_pct"]["4h"] is None
    assert "数据不可用" in report
    assert "合约接口不可用" in report


@pytest.mark.unit
def test_invalid_snapshot_is_rejected():
    with pytest.raises(BinanceSnapshotValidationError, match="数据源"):
        analyze_market_snapshot({"schema_version": 1, "provider": "other"})


@pytest.mark.unit
def test_save_report_is_utf8_and_atomic(tmp_path):
    report = build_deterministic_market_report(_snapshot())
    target = save_deterministic_market_report(report, tmp_path / "reports" / "market.md")

    assert target.read_text(encoding="utf-8") == report
    assert not target.with_suffix(".md.tmp").exists()
