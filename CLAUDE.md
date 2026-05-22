# Claude Code companion guide

> **Queue automation lives in `docs/queue-automation.md`.** Read that first
> if the user asks about scheduled runs, draining the queue, or
> Claude Desktop / Cloud Code Routine setup. It documents the four
> consume paths (manual, Windows Scheduled Task, remote routine, server
> drainer) and why each exists.



This file tells Claude Code how to participate as a **token-free analyst**
on completed run archives — reading the run's analysis, producing briefs
and deep dives, and submitting the results so the web app surfaces them
without any LLM API calls being made.

## The deal

The web app on the NAS produces run archives at:

```
<NAS>:/volume1/docker/tradingagents/data/logs/<TICKER>/TradingAgentsStrategy_logs/runs/
    <run_id>__<YYYY-MM-DD>__<UTC_ts>.json
```

When a user clicks **"Request via Claude Code"** in the Brief panel, the
web app drops a marker file next to the archive on the NAS. **You don't
need to mount the NAS filesystem locally** — there's an API workflow
that does everything via HTTP.

The API lives at:

```
http://192.168.2.34:8001
```

---

## Primary workflow (API-based, no NAS mount required)

This is the recommended path. Claude Code on any machine that can reach
the NAS over the LAN handles every step via HTTP — no SMB mount, no file
permissions issues, no path-translation. Use the `curl` examples or the
equivalent `WebFetch`/`Bash` tool calls.

### 1. List pending requests

```bash
curl -s http://192.168.2.34:8001/sidecars/pending | jq
```

Returns a JSON list of `{run_id, ticker, trade_date, archive_path,
request_path, request_body, has_brief_already}`. The `request_body` is
the full templated prompt the web app wrote — read it for context.

If the list is empty, there's nothing to do — the user hasn't requested
any briefs yet. Confirm with them before doing anything else.

### 2. Fetch the full archive for one request

```bash
curl -s http://192.168.2.34:8001/sidecars/run/{run_id} | jq
```

Returns `{run_id, ticker, trade_date, archive_path, archive, existing_sidecars,
request_pending, request_body}`. The `archive` field is the full archive
JSON envelope — `metadata`, `state` (all the analyst reports + debates +
final decision), and `tool_trace` (every tool call the agents made).

### 3. Build a Brief

Construct an object matching the schema in `gui/brief.py` (Brief +
Trigger Pydantic models). Required fields:

| Field | Type | Notes |
|---|---|---|
| `decision` | str | One of: `Buy`, `Overweight`, `Hold`, `Underweight`, `Sell` |
| `tldr` | str | 2–3 plain-English sentences a non-investor understands |
| `timeframe` | str | e.g. `"4–6 weeks"`, `"3–6 months"`, `"long-term core position"` |
| `position_size` | str | e.g. `"4–5% of portfolio in three tranches"` |
| `entry_strategy` | str | How to enter — lump sum vs tranches, price targets |
| `stop_loss` | str | Condition or price level to exit if thesis fails |
| `take_profit` | str | Condition or price level to take profits |
| `triggers` | list[{condition, action}] | 3–7 if-then trigger points |
| `key_risks` | list[str] | 3–5 plain-English failure modes |
| `benchmark_view` | str | One sentence on vs SPY for the timeframe |

### 4. Submit it

```bash
curl -s -X POST http://192.168.2.34:8001/sidecars/run/{run_id}/brief \
  -H "content-type: application/json" \
  -d @brief.json
```

Where `brief.json` is the structured Brief you built. The server writes
the sidecar file AND clears the pending request marker as a side-effect.

The web app polls every 8s while a request is pending. When the sidecar
appears, the Brief panel shows a green **🤖 from Claude Code** badge.

### Alternative: free-form markdown

If the analysis doesn't fit the structured schema cleanly, submit
markdown instead:

```bash
curl -s -X POST http://192.168.2.34:8001/sidecars/run/{run_id}/brief/markdown \
  -H "content-type: application/json" \
  -d '{"markdown": "# NVDA — 2026-05-14\n\n## Decision\n..."}'
```

The web app renders this with a "Claude Code (markdown)" badge.

### Cancel a pending request

