"""fetch_earnings_events.py — earnings calendar + dividend dates + recent surprises.

Uses yfinance for company-specific events. Adds a small built-in FOMC
calendar for the next 12 months (manually maintained — Fed publishes the
schedule once a year; update `_FOMC_DATES` annually).

Usage:
    python fetch_earnings_events.py <TICKER> [--horizon-days N] [--output <path>]

Output JSON:
    {
      "ticker": "NVDA",
      "fetched_at": "<UTC ISO>",
      "horizon_days": 60,
      "next_earnings_date": "YYYY-MM-DD" | null,
      "earnings_history": [
        {"date": "...", "eps_estimate": ..., "eps_reported": ..., "surprise_pct": ...}
      ],
      "ex_dividend_date": "YYYY-MM-DD" | null,
      "upcoming_fomc": ["YYYY-MM-DD", ...],
      "fetch_warnings": [...]
    }

Earnings calendar coverage is good for US large-caps via yfinance; spottier
for small-caps and ADRs. Falls back to nulls + warnings rather than
fabricating.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Manually maintained — update at the start of each calendar year. Source:
# https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# Format: ISO date of the *second* day (decision day) of each meeting.
_FOMC_DATES = [
    "2026-01-29",
    "2026-03-19",
    "2026-04-30",
    "2026-06-18",
    "2026-07-30",
    "2026-09-17",
    "2026-10-29",
    "2026-12-10",
    "2027-01-28",
    "2027-03-18",
]


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def fetch(ticker: str, horizon_days: int) -> dict:
    import yfinance as yf

    warnings: list[str] = []
    out: dict = {
        "ticker": ticker.upper(),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "horizon_days": horizon_days,
        "fetch_warnings": warnings,
    }

    yt = yf.Ticker(ticker)

    # ── Earnings dates ─────────────────────────────────────────────────
    try:
        edates = yt.earnings_dates  # pandas df, indexed by date
    except Exception as e:
        warnings.append(f"earnings_dates failed: {e}")
        edates = None

    next_date = None
    history = []
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=horizon_days)
    if edates is not None and len(edates) > 0:
        for idx, row in edates.iterrows():
            try:
                dt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            est = row.get("EPS Estimate")
            reported = row.get("Reported EPS")
            surprise = row.get("Surprise(%)") if "Surprise(%)" in row else None
            history.append({
                "date": dt.date().isoformat(),
                "eps_estimate": float(est) if est is not None and est == est else None,
                "eps_reported": float(reported) if reported is not None and reported == reported else None,
                "surprise_pct": float(surprise) if surprise is not None and surprise == surprise else None,
            })
            if dt > now and dt <= horizon and next_date is None:
                next_date = dt.date().isoformat()

    out["next_earnings_date"] = next_date
    out["earnings_history"] = history[:8]  # last 8

    # ── Ex-dividend ────────────────────────────────────────────────────
    try:
        info = yt.info or {}
        ex_div = info.get("exDividendDate")
        if ex_div:
            try:
                # yfinance returns unix ts here
                out["ex_dividend_date"] = datetime.fromtimestamp(int(ex_div), tz=timezone.utc).date().isoformat()
            except (ValueError, TypeError):
                out["ex_dividend_date"] = str(ex_div)
        else:
            out["ex_dividend_date"] = None
    except Exception as e:
        warnings.append(f"ex_dividend_date failed: {e}")
        out["ex_dividend_date"] = None

    # ── Upcoming FOMC (next 60 days) ───────────────────────────────────
    out["upcoming_fomc"] = [
        d for d in _FOMC_DATES
        if now.date().isoformat() <= d <= horizon.date().isoformat()
    ]

    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("ticker")
    p.add_argument("--horizon-days", type=int, default=60,
                   help="How far ahead to look for events (default 60).")
    p.add_argument("--output", "-o", default=None)
    args = p.parse_args()

    data = fetch(args.ticker, args.horizon_days)

    if args.output:
        out_path = Path(args.output)
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"earnings_events_{args.ticker}_", suffix=".json",
            delete=False, mode="w", encoding="utf-8",
        )
        out_path = Path(tmp.name)
        tmp.close()

    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _eprint(f"OK: next earnings={data.get('next_earnings_date')}, "
            f"FOMC upcoming={len(data.get('upcoming_fomc', []))}; wrote {out_path}")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
