"""Congressional stock trading disclosures (STOCK Act / Form PTR).

Pulls recent buy/sell transactions by U.S. House and Senate members for a
given ticker via Capitol Trades' unofficial BFF API (no auth required).
Filings are typically delayed 30–45 days from the actual trade date due to
the STOCK Act reporting lag, so this is a *lagging* "smart money" signal —
useful for spotting unusual concentrated activity but never a leading
indicator.

Mirror of the more thorough implementation at
``skills/tradingagents-analyze/scripts/fetch_congress_trades.py`` (which
emits JSON for skill consumption). This module emits a markdown/CSV
string for direct embedding into LLM agent prompts.

Returns a structured-but-human-readable string that:
- Tells the analyst whether there's been any meaningful congressional
  activity in the window
- Surfaces party + chamber distribution (buys vs sells aggregated)
- Includes the raw per-trade table for the LLM to interrogate

Failures degrade gracefully: returns a one-line explanation, never raises.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Annotated, List, Optional

CAPITOL_TRADES_BFF = "https://bff.capitoltrades.com/trades"
USER_AGENT = "tradingagents/0.3 (Mark's hedge-trader)"
DEFAULT_LOOKBACK_DAYS = 90


def _fetch_raw(ticker: str, lookback_days: int) -> tuple[List[dict], Optional[str]]:
    """Return (trades, warning). On any error, trades is [] and warning is set."""
    params = {
        "txTicker": ticker.upper(),
        "pageSize": "100",
        "sortBy": "-txDate",
    }
    url = f"{CAPITOL_TRADES_BFF}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, method="GET",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return [], f"capitoltrades HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return [], f"capitoltrades unreachable: {e.reason}"
    except (json.JSONDecodeError, ValueError) as e:
        return [], f"capitoltrades returned non-JSON: {e}"

    rows = doc.get("data") or []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()

    trades: List[dict] = []
    for row in rows:
        try:
            tx_date_str = row.get("txDate")
            if not tx_date_str:
                continue
            tx_date = datetime.fromisoformat(tx_date_str[:10]).date()
            if tx_date < cutoff:
                continue

            filed_date_str = row.get("filedDate") or row.get("filed")
            filed_date = (
                datetime.fromisoformat(filed_date_str[:10]).date()
                if filed_date_str else None
            )
            delay = (filed_date - tx_date).days if filed_date else None

            politician = row.get("politician") or {}
            trade_type = (row.get("txType") or "").lower()
            if "purchase" in trade_type or "buy" in trade_type:
                normalized = "buy"
            elif "sale" in trade_type or "sell" in trade_type:
                normalized = "sell"
            elif "exchange" in trade_type:
                normalized = "exchange"
            else:
                normalized = trade_type or "unknown"

            trades.append({
                "member": politician.get("fullName") or politician.get("name") or "?",
                "party": (politician.get("party") or "")[:1].upper() or "?",
                "chamber": politician.get("chamber") or "?",
                "transaction_date": tx_date.isoformat(),
                "filed_date": filed_date.isoformat() if filed_date else None,
                "type": normalized,
                "amount_low": row.get("valueLow"),
                "amount_high": row.get("valueHigh"),
                "filing_delay_days": delay,
            })
        except (ValueError, KeyError, TypeError):
            continue

    return trades, None


def _format_amount(low, high) -> str:
    """STOCK Act reports come as ranges. Pick a readable display string."""
    if low is None and high is None:
        return "—"
    if low is None:
        return f"≤ ${high:,}"
    if high is None:
        return f"≥ ${low:,}"
    if low == high:
        return f"${low:,}"
    return f"${low:,} – ${high:,}"


def get_congress_trades(
    ticker: Annotated[str, "ticker symbol of the company"],
    lookback_days: Annotated[int, "how many days of history to fetch"] = DEFAULT_LOOKBACK_DAYS,
) -> str:
    """Retrieve congressional stock trading disclosures for a ticker.

    Pulls from Capitol Trades (the unofficial aggregator of House + Senate
    STOCK Act filings). Returns a markdown-formatted report with party +
    chamber breakdown plus a per-trade table.

    Returns a human-readable string suitable for embedding directly into
    an LLM agent's tool-call result. On API failures, returns a one-line
    explanation rather than raising — the agent should treat absent data
    as "no signal" rather than "broken pipeline".
    """
    trades, warning = _fetch_raw(ticker, lookback_days)

    header = (
        f"# Congressional stock trades for {ticker.upper()}\n"
        f"# Window: last {lookback_days} days "
        f"(retrieved {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})\n"
        f"# Source: Capitol Trades (STOCK Act / Form PTR aggregator)\n\n"
    )

    if warning:
        return header + f"_No data — {warning}_\n"

    if not trades:
        return header + (
            f"_No congressional trades for {ticker.upper()} reported in the "
            f"last {lookback_days} days. (STOCK Act filings can lag the actual "
            f"trade by 30-45 days, so a quiet window doesn't mean no activity.)_\n"
        )

    # Aggregates first — what the analyst usually wants in one glance.
    n_buy = sum(1 for t in trades if t["type"] == "buy")
    n_sell = sum(1 for t in trades if t["type"] == "sell")
    n_exch = sum(1 for t in trades if t["type"] == "exchange")
    by_party = {}
    for t in trades:
        by_party.setdefault(t["party"], {"buy": 0, "sell": 0, "exchange": 0, "other": 0})
        bucket = t["type"] if t["type"] in ("buy", "sell", "exchange") else "other"
        by_party[t["party"]][bucket] += 1
    by_chamber = {}
    for t in trades:
        by_chamber.setdefault(t["chamber"], {"buy": 0, "sell": 0})
        if t["type"] in ("buy", "sell"):
            by_chamber[t["chamber"]][t["type"]] += 1

    sig_lines = [
        f"**Summary**: {len(trades)} filings in window — "
        f"{n_buy} buys, {n_sell} sells, {n_exch} exchanges. "
        f"Net {'BUY' if n_buy > n_sell else 'SELL' if n_sell > n_buy else 'BALANCED'} bias.",
        "",
        "**By party**:",
    ]
    for party in sorted(by_party):
        b = by_party[party]
        sig_lines.append(
            f"- {party or '?'}: {b['buy']} buy / {b['sell']} sell / "
            f"{b['exchange']} exchange / {b['other']} other"
        )
    sig_lines.append("")
    sig_lines.append("**By chamber**:")
    for chamber in sorted(by_chamber):
        b = by_chamber[chamber]
        sig_lines.append(f"- {chamber}: {b['buy']} buy / {b['sell']} sell")
    sig_lines.append("")

    # Per-trade table.
    table = [
        "| Date | Filed | Lag | Member | Party | Chamber | Type | Amount |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in trades:
        lag = f"{t['filing_delay_days']}d" if t["filing_delay_days"] is not None else "—"
        table.append(
            f"| {t['transaction_date']} | {t['filed_date'] or '—'} | {lag} | "
            f"{t['member']} | {t['party']} | {t['chamber']} | "
            f"{t['type']} | {_format_amount(t['amount_low'], t['amount_high'])} |"
        )

    footnote = (
        "\n\n_**Interpretation note**: STOCK Act filings are reported 30-45 days "
        "AFTER the trade. Treat these as a lagging confirmation signal, not a "
        "leading indicator. A heavy cluster of same-direction trades by members "
        "of relevant committees (Finance, Armed Services, Energy, etc.) is the "
        "strongest signal worth flagging in your analysis. Amounts are reported "
        "as ranges — the midpoint is a reasonable estimate._"
    )

    return header + "\n".join(sig_lines) + "\n" + "\n".join(table) + footnote
