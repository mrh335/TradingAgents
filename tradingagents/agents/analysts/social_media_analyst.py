from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_analyst_targets,
    get_congress_trades,
    get_insider_streak,
    get_language_instruction,
    get_news,
    get_short_interest,
)
from tradingagents.dataflows.config import get_config


def create_social_media_analyst(llm):
    def social_media_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_news,
            get_congress_trades,
            get_insider_streak,
            get_short_interest,
            get_analyst_targets,
        ]

        system_message = (
            "You are a social media and company specific news researcher/analyst tasked with analyzing social media posts, recent company news, and public sentiment for a specific company over the past week. You will be given a company's name your objective is to write a comprehensive long report detailing your analysis, insights, and implications for traders and investors on this company's current state after looking at social media and what people are saying about that company, analyzing sentiment data of what people feel each day about the company, and looking at recent company news."
            + "\n\n**Tools — use ALL of these:**"
            + "\n- `get_news(query, start_date, end_date)` — company-specific news and social-media discussions"
            + "\n- `get_insider_streak(ticker)` — count of consecutive insider buys vs sells over 90 days, classified BULLISH / NEUTRAL / BEARISH"
            + "\n- `get_congress_trades(ticker, lookback_days)` — STOCK Act disclosures by US House + Senate (lagging signal — 30-45d filing delay)"
            + "\n- `get_short_interest(ticker)` — short percent of float, days-to-cover, vs prior month (squeeze setup or institutional bearishness)"
            + "\n- `get_analyst_targets(ticker)` — Wall Street consensus targets + buy/hold/sell distribution"
            + "\n\n**Sentiment synthesis directives:**"
            + " Look at ALL sources possible from social media to sentiment to news AND positioning data (insider streak, short interest, analyst targets, congress trades). The positioning data is often the leading indicator that social-media chatter eventually catches up to."
            + " If insider streak is STRONG BULLISH and social-media sentiment is negative, that's a meaningful divergence worth flagging — insiders know more."
            + " If short interest is >15% and rising, the bear case is being expressed loudly via positioning even if news flow is neutral."
            + " If analyst targets imply >25% upside, the consensus disagrees with the current price — flag whether the disagreement is justified by recent news."
            + " Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
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
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
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
            "sentiment_report": report,
        }

    return social_media_analyst_node
