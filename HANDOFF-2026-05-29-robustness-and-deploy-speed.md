# Handoff — 2026-05-29 — deploy speed + robustness sweep + test foundation

> ## ⏭️ START HERE (added 2026-05-30, end of session)
>
> **Git is clean and synced (HEAD = origin/main).** All work is
> committed + pushed + deployed; all containers healthy (api/web/mcp).
>
> **★ NEW FEATURE LIVE — Portfolio Lab at `/simulation`** (commits
> 2d2e05b → d5d41cf). A professional what-if/backtest/Monte-Carlo suite:
> - Backend engine `service/portfolio_analytics.py` — pure numpy/pandas
>   (no scipy/dep added), **18 unit tests** in
>   `tests/test_portfolio_analytics.py`: CAGR, vol, Sharpe, Sortino, Calmar,
>   max-drawdown, beta/alpha/correlation, windowed (1/2/3/5y) returns, and
>   Monte Carlo (bootstrap + normal) with VaR/CVaR. Verified exact (beta=1
>   self / beta=2 double-market, MC determinism, VaR≤CVaR).
> - Endpoints on `/sim`: `POST /sim/backtest`, `POST /sim/montecarlo`,
>   `GET /sim/portfolio-actual` (seeds "your actual mix" from live
>   positions). All return 200 live; backtest E2E with the real book gives
>   coherent numbers (your mix 6y CAGR 26.4%/Sharpe 0.93/maxDD −33%;
>   big-6 tech 31.6%; QQQ 18.7%; SPY 11.1%).
> - Frontend `web/app/simulation/page.tsx` — scenario builder seeded with
>   4 presets, equity-curve vs SPY, risk-stat table, trailing-window table,
>   correlation heatmap, Monte Carlo fan + outcome distribution. Types in
>   `web/lib/simTypes.ts`. `/simulation` renders 200 on a clean build.
> - **Two route-shadow gotchas fixed:** `/sim/{sid:int}` converter so
>   `/sim/portfolio-actual` resolves (was 422). And a self-inflicted broken
>   build (page committed before api.ts methods existed) was repaired in
>   d5d41cf — lesson: when a page + its api client land together, build web
>   BEFORE trusting the commit.
> - **Follow-up ideas the user wants next:** efficient frontier, drawdown-
>   over-time chart, tax-aware de-risking (model the AAPL embedded gain),
>   DCA vs lump-sum, rolling-returns, save-scenario-as-paper-trades.
> - 63 backend unit tests pass total (`py -3 -m pytest tests/`).
>
> **No known open bugs.** Final consolidated live smoke — ALL correct:
> health 200, charts json 200 + png 200, portfolio 200, regime/run 200,
> calendar 200(ok)/400(bad-input), news/feed 200, paper/summary 200,
> web 200. 45 unit tests pass; chat WS E2E streamed (deltas=4).
>
> **Last bug fixed (`836498d`):** `GET /regime/run/{run_id}` was 500ing —
> `get_run_regime` called the `get_runs_by_regime` *route handler* directly
> from Python, so its FastAPI `Query(...)` defaults became the literal arg
> values; `timedelta(days=<Query object>)` raised TypeError. Fixed by
> passing explicit ints. **Lesson for next time: never call a FastAPI route
> handler as a plain function** — extract a helper, or pass real args. Worth
> grepping for other intra-Python calls of `@router`-decorated functions.
>
> **Possible follow-up (NOT a bug):** that regime call does iterate recent
> runs each request; if the brief panel feels slow, cache the
> `get_runs_by_regime` aggregate (slow-changing) rather than recomputing.
>
> **⚠️ TOOL-CHANNEL HAZARD (cost me a lot this session — read §8 too):**
> on this network share, **parallel/batched tool calls return scrambled,
> misattributed, or hallucinated file contents**, which caused several
> Edits to match fabricated text and silently no-op, and once **deleted a
> route** (`7a78745` dropped the charts JSON route; restored in `831d505`).
> **Mitigations that worked:** (1) ONE tool call per message; (2) after
> every edit, **verify via an independent channel** — `git show`/`grep` the
> committed diff, `py -3 -m py_compile`, and **curl the live endpoint** —
> never trust the Edit "success" message alone; (3) Read a file in the same
> turn immediately before editing it; (4) for NAS commands, don't pipe
> `docker build` through `| tail` — the pipe masks the build's real exit
> code (a failed `next build` reported exit 0 once). Use the bare
> `nas_run.py` form which reports the true remote exit code.
>
> **Verified-live fixes this session** (curl-confirmed): chat WS streaming
> (E2E deltas=4), charts json=200 + png=200, calendar bad-input=400.
> 45 unit tests pass (`py -3 -m pytest tests/test_gui_storage.py
> tests/test_gui_brief.py`).


