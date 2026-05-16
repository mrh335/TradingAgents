# Scheduled runs

Once the skill works end-to-end interactively, you can run it on a
recurring schedule using the built-in `/schedule` skill (which exists
in your Claude Code install — `/schedule list` to see existing routines).

## Why pair with update mode

Scheduled runs are the canonical use case for **update mode** (the
incremental-analysis feature added in this skill's Phase 0.5). Each
scheduled invocation:

1. Looks up the most recent run for the ticker.
2. Pulls only the news/data delta since that run completed.
3. Asks the analysts to **update**, not restate.
4. The PM explicitly reports whether the rating changed.

A daily NVDA run takes a few thousand tokens, not tens of thousands,
because most of the analysis carries forward. Over a year of weekday
runs you get a longitudinal record of how the thesis evolved.

## Recommended schedules

### Daily check on a watchlist (weekdays before US market open)

```
/schedule create --name "daily-watchlist" \
    --at "weekdays 06:00 America/New_York" \
    --prompt "/tradingagents-analyze NVDA AMD AVGO MSFT GOOG"
```

This runs each ticker in update mode. Each gets its own archive in the
webapp; a batch synthesis is attached as an `analysis.md` sidecar on
each run. The first run takes a few minutes (fresh per ticker); from
day 2 onwards it's much faster.

### Weekly deep-dive on a single position (Sunday evening, fresh run)

```
/schedule create --name "weekly-nvda-deep" \
    --at "sundays 18:00 America/New_York" \
    --prompt "/tradingagents-analyze NVDA --fresh --debate-rounds 3 --risk-rounds 2"
```

The `--fresh` flag forces a fresh run even though a daily update may
exist — useful periodically to reset accumulated drift in the analysis.

### Post-earnings backtest sweep (last Friday of every month)

```
/schedule create --name "monthly-backtest" \
    --at "monthly Friday 20:00" \
    --prompt "list my last 10 NVDA runs and backtest each at horizon=30"
```

(Requires interrogating the framework API for the last 10 runs and
calling `scripts/backtest.py` on each. Currently relies on Claude to
orchestrate this; could become a dedicated script in a future phase.)

## How `/schedule` invokes the skill

The schedule runtime opens a fresh Claude Code session, runs the prompt
you registered, and exits. Each invocation:

1. Inherits no chat history (it's a fresh session).
2. Has access to all your installed skills (`tradingagents-analyze`
   included once you've restarted Claude Code at least once after
   installing it).
3. Should NOT prompt the user mid-run — `--debate-rounds` etc. must be
   set in the schedule prompt itself or in `config/defaults.yaml`.

## What's logged

Every scheduled run appends a row to **both** `io_tokens.md` files (see
`scripts/token_logger.py`). The framework webapp's History page shows
the run as soon as the publish step completes (within 30s of the run
finishing).

## Failure handling

If a scheduled run fails:
- `lookup_parent_run.py` returning empty → first run, runs fresh
- API unreachable → entire run fails; `/schedule` records the failure
- Brief validation fails → publish step retries once, then aborts; no
  partial run is left in the DB (the import endpoint is atomic on the
  archive side; brief sidecar write is best-effort)

The schedule runtime should surface failures back to you in its own
notification stream — check `/schedule list` for run status.

## Adjusting per-environment

Different machines may need different settings. The skill reads from
`config/defaults.yaml` in the skill directory; if you have multiple
hosts running the scheduler, keep that file synced (it's part of the
skill bundle).

## Future enhancements (not yet built)

- **Watchlist auto-batch**: `/tradingagents-analyze watchlist` that
  reads `GET /watchlist` from the framework API and runs each ticker.
- **Decision-log integration**: at the end of each scheduled run,
  append a one-line outcome reflection to
  `~/.tradingagents/memory/trading_memory.md` — the existing framework
  reads this for `past_context` in future runs.
- **Auto-backtest cycle**: when a new run completes, automatically
  backtest the parent run if 30+ days have elapsed since it. Closes the
  loop on prediction accuracy.
