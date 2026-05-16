---
name: tradingagents-analyze
description: |
  Run a full multi-agent stock analysis using the TradingAgents methodology
  (4 analysts → bull/bear debate → research manager → trader → 3-way risk
  debate → portfolio manager → structured brief). Claude plays every agent
  role. Produces a run archive + brief sidecar in the format consumed by the
  existing TradingAgents webapp at http://192.168.2.34:8001, so the run shows
  up in the History page alongside framework-generated runs. Token usage is
  appended to two io_tokens.md files. Use this skill when the user asks to
  "analyze <TICKER>" or invokes /tradingagents-analyze.
---

# TradingAgents — full-pipeline analysis skill

You are running a multi-agent financial analysis. Play every role in turn,
producing the same prose artifacts the Python framework produces, and at the
end publish a run archive + brief that the existing webapp will index.

## Invocation modes

| Pattern | What it does |
|---|---|
| `/tradingagents-analyze <TICKER>` | Single ticker, today, auto-detect update mode |
| `/tradingagents-analyze <TICKER> <YYYY-MM-DD>` | Single ticker, specific date |
| `/tradingagents-analyze <T1> <T2> <T3>` | Multi-ticker batch (Phase 2) |
| `/tradingagents-analyze <TICKER> --ensemble N` | Run N times for consensus (Phase 2) |
| `/tradingagents-analyze <TICKER> --debate-rounds N --risk-rounds N` | Override depth |
| `/tradingagents-analyze <TICKER> --fresh` | Force fresh run, ignore any parent |
| `/tradingagents-analyze ask <run_id> "<question>"` | Interrogate a past run (Phase 4) |
| `/tradingagents-analyze backtest <run_id>` | Realised return vs SPY (Phase 4) |
| `/tradingagents-analyze portfolio <batch_id>` | Cross-ticker portfolio synthesis (Phase 2) |
| `/tradingagents-analyze plot <TICKER>` | Decision-history chart of past runs (Phase 6) |
| `/tradingagents-analyze <TICKER> --holdings` | Include your current positions in the recommendation (Phase 6) |
| `/tradingagents-analyze <TICKER> --horizon long\|short` | Tune the recommendation to your planning horizon (Phase 6) |
| `/tradingagents-analyze --queue` | Drain the webapp's run queue (Phase 9) |
| "process the run queue" / "drain pending analyses" / "do queued analyses" | Same — natural language |

Natural language maps to these — Claude interprets:
- `analyze NVDA today` → defaults
- `run NVDA with deep debate` → `--debate-rounds 3 --risk-rounds 2`
- `update my NVDA analysis` → `--update` (the default behaviour anyway)
- `ask the NVDA run why bull won` → `ask <latest_nvda_run_id> "..."`
- `run NVDA and AMD and AVGO` → multi-ticker batch
- `process the run queue` / `do pending analyses` → queue-drain mode (Phase 9)

## Phase progression

Execute these phases in order. Each phase reads the named persona prompt
under `prompts/`, plays that role, and stores its output under the specified
state key in working memory. **Do not skip phases.** Each downstream phase
depends on the prose produced upstream.

### Phase 0 — Resolve args

1. Parse the user's invocation. Resolve `ticker(s)`, `trade_date` (default
   today UTC), `debate_rounds` (default 2), `risk_rounds` (default 1),
   `ensemble_size` (default 1), `force_fresh` (default false),
   `lookback_days` (default 7).
2. Allocate a `run_id` of the form `claude-<8-char-uuid>` (e.g.
   `claude-a3f72b1d`). Record `started_at` as the current UTC ISO-8601
   timestamp.
3. If the invocation has more than one ticker, this is a **batch run** —
   allocate a `batch_id` (`batch-<8-char-uuid>`) and loop the per-ticker
   flow for each. See "Multi-ticker batch" below.
4. If `ensemble_size > 1`, run the per-ticker flow N times in sequence,
   each with `ensemble_index` 0..N-1. See "Ensemble" below.

