import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from cli.models import AnalystType, AssetType
from cli.utils import detect_asset_type, filter_analysts_for_asset_type
from tradingagents.agents.analysts.market_analyst import _select_market_tools
from tradingagents.graph import trading_graph
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph


class CryptoAssetModeTests(unittest.TestCase):
    def test_detects_crypto_pair_symbols(self):
        self.assertEqual(detect_asset_type("BTC-USD"), AssetType.CRYPTO)
        self.assertEqual(detect_asset_type("eth-usd"), AssetType.CRYPTO)

    def test_defaults_non_crypto_symbols_to_stock(self):
        self.assertEqual(detect_asset_type("AAPL"), AssetType.STOCK)
        self.assertEqual(detect_asset_type("SPY"), AssetType.STOCK)

    def test_filters_out_fundamentals_analyst_for_crypto(self):
        analysts = [
            AnalystType.MARKET,
            AnalystType.SOCIAL,
            AnalystType.NEWS,
            AnalystType.FUNDAMENTALS,
        ]

        self.assertEqual(
            filter_analysts_for_asset_type(analysts, AssetType.CRYPTO),
            [
                AnalystType.MARKET,
                AnalystType.SOCIAL,
                AnalystType.NEWS,
            ],
        )

    def test_keeps_all_analysts_for_stock(self):
        analysts = [
            AnalystType.MARKET,
            AnalystType.SOCIAL,
            AnalystType.NEWS,
            AnalystType.FUNDAMENTALS,
        ]

        self.assertEqual(
            filter_analysts_for_asset_type(analysts, AssetType.STOCK),
            analysts,
        )

    def test_propagator_includes_asset_type_in_initial_state(self):
        state = Propagator().create_initial_state(
            "BTC-USD", "2026-04-18", asset_type=AssetType.CRYPTO.value
        )

        self.assertEqual(state["asset_type"], AssetType.CRYPTO.value)


@pytest.mark.unit
def test_crypto_market_analyst_only_binds_binance_tool():
    tool_names = [tool.name for tool in _select_market_tools("crypto")]
    assert tool_names == ["get_crypto_market_report"]


@pytest.mark.unit
def test_stock_market_analyst_keeps_original_tools():
    tool_names = [tool.name for tool in _select_market_tools("stock")]
    assert tool_names == [
        "get_stock_data",
        "get_indicators",
        "get_verified_market_snapshot",
    ]


@pytest.mark.unit
def test_crypto_context_does_not_resolve_yahoo_identity(monkeypatch):
    def fail_if_called(_ticker):
        raise AssertionError("加密模式不应调用 Yahoo 身份解析。")

    monkeypatch.setattr(trading_graph, "resolve_instrument_identity", fail_if_called)
    context = TradingAgentsGraph.resolve_instrument_context(None, "BTC-USD", "crypto")
    assert "BTC-USD" in context
    assert "crypto asset" in context


@pytest.mark.unit
def test_crypto_propagate_skips_yahoo_return_resolution():
    graph = MagicMock()
    graph.checkpoint_scope.return_value = nullcontext(None)
    graph._run_graph.return_value = ({}, "Hold")

    result = TradingAgentsGraph.propagate(graph, "BTC-USD", "2026-09-18", "crypto")

    graph._resolve_pending_entries.assert_not_called()
    graph._run_graph.assert_called_once_with(
        "BTC-USD",
        "2026-09-18",
        asset_type="crypto",
        checkpoint_thread_id=None,
    )
    assert result == ({}, "Hold")


@pytest.mark.unit
def test_crypto_run_does_not_read_or_store_yahoo_memory():
    graph = MagicMock()
    graph.debug = False
    graph.checkpoint_input.side_effect = lambda state: state
    graph.resolve_instrument_context.return_value = "crypto context"
    graph.propagator.create_initial_state.return_value = {"messages": []}
    graph.propagator.get_graph_args.return_value = {}
    graph.graph.invoke.return_value = {"final_trade_decision": "Rating: Hold"}
    graph.process_signal.return_value = "Hold"

    TradingAgentsGraph._run_graph(graph, "BTC-USD", "2026-09-18", "crypto")

    graph.memory_log.get_past_context.assert_not_called()
    graph.memory_log.store_decision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
