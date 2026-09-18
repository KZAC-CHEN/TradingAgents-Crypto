from __future__ import annotations

import pytest

from tradingagents.agents.utils import crypto_market_tools


@pytest.fixture(autouse=True)
def _clear_report_cache():
    crypto_market_tools.clear_crypto_market_report_cache()
    yield
    crypto_market_tools.clear_crypto_market_report_cache()


@pytest.mark.unit
def test_report_tool_reuses_same_snapshot(monkeypatch):
    calls = []

    def fake_collect(symbol, *, as_of):
        calls.append((symbol, as_of))
        return {"provider": "binance", "symbol": symbol}

    monkeypatch.setattr(crypto_market_tools, "collect_market_snapshot", fake_collect)
    monkeypatch.setattr(
        crypto_market_tools,
        "build_deterministic_market_report",
        lambda snapshot: f"report:{snapshot['symbol']}",
    )

    arguments = {"symbol": "BTC-USD", "curr_date": "2026-09-18"}
    first = crypto_market_tools.get_crypto_market_report.invoke(arguments)
    second = crypto_market_tools.get_crypto_market_report.invoke(arguments)

    assert first == second == "report:BTCUSDT"
    assert calls == [("BTCUSDT", "2026-09-18")]


@pytest.mark.unit
def test_report_tool_separates_symbol_and_date_cache_keys(monkeypatch):
    calls = []

    def fake_collect(symbol, *, as_of):
        calls.append((symbol, as_of))
        return {"provider": "binance", "symbol": symbol, "as_of": as_of}

    monkeypatch.setattr(crypto_market_tools, "collect_market_snapshot", fake_collect)
    monkeypatch.setattr(
        crypto_market_tools,
        "build_deterministic_market_report",
        lambda snapshot: f"{snapshot['symbol']}:{snapshot['as_of']}",
    )

    btc = crypto_market_tools.get_crypto_market_report.invoke(
        {"symbol": "BTC", "curr_date": "2026-09-18"}
    )
    eth = crypto_market_tools.get_crypto_market_report.invoke(
        {"symbol": "ETH", "curr_date": "2026-09-18"}
    )
    older_btc = crypto_market_tools.get_crypto_market_report.invoke(
        {"symbol": "BTC", "curr_date": "2026-09-17"}
    )

    assert btc == "BTCUSDT:2026-09-18"
    assert eth == "ETHUSDT:2026-09-18"
    assert older_btc == "BTCUSDT:2026-09-17"
    assert len(calls) == 3