```bash
curl -s -X DELETE http://192.168.2.34:8001/sidecars/run/{run_id}/request
```

---

## Filesystem workflow (only if API isn't reachable)

If for some reason the API isn't accessible, you can do this directly on
the NAS filesystem (requires the NAS shared at `/volume1/docker/` or
equivalent SMB mount):

1. Walk `~/.tradingagents/logs/<TICKER>/TradingAgentsStrategy_logs/runs/`
   for files matching `*.brief.request.md`.
2. For each: read the request file (contains the archive path inline).
3. Read the archive JSON at the path the request mentions.
4. Build the Brief and write it to `<basename>.brief.json` next to the
   archive.
5. Delete the `*.brief.request.md` marker.

The web app's `GET /runs/{id}/brief` reads `*.brief.json` if present and
returns it tagged `source: "sidecar"`. The user sees a green badge.

---

## Sidecar file conventions (file-level reference)

When using either workflow, the file layout is:

| File | Purpose | Who writes |
|---|---|---|
| `*.json` | The archive (machine-written analysis output) | Web app worker (immutable) |
| `*.brief.json` | Structured brief — preferred | You (via API or filesystem) |
| `*.brief.md` | Free-form markdown brief — fallback | You |
| `*.brief.request.md` | Marker the web app drops to ask for a brief | Web app drops; you delete |
| `*.analysis.md` | Optional deep-dive (not surfaced yet) | You |
| `*.chat.md` | Optional chat transcript (not surfaced yet) | You |

The **web app never modifies sidecar files** other than dropping or
deleting the request marker. Your work is safe — re-running the web side
never overwrites a brief.json that Claude Code wrote.

---

## Conventions for good briefs

### Audience
Write for a **mechanical engineer who is not a finance person.** They
understand percentages, ratios, units, tolerances, and basic stats but
DO NOT know Wall Street vocabulary. Think someone who reads engineering
specs and runs FEA simulations but has never traded options. Analogies
from physics / engineering are welcome when they clarify something —
e.g. "volatility is like vibration amplitude" or "expected return is
the mean of the distribution, not a guarantee."

### Vocabulary rules (strict)

- **Decision** stays in the 5-tier schema (Buy/Overweight/Hold/Underweight/Sell)
  but ALSO fill `action_plain` with 3-8 everyday words:
    Buy         → 'buy a starter position'
    Overweight  → 'add more than usual'
    Hold        → 'keep what you have, no new money'
    Underweight → 'trim about half'
    Sell        → 'sell out completely'
- **Banned without a parenthetical translation**: Overweight, Underweight,
  PEG, EV/EBITDA, beta, alpha, RSI, MACD, MA crossover, Sharpe, drawdown,
  MOC, tranche. If you must use them, put plain English in parens right
  after: `"PEG of 0.63 (cheaper than a fairly-priced stock — lower is
  better here)"`.
- **Specific dollar prices and percentages stay as-is.** Those are
  concrete numbers, not jargon. Quote them when the analysis gives them.
  E.g. "stop at $183 (200-day SMA)" or "20% upside if Q3 hits guidance".
- **Synonym map** when the analysis uses non-canonical vocab:
  Accumulate/Bullish/Long → Buy. Reduce/Trim → Underweight.
  Avoid/Short/Exit → Sell. Neutral/Wait → Hold.

### Field conventions

- **`tldr`** leads with the action a person would actually take, in
  one sentence. Optional second sentence explains why in plain terms.
  "Buy a starter position and add more if it pulls back to $190" beats
  "After considering the analysis, one might initiate a modest position."
- **`triggers`** are concrete and measurable with specific numbers.
  "Stock closes below $183 for 2 days in a row → sell out" beats
  "if momentum weakens, reduce exposure". Use everyday language for the
  condition too.
- **`key_risks`** are 'what could go wrong' written in everyday English.
  "AI spending slows and demand for chips drops" beats "elevated multiple
  compression risk during late-cycle dynamics".
- **`benchmark_view`** says whether this is expected to beat just-buying-SPY,
  and roughly by how much. "Probably matches SPY — there's not much edge
  here over a passive index buy" is a valid answer if the analysis is thin.

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