Supersedes `HANDOFF-2026-05-27-...`. Read that one too for the broader
infra map (NAS, containers, regime work, MCP). This doc focuses on what
changed 2026-05-28/29 and the prioritized backlog for "make it super
robust + feature complete."

## 1. Session focus / next-session focus

This session: (a) fixed the slow-rebuild deploy, (b) stood up the first
tests for the webapp layer, (c) ran two adversarial robustness audits
(backend + frontend) and fixed the highest-value findings. **Everything
below is committed + pushed to `origin/main` and deployed to the NAS,
all services healthy (api/web/mcp = 200/200/up).**

**Next-session focus:** keep working the §6 backlog (remaining audit
findings, prioritized) and §7 features. The audits are the backlog —
don't re-derive them.

- **Repo:** `Z:\My Documents\code repo\active\hedge_trader\TradingAgents`
- **Branch:** `main` (push-as-you-go; fork `mrh335/TradingAgents`).
- **Live commits this session:** `40abf25 … e7f337d` (see §3).

## 2. NAS access — IMPORTANT (sshpass is NOT installed here)

`scripts/nas/nas-cmd.sh` + `upgrade.sh` rely on `sshpass`, which is **not
installed** on this Windows/Git-Bash box, so they hang/fail. The working
path this session is a paramiko helper:

- **Helper:** `C:\Users\markh\AppData\Local\Temp\nas_run.py`
  (recreate it if the temp dir was cleared — it reads
  `scripts/nas/credentials.local`, connects via paramiko, runs a remote
  `bash -lc` command, prints a tail + elapsed + exit code, never prints
  secrets, sets stdout to utf-8 so npm/docker box-drawing chars don't
  crash it).
- **Interpreter:** use `py -3` (Python 3.14 at
  `C:\Users\markh\AppData\Local\Python\pythoncore-3.14-64\python.exe`).
  It has paramiko 4.0, pip, pytest 9, pydantic 2.12. The *agent venv*
  python has none of these — always use `py -3`.
- **Run a NAS command:**
  `py -3 "C:\Users\markh\AppData\Local\Temp\nas_run.py" "cd /volume1/docker/tradingagents && docker compose ps" 30`
- Long builds auto-background; you get a completion notification and read
  the `...tasks/<id>.output` file.
- Builds are safe (no restart). Only `docker compose up -d` restarts
  containers (~45-60s api startup due to yfinance broadcaster pre-warm —
  smoke-test AFTER a 45s+ sleep, not 12s).

## 3. What shipped this session (all live)

| Commit | What |
|---|---|
| `40abf25` | **Deploy speed:** Dockerfile.api installs deps in a layer keyed to pyproject (stub-package trick) then `pip install --no-deps .` for source; web uses `npm ci` + committed lockfile |
| `1c82b06` | **Tests:** gui/storage.py regression suite (8 tests) |
| `acb0a56` | **Tests:** gui/brief.py suite (37 tests) |
| `e126a53` | **Fix (frontend CRITICAL):** Markdown.tsx hooks-order white-screen; ChatPanel WebSocket leak + onerror race |
| `80986fd` | **Fix (backend):** non-blocking paper-open (asyncio.to_thread), calendar date validation (400 not 500), tz-aware timestamps (storage._now + reclaim) |
| `4f68f66` | **Perf:** api runner-stage reorder (user/workdir layers cached on backend-only rebuilds) |
| `e7f337d` | **Fix (frontend):** null guards for portfolio `(undefined%)` and ticker `$null` 52-week range |

