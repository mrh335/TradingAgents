from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_analyst_targets,
    get_balance_sheet,
    get_cashflow,
    get_congress_trades,
    get_earnings_calendar,
    get_fundamentals,
    get_income_statement,
    get_insider_streak,
    get_insider_transactions,
    get_language_instruction,
    get_short_interest,
)
from tradingagents.dataflows.config import get_config


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
            # Smart-money + market-positioning signals.
            get_insider_transactions,
            get_insider_streak,
            get_congress_trades,
            get_short_interest,
            get_analyst_targets,
            get_earnings_calendar,
        ]

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + "\n\n**Tools available:**"
            + "\n- `get_fundamentals` — comprehensive company snapshot"
            + "\n- `get_balance_sheet`, `get_cashflow`, `get_income_statement` — specific financial statements"
            + "\n- `get_insider_transactions` — raw officer/director Form 4 buys and sells"
            + "\n- `get_insider_streak` — counts CONSECUTIVE insider buys vs sells over 90 days and classifies as STRONG BULLISH / BULLISH / NEUTRAL / BEARISH / STRONG BEARISH; a sustained 5+ buy streak is one of the strongest smart-money signals available"
            + "\n- `get_congress_trades` — STOCK Act disclosures by US House + Senate members (lagging signal — 30-45d filing delay — but heavy clustering by members of relevant committees can corroborate fundamental theses)"
            + "\n- `get_short_interest` — short percent of float, days-to-cover, vs prior month change (squeeze risk + inverse-sentiment signal)"
            + "\n- `get_analyst_targets` — Wall Street consensus targets + recommendation distribution (cross-check against in-house conclusions)"
            + "\n- `get_earnings_calendar` — upcoming earnings date + EPS estimate + days-until (sizing for binary events; flag IMMINENT if T-7 or sooner)"
            + "\n\n**How to use these in your report:**"
            + " Cite specific insider names, amounts, and dates when smart-money activity is meaningful; explicitly note when activity is absent rather than skipping the topic."
            + " If short interest is elevated (>10% of float) flag squeeze potential or institutional bearishness explicitly."
            + " If the mean analyst target implies >20% upside or downside vs current price, flag the disagreement between Wall Street consensus and the price action."
            + " If earnings is within 7 days, ALWAYS flag this — the trader needs to know to size conservatively."
            + get_language_instruction(),
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
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
