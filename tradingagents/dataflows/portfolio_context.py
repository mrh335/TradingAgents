"""Portfolio context — surface the user's actual current holdings into agent prompts.

Reads from ``gui.storage`` (the SQLite positions table populated by the
``/planner/sync`` endpoint from their financial planner instance) and
returns a markdown summary tailored for direct injection into trader /
PM agent prompts.

Goals:
- The trader sizes recommendations based on what the user already owns
  ("you already hold 4% NVDA — adding another tranche brings you to 6%
  which exceeds typical single-name limits").
- The PM cross-checks the recommendation against overall portfolio
  concentration (sector, country, factor exposure).
- The Fundamentals analyst can call this as a *tool* if it needs to
  reference holdings mid-analysis (rare, but available).

Failure modes degrade gracefully — if the storage layer is unreachable
or the positions table is empty, returns a clear "no positions tracked"
explanation rather than raising.
"""

from __future__ import annotations

from typing import Annotated, Optional


def _safe_list_positions(include_closed: bool = False):
    """Local import + defensive — let this module load even if the storage
    layer isn't initialized (e.g. running outside the API container)."""
    try:
        from gui import storage  # local import to avoid hard dep at module load
        return storage.list_positions(include_closed=include_closed)
    except Exception:
        return []


def get_portfolio_context(
    ticker: Annotated[str, "ticker symbol the analysis is focused on"],
    include_full_portfolio: Annotated[
        bool,
        "Include every other open position too, not just the one for `ticker`",
    ] = True,
) -> str:
    """Surface the user's current holdings — specifically their position in
    the analyzed ticker plus, optionally, their full open portfolio for
    concentration-risk context.

    Cost-basis is best-effort: positions imported from a financial planner
    may have estimated or stale cost-basis numbers. Treat per-position
    P&L as advisory.

    Returns a markdown block. Empty/zero-position cases return a clean
    explanation rather than raising.
    """
    ticker_upper = ticker.strip().upper()
    positions = _safe_list_positions(include_closed=False)

    if not positions:
        return (
            "# Portfolio context\n\n"
            "_No positions are currently tracked in the framework. The user has not "
            "synced holdings from their financial planner, or the planner sync returned "
            "no rows. The analysis should proceed in **generic** mode — assume the "
            "user is sizing fresh capital rather than rebalancing an existing book._"
        )

    # Slice 1: this ticker.
    here = [p for p in positions if p["ticker"] == ticker_upper]
    here_shares = sum(p["shares"] for p in here)
    here_basis_avg = (
        sum(p["shares"] * p["cost_basis_per_share"] for p in here) / here_shares
        if here_shares > 0 else None
    )

    parts = [f"# Portfolio context for {ticker_upper}\n"]

    if here:
        accounts = sorted({(p.get("account") or "—") for p in here})
        parts.append(
            f"**Existing position in {ticker_upper}**: "
            f"{here_shares:g} shares across {len(here)} lot(s) "
            f"in {len(accounts)} account(s) ({', '.join(accounts)}). "
            f"Weighted avg cost basis: "
            f"${here_basis_avg:,.2f}/share."
            if here_basis_avg is not None else
            f"**Existing position in {ticker_upper}**: "
            f"{here_shares:g} shares across {len(here)} lot(s). Cost basis unknown."
        )
        # Per-lot breakdown if multiple lots exist (relevant for tax-loss
        # harvesting + cost-basis matching).
        if len(here) > 1:
            parts.append("\n  Per-lot breakdown:")
            for p in sorted(here, key=lambda x: x.get("opened_at") or ""):
                opened = (p.get("opened_at") or "")[:10]
                acct = p.get("account") or "—"
                parts.append(
                    f"  - {p['shares']:g} sh @ ${p['cost_basis_per_share']:,.2f} "
                    f"({acct}, opened {opened})"
                )
    else:
        parts.append(
            f"**Existing position in {ticker_upper}**: NONE — the user does not "
            f"currently hold this ticker. Any Buy / Overweight recommendation is "
            f"a new-position initiation, not an add."
        )

    # Slice 2: full portfolio if requested.
    if include_full_portfolio:
        other_tickers: dict = {}
        for p in positions:
            t = p["ticker"]
            if t == ticker_upper:
                continue
            other_tickers.setdefault(t, {"shares": 0.0, "basis_total": 0.0})
            other_tickers[t]["shares"] += p["shares"]
            other_tickers[t]["basis_total"] += p["shares"] * p["cost_basis_per_share"]

        if other_tickers:
            parts.append(
                f"\n**Other open positions** ({len(other_tickers)} other tickers):"
            )
            # Sort by basis-weighted size descending so the biggest exposures
            # surface first.
            rows = sorted(
                other_tickers.items(),
                key=lambda kv: kv[1]["basis_total"],
                reverse=True,
            )
            parts.append("\n| Ticker | Shares | Basis (per share) | Position value at basis |")
            parts.append("|---|---|---|---|")
            for t, agg in rows[:30]:  # cap at 30 to keep prompt tight
                per_share = (
                    agg["basis_total"] / agg["shares"] if agg["shares"] else 0
                )
                parts.append(
                    f"| {t} | {agg['shares']:g} | ${per_share:,.2f} | "
                    f"${agg['basis_total']:,.0f} |"
                )
            if len(rows) > 30:
                parts.append(f"| _…and {len(rows) - 30} more_ |  |  |  |")

            total_basis = sum(a["basis_total"] for a in other_tickers.values()) + (
                here_shares * (here_basis_avg or 0) if here else 0
            )
            parts.append(
                f"\n**Total tracked book value at cost basis**: "
                f"${total_basis:,.0f}. Use this for sizing percentages "
                f"(e.g. \"a 5% position\" = ${total_basis * 0.05:,.0f})."
            )

    parts.append(
        "\n_**How to use**: Size recommendations against the user's existing "
        "exposure. If they already hold a meaningful position in this name, "
        "a Buy might mean 'add a small tranche' rather than 'initiate a fresh "
        "starter position'. Concentration matters — flag if any single-name "
        "weight exceeds ~10% of the book._"
    )
    return "\n".join(parts)
