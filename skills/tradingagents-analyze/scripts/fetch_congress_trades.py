"""fetch_congress_trades.py — pull recent congressional stock transactions for a ticker.

Uses the Capitol Trades unofficial API (https://bff.capitoltrades.com/trades).
No auth required. Filings are typically delayed ~30-45 days from the trade
date due to STOCK Act reporting lag — useful as "smart money" signal but
not a leading indicator.

Usage:
    python fetch_congress_trades.py <TICKER> [--lookback-days N] [--since-iso ISO]
                                              [--output <path>]

Output JSON:
    {
      "ticker": "NVDA",
      "fetched_at": "<UTC ISO>",
      "lookback_days": 90,
      "since_iso": "<optional>",
      "trades": [
        {
          "member": "...",
          "party": "D|R|I",
          "chamber": "House|Senate",
          "transaction_date": "YYYY-MM-DD",
          "filed_date": "YYYY-MM-DD",
          "type": "buy|sell|exchange",
          "amount_low": 1001,
          "amount_high": 15000,
          "filing_delay_days": 42
        }
      ],
      "total_buys": N,
      "total_sells": N,
      "fetch_warnings": [...]
    }

Failures degrade gracefully — if the API is unreachable, returns an empty
trades list with a warning. Never raises.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CAPITOL_TRADES_BFF = "https://bff.capitoltrades.com/trades"
USER_AGENT = "tradingagents-analyze/0.1 (Claude Code skill)"


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _fetch(ticker: str, lookback_days: int) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []

    # Capitol Trades BFF accepts ?txTicker=NVDA&pageSize=N
    params = {
        "txTicker": ticker.upper(),
        "pageSize": "100",
        "sortBy": "-txDate",
    }
    url = f"{CAPITOL_TRADES_BFF}?{urllib.parse.urlencode(params)}"
    _eprint(f"GET {url}")

    req = urllib.request.Request(
        url, method="GET",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            doc = json.loads(raw)
    except urllib.error.HTTPError as e:
        warnings.append(f"capitoltrades HTTP {e.code}: {e.reason}")
        return [], warnings
    except urllib.error.URLError as e:
        warnings.append(f"capitoltrades unreachable: {e.reason}")
        return [], warnings
    except (json.JSONDecodeError, ValueError) as e:
        warnings.append(f"capitoltrades returned non-JSON: {e}")
        return [], warnings

    rows = doc.get("data") or []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()

    trades: list[dict] = []
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
            # Normalize buy/sell labels
            if "purchase" in trade_type or "buy" in trade_type:
                normalized = "buy"
            elif "sale" in trade_type or "sell" in trade_type:
                normalized = "sell"
            elif "exchange" in trade_type:
                normalized = "exchange"
            else:
                normalized = trade_type or "unknown"

            trades.append({
                "member": politician.get("fullName") or politician.get("name"),
                "party": (politician.get("party") or "")[:1],
                "chamber": politician.get("chamber"),
                "transaction_date": tx_date.isoformat(),
                "filed_date": filed_date.isoformat() if filed_date else None,
                "type": normalized,
                "amount_low": row.get("valueLow"),
                "amount_high": row.get("valueHigh"),
                "filing_delay_days": delay,
            })
        except (ValueError, KeyError, TypeError) as e:
            warnings.append(f"skip malformed row: {e}")
            continue

    return trades, warnings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("ticker")
    p.add_argument("--lookback-days", type=int, default=90,
                   help="How many days of history to fetch (default 90).")
    p.add_argument("--since-iso", default=None,
                   help="Additionally filter trades to those on/after this ISO date.")
    p.add_argument("--output", "-o", default=None,
                   help="Output JSON path (default: temp file)")
    args = p.parse_args()

    trades, warnings = _fetch(args.ticker, args.lookback_days)

    # Optional secondary filter by --since-iso (more restrictive than lookback)
    since_dt = None
    if args.since_iso:
        try:
            since_dt = datetime.fromisoformat(args.since_iso.replace("Z", "+00:00")).date()
        except ValueError:
            warnings.append(f"invalid --since-iso: {args.since_iso!r}")
        if since_dt:
            trades = [
                t for t in trades
                if t["transaction_date"] >= since_dt.isoformat()
            ]

    n_buy = sum(1 for t in trades if t["type"] == "buy")
    n_sell = sum(1 for t in trades if t["type"] == "sell")

    out = {
        "ticker": args.ticker.upper(),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lookback_days": args.lookback_days,
        "since_iso": args.since_iso,
        "trades": trades,
        "total_buys": n_buy,
        "total_sells": n_sell,
        "fetch_warnings": warnings,
    }

    if args.output:
        out_path = Path(args.output)
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"congress_trades_{args.ticker}_", suffix=".json",
            delete=False, mode="w", encoding="utf-8",
        )
        out_path = Path(tmp.name)
        tmp.close()

    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    _eprint(f"OK: {len(trades)} trades ({n_buy} buys, {n_sell} sells); wrote {out_path}")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
