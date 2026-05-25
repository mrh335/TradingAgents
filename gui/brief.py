"""Plain-English "what should I actually do" brief for a completed run.

The framework's analyst reports and the Portfolio Manager's final
decision are detailed but technical. A novice reading them has to dig
through prose to find the actionable bits: decision, position size,
timeframe, entry/exit triggers, key risks.

This module asks the user's *quick-think* model to extract those into
a structured ``Brief`` (Pydantic schema, validated by LangChain's
``.with_structured_output``). The generated brief is cached per run
in SQLite so re-opening a run is instant — no LLM call.

Why quick-think and not deep-think:
- Extraction over already-written text is exactly the job model tiers
  like Haiku / gpt-4o-mini are good at.
- It's cheap (typically <$0.005 per brief) so users can run it
  routinely on every analysis.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from gui import storage
from gui.chat import _build_llm, bootstrap_env


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class Trigger(BaseModel):
    """A specific market/data condition that should drive an action."""
    condition: str = Field(
        description=(
            "Specific, measurable market or data condition. Be concrete "
            "with numbers and timeframe where possible. "
            "Example: 'NVDA closes below $183 (200-day SMA)' or "
            "'Q3 revenue miss > 5% vs consensus'."
        )
    )
    action: str = Field(
        description=(
            "Concrete action to take when the condition fires. "
            "Example: 'Reduce position by 50%; reassess thesis' or "
            "'Add the second purchase (about 45% of your target $$)'."
        )
    )


class EntryStep(BaseModel):
    """One row of the buy-in plan, table-shaped for easy reading."""
    label: str = Field(
        description=(
            "Short label for this entry step. Examples: 'Buy now (anchor)', "
            "'Wait for pullback', 'Confirm breakout', 'Earnings beat'. "
            "Avoid the word 'tranche' — use 'first purchase / second "
            "purchase' or 'now / pullback / breakout'."
        )
    )
    when: str = Field(
        description=(
            "Plain-English when. Examples: 'today at the open', "
            "'if it pulls back to $381', 'after the next earnings call', "
            "'when the 50-day average crosses above the 200-day average'."
        )
    )
    price: Optional[str] = Field(
        default=None,
        description=(
            "Target price as a string. Examples: '~$389', '$381 limit', "
            "'$220-225 range', 'market price'. Null when there's no "
            "specific price (e.g., earnings-trigger entry)."
        ),
    )
    size_pct: Optional[str] = Field(
        default=None,
        description=(
            "Size as a string with units. Examples: '0.3% of portfolio', "
            "'15 shares', '$3,000', '1/3 of total position'. Use whatever "
            "the analysis specifies, but always include the unit."
        ),
    )
    notes: Optional[str] = Field(
        default=None,
        description="One short clarifier (under 80 chars). Optional.",
    )


class ExitRule(BaseModel):
    """One row of the exit plan — when to take profits, cut losses, or
    exit on a thesis-break. Multiple rules per brief, each addressing
    a different scenario, so the user has a complete decision tree."""
    kind: str = Field(
        description=(
            "One of: 'stop_loss' (exit to limit damage), 'take_profit' "
            "(exit to lock in gains), 'time_based' (exit after a fixed "
            "date or holding period), 'thesis_break' (exit because the "
            "reason for buying is gone). Choose the closest match."
        )
    )
    condition: str = Field(
        description=(
            "Plain-English condition. Examples: 'price closes below $370 "
            "for two days in a row', 'price hits $440', 'after the Q3 "
            "earnings call regardless', 'if the DOJ announces a forced "
            "break-up of the company'."
        )
    )
    price: Optional[str] = Field(
        default=None,
        description="Concrete trigger price as a string when applicable.",
    )
    action: str = Field(
        description=(
            "What to do when triggered. Examples: 'sell everything', "
            "'sell half', 'sell 25% and re-evaluate', 'just review, no "
            "automatic action'. Be specific about the percentage."
        )
    )
    notes: Optional[str] = Field(
        default=None,
        description="One short clarifier under 80 chars. Optional.",
    )


class KeyNumber(BaseModel):
    """One row of the numbers-at-a-glance table. Common rows: current
    price, recent earnings date, next earnings date, 50-day average,
    200-day average, 52-week high, 52-week low, P/E, free cash flow
    yield. The brief picks 4-8 rows that matter most for THIS trade.
    """
    label: str = Field(
        description=(
            "What this number is, in everyday language. Examples: "
            "'Current price', 'Next earnings call', '52-week high', "
            "'Average price over last 50 days (50d SMA)'. If using a "
            "technical term, add a parenthetical in plain English."
        )
    )
    value: str = Field(
        description=(
            "The value as a string with units. Examples: '$389.21', "
            "'2026-07-30', '36% (high — competitors are 20-25%)'."
        )
    )


class LongTermView(BaseModel):
    """Long-term-investor lens — for people who can't (or don't want to)
    change positions on a daily/weekly basis.

    The rest of the brief is calibrated for active management — daily
    closing prices, technical breakouts, trader-style staged entries.
    This section steps back and asks: 'if I'm going to hold for 1-3
    years and review quarterly, what's the story?'
    """

    thesis_summary: str = Field(
        description=(
            "Plain-English 2-3 sentence summary of why this company is "
            "(or isn't) worth holding for years. Focus on the structural "
            "business advantages, growth trajectory, and competitive moat. "
            "No daily price levels. No 'tradeable levels'. Just: what's "
            "the company, and is it durably good?"
        )
    )
    core_position: str = Field(
        description=(
            "One of: 'core_position' (worth holding as a permanent part "
            "of the portfolio), 'satellite' (worth holding but not core "
            "— could be cycled out), 'avoid_long_term' (don't hold this "
            "for years; better short-term trades elsewhere), 'unclear' "
            "(thesis not strong enough either way). Be honest."
        )
    )
    multi_year_horizon: str = Field(
        description=(
            "What you expect to happen on a multi-year view. Examples: "
            "'Likely to compound 12-15% per year over 3-5 years if AI "
            "spending stays on track', 'Mature business; expect 4-6% "
            "annual price growth plus 2.8% dividend = ~7-9% total return'."
        )
    )
    accumulation_plan: Optional[str] = Field(
        default=None,
        description=(
            "For long-term investors: how to build the position WITHOUT "
            "trying to time daily entries. Examples: 'Buy 1/3 today, 1/3 "
            "in 6 months, 1/3 in 12 months — dollar-cost averaging', "
            "'Lump sum is fine; valuation is reasonable and the position "
            "is small'. Avoid daily-price triggers in this field."
        ),
    )
    structural_risks: List[str] = Field(
        default_factory=list,
        description=(
            "2-4 risks that would break the multi-year thesis — not daily "
            "wiggles. Examples: 'Antitrust break-up of the company', "
            "'Cloud commoditization eats into margins over 5 years', "
            "'Founder-CEO succession risk'. Different from `key_risks` "
            "which covers shorter-term setbacks."
        ),
    )
    review_cadence: str = Field(
        description=(
            "When to revisit this position. Examples: 'Quarterly after "
            "each earnings call', 'Annually unless a major news event', "
            "'When the stock has moved more than ±25% from your average "
            "cost basis'. Tells the user 'you don't need to look at this "
            "every day'."
        )
    )


class Brief(BaseModel):
    """Plain-English summary a non-expert can act on."""

    decision: str = Field(
        description="The single-word verdict: BUY, SELL, HOLD, REDUCE, AVOID, or WATCH."
    )
    action_plain: Optional[str] = Field(
        default=None,
        description=(
            "Plain-English action in 3-8 everyday words for a non-finance "
            "reader (e.g. a mechanical engineer who doesn't trade). NO Wall "
            "Street jargon — no 'Overweight', no 'tranche', no 'accumulate'. "
            "Map the canonical decision like so:\n"
            "  Buy         → 'buy a starter position'\n"
            "  Overweight  → 'add more than usual'\n"
            "  Hold        → 'keep what you have, no new money'\n"
            "  Underweight → 'trim about half'\n"
            "  Sell        → 'sell out completely'\n"
            "OPTIONAL ONLY for backwards-compat with pre-2026-05-15 briefs; "
            "ALWAYS fill it in new briefs. The Brief panel surfaces this "
            "directly under the decision header for readers who don't speak "
            "5-tier rating vocabulary."
        ),
    )
    tldr: str = Field(
        description=(
            "2-3 sentence plain-English summary a non-investor would understand. "
            "Avoid jargon. Lead with what action to take."
        )
    )
    timeframe: str = Field(
        description=(
            "How long this view is expected to hold, e.g. '4-6 weeks', "
            "'3-6 months', 'long-term core position'. If the analysis "
            "doesn't say, infer the most likely horizon based on the reasoning."
        )
    )
    position_size: str = Field(
        description=(
            "Recommended portfolio weight or sizing guidance. "
            "Example: '4-5% of portfolio in three tranches' or 'starter position only'."
        )
    )
    entry_strategy: str = Field(
        description=(
            "How to enter — lump sum vs scaled, with price targets where the "
            "analysis provides them. One or two short sentences."
        )
    )
    stop_loss: str = Field(
        description=(
            "Conditions or price level at which to exit if the thesis is wrong. "
            "Quote the analysis's specific level if given."
        )
    )
    take_profit: str = Field(
        description=(
            "Conditions or price level at which to take profits / scale out. "
            "May be 'no explicit target — review at <date/condition>'."
        )
    )
    triggers: List[Trigger] = Field(
        description=(
            "3-7 specific if-then trigger points the user should watch for. "
            "These are the 'tripwires' that should drive action."
        )
    )
    key_risks: List[str] = Field(
        description=(
            "3-5 main risks to this thesis, written in plain English. "
            "What would make this trade fail?"
        )
    )
    benchmark_view: str = Field(
        description=(
            "One sentence on whether this is expected to outperform a "
            "passive S&P 500 (SPY) hold over the recommended timeframe, "
            "and roughly by how much / why. Be honest if the answer is 'unclear'."
        )
    )

    # ── NEW structured table fields (v2 brief format) ──
    # Optional for backwards compat with old briefs that only have prose.
    # New briefs SHOULD populate all three so the UI can render tables
    # instead of walls of text. The LLM prompt below makes them effectively
    # required.

    entry_plan: Optional[List[EntryStep]] = Field(
        default=None,
        description=(
            "TABLE of buy-in steps. 1-4 rows depending on whether this is "
            "a single entry or a staged buy. Each row: label, when, price, "
            "size, optional notes. The user sees this as a clean table — "
            "make every cell short and scannable. NEVER stuff a paragraph "
            "into a single cell. If you find yourself wanting to write "
            "more than ~80 chars in one cell, split into more rows."
        ),
    )
    exit_plan: Optional[List[ExitRule]] = Field(
        default=None,
        description=(
            "TABLE of exit rules. Typically 3-5 rows covering: a stop_loss, "
            "one or two take_profit levels, optionally a time_based exit, "
            "and one thesis_break rule. Each row tells the user exactly "
            "when to act and how much to sell. This is the most-asked-for "
            "field — keep it tight and complete."
        ),
    )
    key_numbers: Optional[List[KeyNumber]] = Field(
        default=None,
        description=(
            "TABLE of 4-8 numbers that matter most for THIS trade. Common: "
            "Current price, Next earnings date, 50-day average, 200-day "
            "average, 52-week high, 52-week low, P/E, Free cash flow yield, "
            "Operating margin, Revenue growth (year-over-year). Pick what "
            "the underlying analysis emphasizes; skip what's not material."
        ),
    )
    jargon_glossary: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Optional plain-English definitions for any technical terms that "
            "appear in this brief. Map term → 1-sentence definition. "
            "Examples: {'200-day SMA': 'The average closing price over the "
            "last 200 trading days — a slow trend line.', 'P/E ratio': "
            "'Stock price divided by the past year of earnings per share.'}. "
            "The UI surfaces these as tooltips so the user can hover for the "
            "meaning. Skip if there's no jargon in your brief (the goal). "
            "NOTE: ~100 common terms are auto-defined globally — you only "
            "need to add entries for ticker- or analysis-specific jargon."
        ),
    )
    long_term_view: Optional[LongTermView] = Field(
        default=None,
        description=(
            "Required for new briefs. The long-term-investor lens. Most of "
            "this brief is calibrated for active management (daily prices, "
            "technical breakouts, staged entries). This section is for "
            "users who hold for years and review quarterly — a totally "
            "different decision lens. ALWAYS populate this; even if the "
            "underlying analysis is short-term-focused, you can infer the "
            "multi-year story from the fundamentals."
        ),
    )

    def to_markdown(self) -> str:
        # Plain-English action lives just under the rating header for readers
        # who don't speak Overweight/Underweight.
        plain_header = (
            f"**{self.action_plain.strip()}**\n\n" if self.action_plain else ""
        )

        # Key numbers table (if structured rows exist)
        key_numbers_md = ""
        if self.key_numbers:
            rows = "\n".join(
                f"| {n.label} | {n.value} |" for n in self.key_numbers
            )
            key_numbers_md = (
                "\n#### Key numbers at a glance\n\n"
                "| What | Value |\n|---|---|\n" + rows + "\n"
            )

        # Entry plan table
        entry_md = ""
        if self.entry_plan:
            rows = "\n".join(
                f"| {e.label} | {e.when} | {e.price or '—'} | {e.size_pct or '—'} | {e.notes or ''} |"
                for e in self.entry_plan
            )
            entry_md = (
                "\n#### How to enter\n\n"
                "| Step | When | Price | Size | Notes |\n|---|---|---|---|---|\n"
                + rows + "\n"
            )
        else:
            entry_md = f"\n**Entry strategy:** {self.entry_strategy.strip()}\n"

        # Exit plan table
        exit_md = ""
        if self.exit_plan:
            rows = "\n".join(
                f"| {e.kind.replace('_', ' ').title()} | {e.condition} | {e.price or '—'} | {e.action} |"
                for e in self.exit_plan
            )
            exit_md = (
                "\n#### How to exit\n\n"
                "| Type | Condition | Price | What to do |\n|---|---|---|---|\n"
                + rows + "\n"
            )
        else:
            exit_md = (
                f"\n**Stop loss:** {self.stop_loss.strip()}  \n"
                f"**Take profit:** {self.take_profit.strip()}\n"
            )

        triggers_md = "\n".join(
            f"| {t.condition.strip()} | {t.action.strip()} |"
            for t in self.triggers
        )
        triggers_block = (
            "\n#### Trigger points (what to watch for)\n\n"
            "| If this happens | Then do this |\n|---|---|\n" + triggers_md + "\n"
        ) if self.triggers else ""

        risks_md = "\n".join(f"- {r.strip()}" for r in self.key_risks) or "_(none)_"

        # Long-term-investor lens — separate decision frame for buy-and-hold
        ltv = self.long_term_view
        long_term_md = ""
        if ltv:
            struct_risks = "\n".join(f"- {r.strip()}" for r in ltv.structural_risks)
            long_term_md = (
                "\n#### For long-term investors (1-3 year horizon)\n\n"
                f"**Thesis:** {ltv.thesis_summary.strip()}\n\n"
                f"| Field | Value |\n|---|---|\n"
                f"| Core holding? | `{ltv.core_position}` |\n"
                f"| Multi-year outlook | {ltv.multi_year_horizon.strip()} |\n"
                f"| Review cadence | {ltv.review_cadence.strip()} |\n"
            )
            if ltv.accumulation_plan:
                long_term_md += f"| How to build position | {ltv.accumulation_plan.strip()} |\n"
            if struct_risks:
                long_term_md += (
                    "\n**Structural risks (multi-year, not daily):**\n\n"
                    + struct_risks + "\n"
                )

        return (
            f"### {self.decision}\n\n"
            f"{plain_header}"
            f"{self.tldr.strip()}\n\n"
            f"**Timeframe:** {self.timeframe.strip()}  \n"
            f"**Position size:** {self.position_size.strip()}\n"
            f"{key_numbers_md}"
            f"{entry_md}"
            f"{exit_md}"
            f"{triggers_block}"
            f"\n#### Key risks (shorter-term setbacks)\n\n{risks_md}\n\n"
            f"**vs S&P 500:** {self.benchmark_view.strip()}\n"
            f"{long_term_md}"
        )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

_PROMPT_HEADER = (
    "You are extracting an actionable trading brief from a multi-agent "
    "stock analysis. The analysis covers fundamentals, sentiment, news, "
    "technical indicators, a bull/bear debate, a trader plan, and a "
    "risk-management debate, ending with a final Portfolio Manager "
    "decision.\n\n"
    "## Audience (this matters more than anything else)\n"
    "The reader is a **mechanical engineer with NO finance background**. "
    "They understand: percentages, ratios, units, tolerances, mean/median, "
    "trend lines, and signed numbers. They DO NOT understand: 'Overweight', "
    "'Underweight', 'tranche', 'accumulate', 'multiple compression', "
    "'sector rotation', 'mean reversion', 'consensus revision', 'beta', "
    "'alpha', 'Sharpe', 'GTC limit', 'MOC'.\n\n"
    "Every sentence you write goes through a filter: 'Would my engineer "
    "friend who has never traded a stock understand this without a "
    "dictionary?' If not, rewrite it.\n\n"
    "## STRUCTURE: tables over walls of text\n"
    "Fields that hold lists (entry_plan, exit_plan, key_numbers, triggers, "
    "key_risks) get rendered as TABLES in the UI. Each cell should be:\n"
    "  - SHORT (under 80 characters where possible)\n"
    "  - SCANNABLE (a glance gives the answer)\n"
    "  - REPETITION-FREE (don't restate the ticker name in every row)\n\n"
    "If you find yourself wanting to write a paragraph into one cell, split "
    "it into multiple rows. If you find yourself repeating the same context "
    "across rows, move that context into the top-level ``tldr`` instead.\n\n"
    "Example of BAD (do not do this):\n"
    "  entry_plan: [{label: 'Multi-step entry plan', when: 'Tranche 1: 0.3% "
    "(~15 shares) at next-session open ~$389 — small anchor to guarantee "
    "participation. Tranche 2: 0.45% (~24 shares) as GTC limit at $381 …'}]\n\n"
    "Example of GOOD (do this):\n"
    "  entry_plan: [\n"
    "    {label: 'First purchase (now)', when: 'tomorrow at market open', "
    "price: '~$389', size_pct: '15 shares (0.3%)', notes: 'small anchor'},\n"
    "    {label: 'Second purchase', when: 'only if it pulls back', "
    "price: '$381', size_pct: '24 shares (0.45%)', notes: ''},\n"
    "    {label: 'Third purchase', when: 'on breakout above $400', "
    "price: '$400+', size_pct: '~27 shares (0.5%)', notes: 'momentum add'},\n"
    "  ]\n\n"
    "## Vocabulary rules (strict — no exceptions)\n"
    "1. **decision** stays in the canonical 5-tier schema (Buy / Overweight "
    "/ Hold / Underweight / Sell) because that's the API contract.\n"
    "2. **action_plain** is REQUIRED. Map the decision like this and put it "
    "in everyday language:\n"
    "    Buy         → 'buy a starter position'\n"
    "    Overweight  → 'add more than usual — gradually scale up'\n"
    "    Hold        → 'keep what you have, do not buy more'\n"
    "    Underweight → 'sell about half'\n"
    "    Sell        → 'sell out completely'\n"
    "   Never leave action_plain empty.\n"
    "3. Any of these terms in your output MUST be immediately followed by "
    "a parenthetical plain-English translation (or just drop the term):\n"
    "    Overweight, Underweight, tranche, accumulate, PEG, EV/EBITDA, "
    "    beta, alpha, RSI, MACD, MA crossover, Sharpe, drawdown, MOC, "
    "    GTC, Bollinger band, ATR, mean reversion, multiple compression, "
    "    sector rotation\n"
    "   Examples:\n"
    "    BAD : 'PEG of 0.63 makes this attractive'\n"
    "    GOOD: 'PEG ratio of 0.63 — cheaper than a fairly-priced stock; "
    "    lower numbers are better here'\n"
    "    BAD : 'place a GTC limit at $381'\n"
    "    GOOD: 'set a standing buy order at $381 that stays active until "
    "    filled or cancelled'\n"
    "4. Use jargon_glossary to define any technical term you couldn't "
    "rewrite. The UI shows these as hover-tooltips.\n"
    "5. Specific prices and percentages stay as-is. Those are concrete.\n\n"
    "## tldr\n"
    "Lead with the action a person would actually take, in ONE sentence. "
    "Optional second sentence explains why in plain terms. Do NOT include "
    "'Overweight' / 'Underweight' as standalone words in the tldr — use "
    "the plain-English mapping. Total length: 2 sentences max, 50 words "
    "max, no Wall Street jargon.\n\n"
    "BAD tldr: 'Maintain GOOGL Overweight after a 3% pullback to $388.91. "
    "Trend strongly intact (above all MAs, 50-SMA buffer +15%, 200-SMA "
    "buffer +32%). Fundamentals top-tier...'\n\n"
    "GOOD tldr: 'Add more GOOGL gradually over the next month. The stock "
    "is well above its long-term averages and the business is growing "
    "revenue 22% with 36% margins — both very strong by industry standards.'\n\n"
    "## key_risks\n"
    "3-5 plain-English 'what could go wrong' bullets. No 'multiple "
    "compression', no 'sector rotation', no 'mean reversion'.\n\n"
    "BAD: 'Multiple compression in the GenAI capex narrative'\n"
    "GOOD: 'AI spending could slow if cloud customers cut budgets, which "
    "would knock down the price even if earnings stay flat'\n\n"
    "## Process\n"
    "Read the full analysis below and produce a structured brief. Quote "
    "specific prices, levels, and timeframes from the analysis whenever it "
    "gives them. If the analysis is silent on a field, give the most "
    "reasonable inference based on the rest of the content (don't say "
    "'not specified' — make the call). When the underlying analysis is "
    "contradictory or thin (often the case with smaller local models), "
    "say so honestly in ``tldr`` and default ``decision`` to Hold.\n\n"
    "ALWAYS populate the structured table fields (entry_plan, exit_plan, "
    "key_numbers). Do not leave them null — they're the most important "
    "improvement in this brief format. Even if the analysis is thin, "
    "make reasonable inferences and fill them in.\n\n"
    "## long_term_view (REQUIRED, separate lens)\n"
    "Most of the brief is calibrated for an active trader who watches "
    "daily closing prices. **Many users are long-term investors who can't "
    "or won't change positions weekly.** The long_term_view section is "
    "for them: a totally separate decision lens that asks 'if I hold this "
    "for 1-3 years and review quarterly, is the story still good?'\n\n"
    "Required fields in long_term_view:\n"
    "- **thesis_summary** (2-3 sentences) — durable business story, NO "
    "  daily prices, NO 'tradeable levels', just what the company is and "
    "  why it's worth holding.\n"
    "- **core_position** (one of: core_position / satellite / "
    "  avoid_long_term / unclear) — Be honest. Many decent trades are "
    "  NOT core holdings.\n"
    "- **multi_year_horizon** — plain-English projection over 3-5 years.\n"
    "- **accumulation_plan** — how to build the position WITHOUT trying "
    "  to time daily entries (DCA = dollar-cost averaging is great here).\n"
    "- **structural_risks** (2-4) — risks to the MULTI-YEAR thesis, not "
    "  daily wiggles. Antitrust, secular disruption, succession.\n"
    "- **review_cadence** — when to revisit (typically quarterly or "
    "  annually). Reassures the user 'you don't need to look at this "
    "  every day'.\n\n"
    "Even if the upstream analysts focused on short-term technicals, "
    "you can almost always infer the long-term story from the "
    "fundamentals_report. Don't skip this field.\n"
)


def _state_text_for_brief(state: Dict[str, Any]) -> str:
    """Compact textual rendering of the run state for the brief prompt."""
    pieces: List[str] = []

    def add(label: str, body: Optional[str]) -> None:
        if body:
            pieces.append(f"## {label}\n\n{body}\n")

    add("Market", state.get("market_report"))
    add("Sentiment", state.get("sentiment_report"))
    add("News", state.get("news_report"))
    add("Fundamentals", state.get("fundamentals_report"))

    debate = state.get("investment_debate_state") or {}
    add("Bull case", debate.get("bull_history"))
    add("Bear case", debate.get("bear_history"))
    add("Research manager verdict", debate.get("judge_decision"))

    add("Trader plan",
        state.get("trader_investment_decision")
        or state.get("trader_investment_plan")
        or state.get("investment_plan"))

    risk = state.get("risk_debate_state") or {}
    add("Aggressive risk view", risk.get("aggressive_history"))
    add("Conservative risk view", risk.get("conservative_history"))
    add("Neutral risk view", risk.get("neutral_history"))
    add("Risk judge", risk.get("judge_decision"))
    add("FINAL PM DECISION", state.get("final_trade_decision"))

    return "\n".join(pieces)


def generate_brief(state: Dict[str, Any], meta: Dict[str, Any]) -> Brief:
    """Run the LLM call to produce a structured brief.

    Doesn't touch any cache. Callers should normally use ``get_brief``.

    Two-tier strategy (2026-05-25):

    1. **Strict structured-output path** (fast + reliable for big models)
       — uses LangChain's ``with_structured_output(Brief)`` which forces
       the LLM to comply with the full Pydantic schema. Works on
       Anthropic / OpenAI / Gemini frontier models.

    2. **Markdown-fallback path** (for small / local models that can't
       comply with strict JSON schemas) — asks the LLM to emit a
       markdown-structured response we can regex-parse into approximate
       fields. Triggered when the strict path errors out OR returns a
       brief with the required ``decision`` field empty.

    Local Ollama models (qwen2.5:7b, etc.) tended to silently fail the
    strict path and leave the brief with no tldr / triggers / risks.
    Now they get a working brief — just with fewer structured-table
    fields filled in.
    """
    bootstrap_env()
    llm = _build_llm()

    user_prompt = (
        f"Ticker: {meta.get('ticker', '?')}\n"
        f"Trade date: {meta.get('trade_date', '?')}\n"
        f"Final decision (one-word): {meta.get('decision') or '—'}\n\n"
        + _state_text_for_brief(state)
    )
    full_prompt = _PROMPT_HEADER + "\n\n" + user_prompt

    # Tier 1: try strict structured output.
    structured_err: Optional[str] = None
    try:
        structured = llm.with_structured_output(Brief)
        brief = structured.invoke(full_prompt)
        # Sanity check: a "successful" structured response that has no
        # decision means the LLM produced a malformed result that
        # LangChain swallowed silently. Treat that as a failure so we
        # fall through to the markdown path.
        if brief and brief.decision and brief.tldr:
            return brief
        structured_err = "structured response missing required fields"
    except Exception as e:
        structured_err = str(e)

    # Tier 2: markdown-fallback for models that can't comply with the
    # strict schema. Ask for a SIMPLE markdown layout we can regex.
    return _generate_brief_markdown_fallback(llm, full_prompt, meta, structured_err)


# ---------------------------------------------------------------------------
# Markdown-fallback path — used when strict structured output fails
# ---------------------------------------------------------------------------

_MARKDOWN_FALLBACK_PROMPT = (
    "\n\n---\n\n"
    "**Format instruction (mandatory):** respond in EXACTLY the markdown\n"
    "skeleton below. Do not add any other sections. Keep each section\n"
    "concise; if a field doesn't apply, write 'n/a' but DON'T omit the\n"
    "header.\n\n"
    "## Decision\n"
    "<ONE WORD: Buy | Overweight | Hold | Underweight | Sell>\n\n"
    "## Action (plain English)\n"
    "<3-8 everyday words, e.g. 'buy a starter position'>\n\n"
    "## TL;DR\n"
    "<2 sentences max; no Wall Street jargon>\n\n"
    "## Timeframe\n"
    "<e.g. '4-6 weeks'>\n\n"
    "## Position size\n"
    "<e.g. '4-5% of portfolio, 3 separate purchases'>\n\n"
    "## Entry\n"
    "<2-3 sentences on when/how to buy>\n\n"
    "## Stop loss\n"
    "<price + condition>\n\n"
    "## Take profit\n"
    "<price + condition>\n\n"
    "## Triggers\n"
    "- IF <condition> THEN <action>\n"
    "- IF <condition> THEN <action>\n"
    "- IF <condition> THEN <action>\n\n"
    "## Key risks\n"
    "- <risk 1>\n"
    "- <risk 2>\n"
    "- <risk 3>\n\n"
    "## Benchmark view\n"
    "<one sentence on vs SPY over the timeframe>\n"
)


def _generate_brief_markdown_fallback(
    llm: Any, full_prompt: str, meta: Dict[str, Any], failure_reason: str,
) -> Brief:
    """Ask the LLM to emit markdown we can regex-parse into a Brief.

    Used when strict structured output errors or yields a blank result.
    Always returns SOMETHING — at minimum a Brief with the required
    fields filled by sensible fallbacks based on ``meta``. Never raises.
    """
    import logging
    log = logging.getLogger(__name__)
    log.info("brief: structured output failed (%s); falling back to markdown", failure_reason[:200])

    try:
        from langchain_core.messages import HumanMessage
        prompt_with_format = full_prompt + _MARKDOWN_FALLBACK_PROMPT
        response = llm.invoke(prompt_with_format)
        text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        log.warning("brief: markdown-fallback LLM call also failed: %s", e)
        text = ""

    return _parse_markdown_to_brief(text or "", meta)


def _parse_markdown_to_brief(text: str, meta: Dict[str, Any]) -> Brief:
    """Regex out the markdown sections the fallback prompt asks for.

    Tolerant of: missing sections (fills with sensible default), extra
    text outside sections (ignored), case variations in headers, bullet
    styles (-, *, •), and triggers written as 'if X then Y' or
    'condition: X / action: Y'.
    """
    import re

    def _section(name: str) -> str:
        """Extract the body of a `## {name}` section, trimmed."""
        m = re.search(
            rf"^##\s*{re.escape(name)}\s*\n(.+?)(?=\n##\s|\Z)",
            text, re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        return (m.group(1).strip() if m else "")

    raw_decision = _section("Decision").split("\n")[0].strip()
    # Map common variants to the canonical 5-tier.
    decision = _normalize_decision(raw_decision) or (
        meta.get("decision") or "Hold"
    )
    action_plain = _section("Action (plain English)") or _section("Action") or None
    tldr = _section("TL;DR") or _section("TLDR") or (
        f"Analysis decision: {decision}. (Auto-generated fallback — the local "
        f"model couldn't produce a full structured brief.)"
    )
    timeframe = _section("Timeframe") or "4-6 weeks (unspecified)"
    position_size = _section("Position size") or "unspecified"
    entry = _section("Entry") or _section("Entry strategy") or "n/a"
    stop = _section("Stop loss") or "n/a"
    take = _section("Take profit") or "n/a"
    benchmark = _section("Benchmark view") or _section("Benchmark") or "unspecified"

    # Triggers — accept a few formats.
    triggers: List[Trigger] = []
    trigger_block = _section("Triggers")
    for line in trigger_block.splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if not line:
            continue
        # Match 'IF X THEN Y' (case insensitive) — preferred shape.
        m = re.match(r"^if\s+(.+?)\s+then\s+(.+)$", line, re.IGNORECASE)
        if m:
            triggers.append(Trigger(condition=m.group(1).strip(), action=m.group(2).strip()))
            continue
        # Match 'X → Y' / 'X -> Y'
        m = re.match(r"^(.+?)\s*(?:→|->)\s*(.+)$", line)
        if m:
            triggers.append(Trigger(condition=m.group(1).strip(), action=m.group(2).strip()))
            continue
        # Best-effort: split on first colon if it looks like 'condition: action'
        if ":" in line:
            cond, _, act = line.partition(":")
            triggers.append(Trigger(condition=cond.strip(), action=act.strip()))
            continue
        # Otherwise treat as a one-sided trigger (action only)
        triggers.append(Trigger(condition=line, action="review the position"))

    if not triggers:
        triggers = [Trigger(
            condition="No specific triggers extracted from the analysis",
            action="Review the underlying reports before acting",
        )]

    risks: List[str] = []
    risk_block = _section("Key risks") or _section("Risks")
    for line in risk_block.splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if line:
            risks.append(line)
    if not risks:
        risks = ["No specific risks extracted — review the underlying reports"]

    return Brief(
        decision=decision,
        action_plain=action_plain,
        tldr=tldr,
        timeframe=timeframe,
        position_size=position_size,
        entry_strategy=entry,
        stop_loss=stop,
        take_profit=take,
        triggers=triggers,
        key_risks=risks,
        benchmark_view=benchmark,
        # v2 structured fields left None — fallback path only produces prose
    )


def _normalize_decision(raw: str) -> Optional[str]:
    """Map common decision-word variants to the canonical 5-tier schema."""
    if not raw:
        return None
    lower = raw.lower().strip().rstrip(".:,")
    # Strip surrounding markdown emphasis like **Buy**
    lower = lower.strip("*_ ")
    if lower in ("buy", "strong buy", "accumulate", "bullish", "long"):
        return "Buy"
    if lower in ("overweight", "add", "increase"):
        return "Overweight"
    if lower in ("hold", "neutral", "wait", "watch", "maintain"):
        return "Hold"
    if lower in ("underweight", "reduce", "trim"):
        return "Underweight"
    if lower in ("sell", "exit", "short", "avoid"):
        return "Sell"
    # Take the first word if multi-word
    first = lower.split()[0] if lower.split() else ""
    return _normalize_decision(first) if first and first != lower else None


# ---------------------------------------------------------------------------
# Cache (SQLite)
# ---------------------------------------------------------------------------

_BRIEF_COLUMN_INITIALIZED = False


def _ensure_column() -> None:
    """Lazy-add the ``brief_json`` column on first use.

    Old DBs created before this feature don't have the column; rather
    than ship a migration system for what is currently a single table
    addition, we ALTER on demand. The ``OperationalError`` on duplicate
    add is swallowed so repeated calls are no-ops.
    """
    global _BRIEF_COLUMN_INITIALIZED
    if _BRIEF_COLUMN_INITIALIZED:
        return
    storage.init_db()
    try:
        with sqlite3.connect(storage.DB_PATH) as c:
            c.execute("ALTER TABLE runs ADD COLUMN brief_json TEXT")
            c.commit()
    except sqlite3.OperationalError:
        # Column already exists.
        pass
    _BRIEF_COLUMN_INITIALIZED = True


def get_cached_brief(run_id: str) -> Optional[Brief]:
    """Return the cached brief for a run, or ``None`` if none generated yet."""
    if not run_id:
        return None
    _ensure_column()
    with sqlite3.connect(storage.DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT brief_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row or not row["brief_json"]:
        return None
    try:
        return Brief.model_validate_json(row["brief_json"])
    except Exception:
        return None


def store_brief(run_id: str, brief: Brief) -> None:
    if not run_id:
        return
    _ensure_column()
    with sqlite3.connect(storage.DB_PATH) as c:
        c.execute(
            "UPDATE runs SET brief_json=? WHERE run_id=?",
            (brief.model_dump_json(), run_id),
        )
        c.commit()


def get_or_generate_brief(run_id: str, state: Dict[str, Any],
                          meta: Dict[str, Any]) -> Brief:
    """Return the cached brief or generate one (and cache it)."""
    cached = get_cached_brief(run_id)
    if cached is not None:
        return cached
    brief = generate_brief(state, meta)
    store_brief(run_id, brief)
    return brief
