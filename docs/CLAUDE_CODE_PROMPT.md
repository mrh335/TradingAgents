# Copy-paste prompt: process pending brief requests

This is a **self-contained prompt** for a fresh Claude Code session.
Paste the block between `---` markers below. The session does NOT need
the TradingAgents repo cloned — everything happens over HTTP to the
NAS-deployed API at `http://192.168.2.34:8001`.

The prompt instructs Claude Code to:

1. Discover pending brief requests
2. For each, fetch the run's archive
3. Build a structured Brief object
4. POST it back — the server writes the sidecar and clears the marker
5. Report what it did

Re-run the same prompt any time the web app shows pending requests.

---

## The prompt — paste this verbatim

```
You are processing pending brief requests from a self-hosted multi-agent
trading research app called TradingAgents, running on a Synology NAS at
http://192.168.2.34:8001. The app drops "request markers" when the user
wants a plain-English brief generated for a completed run without
spending API tokens. Your job is to build those briefs from the recorded
analysis and submit them back via REST.

## API surface (use curl, fetch, or your http tooling)

GET  http://192.168.2.34:8001/sidecars/pending
     -> list of {run_id, ticker, trade_date, archive_path,
                 request_path, request_body, has_brief_already}

GET  http://192.168.2.34:8001/sidecars/run/{run_id}
     -> {run_id, ticker, trade_date, archive_path,
         archive: { metadata, state, tool_trace },
         existing_sidecars: [...], request_pending: bool, request_body }

POST http://192.168.2.34:8001/sidecars/run/{run_id}/brief
     content-type: application/json
     body: a Brief object matching the schema below
     -> server writes <archive>.brief.json AND clears the request marker
        in one atomic step

POST http://192.168.2.34:8001/sidecars/run/{run_id}/brief/markdown
     content-type: application/json
     body: {"markdown": "..."}
     -> use this only if the analysis is too messy to fit the schema

DELETE http://192.168.2.34:8001/sidecars/run/{run_id}/request
     -> cancel a request without producing a brief

## Brief schema (copy this shape; all fields required)

{
  "decision": "Buy",                  // one of: Buy, Overweight, Hold, Underweight, Sell
  "tldr": "...",                      // 2-3 plain-English sentences a non-investor understands. Lead with the action.
  "timeframe": "4-6 weeks",           // e.g. "next 3 months", "long-term core position"
  "position_size": "...",             // e.g. "4-5% of portfolio across three tranches"
  "entry_strategy": "...",            // lump sum vs tranches + price targets
  "stop_loss": "...",                 // condition or price level to exit if thesis fails
  "take_profit": "...",               // condition or price level to take profits
  "triggers": [                       // 3-7 if-then trigger points; concrete numbers > vague language
    {"condition": "NVDA closes below $183 on volume", "action": "Exit position; thesis broken"},
    {"condition": "MACD bullish crossover on daily",   "action": "Add tranche 2 immediately"},
    {"condition": "Q3 revenue miss > 5%",              "action": "Cut position to half"}
  ],
  "key_risks": [                      // 3-5 plain-English failure modes, NOT jargon
    "Cyclical demand cooldown if hyperscaler capex pauses",
    "China export curbs widening to consumer-grade chips"
  ],
  "benchmark_view": "..."             // one sentence on vs SPY for the timeframe
}

## Procedure

1. GET /sidecars/pending
   - If empty: STOP. Tell me "no pending requests, done."
   - Do NOT fabricate work.

2. For each pending request, in order:
   a. GET /sidecars/run/{run_id}
   b. Inspect the archive — particularly:
      - archive.state.final_trade_decision        (Portfolio Manager verdict)
      - archive.state.trader_investment_plan      (explicit Buy/Sell/Hold + size)
      - archive.state.investment_debate_state.bull_history / bear_history
      - archive.state.investment_debate_state.judge_decision
      - archive.state.risk_debate_state.{aggressive,conservative,neutral}_history
      - archive.state.risk_debate_state.judge_decision
      - archive.state.market_report, sentiment_report, news_report, fundamentals_report
      - archive.tool_trace (every tool call the agents made + their output)
   c. Build a Brief matching the schema above. Quote specific prices,
      levels, percentages, and timeframes that appear in the analysis.
      DON'T add numbers that aren't in the source.
   d. POST it to /sidecars/run/{run_id}/brief
   e. Verify the response shows {saved: ..., request_cleared: true}

3. After all are submitted: GET /sidecars/pending one more time to
   confirm the list is now empty.

4. Final report: for each ticker, one line with the run_id (first 8 chars),
   the ticker, and the decision you submitted.
   Format:
       abc12345  NVDA      Buy        (4-6 weeks)
       def67890  MSFT      Overweight (long-term)

## Rules

- DON'T burn API tokens calling out to other LLMs. Your parametric
  knowledge plus the recorded analysis is all you need.

- DON'T modify the archive JSON. Treat it as read-only.

- DON'T write sidecars for runs that aren't in /sidecars/pending. The
  user has to explicitly ask via the web app or the bulk-request button.

- If the analysis is genuinely inconclusive (the local model produced
  contradictory or incoherent output), say so honestly in the brief:
      "decision": "Hold",
      "tldr": "The local model's analysis was inconclusive — the PM
       didn't commit to a clear thesis and the bull/bear arguments
       cancel out. Holding is the safe default until a re-run with a
       stronger model.",
      ...
  Better honest "I can't extract a verdict" than fabricating one.

- Synonym map: if the analysis uses non-canonical vocab, normalise:
  Accumulate/Bullish/Long  -> Buy
  Reduce/Trim              -> Underweight
  Avoid/Short/Exit         -> Sell
  Neutral/Wait/Watch       -> Hold

- For the `decision` field, choose based on the trader_investment_plan's
  explicit verdict if present (e.g. "FINAL TRANSACTION PROPOSAL: BUY"),
  falling back to the Portfolio Manager's free-text conclusion only if
  the trader plan is missing.

Start now. Go.
```

