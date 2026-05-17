"""Trading restrictions — surface per-ticker blackout windows into agent prompts.

The user may have hard restrictions on certain tickers (employee restricted
lists at their employer, 10b5-1 trading-plan closure windows around earnings,
regulatory holds, etc). These are **hard constraints** — the trader and PM
must not recommend trades inside the window, regardless of fundamental
or technical signal strength.

Stored as rows in the ``trading_restrictions`` SQLite table. This module
reads them and produces a clear, prompt-ready string that the agent
can't easily miss or override.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated


def _safe_list_restrictions(ticker: str, active_on: str):
    """Local import + defensive — let this module load even if the storage
    layer isn't initialized."""
    try:
        from gui import storage
        return storage.list_restrictions(ticker=ticker, active_on=active_on)
    except Exception:
        return []


def _today_iso() -> str:
    return date.today().isoformat()


def get_trading_restrictions(
    ticker: Annotated[str, "ticker symbol the analysis is focused on"],
    as_of_date: Annotated[
        str,
        "YYYY-MM-DD date to check restrictions for; defaults to today",
    ] = "",
) -> str:
    """Return a markdown summary of any active trading restrictions for
    the given ticker, on the given date.

    If there are no active restrictions, returns a clear "none" message
    (still useful for the agent to know the slate is clear). If there are
    active restrictions, returns a hard-constraint block in language the
    agent should treat as override-proof.

    Examples of restrictions the user might add:
    - Pre-earnings 10b5-1 blackout (e.g. "AAPL 2026-04-15 → 2026-05-05")
    - Restricted-list employer holding (e.g. "LCID indefinite")
    - Regulatory hold during M&A activity
    """
    ticker_upper = ticker.strip().upper()
    as_of = (as_of_date or "").strip() or _today_iso()

    # Sanity-check the date format. Empty string -> today (handled above).
    try:
        datetime.fromisoformat(as_of)
    except ValueError:
        as_of = _today_iso()

    rows = _safe_list_restrictions(ticker=ticker_upper, active_on=as_of)
    if not rows:
        return (
            f"# Trading restrictions for {ticker_upper} (as of {as_of})\n\n"
            f"_No active trading restrictions. The user is free to trade this "
            f"ticker without compliance blackouts. Proceed with the normal "
            f"recommendation flow._"
        )

    parts = [
        f"# 🚫 ACTIVE TRADING RESTRICTIONS for {ticker_upper} (as of {as_of})\n",
        "**HARD CONSTRAINT — DO NOT recommend BUY / OVERWEIGHT / SELL / "
        "UNDERWEIGHT actions on this ticker while these restrictions are active. "
        "The only legitimate recommendations during a blackout window are HOLD "
        "(keep what you have, no new money) or a DEFERRED recommendation "
        "(\"after the blackout ends on YYYY-MM-DD\"). This overrides any bullish "
        "or bearish signal from the analysis.**\n",
        "Active restrictions:\n",
        "| Kind | Window | Reason |",
        "|---|---|---|",
    ]
    for r in rows:
        end = r.get("end_date") or "indefinite"
        kind = (r.get("kind") or "blackout").replace("_", " ")
        reason = (r.get("reason") or "").replace("|", "/")
        parts.append(f"| {kind} | {r['start_date']} → {end} | {reason or '—'} |")

    parts.append(
        "\nIf the analysis is bullish, your final recommendation should be: "
        "\"Hold during blackout; reconsider initiating / adding on YYYY-MM-DD "
        "when the window closes.\" If the analysis is bearish AND the user "
        "already holds the position, the same rule applies — do NOT recommend "
        "exiting during the blackout. Explain the restriction in plain English "
        "in the final brief so the user understands why the recommendation "
        "is muted."
    )
    return "\n".join(parts)


def has_active_restriction(ticker: str, as_of_date: str = "") -> bool:
    """Cheap boolean check — true if any restriction is active right now.

    Used by the trader / PM nodes to decide whether to inject the full
    restriction block into the prompt context (skip the cost when there's
    nothing to inject).
    """
    as_of = (as_of_date or "").strip() or _today_iso()
    return bool(_safe_list_restrictions(ticker=ticker.strip().upper(), active_on=as_of))
