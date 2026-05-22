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
            "meaning. Skip if there's no jargon in your brief (the goal)."
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
            f"\n#### Key risks\n\n{risks_md}\n\n"
            f"**vs S&P 500:** {self.benchmark_view.strip()}\n"
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
    "make reasonable inferences and fill them in.\n"
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
    """
    bootstrap_env()
    llm = _build_llm()
    structured = llm.with_structured_output(Brief)

    user_prompt = (
        f"Ticker: {meta.get('ticker', '?')}\n"
        f"Trade date: {meta.get('trade_date', '?')}\n"
        f"Final decision (one-word): {meta.get('decision') or '—'}\n\n"
        + _state_text_for_brief(state)
    )

    return structured.invoke(_PROMPT_HEADER + "\n\n" + user_prompt)


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
