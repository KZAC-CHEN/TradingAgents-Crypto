"""基于币安统一快照的确定性技术指标与中文市场报告。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

_MARKET_LABELS = {"spot": "现货", "perpetual": "U 本位永续"}
_INTERVAL_LABELS = {"4h": "4 小时", "1d": "日线"}
_REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume", "Close time")


class BinanceSnapshotValidationError(ValueError):
    """币安快照缺少确定性分析所需的数据。"""


def _finite_float(value: Any) -> float | None:
    """把有限数值转换为 float，无效值统一返回 None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 8) -> float | None:
    number = _finite_float(value)
    return round(number, digits) if number is not None else None


def _pct_change(current: Any, previous: Any) -> float | None:
    current_value = _finite_float(current)
    previous_value = _finite_float(previous)
    if current_value is None or previous_value in {None, 0.0}:
        return None
    return round((current_value / previous_value - 1.0) * 100.0, 6)


def _snapshot_frame(payload: dict[str, Any]) -> pd.DataFrame:
    """校验一个市场周期，并返回按收盘时间升序排列的数据表。"""
    candles = payload.get("candles") if isinstance(payload, dict) else None
    if not isinstance(candles, list) or not candles:
        raise BinanceSnapshotValidationError("市场周期中没有 K 线数据。")
    frame = pd.DataFrame(candles)
    missing = [column for column in _REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise BinanceSnapshotValidationError(f"K 线缺少字段：{', '.join(missing)}。")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["Close time"] = pd.to_datetime(frame["Close time"], utc=True, errors="coerce")
    frame = frame.dropna(subset=list(_REQUIRED_COLUMNS)).sort_values("Close time")
    if frame.empty:
        raise BinanceSnapshotValidationError("K 线字段无法转换为有效数值。")
    return frame.reset_index(drop=True)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """使用 Wilder 平滑方法计算 RSI。"""
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = average_gain / average_loss.replace(0, float("nan"))
    result = 100 - 100 / (1 + relative_strength)
    result = result.mask((average_loss == 0) & (average_gain > 0), 100.0)
    return result.mask((average_loss == 0) & (average_gain == 0), 50.0)


def _trend_label(close: float, ema10: float | None, sma50: float | None, sma200: float | None) -> str:
    """依据均线相对位置给出机械化趋势标签。"""
    if sma50 is not None and sma200 is not None:
        if close > sma50 > sma200:
            return "多头排列"
        if close < sma50 < sma200:
            return "空头排列"
        if close > sma50:
            return "中期偏强"
        if close < sma50:
            return "中期偏弱"
        return "中期中性"
    if ema10 is not None:
        if close > ema10:
            return "短期偏强（长期样本不足）"
        if close < ema10:
            return "短期偏弱（长期样本不足）"
    return "样本不足"


def calculate_indicators(
    payload: dict[str, Any],
    *,
    interval: str,
    market: str,
) -> tuple[dict[str, Any], list[str]]:
    """对单个市场周期计算固定口径的技术指标。"""
    frame = _snapshot_frame(payload)
    close = frame["Close"]
    high = frame["High"]
    low = frame["Low"]
    volume = frame["Volume"]

    ema10 = close.ewm(span=10, adjust=False, min_periods=10).mean()
    sma50 = close.rolling(50, min_periods=50).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    rsi14 = _rsi(close)
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    macd_histogram = macd - macd_signal
    bollinger_middle = close.rolling(20, min_periods=20).mean()
    bollinger_std = close.rolling(20, min_periods=20).std(ddof=0)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr14 = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    volume_sma20 = volume.rolling(20, min_periods=20).mean()

    latest_close = float(close.iloc[-1])
    values = {
        "candle_count": int(len(frame)),
        "last_closed_at_utc": frame.iloc[-1]["Close time"].isoformat(),
        "open": _round(frame.iloc[-1]["Open"]),
        "high": _round(frame.iloc[-1]["High"]),
        "low": _round(frame.iloc[-1]["Low"]),
        "close": _round(latest_close),
        "volume": _round(volume.iloc[-1]),
        "change_1_bar_pct": _pct_change(close.iloc[-1], close.iloc[-2]) if len(close) >= 2 else None,
        "change_24h_pct": (
            _pct_change(close.iloc[-1], close.iloc[-7])
            if interval == "4h" and len(close) >= 7
            else _pct_change(close.iloc[-1], close.iloc[-2])
            if interval == "1d" and len(close) >= 2
            else None
        ),
        "ema10": _round(ema10.iloc[-1]),
        "sma50": _round(sma50.iloc[-1]),
        "sma200": _round(sma200.iloc[-1]),
        "rsi14": _round(rsi14.iloc[-1], 6),
        "macd": _round(macd.iloc[-1]),
        "macd_signal": _round(macd_signal.iloc[-1]),
        "macd_histogram": _round(macd_histogram.iloc[-1]),
        "bollinger_middle": _round(bollinger_middle.iloc[-1]),
        "bollinger_upper": _round((bollinger_middle + 2 * bollinger_std).iloc[-1]),
        "bollinger_lower": _round((bollinger_middle - 2 * bollinger_std).iloc[-1]),
        "atr14": _round(atr14.iloc[-1]),
        "atr14_pct": (
            _round(atr14.iloc[-1] / latest_close * 100, 6) if latest_close else None
        ),
        "volume_sma20": _round(volume_sma20.iloc[-1]),
        "volume_ratio_20": (
            _round(volume.iloc[-1] / volume_sma20.iloc[-1], 6)
            if _finite_float(volume_sma20.iloc[-1]) not in {None, 0.0}
            else None
        ),
    }
    values["trend"] = _trend_label(
        latest_close,
        values["ema10"],
        values["sma50"],
        values["sma200"],
    )

    requirements = {
        "EMA10": 10,
        "RSI14/ATR14": 14,
        "布林带/成交量均值": 20,
        "MACD 信号线": 34,
        "SMA50": 50,
        "SMA200": 200,
    }
    unavailable = [name for name, minimum in requirements.items() if len(frame) < minimum]
    warnings = []
    if unavailable:
        label = f"{_INTERVAL_LABELS.get(interval, interval)} {_MARKET_LABELS.get(market, market)}"
        warnings.append(
            f"{label}只有 {len(frame)} 根已收盘 K 线，以下指标不可用：{'、'.join(unavailable)}。"
        )
    return values, warnings


def _latest(series: Any) -> dict[str, Any] | None:
    if not isinstance(series, list):
        return None
    return next((item for item in reversed(series) if isinstance(item, dict)), None)


def _series_change(series: Any, field: str, periods: int) -> float | None:
    if not isinstance(series, list) or len(series) <= periods:
        return None
    return _pct_change(series[-1].get(field), series[-1 - periods].get(field))


def _analyze_derivatives(snapshot: dict[str, Any]) -> dict[str, Any]:
    """提取合约市场最新值及固定周期变化。"""
    derivatives = snapshot.get("derivatives")
    if not isinstance(derivatives, dict):
        derivatives = {}
    funding_series = derivatives.get("funding_rate", [])
    funding_latest = _latest(funding_series)
    recent_funding = [
        value
        for item in funding_series[-3:]
        if isinstance(item, dict) and (value := _finite_float(item.get("funding_rate"))) is not None
    ]
    open_interest_series = derivatives.get("open_interest_4h", [])
    open_interest_latest = _latest(open_interest_series)
    long_short_latest = _latest(derivatives.get("global_long_short_ratio_4h", []))
    taker_latest = _latest(derivatives.get("taker_buy_sell_ratio_4h", []))

    return {
        "latest_funding_rate": _round(
            funding_latest.get("funding_rate") if funding_latest else None, 10
        ),
        "latest_funding_time": funding_latest.get("timestamp") if funding_latest else None,
        "average_last_3_funding_rate": (
            _round(sum(recent_funding) / len(recent_funding), 10) if recent_funding else None
        ),
        "open_interest_value": _round(
            open_interest_latest.get("sumOpenInterestValue") if open_interest_latest else None
        ),
        "open_interest_time": open_interest_latest.get("timestamp") if open_interest_latest else None,
        "open_interest_change_24h_pct": _series_change(
            open_interest_series, "sumOpenInterestValue", 6
        ),
        "global_long_short_ratio": _round(
            long_short_latest.get("longShortRatio") if long_short_latest else None, 6
        ),
        "long_account_ratio": _round(
            long_short_latest.get("longAccount") if long_short_latest else None, 6
        ),
        "short_account_ratio": _round(
            long_short_latest.get("shortAccount") if long_short_latest else None, 6
        ),
        "taker_buy_sell_ratio": _round(
            taker_latest.get("buySellRatio") if taker_latest else None, 6
        ),
    }


def _basis_pct(spot: dict[str, Any] | None, perpetual: dict[str, Any] | None) -> float | None:
    if not spot or not perpetual:
        return None
    return _pct_change(perpetual.get("close"), spot.get("close"))


def analyze_market_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """计算完整快照的指标并返回可序列化分析结果。"""
    if not isinstance(snapshot, dict):
        raise BinanceSnapshotValidationError("快照必须是字典。")
    if snapshot.get("provider") != "binance":
        raise BinanceSnapshotValidationError("快照数据源不是 Binance。")
    if snapshot.get("schema_version") != 1:
        raise BinanceSnapshotValidationError("不支持该快照版本。")
    if not snapshot.get("symbol"):
        raise BinanceSnapshotValidationError("快照缺少交易对。")
    timeframes = snapshot.get("timeframes")
    if not isinstance(timeframes, dict):
        raise BinanceSnapshotValidationError("快照缺少周期数据。")

    warnings = list(snapshot.get("warnings") or [])
    markets: dict[str, dict[str, Any]] = {}
    for interval in ("4h", "1d"):
        interval_payload = timeframes.get(interval)
        if not isinstance(interval_payload, dict):
            raise BinanceSnapshotValidationError(f"快照缺少 {interval} 周期。")
        markets[interval] = {}
        for market in ("spot", "perpetual"):
            payload = interval_payload.get(market)
            if payload is None:
                markets[interval][market] = None
                continue
            indicators, indicator_warnings = calculate_indicators(
                payload,
                interval=interval,
                market=market,
            )
            markets[interval][market] = indicators
            warnings.extend(indicator_warnings)

    basis = {
        interval: _basis_pct(markets[interval]["spot"], markets[interval]["perpetual"])
        for interval in ("4h", "1d")
    }
    return {
        "schema_version": 1,
        "source_snapshot_schema_version": snapshot["schema_version"],
        "provider": "binance",
        "symbol": snapshot["symbol"],
        "as_of_utc": snapshot.get("as_of_utc"),
        "as_of_beijing": snapshot.get("as_of_beijing"),
        "collected_at_utc": snapshot.get("collected_at_utc"),
        "markets": markets,
        "perpetual_basis_pct": basis,
        "derivatives": _analyze_derivatives(snapshot),
        "warnings": warnings,
    }


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = _finite_float(value)
    return "—" if number is None else f"{number:,.{digits}f}{suffix}"


def _derivatives_interpretation(analysis: dict[str, Any]) -> list[str]:
    """根据明确阈值生成合约数据的机械说明。"""
    derivatives = analysis["derivatives"]
    lines: list[str] = []
    funding = derivatives.get("latest_funding_rate")
    if funding is not None:
        if funding > 0:
            lines.append("最新资金费率为正，多头向空头支付资金费。")
        elif funding < 0:
            lines.append("最新资金费率为负，空头向多头支付资金费。")
        else:
            lines.append("最新资金费率为零。")
    open_interest_change = derivatives.get("open_interest_change_24h_pct")
    price_change = analysis["markets"]["4h"].get("perpetual")
    price_change = price_change.get("change_24h_pct") if price_change else None
    if open_interest_change is not None and price_change is not None:
        position = "增仓" if open_interest_change > 0 else "减仓" if open_interest_change < 0 else "持仓不变"
        price = "上涨" if price_change > 0 else "下跌" if price_change < 0 else "价格持平"
        lines.append(f"过去约 24 小时呈现{position}{price}。")
    return lines


def render_deterministic_market_report(analysis: dict[str, Any]) -> str:
    """把分析结果渲染为稳定的中文 Markdown 报告。"""
    lines = [
        f"# {analysis['symbol']} 确定性市场报告",
        "",
        "- 数据源：Binance 公共市场数据",
        f"- 快照截止时间（UTC）：{analysis.get('as_of_utc') or '—'}",
        f"- 快照截止时间（北京时间）：{analysis.get('as_of_beijing') or '—'}",
        "- 计算方式：固定公式直接计算，不调用大模型",
        "",
        "## 多周期技术指标",
        "",
        "| 周期 | 市场 | 收盘价 | 单根涨跌 | 24h 涨跌 | EMA10 | SMA50 | SMA200 | RSI14 | MACD 柱 | ATR14 | ATR% | 趋势 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for interval in ("4h", "1d"):
        for market in ("spot", "perpetual"):
            values = analysis["markets"][interval].get(market)
            if values is None:
                lines.append(
                    f"| {_INTERVAL_LABELS[interval]} | {_MARKET_LABELS[market]} | 数据不可用 | — | — | — | — | — | — | — | — | — | — |"
                )
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        _INTERVAL_LABELS[interval],
                        _MARKET_LABELS[market],
                        _fmt(values["close"]),
                        _fmt(values["change_1_bar_pct"], suffix="%"),
                        _fmt(values["change_24h_pct"], suffix="%"),
                        _fmt(values["ema10"]),
                        _fmt(values["sma50"]),
                        _fmt(values["sma200"]),
                        _fmt(values["rsi14"]),
                        _fmt(values["macd_histogram"], 4),
                        _fmt(values["atr14"]),
                        _fmt(values["atr14_pct"], suffix="%"),
                        values["trend"],
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## 波动区间与成交量",
            "",
            "| 周期 | 市场 | 布林下轨 | 布林中轨 | 布林上轨 | 最新成交量 | 20 期均量 | 量比 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for interval in ("4h", "1d"):
        for market in ("spot", "perpetual"):
            values = analysis["markets"][interval].get(market)
            if values is None:
                lines.append(
                    f"| {_INTERVAL_LABELS[interval]} | {_MARKET_LABELS[market]} | — | — | — | — | — | — |"
                )
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        _INTERVAL_LABELS[interval],
                        _MARKET_LABELS[market],
                        _fmt(values["bollinger_lower"]),
                        _fmt(values["bollinger_middle"]),
                        _fmt(values["bollinger_upper"]),
                        _fmt(values["volume"]),
                        _fmt(values["volume_sma20"]),
                        _fmt(values["volume_ratio_20"], 4),
                    ]
                )
                + " |"
            )

    derivatives = analysis["derivatives"]
    lines.extend(
        [
            "",
            "## 合约市场数据",
            "",
            "| 指标 | 数值 |",
            "|---|---:|",
            f"| 最新资金费率 | {_fmt(_finite_float(derivatives['latest_funding_rate']) * 100 if derivatives['latest_funding_rate'] is not None else None, 6, '%')} |",
            f"| 最近 3 次平均资金费率 | {_fmt(_finite_float(derivatives['average_last_3_funding_rate']) * 100 if derivatives['average_last_3_funding_rate'] is not None else None, 6, '%')} |",
            f"| 未平仓名义价值 | {_fmt(derivatives['open_interest_value'])} USDT |",
            f"| 未平仓量 24h 变化 | {_fmt(derivatives['open_interest_change_24h_pct'], suffix='%')} |",
            f"| 全市场账户多空比 | {_fmt(derivatives['global_long_short_ratio'], 4)} |",
            f"| 主动买卖量比 | {_fmt(derivatives['taker_buy_sell_ratio'], 4)} |",
            f"| 4 小时永续溢价 | {_fmt(analysis['perpetual_basis_pct']['4h'], 4, '%')} |",
            f"| 日线永续溢价 | {_fmt(analysis['perpetual_basis_pct']['1d'], 4, '%')} |",
        ]
    )
    interpretation = _derivatives_interpretation(analysis)
    if interpretation:
        lines.extend(["", "## 机械判读", ""])
        lines.extend(f"- {item}" for item in interpretation)
    lines.extend(
        [
            "",
            "## 数据覆盖",
            "",
            "| 周期 | 市场 | 已收盘 K 线数量 | 最后一根收盘时间（UTC） |",
            "|---|---|---:|---|",
        ]
    )
    for interval in ("4h", "1d"):
        for market in ("spot", "perpetual"):
            values = analysis["markets"][interval].get(market)
            count = values["candle_count"] if values else "—"
            closed_at = values["last_closed_at_utc"] if values else "—"
            lines.append(
                f"| {_INTERVAL_LABELS[interval]} | {_MARKET_LABELS[market]} | {count} | {closed_at} |"
            )
    lines.extend(["", "## 数据完整性", ""])
    warnings = analysis.get("warnings") or []
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- 本次快照所需数据完整，未记录降级项。")
    lines.extend(
        [
            "",
            "> 本报告只陈述快照数据和固定公式结果，不构成投资建议。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_deterministic_market_report(snapshot: dict[str, Any]) -> str:
    """从统一快照直接生成确定性市场报告。"""
    return render_deterministic_market_report(analyze_market_snapshot(snapshot))


def save_deterministic_market_report(report: str, path: str | Path) -> Path:
    """将 Markdown 报告以 UTF-8 编码原子写入磁盘。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(report, encoding="utf-8")
    temporary.replace(target)
    return target