### Deploy speed result (the headline ask)
- **Before:** every backend edit reinstalled the ENTIRE dep tree +
  recompiled pycairo → **~22 min** (cold) on this NAS.
- **After:** deps layer cached; backend edit rebuild ≈ **9 min**, now
  dominated by the NAS's very slow disk IO (groupadd 65s, layer export
  73s — hardware, not the Dockerfile). The `4f68f66` reorder trims
  another ~100s.
- **Further win available (not done):** the venv is re-COPY'd on every
  source change because `pip install --no-deps .` writes the package into
  `/opt/venv`. If instead you keep the project OUT of the venv and run
  via `PYTHONPATH=/app` (deps-only venv → venv COPY stays cached), a
  backend rebuild drops to ~2-3 min. RISK: verify nothing calls
  `importlib.metadata.version("tradingagents")` or relies on the console
  entry-points; the container runs `uvicorn service.app:app` which works
  via PYTHONPATH. Worth doing.

## 4. Tests — how to run + what's covered

- **Run locally:** `cd <repo> && py -3 -m pytest tests/test_gui_storage.py tests/test_gui_brief.py -q -p no:cacheprovider` → 45 pass. These are stdlib/pydantic-only (no API keys, no network, no LLM/langchain).
- **Covered:** `gui/storage.py` (init_db idempotency, lazy ALTER migration columns, WAL mode, run lifecycle CRUD, list filters) and `gui/brief.py` (`_normalize_decision`, `_parse_markdown_to_brief` incl. all trigger formats + sparse/meta fallbacks, Brief JSON round-trip + to_markdown).
- **NOT covered yet (task #2/#3):**
  - **service/ routers** — need the full dep tree (fastapi TestClient pulls langchain/hmmlearn). Options: (a) `py -3 -m pip install -e ".[service]"` locally — RISK: Python 3.14 may lack wheels for scipy/hmmlearn; (b) run pytest inside the api container via `docker cp tests + pip install pytest` (container has all deps). Recommend writing TestClient tests for: route-ordering regressions, calendar 400, paper-open, health, and a few read endpoints, then running them in-container.
  - **frontend Vitest** — no runner exists. Add vitest + jsdom to web/devDeps, test `web/lib` (api.ts request(), ws.ts URL builder, format helpers) and the Markdown hooks fix. Generate the lockfile via node:20 (see §5) so `npm ci` stays valid.
- **Gotcha:** the real `brief.py` API is `_normalize_decision` / `_parse_markdown_to_brief` / `Trigger` (private names; returns None for unknown decisions, fills non-empty fallbacks). Don't invent `normalize_decision`/`parse_markdown_brief` — they don't exist (a scrambled file read led me astray once).

## 5. package-lock.json note

`web/package-lock.json` was generated with `npm install --package-lock-only --legacy-peer-deps` and **works with `npm ci`** (verified — the earlier "build failure" was the helper's Unicode crash, not npm). If `npm ci` ever rejects it after a package.json change, regenerate inside the exact build image:
`docker run --rm -v /tmp/wl:/w -w /w node:20-alpine npm install --legacy-peer-deps` then pull the lock back (see the gen_lock approach). Do NOT hand-edit the lock.

## 6. Robustness backlog (from the two audits — prioritized, UNFIXED)

**FIXED + LIVE this session** (descriptions, since the two audits number
independently):
- FE: Markdown hooks white-screen, ChatPanel WS leak+race, portfolio
  `undefined%`, ticker `$null` 52w, correlation-matrix crash, simulation
  `NaN%`, api.ts non-JSON-200 guard, watchlist WS churn, LiveTickerStrip
  `$NaN`.
- BE: paper-open async block, calendar 400 validation, storage tz-aware
  timestamps, **charts dead .png route** (reordered so /decisions/{ticker}.png
  isn't shadowed by /decisions/{ticker}; both verified 200 live. NOTE: a
  couple of commit messages mention ".csv" — there is NO .csv route; that
  was a garble-induced phantom, harmless), streaming subscriber-queue leak,
  earnings yfinance type-guard, chat.py event-loop block (E2E verified,
  deltas=4), tuned sqlite conns (runs/runner_pool), news_feed cache lock,
  remaining datetime.utcnow() (ask.py, simulation.py — service/ now
  utcnow-free; gui/runner_worker.py + gui/export.py still have it, CLI-path,
  low priority).
  CORRECTION: the "regime duplicate sweep" listed earlier was a MISREAD —
  get_run_regime calls get_runs_by_regime() once. Not a bug; nothing changed.

**ALL of the above HIGH/MEDIUM items are now FIXED + LIVE.** chat.py
event-loop (E2E deltas=4), runs/runner_pool tuned conns, news_feed lock,
ask.py+simulation.py utcnow, run-page WS onclose, `/run?ticker=` (via
window.location.search — sidesteps the Suspense gotcha), earnings markdown,
charts JSON route restored. The `storage.py:1899` "bogus inserted line"
was a phantom from a garbled read — that code isn't present; nothing to fix.

**STILL OPEN (next session) — genuinely LOW priority, deferred:**
- FE `ws.ts` `:8001` fallback: only matters behind a reverse proxy; fine
  for the current single-LAN deploy (NEXT_PUBLIC_WS_BASE is set in compose).
- FE cosmetics: macro raw-float formatting; history page hardcoded
  `192.168.2.34:8001` in on-screen copy → make it `/api/...` or env-driven.
- BE: `ask.py` per-position smart-money loop (perf: N queries/request +
  one broad `except`); gui/runner_worker.py + gui/export.py still use
  `datetime.utcnow()` (CLI/export path, not the live API).
- TESTS: service-router tests not yet written (need the api container —
  local py 3.14 lacks scipy/hmmlearn wheels). Frontend Vitest not set up.

### Backend (service/ + gui/) — remaining
- **HIGH `chat.py:89` `stream_chat`** — iterates a *synchronous* LLM
  generator (`llm.stream`) inside an `async def` WS handler → blocks the
  whole event loop for the full multi-second answer. Fix: pump
  `stream_response` via `asyncio.to_thread` into an `asyncio.Queue` the
  async handler drains.
- **HIGH `charts.py:147`** — `GET /charts/decisions/{ticker}.png` is
  declared AFTER `/{ticker}`, so `{ticker}` (regex `[^/]+`) swallows
  `NVDA.png` → the PNG route is dead (returns JSON). Fix: declare the
  `.png` route BEFORE the bare `{ticker}` route (move the function up).
- **HIGH `regime.py:346` `get_run_regime`** — the per-run brief widget
  calls `get_runs_by_regime()` (defaults) which backtests ALL done runs
  in 365d on every call. Cache the aggregate or pass only this run's
  regime row.
- **MED `datetime.utcnow()` still in** `ask.py:60,146`, `simulation.py:163`
  (storage.py already fixed). Same `datetime.now(timezone.utc)` swap.
- **MED `runs.py:323` + `runner_pool.py:164`** open raw
  `sqlite3.connect(storage.DB_PATH)` → bypass WAL/busy_timeout → can hit
  "database is locked" under concurrency. Route through `storage._conn()`.
- **MED `streaming.py:73-74` combined_stream** — the two `subscribe()`
  calls are before the `try`, so a failure on the 2nd leaks the 1st
  subscriber queue. Move both inside try / unsubscribe on failure.
- **MED `earnings.py:257,273` `_fetch_revisions`** — `.empty`/`.index`
  on the yfinance return without isinstance guard → 500 on a yfinance
  shape change (also breaks the earnings_summary drainer). Guard types.
- **MED `news_feed.py:26-40`** `_CACHE` dict mutated from threadpool
  without a lock (benign in CPython; add a `threading.Lock`).
- **MED heavy endpoints** (regime/portfolio_metrics/risk/correlation) —
  no in-flight de-dup on cache miss → cold-cache thundering herd of
  yfinance/HMM. Add a per-key in-flight future.
- **LOW `storage.py:1899`** `inserted += c.total_changes` is cumulative
  garbage (masked by a re-count; just delete the line).
- **MED `ask.py:162-178`** per-position smart-money loop + broad
  `except: pass` (latency + hides DB errors).
- Audit confirmed: **no SQL injection** anywhere; pollers are robust;
  other route ordering is correct.

### Frontend (web/) — remaining
- **HIGH `run/page.tsx:158`** run WS effect deps `[runId]` only, no
  `onclose`/reconnect → on a network blip the run sticks on "Streaming…"
  forever with no error. Add onclose→error/reconnect.
- **HIGH `ws.ts:6`** — when `NEXT_PUBLIC_WS_BASE` is unset, falls back to
  `ws://<host>:8001`. Works for THIS deployment (compose sets the env),
  but breaks behind any reverse proxy / different host. Consider proxying
  WS through `/api`. (Low urgency for current single-LAN setup.)
- **HIGH `watchlist/page.tsx:27`** WS effect keyed on `[list.data]`
  (object identity) → tears down/reopens every ticker socket on every
  refetch. Fix: key on `[(list.data??[]).map(e=>e.ticker).join(",")]`
  (portfolio page already does this).
- **HIGH `run/page.tsx`** ignores `?ticker=` query param (links from
  watchlist/ticker pass it) → form stays on NVDA, easy to run the wrong
  ticker. Fix: `useSearchParams().get("ticker")` — NOTE Next 15 needs the
  reader wrapped in `<Suspense>` or the build fails.
- **MED `portfolio-analytics/page.tsx:422`** `data.matrix[i].map` assumes
  square matrix vs tickers → white-screens if they desync. `(data.matrix[i] ?? []).map`.
- **MED `simulation/page.tsx:221`** `t.cost.toFixed` / `t.mu_annual*100`
  on untyped per_trade rows → crash/NaN if a trade is unpriced. Guard.
- **MED `api.ts:41` `request()`** — `res.json()` throws on a non-JSON 200
  (proxy/HTML) → "Unexpected token <". try/catch → undefined/text.
- **MED `earnings/[ticker]/page.tsx:244`** `bullets_md` rendered as raw
  text, not `<Markdown>`. **MED `macro/page.tsx:114`** raw float display.
  **MED `LiveTickerStrip.tsx:45`** `$NaN` possible. **MED `history/page.tsx:189`**
  hardcoded `192.168.2.34:8001` in UI copy → use `/api/...`.
- Audit confirmed: react-markdown v9 has no rehype-raw → no markdown XSS.
  Date formatting (`lib/format.ts`) is well-guarded.

## 7. Features backlog (task #5 — "feature complete + feature filled")

User opted into ALL of these + "more if you can think of them":
1. **Price/volume chart on `/ticker/[ticker]`** — handoff-flagged gap.
   Backend: a `/tickers/{t}/history` endpoint (yfinance OHLCV, cached) or
   reuse charts data; Frontend: Recharts (already a dep) line/area +
   volume, overlay SMA50/200 (snapshot already has them).
2. **Regime-aware decisioning** — route the framework decision through
   the regime signal (e.g. down-weight Buys in VOLATILE_BEAR). The dual
   regime is already surfaced in BriefPanel; this makes it act.
3. **Surface walk-forward verdict** — turn `/backtest/walk-forward` into
   a clear UI answer ("does the framework beat SPY, by regime").
4. Extra ideas worth proposing: portfolio concentration/risk alerts on
   the dashboard; a "what changed since last run" diff per ticker;
   earnings-countdown badges; CSV export of paper-trade P&L; a global
   error boundary component (would have caught the Markdown white-screen
   gracefully — high value).

## 8. Environment gotchas (cost me time — read this)

- **Tool channel is flaky on this network share under PARALLEL tool
  calls** — issuing many tool calls in one message produced scrambled,
  misattributed, and duplicated outputs, and cancelled sibling calls.
  **Work strictly ONE tool call at a time.** Sequential calls were
  reliable; parallel batches were not.
- **Two pythons:** agent venv python (no pip/paramiko/pytest) vs `py -3`
  (has everything). Always `py -3`.
- **LF→CRLF** warnings on commit are benign (NAS checks out LF).
- **Smoke-test after ≥45s** post `up -d` (api startup pre-warm).
- The stale worktree `.claude/worktrees/determined-greider-e8ac6c` is
  still there (out of scope; safe to `git worktree remove --force`).

## 9. Pickup checklist
1. `git -C "<repo>" status` — expect clean except `io_tokens.md`.
2. `git -C "<repo>" log --oneline -8` — top should be `e7f337d` (or later).
3. `py -3 -m pytest tests/test_gui_storage.py tests/test_gui_brief.py -q` → 45 pass.
4. `py -3 "C:\Users\markh\AppData\Local\Temp\nas_run.py" "cd /volume1/docker/tradingagents && docker compose ps" 20` → api/web/mcp healthy. (Recreate nas_run.py from §2 if missing.)
5. `curl http://192.168.2.34:8001/health` → `{"status":"ok"}`.
6. Pick the top of §6 (backend `chat.py` blocking stream or `charts.py`
   .png route) or a §7 feature.


> ## ✅ TAX BACKEND VERIFIED + RECONCILED (2026-05-31, commit 8ff413b)
>
> /tax/lots now reconciles to the REAL book (read live, every position
> self-checks embedded_gain == LTg+STg+LTloss):
>   BOOK $2,176,498  |  AAPL 95.57% concentration
>   AAPL 6,665.6 sh  val $2,080,064  embed +$1,774,933 (LT +1,762,067 | ST +12,866)
>   MSFT    45   sh  val $20,261     embed +$1,784 (all LT)
>   NVDA   210   sh  val $44,339     embed +$14,721 (LT gain +25,499 | LT loss -10,778)
>   RIVN  1,953  sh  val $31,834     embed -$23,435 (all LT loss — harvestable)
> Fix chain (all committed+pushed): lot_from_planner reads
> shares_remaining_this_lot, falls back to shares_acquired-shares_sold,
> derives term from purchase_date; reconcile_lots_to_totals scales raw
> over-counted lots to the consolidated /summary authoritative shares+cost
> and drops phantom fully-sold symbols (PYPL). 20 unit tests pass.
> Earlier $2.74M/8,338-sh/PYPL readings were the PRE-reconcile bug — ignore.
>
> NOTE: SFTP is DISABLED on the Synology ('Channel closed' from paramiko
> open_sftp). To read NAS data: run python INSIDE the container via
> `docker exec tradingagents-api python3 -c '...'` and print a compact
> summary to stdout (exec_command works; SFTP does not). Do the curl AND the
> json parse in the SAME docker exec (container /tmp != host /tmp).
>
> REMAINING: build /tax UI page (web/app/tax/page.tsx + web/lib client/types):
> lot table, de-risk slider w/ HIFO/FIFO/LIFO compare, loss-harvest card
> (RIVN -23k + NVDA -10.8k), charitable-vs-sell card. /tax/derisk,
> /tax/harvest, /tax/charitable endpoints already live (verify their numbers
> the same in-container way before trusting). Default CA top 37.1/54.1.

> ## TAX FEATURE COMPLETE + LIVE + VERIFIED (2026-05-31, commit 1d9c2f4)
> /tax page renders 200; web build EXIT=0 (tsc clean). All endpoints
> verified in-container with self-check (tax == LTg*0.371 + STg*0.541, all OK):
>   - /tax/lots: book $2,176,498, AAPL 95.6%, every position reconciles.
>   - /tax/derisk AAPL $400k: HIFO/LIFO $132,219 tax (33.1% drag), FIFO
>     $143,479 (35.9%) -> HIFO saves $11,260. Self-check OK on all 3.
>   - /tax/charitable $100k AAPL: benefit $89,316 (cap-gains avoided $35,216
>     + deduction $54,100) vs $31,632 tax if sold -> donating wins.
> Files: service/tax_analytics.py (20 unit tests), service/routers/tax.py,
> web/app/tax/page.tsx, web/lib/taxTypes.ts + Tax client, /tax nav in layout.tsx.
> Data path: planner /investment-ledger/lots, reconciled to consolidated
> /summary (reconcile_lots_to_totals). SFTP is OFF on the Synology -> read NAS
> data via docker exec python printing compact stdout, curl+parse in the SAME
> exec (container /tmp != host /tmp).
> ENHANCEMENT IDEAS: gain-budget-by-year, before/after concentration on derisk,
> multi-year DCA-out plan, surface RIVN/NVDA loss lots on the harvest card.
