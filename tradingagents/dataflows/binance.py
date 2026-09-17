"""Read-only Binance spot and USD-M futures market-data snapshots.

This module deliberately uses only public market-data endpoints.  It never
accepts an API secret and cannot place orders.  A snapshot is collected once
and can be persisted by higher layers so every analyst works from the same
closed candles and derivatives observations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .errors import NoMarketDataError, VendorError, VendorRateLimitError

SPOT_BASE_URL = "https://data-api.binance.vision"
FUTURES_BASE_URL = "https://fapi.binance.com"
DEFAULT_TIMEOUT_SECONDS = 12.0
SUPPORTED_INTERVALS = frozenset({"4h", "1d"})
_QUOTE_ASSETS = ("USDT", "USDC", "BUSD", "USD")
_BEIJING = ZoneInfo("Asia/Shanghai")


class BinanceAPIError(VendorError):
    """Binance returned an invalid response or a non-retryable API error."""


class BinanceRestrictedLocationError(BinanceAPIError):
    """Binance denied an endpoint because it is unavailable in this location."""


@dataclass(frozen=True)
class BinanceEndpoints:
    spot: str = SPOT_BASE_URL
    futures: str = FUTURES_BASE_URL


def normalize_binance_symbol(raw: str) -> str:
    """Return a Binance USDT pair such as ``BTCUSDT``.

    Common Yahoo/broker spellings are accepted for convenience.  USD, USDC and
    BUSD quoted aliases resolve to the corresponding Binance USDT pair because
    this integration compares spot with the USD-M perpetual market.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("A Binance symbol is required.")
    compact = raw.strip().upper().replace("-", "").replace("/", "").replace("_", "")
    if not compact.isalnum():
        raise ValueError(f"Invalid Binance symbol: {raw!r}.")
    for quote in _QUOTE_ASSETS:
        if compact.endswith(quote) and len(compact) > len(quote):
            return f"{compact[:-len(quote)]}USDT"
    # A bare base asset is convenient in the web watchlist.
    if 2 <= len(compact) <= 12:
        return f"{compact}USDT"
    raise ValueError(f"Cannot resolve {raw!r} to a Binance USDT pair.")