### Phase 0.7 — Holdings & horizon (optional)

If the user passed `--holdings` (or the natural-language equivalent
"using my current positions") and/or `--horizon long|short`:

1. Run `scripts/fetch_holdings.py [--ticker <TICKER>] [--horizon X]`.
2. Read the JSON it writes. Store as `holdings_block` in working memory.
3. **Load `prompts/18-holdings-context.md`** — this file's directives
   apply to Phase 7 (Portfolio Manager) and Phase 8 (Brief Extractor).
4. If `--holdings` was passed but the framework returns an empty
   positions list, ask the user inline: "You don't have any positions
   registered in the framework. Should I proceed in generic (no-holdings)
   mode, or would you like to share your positions in chat first?"

If the user did NOT pass any of these flags, skip this phase entirely.

### Phase 0.5 — Look up parent run (update mode)

For each ticker being analysed:

1. Run `scripts/lookup_parent_run.py <TICKER>` (unless `--fresh` was
   passed). Read the JSON it writes.
2. If `found: true` AND the parent's `completed_at` is within
   `lookback_days` AND `force_fresh` is false:
   - Set `update_mode = true`
   - Store the parent envelope in working memory (`parent_run`)
   - **Read `prompts/00-update-mode.md`** — this file's directives apply
     to every analyst / debate / decision phase below
   - The data fetch in Phase 1 will use `--since-iso <parent.completed_at>`
     to narrow the news window
3. Otherwise: `update_mode = false`, skip the update-mode directives.

### Phase 1 — Gather data

