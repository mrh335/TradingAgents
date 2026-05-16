"""fetch_market_data.py — pull yfinance data for a ticker on a date.

Produces a JSON blob the four analyst phases consume. Output goes to a temp
file whose path is printed to stdout; humans-readable status goes to stderr.

Usage:
    python fetch_market_data.py <TICKER> <YYYY-MM-DD> [--output <path>]

Output JSON shape:
    {
      "ticker": "NVDA",
      "trade_date": "2026-05-15",
      "fetched_at": "<UTC ISO-8601>",
      "current_price": 198.42,
      "company_profile": { sector, industry, market_cap, ... },
      "fundamentals_summary": { pe, ps, pb, profit_margin, roe, debt_to_equity, ... },
      "income_statement": [ {year, revenue, gross_profit, operating_income, net_income}, ... ],
      "balance_sheet": [ {year, total_assets, total_liabilities, equity, cash, debt}, ... ],
      "cashflow": [ {year, operating, investing, financing}, ... ],
      "price_history": [ {date, open, high, low, close, volume}, ... 252 rows ],
      "recent_news": [ {title, publisher, published_at, summary, link}, ... ],
      "global_macro": { sp500, nasdaq, vix, ten_year_yield, dxy, sector_etfs: {...} }
    }

Missing fields are set to null with a note in `fetch_warnings`. The script
never aborts on a per-field failure; it aborts only if the ticker itself is
invalid (no price history at all).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _safe(callable_, *, default=None):
    try:
        return callable_()
    except Exception as e:
        _eprint(f"  warn: {e}")
        return default


def _jsonable(obj: Any) -> Any:
    """Convert pandas/numpy types to JSON-safe Python builtins."""
    import pandas as pd
    import numpy as np

    if obj is None:
        return None
    if isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        return obj if not math.isnan(obj) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        return f if not math.isnan(f) else None
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    return str(obj)


def fetch(ticker: str, trade_date: str, since_iso: str | None = None) -> dict:
    import yfinance as yf

    warnings: list[str] = []
    out: dict = {
        "ticker": ticker.upper(),
        "trade_date": trade_date,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fetch_warnings": warnings,
        # Signals update-mode to the analyst prompts. Filled in when
        # --since-iso is given.
        "is_update": bool(since_iso),
        "delta_window": (
            {"from": since_iso,
             "to": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if since_iso else None
        ),
    }
    since_dt: datetime | None = None
    if since_iso:
        try:
            since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            warnings.append(f"invalid --since-iso {since_iso!r}; ignoring")
            since_dt = None

    _eprint(f"yfinance: opening {ticker}")
    yt = yf.Ticker(ticker)

    # ── Price history (1y up to trade_date) ─────────────────────────────
    end = datetime.fromisoformat(trade_date) + timedelta(days=1)
    start = end - timedelta(days=400)  # ~252 trading days + slack
    _eprint(f"yfinance: history {start.date()} → {end.date()}")
    hist = _safe(lambda: yt.history(start=start.date().isoformat(),
                                    end=end.date().isoformat(),
                                    auto_adjust=False))
    if hist is None or len(hist) == 0:
        raise SystemExit(f"No price history for {ticker} — invalid ticker?")
    hist = hist.tail(252).reset_index()
    out["price_history"] = [
        {
            "date": row["Date"].isoformat() if hasattr(row["Date"], "isoformat") else str(row["Date"]),
            "open": _jsonable(row.get("Open")),
            "high": _jsonable(row.get("High")),
            "low": _jsonable(row.get("Low")),
            "close": _jsonable(row.get("Close")),
            "volume": _jsonable(row.get("Volume")),
        }
        for _, row in hist.iterrows()
    ]
    out["current_price"] = out["price_history"][-1]["close"] if out["price_history"] else None

    # ── Company profile ─────────────────────────────────────────────────
    info = _safe(lambda: yt.info, default={}) or {}
    out["company_profile"] = {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": _jsonable(info.get("marketCap")),
        "employees": _jsonable(info.get("fullTimeEmployees")),
        "country": info.get("country"),
        "website": info.get("website"),
        "long_business_summary": info.get("longBusinessSummary"),
    }

    out["fundamentals_summary"] = {
        "pe_trailing": _jsonable(info.get("trailingPE")),
        "pe_forward": _jsonable(info.get("forwardPE")),
        "ps": _jsonable(info.get("priceToSalesTrailing12Months")),
        "pb": _jsonable(info.get("priceToBook")),
        "profit_margin": _jsonable(info.get("profitMargins")),
        "operating_margin": _jsonable(info.get("operatingMargins")),
        "roe": _jsonable(info.get("returnOnEquity")),
        "roa": _jsonable(info.get("returnOnAssets")),
        "debt_to_equity": _jsonable(info.get("debtToEquity")),
        "current_ratio": _jsonable(info.get("currentRatio")),
        "revenue_growth": _jsonable(info.get("revenueGrowth")),
        "earnings_growth": _jsonable(info.get("earningsGrowth")),
        "dividend_yield": _jsonable(info.get("dividendYield")),
        "beta": _jsonable(info.get("beta")),
        "fifty_two_week_high": _jsonable(info.get("fiftyTwoWeekHigh")),
        "fifty_two_week_low": _jsonable(info.get("fiftyTwoWeekLow")),
    }

    # ── Financial statements (annual, last 2 fiscal years) ──────────────
    def df_to_yearrows(df, mapping):
        if df is None or df.empty:
            return []
        rows = []
        for col in df.columns[:3]:  # most recent 3 fiscal years
            row = {"year": str(col)[:10]}
            for out_key, df_key in mapping.items():
                row[out_key] = _jsonable(df[col].get(df_key)) if df_key in df.index else None
            rows.append(row)
        return rows

    out["income_statement"] = _safe(
        lambda: df_to_yearrows(yt.financials, {
            "revenue": "Total Revenue",
            "gross_profit": "Gross Profit",
            "operating_income": "Operating Income",
            "net_income": "Net Income",
            "ebitda": "EBITDA",
            "eps": "Basic EPS",
        }), default=[],
    )
    out["balance_sheet"] = _safe(
        lambda: df_to_yearrows(yt.balance_sheet, {
            "total_assets": "Total Assets",
            "total_liabilities": "Total Liabilities Net Minority Interest",
            "equity": "Stockholders Equity",
            "cash": "Cash And Cash Equivalents",
            "total_debt": "Total Debt",
        }), default=[],
    )
    out["cashflow"] = _safe(
        lambda: df_to_yearrows(yt.cashflow, {
            "operating": "Operating Cash Flow",
            "investing": "Investing Cash Flow",
            "financing": "Financing Cash Flow",
            "free_cash_flow": "Free Cash Flow",
        }), default=[],
    )

    # ── Recent news ─────────────────────────────────────────────────────
    # In update mode (since_dt set), filter to articles published in the
    # delta window so analysts focus on what's new.
    news = _safe(lambda: yt.news, default=[]) or []
    news_items = []
    for n in news:
        ts = n.get("providerPublishTime")
        published_dt = (
            datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        )
        if since_dt is not None and published_dt is not None:
            if published_dt < since_dt:
                continue
        news_items.append({
            "title": n.get("title"),
            "publisher": n.get("publisher"),
            "published_at": (published_dt.isoformat(timespec="seconds")
                             if published_dt else None),
            "summary": n.get("summary"),
            "link": n.get("link"),
        })
        if len(news_items) >= 20:
            break
    out["recent_news"] = news_items
    if since_dt is not None:
        out["recent_news_filter"] = {
            "since": since_iso,
            "kept": len(news_items),
            "total_available": len(news),
        }

    # ── Global macro snapshot ───────────────────────────────────────────
    macro_tickers = {
        "sp500": "^GSPC",
        "nasdaq": "^IXIC",
        "vix": "^VIX",
        "ten_year_yield": "^TNX",
        "dxy": "DX-Y.NYB",
    }
    sector_etfs = {
        "tech_xlk": "XLK",
        "financials_xlf": "XLF",
        "energy_xle": "XLE",
        "healthcare_xlv": "XLV",
        "consumer_disc_xly": "XLY",
        "consumer_staples_xlp": "XLP",
        "industrials_xli": "XLI",
        "materials_xlb": "XLB",
        "utilities_xlu": "XLU",
        "communication_xlc": "XLC",
        "realestate_xlre": "XLRE",
    }

    def _last_close(sym: str):
        h = _safe(lambda: yf.Ticker(sym).history(period="5d", auto_adjust=False))
        if h is None or h.empty:
            return None
        last = h.tail(1).iloc[0]
        return {
            "close": _jsonable(last.get("Close")),
            "as_of": last.name.isoformat() if hasattr(last.name, "isoformat") else str(last.name),
        }

    out["global_macro"] = {
        k: _last_close(v) for k, v in macro_tickers.items()
    }
    out["global_macro"]["sector_etfs"] = {
        k: _last_close(v) for k, v in sector_etfs.items()
    }

    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("ticker")
    p.add_argument("trade_date", help="YYYY-MM-DD")
    p.add_argument("--since-iso", default=None,
                   help="ISO-8601 UTC timestamp. If set, recent_news is "
                        "filtered to articles published after this time "
                        "(update-mode delta window). Price history is "
                        "still pulled in full — needed for indicators.")
    p.add_argument("--output", "-o", default=None,
                   help="Output JSON path. Defaults to a temp file.")
    args = p.parse_args()

    # Validate trade_date
    try:
        datetime.fromisoformat(args.trade_date)
    except ValueError:
        _eprint(f"ERROR: trade_date must be YYYY-MM-DD, got {args.trade_date!r}")
        return 2

    data = fetch(args.ticker, args.trade_date, since_iso=args.since_iso)

    if args.output:
        out_path = Path(args.output)
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"market_data_{args.ticker}_{args.trade_date}_",
            suffix=".json", delete=False, mode="w", encoding="utf-8",
        )
        out_path = Path(tmp.name)
        tmp.close()

    out_path.write_text(json.dumps(_jsonable(data), indent=2), encoding="utf-8")
    _eprint(f"OK: wrote {out_path}")
    print(out_path)  # machine-parseable
    return 0


if __name__ == "__main__":
    sys.exit(main())
