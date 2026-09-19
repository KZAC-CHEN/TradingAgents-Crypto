from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_balance_sheet,
    get_cashflow,
    get_crypto_fundamentals_report,
    get_fundamentals,
    get_income_statement,
    get_instrument_context_from_state,
    get_language_instruction,
)


def _select_fundamentals_tools(asset_type: str):
    """按资产类型隔离公司财务工具与加密原生基本面工具。"""
    if asset_type == "crypto":
        return [get_crypto_fundamentals_report]
    return [
        get_fundamentals,
        get_balance_sheet,
        get_cashflow,
        get_income_statement,
    ]


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = get_instrument_context_from_state(state)
        tools = _select_fundamentals_tools(asset_type)

        if asset_type == "crypto":
            system_message = (
                "You are a crypto fundamentals researcher. You must call "
                "get_crypto_fundamentals_report(symbol, curr_date) exactly once before writing "
                "the report. Treat its date-bounded snapshot as the only evidence source for "
                "valuation, reported or estimated supply, DefiLlama chain or protocol TVL, "
                "derived ratios, and project-maintainer releases. Preserve every quoted value, "
                "timestamp, estimate label, provider state, and warning exactly as returned. "
                "Distinguish current reported supply from historical supply estimated as market "
                "cap divided by price. Do not convert missing values to zero. Explain that TVL is "
                "locked DeFi value rather than revenue, cash flow, or intrinsic value. Do not "
                "invent active addresses, fees, revenue, token unlocks, treasury balances, team "
                "changes, funding, governance outcomes, or website content. Evaluate valuation, "
                "liquidity, dilution, on-chain capital, development activity, data coverage, and "
                "material limitations. Append a concise Markdown table of evidence, implications, "
                "and risks." + get_language_instruction()
            )
        else:
            system_message = (
                "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
                + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
                + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
                + get_language_instruction()
            )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " Report what your tools support; another agent decides the trade."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