---

## Quick variations

### Process just one ticker

```
Fetch http://192.168.2.34:8001/sidecars/pending. Find the entry where
ticker == "NVDA" (skip the others). Fetch its archive via
/sidecars/run/{run_id}, build a Brief per the schema in CLAUDE.md, and
POST it back. Don't touch the other pending requests.
```

### Dry run — don't POST, just show what you'd build

```
Fetch http://192.168.2.34:8001/sidecars/pending. For each entry, fetch
the archive via /sidecars/run/{run_id} and print the Brief you WOULD
generate (decision + tldr + triggers + key_risks) — but DO NOT POST.
I want to review before you submit.
```

### Submit one and stop for review

```
Fetch http://192.168.2.34:8001/sidecars/pending. Take ONLY the first
entry. Fetch the archive, build a Brief, POST it, then STOP and show
me what you submitted. I'll tell you whether to continue with the rest.
```

### Free-form markdown brief instead of structured

```
Same workflow, but instead of POSTing to /brief, POST to /brief/markdown
with body {"markdown": "..."} — useful when the analysis doesn't fit
the structured schema cleanly. The web app renders it with a
"Claude Code (markdown)" badge.
```

---

## Triggering more requests from the web app side

If `/sidecars/pending` returns empty and you want Claude Code to process
more runs, the web app has two ways to drop more markers:

### From the History page UI

Open http://192.168.2.34:3001/history → top card → click one of:

- **🤖 Request all missing** — drops a marker on every completed run
  that doesn't have a `brief.json` sidecar yet
- **🔄 Re-request all** — also re-requests runs that already have one

### From a curl (one-liner)

```bash
curl -s -X POST http://192.168.2.34:8001/sidecars/request-all-missing
# Optional: include runs that already have a brief
curl -s -X POST 'http://192.168.2.34:8001/sidecars/request-all-missing?include_existing=true'
```

The response is `{requested: [...run_ids...], skipped: [...], no_archive: [...]}` —
the `requested` array is exactly what the next Claude Code session will see in
`/sidecars/pending`.

---

## Reading back the work later

After Claude Code's POSTs land, briefs are visible in the web app at
`http://192.168.2.34:3001/history/<run_id>` under the Brief panel,
labelled with a green **🤖 from Claude Code** badge.

You can also pull a single brief directly:

```bash
curl -s http://192.168.2.34:8001/runs/{run_id}/brief | jq
# returns: { run_id, brief: {...}, cached: true, source: "sidecar", ... }
```

The `source` field tells you where it came from:

| `source` | Meaning |
|---|---|
| `sidecar` | Claude Code-authored `*.brief.json` — green badge in UI |
| `markdown_sidecar` | Claude Code-authored free-form `*.brief.md` — green badge |
| `llm` | LLM-generated via the quick-think model — blue "from API" badge |
| `null` | No brief yet, no request pending — empty state in UI |
