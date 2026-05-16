# Persona — Holdings-aware overlay

This file is loaded when the user invokes the skill with `--holdings` or
`--horizon long|short`. It augments — does not replace — the standard
Portfolio Manager (Phase 7) and Brief Extractor (Phase 8) outputs to
make recommendations specific to the user's actual position and
planning horizon.

Without this file, the PM and brief speak in generic terms ("Initiate a
4% position"). With it, they speak directly to the user's situation
("You already own 50 shares at $175 cost basis — here's what to do
with that").

## Inputs available

If `holdings_block` is present (from `scripts/fetch_holdings.py`):

```
holdings_block = {
  "horizon": "long" | "short" | "auto",
  "ticker_filter": "NVDA" | null,
  "positions": [
    {
      "ticker": "NVDA",
      "shares": 50,
      "cost_basis_per_share": 175.20,
      "current_price": 198.42,
      "unrealized_gain_pct": 13.25,
      "opened_at": "2025-09-01",
      "account": "taxable" | "ira" | ...
    }
  ],
  "summary": { "total_positions", "total_invested", "total_unrealized_pct" }
}
```

If no holdings are registered, the orchestrator may still pass
`horizon` alone — analysis stays generic but tone matches the time
preference.

## What "long" vs "short" means here

The horizon flag is the **user's planning horizon**, not the framework's
debate timeframe:

- **long** = the user is investing for years. They care about
  fundamentals, durable advantages, secular trends. Daily price wiggles
  don't matter much. Tax implications (long-term capital gains > 1 year
  hold) matter a lot.
- **short** = the user is trading for weeks-to-months. They care about
  momentum, near-term catalysts, technical levels. Tax implications
  (short-term gains taxed as ordinary income) matter; they're already
  paying the higher rate.
- **auto** = infer from the position. If the user has held NVDA for
  >12 months and has only ever added (never trimmed), assume long. If
  they've been actively rotating, assume short.

## What changes in the PM's final decision (Phase 7)

If there's a current position in the analysed ticker, the PM must
explicitly address it:

- **Holding profit (>20% unrealized gain)**: don't ignore the embedded
  gain. Address whether to take some off the table, let it run, or
  hedge it. Tax-aware on long-horizon: hold until LTCG eligibility if
  close.
- **Holding loss (<-15% unrealized)**: address whether to average down,
  hold and reassess, or take the loss. Tax-aware: harvesting may be
  optimal even if the thesis is intact.
- **No position**: standard fresh-entry plan as today.

If horizon is **long**: weight fundamental/secular arguments more
heavily; downweight short-term technical setups unless they materially
change the long thesis. Position sizing tilts toward larger, fewer
positions held longer.

If horizon is **short**: weight technical / sentiment / catalyst
arguments more heavily; the bull/bear debate's near-term levels matter
more than 200-SMA structural reads. Position sizing tilts toward
smaller, more numerous, faster-rotated positions.

## What changes in the Brief (Phase 8)

The brief's `position_size` and `entry_strategy` must speak to the
user's actual situation. Examples:

**Has 50 shares NVDA at $175 cost basis, current $198, long horizon:**
> *"Hold all 50 shares. Don't add at current levels (the technical setup
> is mid-cycle, no fat-pitch entry). Consider trimming 10-15 shares only
> if NVDA breaks $245 and the long-term thesis hasn't strengthened — at
> long-term capital gains rates, partial profit-taking there makes
> sense."*

**Has 50 shares NVDA at $175 cost basis, current $198, short horizon:**
> *"Hold for now. If MACD crosses bullish AND volume picks up, add 20
> shares (lift to 70). If price closes below $193 (50-SMA), trim by
> half (down to 25). Re-evaluate at the next earnings, two weeks out."*

**No position, long horizon:**
> *"Buy 40-50 shares at current levels as a starter, plan to add more on
> a 5-10% pullback. This is a long-term hold — set a calendar reminder
> for the 1-year mark for tax purposes."*

**No position, short horizon:**
> *"Skip for now — no clear momentum entry. Wait for a MACD bullish
> cross or a break of the recent $205 high before initiating. If
> entering, sized small (10-20 shares) with a tight stop at $193."*

## What the `triggers` and `key_risks` should reflect

- **Triggers** stay measurable, but are tuned: long horizon emphasizes
  earnings / structural events; short horizon emphasizes price levels
  and momentum signals.
- **Key risks** include user-specific ones when relevant:
  *"You have $X invested at Y cost basis. A 30% drawdown means losing
  $Z — that's the size of the bet."*

## Constraints

- **Never fabricate position data.** If `holdings_block` is null but
  the user invoked with `--horizon`, acknowledge that horizon-tuning
  applies but no specific holding is referenced.
- **Don't give tax advice in detail.** Mention LTCG / short-term
  treatment generally, but say "consult a tax professional" for
  anything specific.
- **Account types matter for trading rules**: IRAs can't have wash
  sales, can't margin, etc. If `account` is specified, respect it.

## When this file does NOT apply

- The user invoked with no `--holdings` and no `--horizon` flag —
  standard ticker-only analysis runs.
- This is a multi-ticker batch with mixed horizons — defer to the
  per-ticker `--horizon` if set, else fall back to standard analysis.
