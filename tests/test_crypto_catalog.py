"""Web 加密币种目录与交易对预检测试。"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from tradingagents.config_store import ConfigStore
from tradingagents.dataflows.binance import BinanceRestrictedLocationError
from tradingagents.runtime import AnalysisRequest
from tradingagents.web.app import create_web_app
from tradingagents.web.crypto_catalog import (
    CryptoAssetCatalog,
    normalize_crypto_symbol,
    parse_exchange_info,
)
from tradingagents.web.run_manager import preflight_analysis


def _symbol(
    base: str,
    *,
    quote: str = "USDT",
    status: str = "TRADING",
    spot: bool = True,
) -> dict:
    return {
        "symbol": f"{base}{quote}",
        "baseAsset": base,
        "quoteAsset": quote,
        "status": status,
        "isSpotTradingAllowed": spot,
    }


def _request(symbol: str) -> AnalysisRequest:
    return AnalysisRequest(
        symbol=symbol,
        analysis_date=date.today().isoformat(),
        analysts=("market",),
        llm_provider="ollama",
        quick_model="local-fast",
        deep_model="local-deep",
    )


def _config() -> dict:
    return {
        "llm_provider": "ollama",
        "quick_think_llm": "local-fast",
        "deep_think_llm": "local-deep",
    }


def test_exchange_info_filters_and_sorts_supported_pairs():
    assets = parse_exchange_info(
        {
            "symbols": [
                _symbol("ZRX"),
                _symbol("ETH"),
                _symbol("BTC"),
                _symbol("BTCUP"),
                _symbol("JUP"),
                _symbol("SOL", status="BREAK"),
                _symbol("ADA", quote="BTC"),
                _symbol("XRP", spot=False),
            ]
        }
    )

    assert [asset["baseAsset"] for asset in assets] == ["BTC", "ETH", "JUP", "ZRX"]
    assert assets[0]["nameZh"] == "比特币"
    assert assets[0]["symbol"] == "BTC-USDT"
    assert assets[0]["featured"] is True


def test_catalog_uses_fresh_cache_and_stale_cache_after_failure():
    clock = [100.0]
    calls = []

    def fetcher():
        calls.append(True)
        if len(calls) > 1:
            raise RuntimeError("offline")
        return {"symbols": [_symbol("BTC"), _symbol("ETH")]}

    catalog = CryptoAssetCatalog(fetcher=fetcher, ttl_seconds=10, clock=lambda: clock[0])
    first = catalog.discover()
    cached = catalog.discover()
    clock[0] = 111.0
    stale = catalog.discover()
    cached_stale = catalog.discover()

    assert first["source"] == "binance"
    assert cached["source"] == "cache"
    assert stale["source"] == "cache"
    assert "offline" in stale["warning"]
    assert "offline" in cached_stale["warning"]
    assert len(calls) == 2


def test_catalog_returns_builtin_fallback_when_first_fetch_fails():
    calls = []

    def fetcher():
        calls.append(True)
        raise RuntimeError("451")

    catalog = CryptoAssetCatalog(fetcher=fetcher)

    result = catalog.discover()
    cached_fallback = catalog.discover()
    catalog.discover(refresh=True)

    assert result["source"] == "fallback"
    assert cached_fallback["source"] == "fallback"
    assert result["items"][0]["symbol"] == "BTC-USDT"
    assert "451" in result["warning"]
    assert len(calls) == 2


def test_catalog_turns_http_451_into_user_friendly_warning():
    catalog = CryptoAssetCatalog(
        fetcher=lambda: (_ for _ in ()).throw(
            BinanceRestrictedLocationError("raw endpoint detail")
        )
    )

    result = catalog.discover()

    assert result["warning"] == "当前网络地区无法访问币安币种目录（HTTP 451）"
    assert "endpoint" not in result["warning"]


def test_crypto_symbol_normalizes_common_inputs():
    assert normalize_crypto_symbol("sui") == "SUI-USDT"
    assert normalize_crypto_symbol("suiusdt") == "SUI-USDT"
    assert normalize_crypto_symbol("SUI-USDT") == "SUI-USDT"
    assert normalize_crypto_symbol("BTC-USD") == "BTC-USDT"


def test_preflight_rejects_pair_missing_from_authoritative_catalog(tmp_path):
    catalog = CryptoAssetCatalog(fetcher=lambda: {"symbols": [_symbol("BTC")]})

    result = preflight_analysis(
        _request("SUI-USDT"),
        _config(),
        ConfigStore(tmp_path / ".env"),
        catalog,
    )

    assert result["ok"] is False
    assert "SUI-USDT 不是币安当前可交易" in result["errors"][0]


def test_preflight_warns_but_allows_pair_when_catalog_is_unavailable(tmp_path):
    catalog = CryptoAssetCatalog(fetcher=lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    result = preflight_analysis(
        _request("SUI-USDT"),
        _config(),
        ConfigStore(tmp_path / ".env"),
        catalog,
    )

    assert result["ok"] is True
    assert any("无法在线验证 SUI-USDT" in warning for warning in result["warnings"])


def test_crypto_catalog_endpoint_returns_server_side_catalog(tmp_path):
    catalog = CryptoAssetCatalog(fetcher=lambda: {"symbols": [_symbol("BTC")]})
    app = create_web_app(
        env_path=tmp_path / ".env",
        database_path=tmp_path / "runs.db",
        allowed_hosts={"testserver"},
        start_run_manager=False,
        crypto_catalog=catalog,
    )

    with TestClient(app, base_url="http://testserver") as client:
        response = client.get("/api/instruments/crypto")

    assert response.status_code == 200
    assert response.json()["items"][0]["symbol"] == "BTC-USDT"
