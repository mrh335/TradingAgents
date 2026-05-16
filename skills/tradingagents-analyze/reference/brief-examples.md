# Brief examples — what good output looks like

Examples adapted from the existing `TradingAgents/CLAUDE.md` (lines 213-239)
which serves as the canonical example in the framework. Use these as
calibration when filling in the structured Brief.

---

## Example 1 — Buy recommendation (constructive)

```json
{
  "decision": "Buy",
  "tldr": "Initiate a staged 4-5% NVDA position over 4-6 weeks. AI capex remains the primary driver and fundamentals stay strong, but near-term technical setup justifies a measured entry.",
  "timeframe": "4-6 weeks",
  "position_size": "4-5% of portfolio across three tranches",
  "entry_strategy": "Tranche 1 (~15%) at current levels near $198, Tranche 2 (~45%) at $203-205 if MACD re-expands, Tranche 3 (~40%) on any pullback to the $187-192 zone.",
  "stop_loss": "Sustained close below $183 (200-day SMA)",
  "take_profit": "Re-evaluate at $245 or after 6 weeks, whichever first",
  "triggers": [
    {"condition": "NVDA closes below $183 on volume", "action": "Exit position; thesis broken"},
    {"condition": "MACD bullish crossover on the daily", "action": "Add tranche 2 immediately"},
    {"condition": "Q3 revenue miss > 5% vs consensus", "action": "Cut position to half"}
  ],
  "key_risks": [
    "Cyclical demand cooldown if hyperscaler capex pauses",
    "China export curbs widening to consumer-grade chips",
    "AI bubble pop — stock multiple compression even with strong earnings"
  ],
  "benchmark_view": "Likely to outperform SPY by 5-10% over the next 6 weeks if AI capex narrative holds; underperforms hard in a tech selloff."
}
```

---

## Example 2 — Hold (analysis was inconclusive)

```json
{
  "decision": "Hold",
  "tldr": "The analysis was inconclusive — the PM didn't commit to a clear thesis and the bull/bear arguments cancel out. Holding is the safe default until a fresh run with stronger signals.",
  "timeframe": "Re-evaluate in 2-3 weeks",
  "position_size": "No change to existing position",
  "entry_strategy": "No new entry; wait for a directional catalyst.",
  "stop_loss": "No new stop; existing position stops remain in effect.",
  "take_profit": "No new target.",
  "triggers": [
    {"condition": "Material earnings revision (up or down) > 5%", "action": "Re-run analysis immediately"},
    {"condition": "Stock breaks 200-day SMA on volume", "action": "Lean bearish, consider underweight"},
    {"condition": "Sector ETF (XLK) breaks out of 3-month range", "action": "Re-evaluate with sector bias"}
  ],
  "key_risks": [
    "Position drifts in the absence of a clear thesis",
    "A definitive catalyst arrives between now and the next analysis",
    "Holding has an opportunity cost vs. clearer setups elsewhere"
  ],
  "benchmark_view": "Likely tracks SPY within ±2% over the next month."
}
```

---

## Calibration notes

- **`decision`** — Match the Portfolio Manager's `**Rating**` line exactly.
  Don't soften an Overweight to a Hold "to be safe."
- **`tldr`** — Lead with the action verb. "Initiate" / "Trim" / "Hold" /
  "Exit" — not "After considering all factors, one might consider..."
- **`triggers`** — Concrete and measurable. "MACD bullish crossover while
  RSI < 70" beats "if momentum improves."
- **`key_risks`** — Plain English. "Stock could drop sharply if the AI
  capex story slows down" beats "elevated multiple compression risk during
  late-cycle dynamics."
- **`benchmark_view`** — Be honest. "Unclear" is acceptable. Made-up
  precision ("+12.3% alpha") is not.