- **Don't write sidecars unless requested.** Only act on runs that have
  a `*.brief.request.md` marker (or appear in `/sidecars/pending`).
- **Don't modify the archive JSON.** Treat it as read-only forensic data.
- **Don't burn API tokens.** The point of this workflow is to *avoid*
  that. If you need to call out to an LLM for some reason, ask the user
  first.

---

## Related docs

- `OPERATIONS.md` — NAS deployment + how runs reach disk
- `gui/brief.py` — the Brief Pydantic schema (source of truth)
- `gui/sidecars.py` — the web app's read/write helpers for sidecars
- `service/routers/sidecars.py` — the API routes documented above
- `service/routers/briefs.py` — the Brief panel's GET/POST endpoints

---

## Schema reference (copy this into the Brief you POST)

**Note v2 (2026-05-20+):** the schema gained structured-table fields
(``entry_plan``, ``exit_plan``, ``key_numbers``, ``jargon_glossary``).
Always populate them — the UI renders them as tables. Long prose in
the legacy fields (``entry_strategy``, ``stop_loss``, ``take_profit``,
``position_size``) is now considered a regression. Use the new fields.

```json
{
  "decision": "Buy",
  "action_plain": "buy a starter position",
  "tldr": "Buy a small NVDA position now and add gradually over 4-6 weeks. The AI infrastructure buildout drives demand and the company is growing revenue 60% with 70% gross margins.",
  "timeframe": "4–6 weeks",
  "position_size": "4–5% of portfolio total, spread over 3 separate purchases",

  "key_numbers": [
    {"label": "Current price", "value": "$198.40"},
    {"label": "Next earnings call", "value": "2026-08-21"},
    {"label": "Average price last 50 days", "value": "$192.10"},
    {"label": "Average price last 200 days", "value": "$165.30"},
    {"label": "52-week high", "value": "$210.50"},
    {"label": "Revenue growth (year over year)", "value": "+62%"},
    {"label": "Gross margin", "value": "73%"}
  ],

  "entry_plan": [
    {"label": "First purchase (now)", "when": "tomorrow at market open", "price": "~$198", "size_pct": "15% of target $$", "notes": "small anchor"},
    {"label": "Second purchase", "when": "only if it pulls back to $187-192", "price": "$187-192", "size_pct": "40% of target $$", "notes": ""},
    {"label": "Third purchase", "when": "if it breaks above $205 with strong volume", "price": "$205+", "size_pct": "45% of target $$", "notes": "confirms uptrend"}
  ],

  "exit_plan": [
    {"kind": "stop_loss", "condition": "price closes below $183 for two days in a row", "price": "$183", "action": "sell everything", "notes": "below the 200-day average — thesis broken"},
    {"kind": "take_profit", "condition": "price reaches $245", "price": "$245", "action": "sell half", "notes": "lock in gains, let the rest run"},
    {"kind": "time_based", "condition": "6 weeks have passed regardless", "price": null, "action": "review the position; re-decide whether to hold", "notes": ""},
    {"kind": "thesis_break", "condition": "Q3 earnings miss by more than 5%", "price": null, "action": "sell half immediately", "notes": ""}
  ],

  "triggers": [
    {"condition": "NVDA closes below $183 on heavy volume", "action": "sell everything; thesis is broken"},
    {"condition": "Average price over 50 days crosses above average over 200 days", "action": "add the second purchase now"},
    {"condition": "Q3 revenue miss greater than 5%", "action": "sell half of the position"}
  ],

  "key_risks": [
    "AI spending could slow if cloud customers cut their budgets — that would knock the price down even if earnings stay healthy",
    "U.S. export rules to China could widen, hurting another ~20% of revenue",
    "If the AI infrastructure story stalls, the stock could drop a lot even without bad earnings"
  ],

  "jargon_glossary": {
    "200-day SMA": "The average closing price over the last 200 trading days — a slow trend line. Price above it is generally bullish.",
    "P/E ratio": "Stock price divided by the past year of earnings per share. Lower is cheaper for the same earnings."
  },

  "benchmark_view": "Likely to outperform SPY by 5–10% over the next 6
                     weeks if AI capex narrative holds; underperforms
                     hard in a tech selloff."
}
```