Three data blocks feed the analysts. The first two are **load-bearing**
(abort if they fail); the rest are **opportunistic** (warn + continue if
they fail — a quiet API or rate limit shouldn't kill the whole analysis).

**Load-bearing:**
1. Run `scripts/fetch_market_data.py <TICKER> <DATE>`. In update mode,
   append `--since-iso <parent.completed_at>` so news is filtered to the
   delta window. Read the JSON blob it returns.
2. Run `scripts/compute_indicators.py <market_data_path>` to produce
   the final `market_data_block` (with the `indicators` block appended).
   In update mode: `market_data_block.is_update = true` and
   `delta_window` carries the time range.

**Opportunistic:**

3. Run `scripts/fetch_congress_trades.py <TICKER> [--since-iso ...]`.
   Output is the `congress_trades_block`. Surfaces filings up to ~45 days
   delayed (STOCK Act lag) — useful as smart-money signal. If fetch fails
   or the API is down, warn + set this block to null.
4. Run `scripts/fetch_insider_trades.py <TICKER> [--since-iso ...]`.
   Output is the `insider_trades_block` (SEC Form 4 filings). Fast
   reporting (~2 business days). Same null-on-failure semantics.
5. Run `scripts/fetch_earnings_events.py <TICKER>`. Output is the
   `earnings_events_block`: next earnings date, recent surprises,
   ex-dividend date, upcoming FOMC dates. Null on failure.

If any opportunistic block is null, the corresponding analyst phase
should mention "data not available for this run" rather than fabricate.

If **either load-bearing** script fails, abort the run with a clear
error — do not fabricate market data.

### Phase 2 — Four analysts (in order)

For each, load the persona prompt and produce a free-text markdown report.
In **update mode**, also load `prompts/00-update-mode.md` and the
corresponding `parent_run.state.<report_key>` so the analyst can write a
delta-aware update. Store under the named state key. Each report should
run **600–1200 words** in fresh mode or **400–800 words** in update mode
(less restating, more delta).

| Phase | Persona prompt | State key (output) | Parent input (update mode) |
|---|---|---|---|
| 2a | `prompts/01-fundamentals.md` | `state.fundamentals_report` | `parent_run.state.fundamentals_report` |
| 2b | `prompts/02-sentiment.md` | `state.sentiment_report` | `parent_run.state.sentiment_report` |
| 2c | `prompts/03-news.md` | `state.news_report` | `parent_run.state.news_report` |
| 2d | `prompts/04-technical.md` | `state.market_report` | `parent_run.state.market_report` |

End each report with a markdown table summarising the key points.

### Phase 3 — Bull/bear debate (configurable rounds)

For `debate_rounds` iterations (default 2), alternate bull and bear:

1. Load `prompts/05-bull-researcher.md`. Read the four analyst reports plus
   the prior debate history. Write the bull argument (300-600 words).
   Append to `state.investment_debate_state.bull_history` and to
   `state.investment_debate_state.history`.
2. Load `prompts/06-bear-researcher.md`. Read the four analyst reports plus
   the prior debate history (including the bull argument you just wrote).
   Write the bear argument (300-600 words). Append similarly.

After all rounds, `state.investment_debate_state.history` contains the full
bull↔bear transcript.

### Phase 4 — Research Manager verdict

Load `prompts/07-research-manager.md`. Read the debate history. Produce a
**structured output** with three fields (matches `ResearchPlan` in
`tradingagents/agents/schemas.py`):

```
**Recommendation**: <Buy | Overweight | Hold | Underweight | Sell>
**Rationale**: <2-4 sentences explaining which side won>
**Strategic Actions**: <Concrete steps for the trader, including sizing>
```

Store this rendered markdown under both `state.investment_plan` and
`state.investment_debate_state.judge_decision`.

### Phase 5 — Trader plan

Load `prompts/08-trader.md`. Read the investment plan from phase 3. Produce
**structured output** with these fields (matches `TraderProposal`):

```
**Action**: <Buy | Hold | Sell>
**Reasoning**: <2-4 sentences anchored in the analysts' reports>
**Entry Price**: <optional, e.g. 198.50>
**Stop Loss**: <optional, e.g. 183.00>
**Position Sizing**: <optional, e.g. "5% of portfolio in three tranches">

FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**
```

Store under `state.trader_investment_plan`. The trailing `FINAL TRANSACTION
PROPOSAL` line is required — downstream parsers in the existing webapp grep
for it.

### Phase 6 — Risk debate (configurable rounds, 3 voices per round)

For `risk_rounds` iterations (default 1), play three voices in order:

| Phase | Persona prompt | History key |
|---|---|---|
| 5a | `prompts/09-risk-aggressive.md` | `state.risk_debate_state.aggressive_history` |
| 5b | `prompts/10-risk-conservative.md` | `state.risk_debate_state.conservative_history` |
| 5c | `prompts/11-risk-neutral.md` | `state.risk_debate_state.neutral_history` |

Each voice reads the four analyst reports + trader plan + prior speakers'
arguments. 250-500 words each. Append to the named history key AND to
`state.risk_debate_state.history`. Increment `state.risk_debate_state.count`
after each speaker.

### Phase 7 — Portfolio Manager final decision

Load `prompts/13-portfolio-manager.md`. Read the research plan, trader plan,
and risk debate history. Produce **structured output** with these fields
(matches `PortfolioDecision`):

```
**Rating**: <Buy | Overweight | Hold | Underweight | Sell>
**Executive Summary**: <2-4 sentences: entry, sizing, risk, horizon>
**Investment Thesis**: <detailed reasoning anchored in the debate>
**Price Target**: <optional, e.g. 245.00>
**Time Horizon**: <optional, e.g. "3-6 months">
```

Store under both `state.final_trade_decision` and
`state.risk_debate_state.judge_decision`.

> The risk-judge prompt (`prompts/12-risk-judge.md`) is identical framing
> applied to the same step; the existing repo uses one model call here.

### Phase 8 — Brief extraction

Load `prompts/14-brief-extractor.md`. Compose a brief that follows the
`Brief` schema from `gui/brief.py`. All 10 fields are required:

- `decision` (one of Buy / Overweight / Hold / Underweight / Sell)
- `tldr`
- `timeframe`
- `position_size`
- `entry_strategy`
- `stop_loss`
- `take_profit`
- `triggers[]` (each `{condition, action}`)
- `key_risks[]`
- `benchmark_view`

Run `scripts/build_brief.py` with the brief JSON to validate the schema
before publishing. If validation fails, fix the offending field and
re-validate — do not publish an invalid brief.

### Phase 9 — Publish

Run `scripts/build_archive.py` to compose the archive envelope (combines
all state keys + metadata into the canonical archive shape). Pass:

- `--parent-run-id <parent.run_id>` if update mode (Phase 0.5 set it)
- `--batch-id <batch_id>` if multi-ticker batch (Phase 0 set it)
- `--ensemble-index <i> --ensemble-size <N>` if part of an ensemble

It writes a temp file and prints the path.

Then run `scripts/publish.py --archive <archive_temp_path> --brief <brief_temp_path>`
which POSTs both to the framework's `POST /runs/import` endpoint. The
server writes the files into
`<results_dir>/<TICKER>/TradingAgentsStrategy_logs/runs/`, INSERTs a row
into `gui.db.runs` with status='done', and returns the new run row. The
script prints the run URL (`http://192.168.2.34:3001/history/<run_id>`)
to stdout.

If `publish.py` exits non-zero, print the error and stop. Do not silently
fail — the user needs the link.

Common failure modes:
- **Connection refused** → the FastAPI service isn't running, or the
  user's machine can't reach it on the LAN.
- **HTTP 409** → a run with this ID already exists (shouldn't happen
  with freshly-minted UUIDs; if it does, mint a new one and republish).
- **HTTP 400** → the archive failed validation. Read the response body —
  it names the offending field. Almost always means a typo in
  `metadata.run_id` / `ticker` / `trade_date`.

### Phase 10 — Token logging

Run `scripts/token_logger.py <run_id> <ticker> <trade_date>` which
estimates the input/output tokens for this session (tiktoken approximation)
and appends one line to **both** `io_tokens.md` files:

- `C:\Users\markh\.claude\skills\tradingagents-analyze\io_tokens.md`
- `Z:\My Documents\code repo\active\hedge_trader\TradingAgents\io_tokens.md`

### Final response to the user

Report:
- The run URL on the NAS webapp.
- The final rating (from the Brief's `decision` field).
- A one-sentence tldr.
- The estimated token cost.

Keep it short. The full report is on the webapp.

## State keys reference (for fidelity with gui/brief.py)

These are the exact field names the existing webapp's brief extractor reads.
Do not invent variants:

```
state.market_report                       (technical analyst output)
state.sentiment_report                    (sentiment analyst)
state.news_report                         (news analyst)
state.fundamentals_report                 (fundamentals analyst)

state.investment_debate_state.bull_history
state.investment_debate_state.bear_history
state.investment_debate_state.history
state.investment_debate_state.current_response
state.investment_debate_state.judge_decision   (research manager verdict)
state.investment_debate_state.count

state.investment_plan                     (research manager — rendered markdown)
state.trader_investment_plan              (trader — rendered markdown)

state.risk_debate_state.aggressive_history
state.risk_debate_state.conservative_history
state.risk_debate_state.neutral_history
state.risk_debate_state.history
state.risk_debate_state.latest_speaker
state.risk_debate_state.current_aggressive_response
state.risk_debate_state.current_conservative_response
state.risk_debate_state.current_neutral_response
state.risk_debate_state.judge_decision    (portfolio manager — rendered)
state.risk_debate_state.count

state.final_trade_decision                (portfolio manager — rendered markdown)
```

## Errors and partial failure

- If the data fetch fails: abort. Tell the user which source failed (yfinance
  may be rate-limited or the ticker invalid).
- If a phase produces a clearly malformed structured output (e.g. trader
  doesn't emit `FINAL TRANSACTION PROPOSAL`): re-prompt yourself once with
  the formatting requirement, then proceed.
- If publish fails on SMB and SCP both: print the archive + brief contents
  to the user inline and instruct them to drop the files manually.
- Token logging failure is non-fatal — log a warning, continue.

## Multi-ticker batch

When the user invokes with multiple tickers (e.g.
`/tradingagents-analyze NVDA AMD AVGO`):

1. In Phase 0, allocate a single `batch_id = batch-<uuid>` and store it.
2. For each ticker in order: run the full single-ticker flow (Phase 0.5 →
   Phase 10), publishing each as its own archive with
   `metadata.batch_id = batch_id`.
3. After all tickers complete, run **Phase 11 — Cross-ticker portfolio
   synthesis**:
   - Load `prompts/15-portfolio-cross-ticker.md`
   - Read every per-ticker brief and the briefs' decisions
   - Produce a `PortfolioSynthesis` markdown report covering:
     correlations between the picks, sector concentration, suggested
     weights, hedges
   - POST it to the framework as a batch-level portfolio sidecar (see
     `scripts/publish_portfolio.py`)
4. Final user-facing summary: one line per ticker (decision + tldr) plus
   the batch portfolio URL.

## Ensemble (`--ensemble N`)

When the user passes `--ensemble N` (N >= 2) for a single ticker:

1. In Phase 0, allocate one `batch_id = ensemble-<uuid>`.
2. Run the per-ticker flow N times in sequence, each producing its own
   archive with `metadata.ensemble_index = 0..N-1`,
   `metadata.ensemble_size = N`, `metadata.batch_id = batch_id`.
3. After all N runs complete, **Phase 11 — Ensemble consensus**:
   - Load `prompts/16-ensemble-consensus.md`
   - Read all N briefs
   - Produce an `EnsembleConsensus` markdown that votes / aggregates the
     decisions, flags disagreement, surfaces the strongest signals
   - POST as a batch-level consensus sidecar (`scripts/publish_portfolio.py`
     handles both portfolio + ensemble sidecars).

Cannot combine ensemble and multi-ticker in one invocation; pick one.

## Interrogation mode (Phase 4 feature)

`/tradingagents-analyze ask <run_id> "<question>"` loads a prior run
archive via the framework API and answers a follow-up question using its
full content as context. See `prompts/17-interrogation.md` and
`scripts/interrogate.py`. Q&A is saved as a `*.chat.md` sidecar so it
shows up in the webapp.

## Backtest mode (Phase 4 feature)

`/tradingagents-analyze backtest <run_id>` re-fetches the ticker's price
after a configurable horizon (default: trade_date + 30 days), computes
realised return + alpha vs SPY, and writes a `*.backtest.json` sidecar
that captures whether the decision worked. See `scripts/backtest.py`.

## Decision-history plot (`plot <TICKER>`)

`/tradingagents-analyze plot <TICKER>` invokes
`scripts/plot_decision_history.py`, which is a thin client over two
**server-side** endpoints added to the framework:

- `GET /charts/decisions/{ticker}` → JSON data (decisions + price line)
- `GET /charts/decisions/{ticker}.png` → server-rendered PNG image

The script:
1. Calls the data endpoint to check there are decisions in the window.
2. Posts a `chart.md` sidecar on the most recent run that embeds the
   chart via `<img src="…/decisions/NVDA.png">` so it shows on the run's
   webapp page.
3. Prints the chart URL — open it in any browser to view.

Defaults: 180-day lookback. Override with `--lookback-days N`.
Save the PNG locally too with `--save-png <path>`.

**No local matplotlib dep** — rendering happens on the server (which
already has matplotlib via the `service` extras). The skill is
stdlib-only.

## Phase 9 — Queue-drain mode (`--queue` / "process the run queue")

The webapp at `http://192.168.2.34:8001` exposes a `run_queue` table so a
user can hit "🤖 Queue for Claude Desktop" on the Run page and walk away —
this skill (running in Claude Desktop or Claude Code) is the worker that
picks the request up later. Saves the user from babysitting a synchronous
run and routes the token cost through the local subscription instead of
the webapp's pay-per-call API key.

### When to invoke this mode

- User says: "process the run queue", "do pending analyses", "drain the
  queue", `/tradingagents-analyze --queue`.
- Or you're running unattended via `/loop` / scheduled task and want to
  poll for work.

### Procedure

1. **Claim work**:
   ```bash
   curl -s -X POST $API/run-queue/claim \
     -H "content-type: application/json" \
     -d '{"claimed_by": "claude-desktop:<your-id>", "max_items": 1, "mode": "analyze"}'
   ```
   `max_items` is typically 1 — process one at a time so a failure
   doesn't strand multiple jobs. The server atomically marks the row
   `status='claimed'` so a sibling worker won't double-process it.

2. **Empty result → nothing to do**:
   ```
   No pending items. Stop. Tell the user.
   ```
   Do NOT poll in a tight loop — wait at least 60s before re-checking,
   or use `/loop` with `delaySeconds=900` (15 min) for unattended mode.

3. **For each claimed item**, extract the parameters from `options`:
   ```
   {
     "provider": "anthropic",
     "deep_model": "claude-sonnet-4-6",
     "quick_model": "claude-haiku-4-5",
     "debate_rounds": 1,
     "risk_rounds": 1,
     "data_vendors": {...}
   }
   ```
   These came from the Run-page form. Run the **standard single-ticker
   flow** (Phase 0 → Phase 10) honoring these as overrides. **One
   exception**: don't allocate a fresh `run_id` in Phase 0 — use the
   queue item's `id` as a prefix so the resulting run is linkable:
   `run_id = "queue-" + queue_item["id"][:8] + "-" + uuid4()[:8]`.

4. **Publish the result** to the framework with `scripts/publish.py`
   (which POSTs to `/runs/import`). Capture the returned `run_id`.

5. **Mark the queue item complete**:
   ```bash
   curl -s -X POST $API/run-queue/{queue_id}/complete \
     -H "content-type: application/json" \
     -d '{"result_run_id": "<the-run_id-from-import>"}'
   ```
   The UI uses `result_run_id` to render a "View result →" link on the
   /queue page, so always pass it when the run succeeded.

6. **On failure** (any phase errors, archive validation fails,
   `/runs/import` rejects it):
   ```bash
   curl -s -X POST $API/run-queue/{queue_id}/fail \
     -H "content-type: application/json" \
     -d '{"error": "<short human-readable reason>"}'
   ```
   Don't silently skip. The error text shows up next to the row in the
   webapp so the user can re-queue or investigate.

7. **Janitor pass** (optional, do this on every claim or once per
   wake-up): clear stuck claims from a previous worker that died:
   ```bash
   curl -s -X POST "$API/run-queue/reclaim-stale?older_than_seconds=1800"
   ```

### Final user report

After draining (or claiming and finding nothing), summarize:

```
3 queue items processed:
  qid-abc1...  NVDA   Buy   → http://192.168.2.34:3001/history/queue-abc1...
  qid-def2...  AAPL   Hold  → http://192.168.2.34:3001/history/queue-def2...
  qid-ghi3...  MSFT   FAIL: yfinance returned no fundamentals
```

If running unattended via `/loop`, the report is just a 1-line summary;
don't dump prose if there's no human to read it.

### Unattended scheduling

Use Claude Code's `/loop` skill to keep a worker running:

```
/loop process the run queue --interval 15min
```

…or via Windows Task Scheduler, launch Claude Code with the prompt
`/tradingagents-analyze --queue` at intervals. Either way the worker
is fault-tolerant: stale claims (no progress for 30 min) get re-pended
automatically on the next wake-up.

## Scheduled runs

Use the built-in `/schedule` skill to drive this skill on a cron schedule:

```
/schedule create --name "daily-watchlist" \
    --at "weekdays 06:00 America/New_York" \
    --prompt "/tradingagents-analyze NVDA AMD AVGO MSFT GOOG"
```

The scheduled invocation auto-detects update mode via Phase 0.5
(`lookup_parent_run.py`), so each subsequent run leverages the prior
day's analysis and only fetches the news delta. See
`reference/scheduling.md` for cron templates and failure handling.
