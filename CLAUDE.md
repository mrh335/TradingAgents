# Claude Code companion guide

This file tells Claude Code how to participate as a **token-free analyst**
on completed run archives — reading the on-disk JSON, producing briefs
and deep dives, and writing the results back to the same directory where
the web app will pick them up.

## The deal

The web app on the NAS produces run archives at:

```
~/.tradingagents/logs/<TICKER>/TradingAgentsStrategy_logs/runs/
    <run_id>__<YYYY-MM-DD>__<UTC_ts>.json
```

When a user clicks **"Request via Claude Code"** on the Brief panel, the
web app drops a marker file next to the archive:

```
<run_id>__<date>__<ts>.brief.request.md
```

Open Claude Code in this repo, scan for pending markers, produce briefs
**without making any LLM API calls** (your parametric knowledge + the
archive contents are enough), and drop sidecar files. The web app polls
every 8s and surfaces the brief automatically.

---

## Sidecar file conventions

The web app reads any of these if present, alongside the archive:

| File | Purpose | Format |
|---|---|---|
| `*.brief.json` | Structured brief — preferred | `Brief` Pydantic schema from `gui/brief.py` |
| `*.brief.md` | Free-form markdown brief — fallback | any markdown |
| `*.brief.request.md` | Marker the web app drops to ask for a brief | template the app writes; you delete |
| `*.analysis.md` | Optional deep-dive (not surfaced yet) | any markdown |
| `*.chat.md` | Optional chat transcript (not surfaced yet) | any markdown |

The **web app never modifies sidecar files** other than dropping or deleting
request markers. Your work is safe — re-running the web side never overwrites
a brief.json that Claude Code wrote.

---

## Workflow — handle a single pending request

1. Read the request marker file. It contains the archive path and a
   templated prompt.
2. Read the archive JSON. Top-level shape:
   ```json
   {
     "kind": "tradingagents-gui-archive",
     "metadata": {"ticker": "...", "trade_date": "...", "provider": "...", ...},
     "state": {
       "market_report": "...",
       "sentiment_report": "...",
       "news_report": "...",
       "fundamentals_report": "...",
       "investment_debate_state": {"bull_history": "...", "bear_history": "...", "judge_decision": "..."},
       "trader_investment_plan": "...",
       "risk_debate_state": {"aggressive_history": "...", "conservative_history": "...", "neutral_history": "...", "judge_decision": "..."},
       "final_trade_decision": "..."
     },
     "tool_trace": [...]
   }
   ```
3. Build a `Brief` matching the schema in `gui/brief.py`. Required fields:

   | Field | Type | Notes |
   |---|---|---|
   | `decision` | str | One of: `Buy`, `Overweight`, `Hold`, `Underweight`, `Sell` |
   | `tldr` | str | 2–3 plain-English sentences a non-investor understands |
   | `timeframe` | str | e.g. `"4–6 weeks"`, `"3–6 months"`, `"long-term core position"` |
   | `position_size` | str | e.g. `"4–5% of portfolio in three tranches"` |
   | `entry_strategy` | str | How to enter — lump sum vs tranches, price targets |
   | `stop_loss` | str | Condition or price to exit if thesis fails |
   | `take_profit` | str | Condition or price to take profits |
   | `triggers` | list | 3–7 `{condition, action}` if-then trigger points |
   | `key_risks` | list[str] | 3–5 plain-English failure modes |
   | `benchmark_view` | str | One sentence on vs SPY for the timeframe |

4. Write it to `<archive_basename>.brief.json`. Example:
   ```
   data/logs/NVDA/TradingAgentsStrategy_logs/runs/
       abc123__2026-05-14__20260514T080000Z.json           ← archive
       abc123__2026-05-14__20260514T080000Z.brief.json     ← write this
   ```
5. **Delete** the `*.brief.request.md` marker so it doesn't show as
   pending forever.

The web app's `GET /runs/{id}/brief` reads `*.brief.json` if present and
returns it tagged `source: "sidecar"` — visible in the UI as a green
"🤖 from Claude Code" badge.

---

## Conventions for good briefs

- **Quote specific prices/levels from the analysis when given.** If the
  Portfolio Manager said "stop at $183 (200-day SMA)", use those exact
  numbers in `stop_loss`.
- **5-tier vocabulary for `decision`.** Don't invent new ratings;
  `"Accumulate"` is conventionally `Buy`, `"Reduce"` is `Underweight`,
  `"Avoid"` is `Sell`.
- **`tldr` leads with the action.** "Initiate a 4% position in three
  tranches" beats "After considering the analysis, one might…".
- **Triggers are concrete and measurable.** "MACD bullish crossover
  while RSI < 70" beats "if momentum improves".
- **Key risks are plain English.** Not "elevated multiple compression
  risk during late-cycle dynamics" — try "stock could drop sharply if
  the AI capex story slows down".

---

## Workflow — handle all pending requests at once

Common case: user batched 10 tickers overnight and you want to produce
briefs for all of them.

```bash
# From the repo root in Claude Code:
find ~/.tradingagents/logs -name '*.brief.request.md' -print
```

For each pending file:
- Read the request marker (contains the archive path and meta)
- Read the archive JSON
- Generate the brief
- Write the `.brief.json`
- Delete the `.brief.request.md`

If you batch them, write a short summary at the end describing what
each ticker's recommendation was — useful for the user to scan.

---

## What to do when the analysis is weak

Some Ollama / smaller-model runs produce thin or contradictory output.
If the Portfolio Manager's text doesn't actually arrive at a coherent
recommendation, **say so in the brief** rather than fabricating one:

```json
{
  "decision": "Hold",
  "tldr": "The local model's analysis was inconclusive — the PM didn't
   commit to a clear thesis and the bull/bear arguments cancel out.
   Holding is the safe default until a re-run with a stronger model.",
  ...
}
```

Better an honest "I can't extract a verdict from this" than a confident
recommendation pulled out of thin air.

---

## What NOT to do

- **Don't write `.brief.json` sidecars unless requested.** The web app
  only shows the sidecar source if it was explicitly asked for. Pre-empting
  every archive with a sidecar would confuse the user about which briefs
  are LLM-generated.
- **Don't modify the archive JSON.** Treat it as read-only forensic data.
- **Don't burn API tokens.** The point of this workflow is to *avoid*
  that. If you need to call out to an LLM for some reason, ask the user
  first.

---

## Related docs

- `OPERATIONS.md` — NAS deployment + how runs reach disk
- `gui/brief.py` — the Brief Pydantic schema (source of truth)
- `gui/sidecars.py` — the web app's read/write helpers for sidecars
- `service/routers/briefs.py` — the API routes the web app uses

---

## Schema reference (copy this into the Brief you write)

```json
{
  "decision": "Buy",
  "tldr": "Initiate a staged 4–5% NVDA position over 4–6 weeks. AI capex
            remains the primary driver and fundamentals stay strong, but
            near-term technical setup justifies a measured entry.",
  "timeframe": "4–6 weeks",
  "position_size": "4–5% of portfolio across three tranches",
  "entry_strategy": "Tranche 1 (~15%) at current levels near $198, Tranche
                     2 (~45%) at $203–205 if MACD re-expands, Tranche 3
                     (~40%) on any pullback to the $187–192 zone.",
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
  "benchmark_view": "Likely to outperform SPY by 5–10% over the next 6
                     weeks if AI capex narrative holds; underperforms
                     hard in a tech selloff."
}
```
