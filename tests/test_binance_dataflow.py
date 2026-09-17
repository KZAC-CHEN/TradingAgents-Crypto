"""Binance market snapshots are tested entirely with mocked HTTP responses."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingagents.dataflows.binance import (
    BinanceAPIError,
    BinanceEndpoints,
    BinanceRestrictedLocationError,
    collect_market_snapshot,
    fetch_klines,
    normalize_binance_symbol,
    save_market_snapshot,
)
from tradingagents.dataflows.errors import VendorRateLimitError


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        result = self.routes.get(url)
        if callable(result):
            result = result(params)
        if result is None:
            raise AssertionError(f"unexpected URL: {url}")
        return result


def _kline(open_ms, close_ms, close="102.0"):
    return [
        open_ms,
        "100.0",
        "105.0",
        "99.0",
        close,
        "12.5",
        close_ms,
        "1260.0",
        42,
        "6.5",
        "655.0",
        "0",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BTCUSDT", "BTCUSDT"),
        ("btc-usd", "BTCUSDT"),
        ("ETH/USDC", "ETHUSDT"),
        ("sol", "SOLUSDT"),
    ],
)
def test_normalize_binance_symbol(raw, expected):
    assert normalize_binance_symbol(raw) == expected


@pytest.mark.unit
def test_fetch_klines_excludes_candle_closing_after_cutoff():
    cutoff = datetime(2026, 9, 18, 8, tzinfo=timezone.utc)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    endpoint = "https://spot.test/api/v3/klines"
    session = FakeSession(
        {
            endpoint: FakeResponse(
                [
                    _kline(cutoff_ms - 8_000, cutoff_ms - 1_000),
                    _kline(cutoff_ms - 1_000, cutoff_ms + 1_000, close="999.0"),
                ]
            )
        }
    )
    frame = fetch_klines(
        "BTC-USD",
        "4h",
        as_of=cutoff,
        session=session,
        endpoints=BinanceEndpoints(spot="https://spot.test", futures="https://future.test"),
    )
    assert len(frame) == 1
    assert frame.iloc[0]["Close"] == 102.0
    assert session.calls[0][1]["symbol"] == "BTCUSDT"
    assert session.calls[0][1]["endTime"] == cutoff_ms


@pytest.mark.unit
def test_rate_limit_is_typed():
    endpoint = "https://spot.test/api/v3/klines"
    session = FakeSession({endpoint: FakeResponse({}, status_code=429, text="slow down")})
    with pytest.raises(VendorRateLimitError):
        fetch_klines(
            "BTCUSDT",
            "1d",
            session=session,
            endpoints=BinanceEndpoints(spot="https://spot.test", futures="https://future.test"),
        )


@pytest.mark.unit
def test_restricted_location_is_typed_without_echoing_response_body():
    endpoint = "https://future.test/fapi/v1/klines"
    session = FakeSession(
        {
            endpoint: FakeResponse(
                {"msg": "restricted"},
                status_code=451,
                text="long provider terms and response body",
            )
        }
    )
    with pytest.raises(BinanceRestrictedLocationError) as exc_info:
        fetch_klines(
            "BTCUSDT",
            "1d",
            market="futures",
            session=session,
            endpoints=BinanceEndpoints(spot="https://spot.test", futures="https://future.test"),
        )
    assert "current network location" in str(exc_info.value)
    assert "response body" not in str(exc_info.value)


@pytest.mark.unit
def test_invalid_json_is_typed():
    endpoint = "https://spot.test/api/v3/klines"
    session = FakeSession({endpoint: FakeResponse(ValueError("bad json"))})
    with pytest.raises(BinanceAPIError, match="invalid JSON"):
        fetch_klines(
            "BTCUSDT",
            "1d",
            session=session,
            endpoints=BinanceEndpoints(spot="https://spot.test", futures="https://future.test"),
        )


@pytest.mark.unit
def test_collect_snapshot_keeps_spot_when_futures_is_unavailable():
    close_ms = int(datetime(2026, 9, 18, 0, tzinfo=timezone.utc).timestamp() * 1000)
    candle = _kline(close_ms - 10_000, close_ms)

    def route(params):
        return FakeResponse([candle])

    routes = {
        "https://spot.test/api/v3/klines": route,
        "https://future.test/fapi/v1/klines": FakeResponse(
            {"code": -1121, "msg": "Invalid symbol."}, status_code=400, text="Invalid symbol"
        ),
        "https://future.test/fapi/v1/fundingRate": FakeResponse([], status_code=400, text="no contract"),
        "https://future.test/futures/data/openInterestHist": FakeResponse([], status_code=400, text="no contract"),
        "https://future.test/futures/data/globalLongShortAccountRatio": FakeResponse([], status_code=400, text="no contract"),
        "https://future.test/futures/data/takerlongshortRatio": FakeResponse([], status_code=400, text="no contract"),
    }
    snapshot = collect_market_snapshot(
        "BTCUSDT",
        as_of=datetime(2026, 9, 18, 1, tzinfo=timezone.utc),
        session=FakeSession(routes),
        endpoints=BinanceEndpoints(spot="https://spot.test", futures="https://future.test"),
    )

    assert snapshot["schema_version"] == 1
    assert snapshot["symbol"] == "BTCUSDT"
    assert snapshot["timeframes"]["4h"]["spot"]["candles"][0]["Close"] == 102.0
    assert snapshot["timeframes"]["4h"]["perpetual"] is None
    assert snapshot["timeframes"]["4h"]["spot"]["last_closed_at_beijing"].endswith(
        "+08:00"
    )
    assert snapshot["as_of_beijing"].endswith("+08:00")
    assert any("futures 4h" in warning for warning in snapshot["warnings"])
    assert len(snapshot["warnings"]) == 6


@pytest.mark.unit
def test_save_market_snapshot_is_utf8_json(tmp_path):
    snapshot = {
        "schema_version": 1,
        "provider": "binance",
        "symbol": "BTCUSDT",
        "description": "币安只读行情",
    }
    target = save_market_snapshot(snapshot, tmp_path / "nested" / "snapshot.json")

    assert target.read_text(encoding="utf-8").find("币安只读行情") >= 0
    assert not target.with_suffix(".json.tmp").exists()
