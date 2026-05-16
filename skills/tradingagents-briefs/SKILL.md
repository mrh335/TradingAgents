---
name: tradingagents-briefs
description: |
  Process pending brief requests for the TradingAgents webapp at
  $API — no API tokens spent. The webapp drops marker
  files when the user wants a plain-English brief generated for a completed
  run. This skill reads those markers via the API, fetches each run's
  archive, builds a structured Brief, and POSTs it back. Briefs are
  written for a mechanical engineer audience (no Wall Street jargon).
  Use this skill when the user asks any of: "process pending briefs",
  "rewrite briefs", "do the pending briefs", "process the brief queue",
  "update briefs from claude code", "refresh briefs", "/tradingagents-briefs".
---

# TradingAgents — pending brief processor

You produce structured briefs for already-completed analyses on the
TradingAgents webapp, **without spending any API tokens**. Your
parametric knowledge plus the recorded analysis in each archive is
all you need.

## Invocation patterns

| Phrase | Behavior |
|---|---|
| `/tradingagents-briefs` | Process every pending request |
| "process pending briefs" / "rewrite briefs" / "refresh briefs" | Same |
| "process brief for NVDA" | Filter pending list to ticker NVDA, process only those |
| "process brief for run abc12345" | Process only the matching run_id |
| "show me what's pending" | List pending requests, take no action, ask user to confirm |

## Network target

**API base** — resolves in this priority order:

1. The `TRADINGAGENTS_API` environment variable, if set (Cowork or any
   off-LAN session — usually set to a Cloudflare Tunnel hostname like
   `https://tradingagents.example.com`).
2. `http://192.168.2.34:8001` — the default LAN address of the NAS.

