"use client";

import Link from "next/link";
import { Markdown } from "@/components/Markdown";

const TOC = [
  { id: "quick-start", label: "Quick start" },
  { id: "running-an-analysis", label: "Running an analysis" },
  { id: "the-queue", label: "The queue lifecycle" },
  { id: "draining-the-queue", label: "Draining the queue (workers)" },
  { id: "scheduling", label: "Scheduling unattended runs" },
  { id: "providers-and-models", label: "Providers & models" },
  { id: "briefs", label: "Briefs workflow" },
  { id: "vocabulary", label: "Decision vocabulary" },
  { id: "skills", label: "Claude skills" },
  { id: "api-reference", label: "API reference" },
  { id: "off-lan-access", label: "Off-LAN access (Cowork)" },
  { id: "troubleshooting", label: "Troubleshooting" },
  { id: "files-and-paths", label: "Files & paths" },
];

const CONTENT = `
# TradingAgents — user guide

Recommendations only, not orders. Everything you can do with this app
and the two companion Claude skills.

---

<a id="quick-start"></a>

## Quick start

| You want to… | Do this |
|---|---|
| Analyze a single ticker right now | **Run** page → fill form → **▶ Analyze now**. Streams agent output live. |
| Queue an analysis without running it now | **Run** page → **🤖 Queue for Claude Desktop**. Drain later from any Claude session. |
| Run 5–30 tickers as a batch | **Batch** page → paste a ticker list. |
| Re-generate a plain-English brief for a past run | **History** → open run → Brief panel → **🤖 Request via Claude Code**, then say "process pending briefs" in a Claude Code session. |
| See what's pending | **Queue** for analysis requests, **History** for finished runs. |
| Get an interactive decision history chart | **Trends** page → pick a ticker. |

---

<a id="running-an-analysis"></a>

## Running an analysis

There are **three execution paths**. They differ in where the LLM
tokens come from and how long you wait for results.

### Path A — Synchronous (Run page → "▶ Analyze now")

- **Token source**: the webapp's API key from \`.env\` (\`ANTHROPIC_API_KEY\`,
  \`OPENAI_API_KEY\`, etc.).
- **Latency**: 30–90s depending on debate depth and model.
- **UX**: WebSocket streams every agent's output live; sections light up
  as they're produced.
- **When to use**: you want the answer right now; you're testing a new
  ticker or config; cost isn't an issue for this one-off run.

### Path B — Queued (Run page → "🤖 Queue for Claude Desktop")

- **Token source**: an external worker's tokens. If the worker is Claude
  Desktop or Claude Code, that means your **flat subscription** — much
  cheaper than per-call API for sustained use.
- **Latency**: depends on when the worker drains. If you call
  *"process the run queue"* in Claude Code immediately, it's 30–60s.
  If you leave it overnight, it's overnight.
- **UX**: webapp returns a queue item ID immediately. Status updates on
  the **Queue** page as the worker progresses (\`pending\` →
  \`claimed\` → \`done\`, with a link to the resulting run).
- **When to use**: routine analyses where the answer isn't urgent;
  overnight batches; anything you'd otherwise pay API per-call for.

### Path C — Externally produced, then imported

- **Token source**: whatever ran the analysis (a Claude Code session, a
  Claude Desktop skill, a python script).
- **Mechanism**: that external producer assembles a run archive matching
  the schema and POSTs it to \`/runs/import\`. The webapp inserts the row
  and the run is indistinguishable from a framework-generated one.
- **UX**: no live stream — the result just appears in History when it's
  done.
- **When to use**: when you've already run the multi-agent pipeline
  somewhere outside the webapp (e.g. with the \`tradingagents-analyze\`
  skill on a different machine) and want the result to surface here.

### Batch runs

The **Batch** page wraps Path A in a for-loop: one shared config, a list
of tickers, sequential execution, all tagged with the same batch ID.
To batch via Path B (queue + external worker), submit one queue item per
ticker — the worker handles concurrency.

---

<a id="the-queue"></a>

## The queue lifecycle

Every queued run goes through these states. Status is visible on the
**Queue** page; auto-refreshes every 8s.

| Status | Means | Who sets it |
|---|---|---|
| \`pending\` | Waiting for a worker to claim it | Webapp on POST \`/run-queue\` |
| \`claimed\` | A worker is running it right now | Worker on POST \`/run-queue/claim\` |
| \`done\` | Worker finished; \`result_run_id\` links to the produced run | Worker on POST \`/run-queue/{id}/complete\` |
| \`error\` | Worker reported a failure; \`error_message\` field has details | Worker on POST \`/run-queue/{id}/fail\` |
| \`cancelled\` | User clicked Cancel before completion | Webapp on POST \`/run-queue/{id}/cancel\` |

**Crash recovery**: if a worker dies mid-job, its row stays at
\`claimed\` forever. POST \`/run-queue/reclaim-stale?older_than_seconds=1800\`
reverts any claim older than 30 min back to \`pending\` so another
worker can pick it up. The poller skills run this automatically on
each wake-up.

**Cleanup**: \`done\` / \`error\` / \`cancelled\` rows stay on the dashboard
until you delete them. Use the per-row Delete button on the Queue page,
or DELETE \`/run-queue/{id}\`.

---

<a id="draining-the-queue"></a>

## Draining the queue (workers)

The queue is just a TODO list — it doesn't drain itself. You need a
worker. Here are the available worker setups, from simplest to most
unattended.

### 1. Manual: ask Claude Code

Open a Claude Code session anywhere on your LAN. Say:

> "process the run queue"

The \`tradingagents-analyze\` skill picks up Phase 9 and starts draining.
For a single item, this is 30–60s. For a batch of 10, plan for 5–15 min.

### 2. Semi-persistent: Claude Code \`/loop\`

To keep a worker running for a few hours without re-typing the prompt:

\`\`\`
/loop process the run queue --interval 15min
\`\`\`

Claude Code self-paces using ScheduleWakeup. The session window stays
open; it polls every 15 min, processes anything pending, sleeps until
the next wake-up. Quit by closing the window or running \`/loop stop\`.

### 3. Truly scheduled: Claude Code \`CronCreate\`

For real cron behavior that fires whether or not Claude Code is open in
the foreground:

\`\`\`
/cron create
  --name "tradingagents-queue-drain"
  --schedule "0 6,12,18 * * 1-5"   # 06:00, 12:00, 18:00 weekdays
  --prompt "process the run queue"
\`\`\`

Claude Code maintains the cron registry. When the schedule fires, it
opens a Claude Code session, runs the prompt, exits. The skill drains
whatever's pending and reports the summary.

### 4. Native OS scheduler

If you'd rather drive Claude Code from \`schtasks\` (Windows) or \`cron\`
(macOS/Linux):

\`\`\`bash
# Windows Task Scheduler (PowerShell):
schtasks /Create /SC HOURLY /TN "TradingAgents queue drain" \\
  /TR "claude --skill tradingagents-analyze --prompt 'process the run queue'"

# macOS / Linux launchd or cron:
0 6,12,18 * * 1-5 claude --skill tradingagents-analyze --prompt "process the run queue"
\`\`\`

### 5. From Claude Desktop — \`scheduled-tasks\` MCP

Claude Desktop ships with the **\`scheduled-tasks\` MCP server**
pre-installed at the user level. Tasks land as files under
\`C:\\Users\\<you>\\.claude\\scheduled-tasks\\<taskId>\\SKILL.md\` and
show up in the Claude Desktop sidebar with their next-run timestamp.

To create one from inside Claude Desktop, just say:

> "Schedule a task that runs every weekday at 6:30am called
> *tradingagents-queue-drain* with the prompt 'process the run queue'."

Claude calls \`mcp__scheduled-tasks__create_scheduled_task\` under the
hood. Three scheduling modes:

| Mode | Field | Example |
|---|---|---|
| Recurring | \`cronExpression\` (LOCAL time, 5-field cron) | \`"0 6 * * 1-5"\` = 6am weekdays |
| One-shot | \`fireAt\` (ISO 8601 with offset) | \`"2026-05-17T18:00:00-04:00"\` |
| Ad-hoc | omit both | manual run only |

Same files / same registry are accessible from Claude **Code** on the
same machine — both apps share the user-level state at \`~/.claude/\`.

**The catch — and it's a real one**: scheduled tasks only fire **while
the app is open**. If Claude Desktop was closed when the task was due,
it runs on next launch instead of at the scheduled wall-clock time.
For routine drains (e.g. "every 2 hours while I'm working") this is
fine. For true 24/7 unattended polling (e.g. 6am every morning whether
or not you're logged in), only the server-side auto-poller works —
see option 7 below.

### 6. From Anthropic Cowork

Cowork is cloud-hosted and can't reach the NAS over LAN. To use it as
a worker, set up a Cloudflare Tunnel so it can hit
\`/run-queue/claim\` over public HTTPS. See **[Off-LAN access](#off-lan-access)**
below for the walk-through.

### 7. Server-side auto-poller (not yet built)

A fourth option: a small container alongside the API that polls
\`/run-queue/pending\` and runs analyses using \`ANTHROPIC_API_KEY\`
from \`.env\`. Costs per-call API tokens (defeats the subscription
benefit) but truly unattended even if no Claude session is open.
Available on request — see [Troubleshooting](#troubleshooting) for how
to ask.

---

<a id="scheduling"></a>

## Scheduling unattended runs

Two layers to schedule: **what to run** and **when to run it**.

**What to run** is one of:

| Goal | Prompt |
|---|---|
| Drain whatever's queued | \`process the run queue\` |
| Run fresh analysis on a fixed list | \`/tradingagents-analyze NVDA AMD AVGO MSFT GOOG\` |
| Pull the latest news + sentiment, update the prior decision | \`/tradingagents-analyze NVDA --update\` |
| Re-generate briefs with fresh vocabulary | \`process pending briefs\` |

**When to run it** is the schedule. Pick the mechanism that matches
where you want the worker to live:

| Mechanism | Lives on | Survives reboot? |
|---|---|---|
| Claude Code \`/loop\` | One Claude Code session, manually launched | No — closes with the window |
| Claude Code \`CronCreate\` | Claude Code's registry, fires whenever | Yes |
| Windows Task Scheduler | Your laptop / desktop | Yes |
| Linux/macOS \`cron\` | Whatever Unix machine | Yes |
| \`scheduled-tasks\` MCP in Claude Desktop | Claude Desktop config | Yes |
| Server-side poller (future) | NAS itself, in Docker | Yes, runs 24/7 |

**Recommended setup for most users**: Claude Code's \`CronCreate\` on
the laptop you usually have on. Runs three times a weekday, drains the
queue, posts results back to the webapp. No infra to maintain, no
public exposure, uses your subscription tokens.

---

<a id="providers-and-models"></a>

## Providers & models

Pick the **provider** (the API gateway) first, then a **deep-think
model** (used for the slow careful agents: research mgr, trader, PM)
and a **quick-think model** (used for the fast summarizers: news,
sentiment).

Most users run **anthropic** + sonnet/haiku or **openai** + gpt-4o/mini.
Mixing providers across deep/quick is allowed but rare.

### Anthropic (Claude) — recommended

| Model | Use for |
|---|---|
| \`claude-opus-4-7\` | Top-tier reasoning; the big leagues. Slowest, most expensive. |
| \`claude-sonnet-4-6\` | Balanced. The default deep-think model. |
| \`claude-sonnet-4-5\` | Previous generation; still good. |
| \`claude-haiku-4-5\` | Fast + cheap. The default quick-think model. |

Set \`ANTHROPIC_API_KEY\` in \`.env\`. Verify on **Settings** page.

### OpenAI (GPT)

| Model | Use for |
|---|---|
| \`gpt-5\` | Top tier (where available on your key). |
| \`gpt-4o\` | Multimodal, well-balanced. |
| \`gpt-4-turbo\` | Strong reasoning, slightly older. |
| \`gpt-4\` | Slowest, more deliberate. |
| \`gpt-4o-mini\` | Fast + cheap. |
| \`o1\`, \`o1-mini\` | Reasoning-tuned variants. |

Set \`OPENAI_API_KEY\` in \`.env\`.

### Google (Gemini)

| Model | Use for |
|---|---|
| \`gemini-2.5-pro\`, \`gemini-2-pro\` | Top tier. |
| \`gemini-2-flash\` | Fast. |
| \`gemini-1.5-pro\`, \`gemini-1.5-flash\` | Previous generation. |

Set \`GOOGLE_API_KEY\` in \`.env\`.

### xAI (Grok)

\`grok-3\`, \`grok-2\`, \`grok-2-mini\` — set \`XAI_API_KEY\` in \`.env\`.

### DeepSeek

\`deepseek-r1\` (reasoning), \`deepseek-v3\`, \`deepseek-chat\` — set
\`DEEPSEEK_API_KEY\` in \`.env\`.

### Qwen (Alibaba)

\`qwen-max\`, \`qwen-plus\`, \`qwen-turbo\` — set \`DASHSCOPE_API_KEY\` in \`.env\`.

### GLM (Zhipu)

\`glm-4-plus\`, \`glm-4\`, \`glm-4-flash\` — set \`ZHIPU_API_KEY\` in \`.env\`.

### OpenRouter

One API key, every provider routed through openrouter.ai. Model names
are prefixed: \`anthropic/claude-sonnet-4-6\`, \`openai/gpt-4o\`,
\`meta-llama/llama-3.3-70b-instruct\`, etc.

Set \`OPENROUTER_API_KEY\` in \`.env\`. Useful when you want to A/B test
different providers without managing N API keys.

### Ollama (local, free)

Runs any open-source model on the NAS itself (or another LAN machine).
**Free**, but slower and weaker than the hosted frontier models. Useful
for:
- Sanity-checking the pipeline without burning API tokens
- Privacy-sensitive analyses
- Bulk overnight runs where quality-per-call matters less

Configure the Ollama URL on the **Settings** page. The model dropdown
populates automatically from your local installation (\`ollama list\`).

**Known failure mode**: smaller Ollama models (under 8B params) tend to
fabricate stock prices and miss stock splits. Historical example: a
\`qwen2.5:7b\` run for AMZN produced "Buy at \$3,800" — the post-2022
split price is ~\$190. Brief generation flags this and downgrades to
Hold with a note.

### Choosing depth

Two knobs on the Run page beyond the model picker:

- **Bull/Bear rounds** (1–5): how many back-and-forth rounds the
  investment debate runs. More rounds = deeper but slower.
- **Risk rounds** (1–5): same for the three-way risk debate
  (aggressive / conservative / neutral).

Defaults (1 round each) are tuned for the sonnet/haiku setup. With Opus
you can crank both to 2–3 and get genuinely interesting deliberation;
with Haiku alone, more rounds tend to just repeat themselves.

---

<a id="briefs"></a>

## Briefs workflow

A **brief** is a structured plain-English trade recommendation written
for someone who reads engineering specs but has never traded options.
Lives next to the run archive as \`{run_id}.brief.json\`.

### Fields (all required)

| Field | Format |
|---|---|
| \`decision\` | One of: Buy, Overweight, Hold, Underweight, Sell |
| \`action_plain\` | 3–8 everyday words mapped from the decision (see [Vocabulary](#vocabulary)) |
| \`tldr\` | 2–3 sentences leading with the action |
| \`timeframe\` | e.g. "4–6 weeks", "long-term core position" |
| \`position_size\` | e.g. "4–5% of portfolio in three tranches" |
| \`entry_strategy\` | How to enter — lump sum vs tranches, price targets |
| \`stop_loss\` | Condition or price level to exit if thesis fails |
| \`take_profit\` | Condition or price level to take profits |
| \`triggers\` | 3–7 if-then trigger points with specific numbers |
| \`key_risks\` | 3–5 plain-English failure modes |
| \`benchmark_view\` | One sentence on vs SPY for the timeframe |

### How briefs get produced

**Automatic** (on every completed run): the framework calls the
quick-think model with a templated prompt and a snapshot of the
analysis. Cheap, fast, decent quality.

**Manual via Claude Code** (free, higher quality): on any run's detail
page, click **🤖 Request via Claude Code**. This drops a request marker
next to the archive. Then say "process pending briefs" in a Claude Code
session that has the \`tradingagents-briefs\` skill loaded. The skill
reads the recorded analysis (no fresh LLM calls!), builds a brief using
its parametric knowledge, and POSTs it. Costs **zero API tokens**.

**Bulk re-request** (after a vocabulary or schema change): **History**
page → **🔄 Re-request all** drops markers on every existing run. Then
trigger the skill once and it processes everything.

### Markdown fallback

If the structured schema doesn't fit cleanly (rare), the skill can
submit a free-form markdown brief instead via POST
\`/sidecars/run/{id}/brief/markdown\`. Shows in the UI with a
"Claude Code (markdown)" badge.

---

<a id="vocabulary"></a>

## Decision vocabulary

The audience is **a mechanical engineer who reads FEA reports but has
never traded options**. Engineering analogies are welcome:

> "Volatility is like vibration amplitude — bigger means more
> uncertainty in the prediction band."
> "A stop-loss is a tolerance — exit if the value falls outside this band."
> "Expected return is the mean of the distribution, not a guarantee."

### Five-tier decision schema

| \`decision\` | \`action_plain\` |
|---|---|
| Buy | "buy a starter position" |
| Overweight | "add more than usual" |
| Hold | "keep what you have, no new money" |
| Underweight | "trim about half" |
| Sell | "sell out completely" |

### Banned without a parenthetical translation

Overweight, Underweight, PEG, EV/EBITDA, beta, alpha, RSI, MACD,
MA crossover, Sharpe, drawdown, MOC, tranche, accumulate,
multiple compression, mean reversion, sector rotation. If you must use
them, put plain English in parens right after:

> "PEG of 0.63 (cheaper than a fairly-priced stock — lower is better here)"

### Synonym normalization (when the source uses non-canonical words)

| Source vocabulary | Maps to |
|---|---|
| Accumulate / Bullish / Long | Buy |
| Reduce / Trim | Underweight |
| Avoid / Short / Exit | Sell |
| Neutral / Wait / Watch | Hold |

### What stays unchanged

Specific dollar prices and percentages are concrete numbers, not
jargon. Quote them when the analysis gives them: "stop at \$183
(200-day SMA)" or "20% upside if Q3 hits guidance".

---

<a id="skills"></a>

## Claude skills

Two skills under \`skills/\` in the repo. Symlink or copy to
\`~/.claude/skills/\` so Claude Desktop and Claude Code pick them up.

### \`tradingagents-analyze\`

Runs the full multi-agent pipeline. Plays every agent role
in turn, produces the same prose artifacts the framework produces, and
posts an archive + brief back to the webapp.

**Trigger phrases**:
- \`/tradingagents-analyze <TICKER> [<DATE>]\` — single ticker
- \`/tradingagents-analyze T1 T2 T3\` — batch
- \`/tradingagents-analyze NVDA --ensemble 3\` — N-run consensus
- \`/tradingagents-analyze NVDA --debate-rounds 3 --risk-rounds 2\` — depth
- \`/tradingagents-analyze --queue\` — drain the run queue (Phase 9)
- "analyze NVDA today", "run NVDA with deep debate", "process the run queue"

### \`tradingagents-briefs\`

Processes pending brief requests **without spending any API tokens**.
Reads the recorded analysis, builds a structured brief in
mechanical-engineer vocabulary, POSTs it back.

**Trigger phrases**:
- \`/tradingagents-briefs\` — process every pending request
- "process pending briefs", "rewrite briefs", "refresh briefs"
- "process brief for NVDA" — filter to one ticker
- "show me what's pending" — list, no action

### Installation

\`\`\`bash
# Symlink the repo skills into your user skill dir
ln -s "/path/to/TradingAgents/skills/tradingagents-analyze" ~/.claude/skills/
ln -s "/path/to/TradingAgents/skills/tradingagents-briefs"  ~/.claude/skills/

# OR copy (if you're not on a single LAN machine)
cp -r skills/tradingagents-* ~/.claude/skills/
\`\`\`

Restart Claude Code / Claude Desktop. Both apps share the same skills
dir on the same machine.

---

<a id="api-reference"></a>

## API reference

Auto-generated Swagger UI: **[http://192.168.2.34:8001/docs](http://192.168.2.34:8001/docs)**.
Browse every endpoint, see schemas, try requests inline.

### The endpoints you'll use most

| Method | Path | What it does |
|---|---|---|
| GET | \`/health\` | Liveness check |
| POST | \`/runs\` | Start a synchronous run |
| GET | \`/runs\` | List runs |
| GET | \`/runs/{id}\` | Full run detail + state |
| POST | \`/runs/import\` | Publish an externally-produced run archive |
| DELETE | \`/runs/{id}\` | Remove a run + its sidecars |
| WS | \`/runs/{id}/stream\` | Live event stream while a run is in flight |
| POST | \`/run-queue\` | Queue an analysis for an external worker |
| GET | \`/run-queue\` | List queue items (\`?status=pending\` to filter) |
| GET | \`/run-queue/pending\` | Convenience: just the pending ones |
| POST | \`/run-queue/claim\` | Worker atomically claims items |
| POST | \`/run-queue/{id}/complete\` | Worker reports success |
| POST | \`/run-queue/{id}/fail\` | Worker reports failure |
| POST | \`/run-queue/{id}/cancel\` | User cancels |
| POST | \`/run-queue/reclaim-stale\` | Revert stale claims to pending |
| GET | \`/sidecars/pending\` | Pending brief requests |
| POST | \`/sidecars/run/{id}/brief\` | Submit a structured brief |
| POST | \`/sidecars/request-all-missing\` | Bulk request briefs (\`?include_existing=true\`) |
| GET | \`/runs/{id}/brief\` | Get the brief for a run |

### Pydantic schemas

All request/response shapes live in \`service/schemas.py\` (Brief is in
\`gui/brief.py\`). The OpenAPI spec at \`/openapi.json\` is the
machine-readable contract.

---

<a id="off-lan-access"></a>

## Off-LAN access (Cowork, mobile, remote)

The API is LAN-only by default — bound to \`192.168.2.34:8001\`. To use
it from Anthropic Cowork (which is cloud-hosted) or a phone away from
the LAN:

### Cloudflare Tunnel

Free, no port forwarding, TLS handled for you. Two flavors:

**Quick tunnel** (zero setup, ephemeral \`*.trycloudflare.com\` URL):

\`\`\`bash
docker compose --profile tunnel up -d cloudflared
docker compose logs cloudflared | grep trycloudflare.com
\`\`\`

URL changes on every restart. Fine for a one-off test.

**Named tunnel** (5-min one-time setup, stable URL):

1. Sign up at [dash.cloudflare.com](https://dash.cloudflare.com) (free).
2. **Zero Trust → Networks → Tunnels → Create a tunnel** named
   \`tradingagents-api\`.
3. Copy the Docker install token.
4. Paste into \`.env\`: \`CLOUDFLARED_TOKEN=eyJ...\` and
   \`CLOUDFLARED_CMD=tunnel --no-autoupdate run\`.
5. In the dashboard's Public Hostname tab, route
   \`tradingagents-api.your-domain.com\` → \`http://api:8000\`.
6. \`docker compose --profile tunnel up -d cloudflared\`.

Now Cowork can hit \`https://tradingagents-api.your-domain.com\` with
no LAN access required.

### Use it from a Claude session

\`\`\`bash
export TRADINGAGENTS_API=https://tradingagents-api.your-domain.com
export TRADINGAGENTS_WEB=https://tradingagents.your-domain.com   # if web is tunneled too
\`\`\`

The \`tradingagents-briefs\` and \`tradingagents-analyze\` skills both
honor those env vars and fall back to the LAN default if they're unset.

Full walk-through: \`docs/COWORK.md\` in the repo.

### Optional: auth

Cloudflare Access can require a Google / GitHub / one-time-code login
before any request reaches the tunnel. Add it under **Zero Trust →
Access → Applications**. Useful if you put the API on a domain that's
actually reachable from anywhere.

---

<a id="troubleshooting"></a>

## Troubleshooting

### "API not reachable"
- Same Wi-Fi as the NAS? \`ping 192.168.2.34\` from your machine.
- Containers up? On the NAS: \`docker compose ps\` (you should see
  \`tradingagents-api\` and \`tradingagents-web\` both healthy).
- Logs: \`bash scripts/nas/logs.sh api\` from the repo root.

### "All decisions come back Hold"
Symptom of a weak LLM model that can't reach a conviction. Common with
smaller Ollama models. Fix:
- Use **anthropic** or **openai** provider with sonnet/gpt-4o or better.
- If you must use Ollama, pick a 13B+ parameter model.

### "Brief mentions stock prices that aren't current"
Likely an Ollama hallucination — small models can fabricate prices,
especially for stocks that have done splits (AMZN, GOOGL, NVDA, AAPL
have all split). Re-run with anthropic; the brief generator will
downgrade Ollama-derived briefs with bad prices to Hold and flag the
issue in the tldr.

### "Queue item stuck at 'claimed' forever"
The worker died before reporting back. Recover with:

\`\`\`bash
curl -X POST "http://192.168.2.34:8001/run-queue/reclaim-stale?older_than_seconds=1800"
\`\`\`

That reverts any claim older than 30 min back to \`pending\`. Or DELETE
the item from the Queue page if you don't want to retry.

### "Queue never drains automatically"
No worker is running. Pick one of the options under
[Draining the queue](#draining-the-queue) and set it up. The default
state is "manual" — the queue is a TODO list, not a self-driving job.

### "I want true 24/7 unattended automation"
Two paths:
1. **Cheapest in human-attention terms**: Claude Code \`CronCreate\` on a
   machine that's usually on. Fires the worker on a schedule.
2. **Cheapest in tokens**: a Linux box (or a NAS scratch container) with
   a python poller using your subscription-bound Anthropic key.
3. **If you want the NAS itself to handle it**: ask for the server-side
   auto-poller feature (not yet built — would be a separate \`worker\`
   container in \`docker-compose.yml\`).

### "Model dropdown is missing my model"
The dropdown is a curated list of common models. Pick **"Other
(custom)…"** at the bottom of the dropdown — that reveals a free-text
input where any model name your provider supports will work. If your
provider lacks an entry entirely, just type the model name; the
backend doesn't care about UI state.

---

<a id="files-and-paths"></a>

## Files & paths

### On the NAS

| Path | Contents |
|---|---|
| \`/volume1/docker/tradingagents/\` | Repo clone (deployed via \`scripts/nas/deploy.sh\`) |
| \`/volume1/docker/tradingagents/data/\` | Runtime state (mounted into containers) |
| \`/volume1/docker/tradingagents/data/gui.db\` | SQLite — runs, batches, queue, notes, watchlist, positions |
| \`/volume1/docker/tradingagents/data/logs/<TICKER>/.../*.json\` | Run archives |
| \`/volume1/docker/tradingagents/data/logs/<TICKER>/.../*.brief.json\` | Structured briefs |
| \`/volume1/docker/tradingagents/data/logs/<TICKER>/.../*.brief.request.md\` | Brief request markers |
| \`/volume1/docker/tradingagents/.env\` | API keys, planner config, tunnel token |

### In the repo

| Path | Contents |
|---|---|
| \`CLAUDE.md\` | Audience + vocabulary rules (canonical) |
| \`docs/COWORK.md\` | Cloudflare Tunnel setup walk-through |
| \`docs/CLAUDE_CODE_PROMPT.md\` | One-time prompt for brief generation (legacy) |
| \`gui/brief.py\` | Brief Pydantic schema (source of truth) |
| \`gui/storage.py\` | SQLite schema + helpers |
| \`gui/sidecars.py\` | Read/write helpers for archive sidecars |
| \`service/routers/runs.py\` | Run create/import/stream endpoints |
| \`service/routers/run_queue.py\` | Queue endpoints |
| \`service/routers/sidecars.py\` | Brief request markers + bulk request |
| \`service/routers/briefs.py\` | Brief get/post endpoints |
| \`skills/tradingagents-analyze/\` | Full-pipeline skill |
| \`skills/tradingagents-briefs/\` | Brief processor skill |
| \`scripts/nas/deploy.sh\` | First-time NAS deploy |
| \`scripts/nas/upgrade.sh\` | Pull, rebuild, restart (idempotent) |
| \`scripts/nas/logs.sh\` | Tail container logs over SSH |
| \`scripts/nas/nas-cmd.sh\` | Run arbitrary command on the NAS |

### URLs (LAN defaults)

| URL | What |
|---|---|
| [http://192.168.2.34:3001](http://192.168.2.34:3001) | Web UI (this page) |
| [http://192.168.2.34:8001](http://192.168.2.34:8001) | API root |
| [http://192.168.2.34:8001/docs](http://192.168.2.34:8001/docs) | OpenAPI / Swagger |
| [http://192.168.2.34:8001/openapi.json](http://192.168.2.34:8001/openapi.json) | Machine-readable spec |
| [http://192.168.2.34:8001/health](http://192.168.2.34:8001/health) | Liveness probe |
`;

