"""为 Web 分析页提供币安 USDT 现货币种目录。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import requests

from tradingagents.dataflows.binance import (
    DEFAULT_TIMEOUT_SECONDS,
    BinanceAPIError,
    BinanceEndpoints,
    BinanceRestrictedLocationError,
    _get_json,
    normalize_binance_symbol,
)
from tradingagents.dataflows.errors import VendorRateLimitError

_CACHE_SECONDS = 15 * 60
_FEATURED_BASES = (
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "DOGE",
    "ADA",
    "AVAX",
    "LINK",
    "DOT",
)
_LEVERAGED_SUFFIXES = ("DOWN", "BULL", "BEAR")
_UP_SUFFIX_EXCEPTIONS = {"JUP"}

# 常用币名称保存在本地，避免为了展示名称再引入一个易限流的第三方接口。
_ASSET_NAMES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "BTC": ("比特币", "Bitcoin", ("XBT",)),
    "ETH": ("以太坊", "Ethereum", ("Ether",)),
    "BNB": ("币安币", "BNB", ("Binance Coin",)),
    "SOL": ("索拉纳", "Solana", ()),
    "XRP": ("瑞波币", "XRP", ("Ripple",)),
    "ADA": ("艾达币", "Cardano", ()),
    "DOGE": ("狗狗币", "Dogecoin", ()),
    "AVAX": ("雪崩协议", "Avalanche", ()),
    "DOT": ("波卡", "Polkadot", ()),
    "LINK": ("Chainlink", "Chainlink", ()),
    "LTC": ("莱特币", "Litecoin", ()),
    "BCH": ("比特币现金", "Bitcoin Cash", ()),
    "TRX": ("波场", "TRON", ()),
    "SUI": ("Sui", "Sui", ()),
    "TON": ("开放网络", "Toncoin", ("The Open Network",)),
    "SHIB": ("柴犬币", "Shiba Inu", ()),
    "UNI": ("Uniswap", "Uniswap", ()),
    "AAVE": ("Aave", "Aave", ()),
    "NEAR": ("Near", "NEAR Protocol", ()),
    "PEPE": ("佩佩币", "Pepe", ()),
}
_FALLBACK_BASES = tuple(_ASSET_NAMES)


def normalize_crypto_symbol(raw: str) -> str:
    """把基础币或常见交易对写法统一为 ``BASE-USDT``。"""
    exchange_symbol = normalize_binance_symbol(raw)
    return f"{exchange_symbol[:-4]}-USDT"


def _is_leveraged_token(base_asset: str) -> bool:
    if base_asset.endswith(_LEVERAGED_SUFFIXES):
        return True
    return (
        len(base_asset) > 3
        and base_asset.endswith("UP")
        and base_asset not in _UP_SUFFIX_EXCEPTIONS
    )


def _asset_payload(base_asset: str, exchange_symbol: str) -> dict[str, Any]:
    names = _ASSET_NAMES.get(base_asset)
    return {
        "symbol": f"{base_asset}-USDT",
        "exchangeSymbol": exchange_symbol,
        "baseAsset": base_asset,
        "quoteAsset": "USDT",
        "nameZh": names[0] if names else None,
        "nameEn": names[1] if names else None,
        "aliases": list(names[2]) if names else [],
        "featured": base_asset in _FEATURED_BASES,
    }


def parse_exchange_info(payload: Any) -> list[dict[str, Any]]:
    """从币安 exchangeInfo 响应提取可分析的 USDT 现货交易对。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise ValueError("币安币种目录响应格式无效。")
    assets: dict[str, dict[str, Any]] = {}
    for item in payload["symbols"]:
        if not isinstance(item, dict):
            continue
        base = str(item.get("baseAsset") or "").upper()
        exchange_symbol = str(item.get("symbol") or "").upper()
        if (
            item.get("status") != "TRADING"
            or item.get("quoteAsset") != "USDT"
            or not base
            or not exchange_symbol
            or _is_leveraged_token(base)
        ):
            continue
        spot_allowed = item.get("isSpotTradingAllowed")
        permissions = item.get("permissions")
        if spot_allowed is False or (
            spot_allowed is None
            and isinstance(permissions, list)
            and permissions
            and "SPOT" not in permissions
        ):
            continue
        assets[base] = _asset_payload(base, exchange_symbol)
    featured_order = {base: index for index, base in enumerate(_FEATURED_BASES)}
    return sorted(
        assets.values(),
        key=lambda asset: (
            0 if asset["featured"] else 1,
            featured_order.get(asset["baseAsset"], len(featured_order)),
            asset["baseAsset"],
        ),
    )


