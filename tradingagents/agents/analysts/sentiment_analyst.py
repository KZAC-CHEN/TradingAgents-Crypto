"""面向股票和加密资产的多来源情绪分析代理。

股票模式预取 Yahoo Finance、StockTwits 和 Reddit；加密模式预取统一的新闻
与事件快照，覆盖交易所公告、中英文新闻、项目事件、宏观公告和可选 X 数据。
两种模式都在首次模型调用前注入证据，不允许模型自行调用外部工具。

输出优先使用结构化模型接口，不支持时退回普通文本生成，使情绪区间、分数和
置信度在不同模型供应商之间保持一致格式。
"""

from datetime import datetime, timedelta

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.schemas import SentimentReport, render_sentiment_report
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_news,
)
from tradingagents.agents.utils.crypto_news_tools import get_crypto_news_report_text
from tradingagents.agents.utils.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.reddit import fetch_reddit_posts
from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """创建会预取对应资产来源并生成结构化报告的情绪分析节点。"""
    structured_llm = bind_structured(llm, SentimentReport, "Sentiment Analyst")

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        asset_type = state.get("asset_type", "stock")
        instrument_context = get_instrument_context_from_state(state)

        if asset_type == "crypto":
            crypto_news_block = get_crypto_news_report_text(ticker, end_date)
            system_message = _build_crypto_system_message(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                crypto_news_block=crypto_news_block,
            )
        else:
            # 股票模式继续预取原有三个来源，保持现有行为不变。
            news_block = get_news.func(ticker, start_date, end_date)
            # 将窗口传给社交来源，防止历史分析读入今天的帖子。
            stocktwits_block = fetch_stocktwits_messages(
                ticker, limit=30, start_date=start_date, end_date=end_date
            )
            reddit_block = fetch_reddit_posts(ticker, start_date=start_date, end_date=end_date)

            system_message = _build_system_message(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                news_block=news_block,
                stocktwits_block=stocktwits_block,
                reddit_block=reddit_block,
            )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    # No tool-calling here: the data is pre-fetched into the
                    # prompt, so tool-range wording would only invite a
                    # hallucinated tool call (#1130).
                    " Today's date is {current_date}; treat it as 'now' for all analysis. {instrument_context}"
                    " " + NO_EXTERNAL_TOOLS +
                    "\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # Format the template into a concrete message list so the structured
        # and free-text paths receive the same input. No bind_tools — the
        # data is already in the prompt.
        formatted_messages = prompt.format_messages(messages=state["messages"])

        report_text = invoke_structured_or_freetext(
            structured_llm,
            llm,
            formatted_messages,
            render_sentiment_report,
            "Sentiment Analyst",
        )

        return {
            "messages": [AIMessage(content=report_text)],
            "sentiment_report": report_text,
        }

    return sentiment_analyst_node


def _build_crypto_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    crypto_news_block: str,
) -> str:
    """使用统一加密快照构建情绪分析提示词。"""
    return f"""You are a crypto market sentiment analyst. Produce a comprehensive sentiment report for {ticker} covering {start_date} through {end_date}. All available evidence has already been collected into one date-bounded report below.

## Unified crypto evidence

<start_of_crypto_news>
{crypto_news_block}
<end_of_crypto_news>

## Analysis rules

1. Weight official Binance and OKX announcements, regulator releases, and central-bank releases above commentary or social posts.
2. Use AiCoin as Chinese-language news and an X-information proxy. Clearly distinguish its proxy content from native X API content.
3. Use CoinDesk Data or its official RSS fallback as English-language reporting. Do not infer that a source was queried when its coverage table says unavailable or unconfigured.
4. Treat RootData items as project-fundamental events. Treat funding, token unlocks, governance, team changes, security incidents, and ecosystem adoption as separate catalyst types.
5. Weight social engagement by both sample size and source quality. High engagement can indicate attention or crowding; it does not prove direction.
6. Look for agreement and divergence across Chinese news, English news, official events, macro sources, and social information.
7. Reduce confidence when coverage is sparse, failed, unconfigured, or limited to one source. Never invent missing headlines, posts, links, dates, or sentiment labels.
8. Historical sentiment is evidence for the trader, not a price prediction.

## Output fields

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish.
- **overall_score**: A number from 0 to 10; 5 is neutral.
- **confidence**: low / medium / high, based on coverage, source quality, and sample size.
- **narrative**: Source-by-source evidence, divergences, dominant narratives, catalysts, risks, data limitations, and a Markdown summary table.

{get_language_instruction()}"""


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    news_block: str,
    stocktwits_block: str,
    reddit_block: str,
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on three complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### StockTwits messages — retail-trader social platform indexed by cashtag
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### Reddit posts — r/wallstreetbets, r/stocks, r/investing (past 7 days)
Community discussion. Engagement signal via upvote score and comment count. Subreddit character matters (r/wallstreetbets is often contrarian/exuberant; r/stocks more measured; r/investing longer-term).

<start_of_reddit>
{reddit_block}
<end_of_reddit>

## How to analyze this data (best practices)

1. **Read the StockTwits Bullish/Bearish ratio as a leading retail-sentiment signal.** A 70/30 bullish/bearish split is moderately bullish; ≥90/10 may indicate over-extension and contrarian risk; 50/50 is uncertainty. Sample size matters — base rates on the actual message count, not percentages alone.

2. **Look for cross-source divergences.** If news framing is bearish but StockTwits is overwhelmingly bullish, that mismatch is itself a signal — it can mean retail is leaning into a thesis the news flow hasn't caught up to (or vice versa, that retail is chasing while institutions are cautious).

3. **Weight Reddit posts by engagement.** A 400-upvote / 200-comment thread reflects community attention; a 3-upvote post is noise. Read the body excerpts for context — the title alone often misleads.

4. **Distinguish opinion from event.** A news headline ("Nvidia announces $500M Corning deal") is an event; a StockTwits post ("buying NVDA, this is going to moon") is opinion. Both are inputs but should be weighted differently in your conclusions.

5. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment.

6. **Be honest about data limits.** If StockTwits returned only a handful of messages, or one or more sources returned an "<unavailable>" placeholder, the sentiment read is less robust — flag this explicitly in the `confidence` field and the narrative. If the sources are silent on a given subreddit, say so.

7. **Identify catalysts and risks** that emerge across sources — news of upcoming earnings, product launches, competitive threats, macro headlines, etc.

8. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

## Output fields

Fill the following fields:

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Use Mixed when sources point in clearly different directions; Neutral only when all sources are genuinely silent.
- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish); 5 is neutral. Keep it consistent with overall_band.
- **confidence**: low / medium / high, based on data quality and sample size.
- **narrative**: Full source-by-source breakdown, divergences, dominant narrative themes, catalysts and risks, and a markdown summary table of key sentiment signals (direction, source, supporting evidence).

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