All `curl`/HTTP examples below use `$API` as shorthand for the base —
substitute the resolved value. If the resolved base isn't reachable,
stop and tell the user to check connectivity (LAN session: same Wi-Fi;
Cowork session: tunnel up at `docs/COWORK.md`'s steps). Don't fabricate
work or assume what the runs say.

## Procedure

### Step 1 — Discover work

GET `$API/sidecars/pending`

Response is a list of:
```json
{
  "run_id": "...",
  "ticker": "NVDA",
  "trade_date": "2026-05-14",
  "archive_path": "/home/appuser/.tradingagents/logs/NVDA/.../...json",
  "request_path": "...",
  "request_body": "templated prompt the webapp wrote",
  "has_brief_already": false
}
```

If empty:
- Tell the user: "No pending brief requests. Use the webapp's
  /history page → '🤖 Request all missing' or '🔄 Re-request all' to
  queue more."
- STOP. Do not fabricate work.

If the user invocation specified a ticker or run_id filter, narrow the
list before continuing.

### Step 2 — For each pending request, fetch the archive

GET `$API/sidecars/run/{run_id}`

Response includes:
```json
{
  "run_id": "...",
  "ticker": "NVDA",
  "trade_date": "2026-05-14",
  "archive_path": "...",
  "archive": {
    "metadata": { "provider": "ollama", "deep_model": "...", ... },
    "state": {
      "market_report": "...",
      "sentiment_report": "...",
      "news_report": "...",
      "fundamentals_report": "...",
      "investment_debate_state": {
        "bull_history": "...",
        "bear_history": "...",
        "judge_decision": "..."
      },
      "trader_investment_plan": "...",
      "risk_debate_state": {
        "aggressive_history": "...",
        "conservative_history": "...",
        "neutral_history": "...",
        "judge_decision": "..."
      },
      "final_trade_decision": "..."
    },
    "tool_trace": [...]
  }
}
```

**The trader_investment_plan is the most reliable verdict source** — it
typically contains an explicit line like "FINAL TRANSACTION PROPOSAL: BUY"
or "Action: Buy". Use that as the canonical decision. Fall back to the
Portfolio Manager's free-text in `final_trade_decision` only if the
trader plan is missing or doesn't commit.

### Step 3 — Build the Brief

Construct an object in this exact shape. All fields are required
except where noted:

```json
{
  "decision": "Buy",
  "action_plain": "buy a starter position",
  "tldr": "2-3 sentences leading with the action a person would take",
  "timeframe": "4-6 weeks",
  "position_size": "4-5% of portfolio in three tranches",
  "entry_strategy": "1st tranche at current levels near $198, 2nd at $203-205 if MACD re-expands, 3rd on any pullback to $187-192",
  "stop_loss": "Sustained close below $183 (200-day SMA)",
  "take_profit": "Re-evaluate at $245 or after 6 weeks, whichever comes first",
  "triggers": [
    {"condition": "Stock closes below $183 for 2 days", "action": "Exit position; thesis broken"},
    {"condition": "MACD line crosses above its signal", "action": "Add tranche 2 immediately"},
    {"condition": "Q3 revenue miss > 5%", "action": "Cut position to half"}
  ],
  "key_risks": [
    "AI spending slows and demand for chips drops",
    "Trade restrictions on chip exports get wider",
    "Stock could drop sharply if the AI capex story stalls"
  ],
  "benchmark_view": "Likely beats just-buying-SPY by 5-10% over the next 6 weeks if the AI capex story holds; underperforms hard in a tech selloff."
}
```

### Step 4 — Submit

POST `$API/sidecars/run/{run_id}/brief`
- Content-Type: application/json
- Body: the Brief object built in Step 3

The server writes the sidecar AND clears the request marker as one
atomic operation. Response is `{saved: "...", request_cleared: true}`.

### Step 5 — Confirm + report

GET `/sidecars/pending` once more to confirm the queue is now empty
(or has only the items you intentionally skipped).

Final response to the user:
- One line per processed run with run_id (first 8 chars), ticker, and
  the decision you submitted
- A link to view the result. The base URL resolves in this priority:
  1. `TRADINGAGENTS_WEB` env var if set (Cowork or remote sessions).
  2. `http://192.168.2.34:3001` — the LAN webapp address.
  Then append `/history/{run_id}`.
- If any submission failed, the run_id and the error

Format:
```
abc12345  NVDA   Buy        (4-6 weeks)  → $WEB/history/abc12345...
def67890  MSFT   Overweight (long-term)  → $WEB/history/def67890...
```

---

## CRITICAL — Audience and vocabulary

**Write for a mechanical engineer who has never traded stocks.** They
understand percentages, ratios, units, tolerances, and basic stats but
DO NOT know Wall Street vocabulary. Think of someone who reads
engineering specs and runs FEA simulations but has never traded options.

Engineering / physics analogies are welcome when they clarify a finance
concept:
- "Volatility is like vibration amplitude — bigger means more uncertainty"
- "Expected return is the mean of the distribution, not a guarantee"
- "A stop-loss is a tolerance — exit if the value falls outside this band"

### Vocabulary rules (strict)

**Decision** stays in the canonical 5-tier schema (Buy / Overweight /
Hold / Underweight / Sell) — that's the API contract. But ALSO fill
`action_plain` with 3-8 everyday words mapped from the decision:

```
Buy         → "buy a starter position"
Overweight  → "add more than usual"
Hold        → "keep what you have, no new money"
Underweight → "trim about half"
Sell        → "sell out completely"
```

**Banned without a parenthetical translation**: Overweight, Underweight,
PEG, EV/EBITDA, beta, alpha, RSI, MACD, MA crossover, Sharpe, drawdown,
MOC, tranche, accumulate, multiple compression, mean reversion, sector
rotation. If you must use them, put the plain meaning in parens right
after. e.g. `"PEG of 0.63 (cheaper than a fairly-priced stock — lower
is better here)"`.

**Specific prices and percentages stay as-is.** Those are concrete
numbers, not jargon. Quote them when the analysis gives them.

**Synonym normalisation** when the source analysis uses non-canonical
words:
- Accumulate / Bullish / Long → `Buy`
- Reduce / Trim → `Underweight`
- Avoid / Short / Exit → `Sell`
- Neutral / Wait / Watch → `Hold`

### Field guidelines

- **`tldr`** leads with the action in one sentence. Optional second
  sentence explains why in plain terms.
- **`triggers`** are concrete and measurable with specific numbers.
  "Stock closes below $183 for 2 days in a row → sell out" beats
  "if momentum weakens, reduce exposure".
- **`key_risks`** are 'what could go wrong' in everyday English.
  "AI spending slows and demand for chips drops" beats "elevated
  multiple compression risk during late-cycle dynamics".
- **`benchmark_view`** says whether this beats just-buying-SPY and
  roughly by how much. Be honest if the analysis doesn't suggest an
  edge: "Probably matches SPY — there's not much edge here over a
  passive index buy" is a valid answer.

---

## Failure modes — handle these honestly

### Empty queue
Stop. Tell the user. Don't fabricate work.

### Archive is genuinely inconclusive
Some Ollama / smaller-model runs produce contradictory or thin output —
the PM doesn't commit to a thesis and the bull/bear arguments cancel
out. Don't fabricate a verdict to fill the schema. Say it honestly:

```json
{
  "decision": "Hold",
  "action_plain": "keep what you have, no new money",
  "tldr": "The local model's analysis was inconclusive — the PM didn't
   commit to a clear thesis and the bull/bear arguments cancel out.
   Holding is the safe default until a re-run with a stronger model.",
  ...
}
```

### POST returns 4xx
- 404 = run doesn't exist (someone deleted it between pending list and POST)
- 409 = some state conflict — read the response body
- 422 = your Brief failed Pydantic validation. The response body names
  the offending field. Most common: `triggers` has fewer than 3 items,
  `key_risks` has more than 5 items, or `decision` isn't one of the
  5 canonical values.

Fix the brief and retry. Don't skip the run silently.

### POST returns 5xx
The server hit an error. Surface the run_id + the error to the user;
don't try to recover automatically.

---

## What NOT to do

- **Don't burn API tokens calling other LLMs.** Your parametric
  knowledge + the recorded analysis is all you need.
- **Don't modify the archive JSON.** Treat it as read-only forensic
  data.
- **Don't write briefs for runs that aren't in /sidecars/pending.** The
  user has to explicitly queue them via the webapp's UI buttons or the
  bulk-request endpoint.
- **Don't fabricate prices or specific numbers** that aren't in the
  source analysis. Quote what's there; if a stop-loss isn't given,
  infer a reasonable one from the analysis's reasoning but flag it as
  inference in the `entry_strategy` text.
- **Don't add disclaimers.** No "this is not financial advice", no
  "consult a financial advisor". The webapp's UI already carries that
  caveat once at the top level.

---

## Related

- The original full-pipeline analysis skill is at `tradingagents-analyze`
  (sibling skill). That one runs the whole multi-agent thing from
  scratch; this one ONLY processes already-completed runs.
- The webapp source-of-truth for the Brief schema lives at
  `gui/brief.py` in the TradingAgents repo on the NAS, with a copy in
  `CLAUDE.md` at the repo root.
- For a one-time copy-pasteable prompt (no skill needed), see
  `docs/CLAUDE_CODE_PROMPT.md` in the repo.