def _fallback_items() -> list[dict[str, Any]]:
    return [_asset_payload(base, f"{base}USDT") for base in _FALLBACK_BASES]


class CryptoAssetCatalog:
    """缓存币安币种目录，并在网络受限时提供可解释的降级结果。"""

    def __init__(
        self,
        *,
        fetcher: Callable[[], Any] | None = None,
        ttl_seconds: float = _CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetcher = fetcher or self._fetch_binance
        self._ttl_seconds = max(0.0, float(ttl_seconds))
        self._clock = clock
        self._lock = threading.Lock()
        self._items: list[dict[str, Any]] | None = None
        self._fetched_at: str | None = None
        self._cached_at = 0.0
        self._cache_warning: str | None = None
        self._fallback_warning: str | None = None
        self._fallback_at = 0.0

    @staticmethod
    def _fetch_binance() -> Any:
        session = requests.Session()
        try:
            endpoints = BinanceEndpoints()
            return _get_json(
                session,
                f"{endpoints.spot}/api/v3/exchangeInfo",
                {},
                min(DEFAULT_TIMEOUT_SECONDS, 5.0),
            )
        finally:
            session.close()

    def discover(self, *, refresh: bool = False) -> dict[str, Any]:
        """返回当前目录；远端失败时使用可靠旧缓存或内置目录。"""
        with self._lock:
            now = self._clock()
            if self._items is not None and not refresh and now - self._cached_at < self._ttl_seconds:
                return self._result(
                    self._items,
                    "cache",
                    self._cache_warning,
                    self._fetched_at,
                )
            if (
                self._items is None
                and self._fallback_warning is not None
                and not refresh
                and now - self._fallback_at < self._ttl_seconds
            ):
                return self._result(
                    _fallback_items(),
                    "fallback",
                    self._fallback_warning,
                    None,
                )
            try:
                items = parse_exchange_info(self._fetcher())
                if not items:
                    raise ValueError("币安没有返回可用的 USDT 现货交易对。")
            except Exception as exc:
                warning = self._warning_for_error(exc)
                if self._items is not None:
                    self._cached_at = now
                    self._cache_warning = warning
                    return self._result(self._items, "cache", warning, self._fetched_at)
                self._fallback_warning = warning
                self._fallback_at = now
                return self._result(_fallback_items(), "fallback", warning, None)
            self._items = items
            self._cached_at = now
            self._fetched_at = datetime.now(timezone.utc).isoformat()
            self._cache_warning = None
            self._fallback_warning = None
            return self._result(items, "binance", None, self._fetched_at)

    @staticmethod
    def _warning_for_error(error: Exception) -> str:
        if isinstance(error, BinanceRestrictedLocationError):
            return "当前网络地区无法访问币安币种目录（HTTP 451）"
        if isinstance(error, VendorRateLimitError):
            return "币安币种目录请求频率受限，请稍后重试"
        if isinstance(error, BinanceAPIError):
            return "当前网络无法连接币安币种目录"
        return f"币安币种目录暂不可用：{error}"

    def validate(self, raw_symbol: str) -> tuple[str, bool | None, str | None]:
        """验证交易对；目录不可用时返回未知状态，由预检降级放行。"""
        normalized = normalize_crypto_symbol(raw_symbol)
        result = self.discover()
        if result["source"] == "fallback":
            return normalized, None, result["warning"]
        symbols = {item["symbol"] for item in result["items"]}
        return normalized, normalized in symbols, result["warning"]

    @staticmethod
    def _result(
        items: list[dict[str, Any]],
        source: str,
        warning: str | None,
        fetched_at: str | None,
    ) -> dict[str, Any]:
        return {
            "items": [dict(item) for item in items],
            "source": source,
            "warning": warning,
            "fetchedAt": fetched_at or datetime.now(timezone.utc).isoformat(),
        }