def _as_of_millis(as_of: str | datetime | None) -> int:
    """Resolve an inclusive observation cutoff in UTC milliseconds."""
    if as_of is None:
        return int(datetime.now(timezone.utc).timestamp() * 1000)
    if isinstance(as_of, datetime):
        dt = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)
    try:
        parsed = date.fromisoformat(str(as_of))
    except ValueError as exc:
        raise ValueError("as_of must be an ISO date or datetime.") from exc
    # A date means the end of that UTC day, matching the project's historical
    # analysis-date semantics while still excluding candles closed afterwards.
    dt = datetime.combine(parsed, time.max, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _get_json(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
    timeout: float,
) -> Any:
    try:
        response = session.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise BinanceAPIError(f"Binance request failed for {url}: {exc}") from exc
    if response.status_code in {418, 429}:
        raise VendorRateLimitError(
            f"Binance rate limit reached ({response.status_code}) for {url}."
        )
    if response.status_code == 451:
        raise BinanceRestrictedLocationError(
            f"Binance endpoint is unavailable from the current network location: {url}."
        )
    if response.status_code >= 400:
        detail = response.text[:300].strip()
        raise BinanceAPIError(
            f"Binance returned HTTP {response.status_code} for {url}: {detail}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise BinanceAPIError(f"Binance returned invalid JSON for {url}.") from exc


def fetch_klines(
    symbol: str,
    interval: str,
    *,
    market: str = "spot",
    limit: int = 300,
    as_of: str | datetime | None = None,
    session: requests.Session | None = None,
    endpoints: BinanceEndpoints | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> pd.DataFrame:
    """Fetch closed spot or perpetual candles as a normalized dataframe."""
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported Binance interval {interval!r}; use 4h or 1d.")
    if market not in {"spot", "futures"}:
        raise ValueError("market must be 'spot' or 'futures'.")
    if not 1 <= int(limit) <= 1000:
        raise ValueError("limit must be between 1 and 1000.")

    canonical = normalize_binance_symbol(symbol)
    cutoff_ms = _as_of_millis(as_of)
    endpoints = endpoints or BinanceEndpoints()
    base = endpoints.spot if market == "spot" else endpoints.futures
    path = "/api/v3/klines" if market == "spot" else "/fapi/v1/klines"
    client = session or requests.Session()
    payload = _get_json(
        client,
        f"{base}{path}",
        {
            "symbol": canonical,
            "interval": interval,
            "limit": int(limit),
            "endTime": cutoff_ms,
        },
        timeout,
    )
    if not isinstance(payload, list):
        raise BinanceAPIError("Binance kline response was not a list.")

    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, list) or len(item) < 11:
            raise BinanceAPIError("Binance returned a malformed kline row.")
        close_time = int(item[6])
        if close_time > cutoff_ms:
            continue
        rows.append(
            {
                "Open time": pd.to_datetime(int(item[0]), unit="ms", utc=True),
                "Close time": pd.to_datetime(close_time, unit="ms", utc=True),
                "Open": float(item[1]),
                "High": float(item[2]),
                "Low": float(item[3]),
                "Close": float(item[4]),
                "Volume": float(item[5]),
                "Quote volume": float(item[7]),
                "Trades": int(item[8]),
                "Taker buy volume": float(item[9]),
                "Taker buy quote volume": float(item[10]),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"no closed {market} {interval} candles were returned",
        )
    return frame.sort_values("Open time").reset_index(drop=True)


def _safe_public_series(
    session: requests.Session,
    base_url: str,
    path: str,
    params: dict[str, Any],
    timeout: float,
    warnings: list[str],
) -> Any | None:
    try:
        return _get_json(session, f"{base_url}{path}", params, timeout)
    except (VendorError, ValueError) as exc:
        warnings.append(f"{path}: {exc}")
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> str | None:
    try:
        return pd.to_datetime(int(value), unit="ms", utc=True).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _series_points(payload: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    points = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        point = {field: _float(item.get(field)) for field in fields}
        point["timestamp"] = _timestamp(item.get("timestamp") or item.get("fundingTime"))
        points.append(point)
    return points


def fetch_derivatives_snapshot(
    symbol: str,
    *,
    as_of: str | datetime | None = None,
    session: requests.Session | None = None,
    endpoints: BinanceEndpoints | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], list[str]]:
    """Fetch public USD-M positioning data, degrading optional series cleanly."""
    canonical = normalize_binance_symbol(symbol)
    cutoff_ms = _as_of_millis(as_of)
    client = session or requests.Session()
    endpoints = endpoints or BinanceEndpoints()
    warnings: list[str] = []
    base = endpoints.futures

    funding = _safe_public_series(
        client,
        base,
        "/fapi/v1/fundingRate",
        {"symbol": canonical, "endTime": cutoff_ms, "limit": 90},
        timeout,
        warnings,
    )
    open_interest = _safe_public_series(
        client,
        base,
        "/futures/data/openInterestHist",
        {"symbol": canonical, "period": "4h", "endTime": cutoff_ms, "limit": 180},
        timeout,
        warnings,
    )
    long_short = _safe_public_series(
        client,
        base,
        "/futures/data/globalLongShortAccountRatio",
        {"symbol": canonical, "period": "4h", "endTime": cutoff_ms, "limit": 180},
        timeout,
        warnings,
    )
    taker = _safe_public_series(
        client,
        base,
        "/futures/data/takerlongshortRatio",
        {"symbol": canonical, "period": "4h", "endTime": cutoff_ms, "limit": 180},
        timeout,
        warnings,
    )

    funding_points = []
    if isinstance(funding, list):
        for item in funding:
            if not isinstance(item, dict):
                continue
            funding_points.append(
                {
                    "funding_rate": _float(item.get("fundingRate")),
                    "mark_price": _float(item.get("markPrice")),
                    "timestamp": _timestamp(item.get("fundingTime")),
                }
            )
    return (
        {
            "funding_rate": funding_points,
            "open_interest_4h": _series_points(
                open_interest, ("sumOpenInterest", "sumOpenInterestValue")
            ),
            "global_long_short_ratio_4h": _series_points(
                long_short, ("longShortRatio", "longAccount", "shortAccount")
            ),
            "taker_buy_sell_ratio_4h": _series_points(
                taker, ("buySellRatio", "buyVol", "sellVol")
            ),
        },
        warnings,
    )


def _frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records = []
    for row in frame.to_dict(orient="records"):
        converted = {}
        for key, value in row.items():
            if isinstance(value, pd.Timestamp):
                converted[key] = value.isoformat()
            elif pd.isna(value):
                converted[key] = None
            else:
                converted[key] = value
        records.append(converted)
    return records


def _market_frame_payload(frame: pd.DataFrame) -> dict[str, Any]:
    close_time = frame.iloc[-1]["Close time"]
    if not isinstance(close_time, pd.Timestamp):
        close_time = pd.Timestamp(close_time)
    if close_time.tzinfo is None:
        close_time = close_time.tz_localize("UTC")
    close_time = close_time.tz_convert("UTC")
    return {
        "last_closed_at_utc": close_time.isoformat(),
        "last_closed_at_beijing": close_time.tz_convert(_BEIJING).isoformat(),
        "candles": _frame_records(frame),
    }


def collect_market_snapshot(
    symbol: str,
    *,
    as_of: str | datetime | None = None,
    limit: int = 300,
    session: requests.Session | None = None,
    endpoints: BinanceEndpoints | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Collect one immutable spot + perpetual market snapshot.

    Spot data is required.  A missing perpetual market is represented through
    ``warnings`` and ``None`` futures timeframes so spot-only assets remain
    analyzable without inventing derivatives data.
    """
    canonical = normalize_binance_symbol(symbol)
    cutoff_ms = _as_of_millis(as_of)
    client = session or requests.Session()
    endpoints = endpoints or BinanceEndpoints()
    warnings: list[str] = []
    timeframes: dict[str, dict[str, Any]] = {}

    for interval in ("4h", "1d"):
        spot = fetch_klines(
            canonical,
            interval,
            market="spot",
            limit=limit,
            as_of=datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc),
            session=client,
            endpoints=endpoints,
            timeout=timeout,
        )
        futures_payload = None
        try:
            futures = fetch_klines(
                canonical,
                interval,
                market="futures",
                limit=limit,
                as_of=datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc),
                session=client,
                endpoints=endpoints,
                timeout=timeout,
            )
            futures_payload = _market_frame_payload(futures)
        except (VendorError, ValueError) as exc:
            warnings.append(f"futures {interval}: {exc}")
        timeframes[interval] = {
            "spot": _market_frame_payload(spot),
            "perpetual": futures_payload,
        }

    derivatives, derivative_warnings = fetch_derivatives_snapshot(
        canonical,
        as_of=datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc),
        session=client,
        endpoints=endpoints,
        timeout=timeout,
    )
    warnings.extend(derivative_warnings)
    cutoff_dt = datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc)
    collected_at = datetime.now(timezone.utc)
    return {
        "schema_version": 1,
        "provider": "binance",
        "symbol": canonical,
        "base_asset": canonical[: -len("USDT")],
        "quote_asset": "USDT",
        "as_of_utc": cutoff_dt.isoformat(),
        "as_of_beijing": cutoff_dt.astimezone(_BEIJING).isoformat(),
        "collected_at_utc": collected_at.isoformat(),
        "collected_at_beijing": collected_at.astimezone(_BEIJING).isoformat(),
        "timeframes": timeframes,
        "derivatives": derivatives,
        "warnings": warnings,
    }


def save_market_snapshot(snapshot: dict[str, Any], path: str | Path) -> Path:
    """Atomically save a snapshot as UTF-8 JSON and return its final path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