export default function DocsPage() {
  return (
    <div className="flex gap-8">
      {/* Sticky TOC */}
      <aside className="hidden lg:block w-56 shrink-0">
        <div className="sticky top-6 space-y-1 text-sm">
          <div className="text-xs uppercase tracking-wider text-muted mb-2">
            On this page
          </div>
          {TOC.map((t) => (
            <Link
              key={t.id}
              href={`#${t.id}`}
              className="block px-2 py-1 rounded hover:bg-surface text-muted hover:text-fg"
            >
              {t.label}
            </Link>
          ))}
          <div className="border-t border-border mt-3 pt-3">
            <Link
              href="http://192.168.2.34:8001/docs"
              target="_blank"
              className="text-xs text-accent hover:underline"
            >
              OpenAPI / Swagger →
            </Link>
          </div>
        </div>
      </aside>

      <main className="flex-1 min-w-0 max-w-4xl">
        <Markdown>{CONTENT.trim()}</Markdown>

        {/* Quick links footer */}
        <div className="mt-12 pt-6 border-t border-border text-sm text-muted">
          <div className="flex flex-wrap gap-4">
            <Link className="text-accent hover:underline" href="/run">
              Run page →
            </Link>
            <Link className="text-accent hover:underline" href="/batch">
              Batch →
            </Link>
            <Link className="text-accent hover:underline" href="/queue">
              Queue →
            </Link>
            <Link className="text-accent hover:underline" href="/history">
              History →
            </Link>
            <Link className="text-accent hover:underline" href="/settings">
              Settings →
            </Link>
            <Link
              className="text-accent hover:underline"
              href="http://192.168.2.34:8001/docs"
              target="_blank"
            >
              API reference →
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}
