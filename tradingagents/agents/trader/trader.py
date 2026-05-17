"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools

from langchain_core.messages import AIMessage

from tradingagents.agents.schemas import TraderProposal, render_trader_proposal
from tradingagents.agents.utils.agent_utils import build_instrument_context
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.portfolio_context import get_portfolio_context
from tradingagents.dataflows.restrictions import (
    get_trading_restrictions,
    has_active_restriction,
)


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        trade_date = state.get("trade_date") or ""
        instrument_context = build_instrument_context(company_name)
        investment_plan = state["investment_plan"]

        # User's current exposure + the rest of their book — so the
        # trader sizes the proposal against reality rather than treating
        # every recommendation as a fresh starter position.
        holdings_block = get_portfolio_context(
            company_name, include_full_portfolio=True
        )

        # Hard constraint: if this ticker is in a blackout window, the
        # trader must default to Hold regardless of the analysts' signal.
        if has_active_restriction(company_name, trade_date):
            restrictions_block = (
                "\n\n" + get_trading_restrictions(company_name, trade_date)
            )
            restriction_directive = (
                " A TRADING RESTRICTION IS CURRENTLY ACTIVE on this ticker — "
                "your proposal MUST be Hold (or a deferred-action note with the "
                "blackout end date). This overrides any bullish or bearish "
                "thesis from the analysts."
            )
        else:
            restrictions_block = ""
            restriction_directive = ""

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    "Anchor your reasoning in the analysts' reports and the research plan. "
                    "Size the recommendation against the user's existing exposure shown in the "
                    "Portfolio context section."
                    + restriction_directive
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\nProposed Investment Plan: {investment_plan}\n\n"
                    f"---\n\n{holdings_block}{restrictions_block}\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        trader_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
