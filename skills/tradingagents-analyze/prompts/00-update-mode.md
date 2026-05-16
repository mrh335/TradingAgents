# Update mode — directives when a parent run exists

This file is loaded by `SKILL.md` Phase 0.5 **only when there is a recent
prior run for the same ticker** (a "parent run"). It augments — does not
replace — every persona prompt in `prompts/01-*.md` through `14-*.md`.

If no parent run exists, ignore this file and run each phase normally.

## What you have when this file applies

`scripts/lookup_parent_run.py <TICKER>` was run and returned a parent
archive. The orchestrator placed it in working memory as:

- `parent_run.metadata` — run_id, ticker, trade_date, completed_at
- `parent_run.state` — same shape as your current state (the full prior
  analysis: market_report, sentiment_report, etc., investment_debate_state,
  final_trade_decision, …)
- `parent_run.brief` — the prior brief sidecar (if one was saved)
- `parent_run.delta_window` — `{from: <parent.completed_at>, to: <now>}` —
  the time range you should focus on for what's new.

## How each phase changes in update mode

### Data gathering (Phase 0)

`scripts/fetch_market_data.py` is invoked with
`--since-iso <parent.completed_at>` so the `recent_news` array only
contains articles published in the delta window. Price history and
indicators are still computed over the full 1-year window (cheap,
needed for SMA200 etc.), but **`market_data_block.is_update = true`**
and **`market_data_block.delta_window`** signal to the analysts that
they should anchor on the deltas.

### Analyst phases (1a–1d)

Each analyst's output is **a complete updated report**, but framed
around what's changed:

- Open with **"Update since {parent.trade_date}"**, summarising the most
  consequential changes in 2-3 sentences.
- Reference the prior report's key claims explicitly: "Previously we
  flagged X; this analysis confirms / revises / supersedes that view
  because…"
- The report still stands alone — a reader who hasn't seen the parent
  should be able to understand the current view. Don't write *only*
  deltas; integrate them.
- Length: 400-800 words (shorter than a fresh run, because much of the
  context carries forward).

### Bull / Bear debate (Phase 2)

Each debater **must** reference the prior debate's conclusion. If the
research manager's previous verdict was Buy, the bull's job is to defend
or strengthen it; the bear's job is to surface what's changed that could
flip it. If previous verdict was Sell, mirror that.

### Research Manager (Phase 3)

Make the comparison explicit:
- **Previous recommendation**: <prior rating>
- **Current recommendation**: <new rating>
- **Direction of change**: <Confirmed / Upgraded / Downgraded / Reversed>
- **Rationale for change** (or for staying put): 2-3 sentences.

Then the standard Rationale + Strategic Actions sections.

### Trader (Phase 4)

If the prior plan had specific entry / stop / target prices, decide
whether they're still valid and explicitly accept, adjust, or replace
them. Don't silently reset.

### Risk debate (Phase 5)

Anchor on what's *new* risk-wise — a quiet week of no new headlines is
itself a signal; an earnings miss between runs is a major delta.

### Portfolio Manager (Phase 6)

The PM's prompt already has a `**Rating**` field; in update mode also
include:

- **Change from previous**: <new rating> ← <prior rating>, "Confirmed" / "Upgraded" / etc.
- If the rating changed, the **Investment Thesis** must explicitly
  explain why this run reached a different conclusion than the prior.

### Brief extractor (Phase 7)

The brief's `tldr` should lead with the action AND signal change vs.
prior. Examples:
- *"Maintain the existing 4% NVDA position — thesis intact, MACD
  bullish cross confirms the original entry."*
- *"Trim NVDA from 4% to 2% — the Q3 revenue miss invalidates last
  week's growth thesis."*

The `triggers` list should be **fresh**, not copies of the prior
brief's. The market has moved; trigger levels should reflect today's
prices.

## Linkage in the archive

`scripts/build_archive.py` is invoked with
`--parent-run-id <parent.run_id>` which writes `metadata.parent_run_id`
into the archive envelope. The webapp can then surface a "chain of
analyses" view (Phase 6 / future UI work). Today it's just stored
metadata; existing read paths ignore it harmlessly.

## When to NOT use update mode

Force a fresh run (skip this file) if:
- The parent is older than the configured `update_lookback_days`
  (default 7).
- The user passes `--fresh` on the invocation.
- The parent's `status != 'done'` (interrupted or errored run).

In all those cases, the orchestrator skips loading the parent and runs
every phase as if starting from scratch.
