"""Macro dashboard + sector rotation — regime check at a glance.

Two endpoints:

GET /macro/dashboard
    Pulls a small set of cross-asset indicators that tell you what
    regime the market is in:
    - VIX (volatility)
    - 2/10 yield curve spread (recession signal)
    - DXY (USD strength)
    - WTI crude oil
    - HYG / IEF ratio (risk-on vs risk-off in credit)
    - Gold (GLD)
    Each returns latest level + % change vs 1d, 1w, 1m ago.

GET /macro/sector-rotation
    Returns % returns over 1m / 3m / 6m / YTD for the 11 S&P sector
    SPDRs. Sorted by 3-month return so the leadership rotation is
    obvious. A heatmap on the frontend renders this nicely.

Both endpoints fetch yfinance synchronously per ticker (~500ms each).
Total request time is acceptable (~5-8s) but should be cached on the
client side — TanStack Query refetchInterval of 5-10 min is fine for
macro data that changes slowly.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/macro", tags=["macro"])


# Macro tickers + a human label + the "what does this tell me" hint.
# Stored at module scope so the dashboard endpoint can iterate them
# uniformly. Each entry: (yfinance ticker, label, regime_hint).
MACRO_SERIES = [
    ("^VIX", "VIX", "Implied vol — under 15 = calm, over 25 = stressed"),
    ("^TNX", "10y Treasury", "10-year Treasury yield. Rising = bond selloff/inflation"),
    ("^IRX", "13w T-Bill", "Short-end yield. Falling = expected Fed cuts"),
    ("DX-Y.NYB", "USD index", "DXY — rising hurts US multinationals + EM"),
    ("CL=F", "WTI crude", "Oil $ per barrel. Spikes = inflation pressure"),
    ("GC=F", "Gold", "$ per oz. Rising = inflation hedging / risk-off"),
    ("HYG", "HYG (high yield)", "High-yield bond ETF. Down = credit stress"),
    ("IEF", "IEF (7-10y Treasuries)", "Risk-free reference for the HYG/IEF ratio"),
]

# S&P sector SPDRs + the ticker each represents.
SECTOR_ETFS = [
    ("XLK", "Technology"),
    ("XLV", "Health Care"),
    ("XLF", "Financials"),
    ("XLY", "Cons. Discretionary"),
    ("XLP", "Cons. Staples"),
    ("XLE", "Energy"),
    ("XLI", "Industrials"),
    ("XLB", "Materials"),
    ("XLU", "Utilities"),
    ("XLRE", "Real Estate"),
    ("XLC", "Communication Services"),
]


class MacroPoint(BaseModel):
    ticker: str
    label: str
    hint: str
    last: Optional[float] = None
    pct_1d: Optional[float] = None
    pct_1w: Optional[float] = None
    pct_1m: Optional[float] = None
    last_updated: Optional[str] = None


class MacroDashboardResponse(BaseModel):
    series: List[MacroPoint]
    derived: Dict[str, Any]   # Composite signals computed from series
    as_of: str


def _hist_return_pct(closes, days_ago: int) -> Optional[float]:
    """Return percent change from N trading days ago to today (latest)."""
    import pandas as pd  # local import — only if yfinance is present
    if closes is None or closes.empty or len(closes) < days_ago + 1:
        return None
    try:
        latest = float(closes.iloc[-1])
        past = float(closes.iloc[-1 - days_ago])
        if past == 0:
            return None
        return (latest / past - 1) * 100.0
    except (IndexError, ValueError):
        return None


def _fetch_closes(ticker: str, period: str = "3mo"):
    """yfinance close-price series for a ticker. Returns None on failure."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=period, auto_adjust=False)
        if hist is None or hist.empty:
            return None
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)
        return hist["Close"].dropna()
    except Exception as e:
        logger.warning(f"macro fetch {ticker}: {e}")
        return None


@router.get("/dashboard", response_model=MacroDashboardResponse)
def macro_dashboard() -> MacroDashboardResponse:
    """Cross-asset regime snapshot. ~5-8s on a cold cache."""
    points: List[MacroPoint] = []
    last_closes: Dict[str, float] = {}

    for ticker, label, hint in MACRO_SERIES:
        closes = _fetch_closes(ticker, period="3mo")
        if closes is None or closes.empty:
            points.append(MacroPoint(ticker=ticker, label=label, hint=hint))
            continue
        last = float(closes.iloc[-1])
        last_closes[ticker] = last
        points.append(MacroPoint(
            ticker=ticker, label=label, hint=hint,
            last=round(last, 4),
            pct_1d=round(p, 2) if (p := _hist_return_pct(closes, 1)) is not None else None,
            pct_1w=round(p, 2) if (p := _hist_return_pct(closes, 5)) is not None else None,
            pct_1m=round(p, 2) if (p := _hist_return_pct(closes, 21)) is not None else None,
            last_updated=str(closes.index[-1].date()),
        ))

    # Composite signals
    derived: Dict[str, Any] = {}

    # 2/10 spread: use ^TNX (10y) minus a proxy for 2y. yfinance doesn't
    # have a clean 2y symbol on a similar feed, so we use ^FVX (5y) as a
    # rough belly proxy and label clearly. For a true 2/10, the user
    # would need a Treasury data feed.
    tnx = last_closes.get("^TNX")
    irx = last_closes.get("^IRX")
    if tnx is not None and irx is not None:
        # ^IRX is 13-week (3mo) yield × 10; ^TNX is 10y × 10. Both are
        # decimal-shifted, so subtraction works.
        derived["10y_minus_3mo_spread_pct"] = round(tnx - irx, 3)
        derived["10y_minus_3mo_inverted"] = (tnx - irx) < 0

    # HYG / IEF ratio — credit risk-on signal
    hyg = last_closes.get("HYG")
    ief = last_closes.get("IEF")
    if hyg and ief:
        derived["hyg_ief_ratio"] = round(hyg / ief, 4)

    # Overall regime tag (heuristic)
    vix = last_closes.get("^VIX")
    if vix is not None:
        if vix > 25:
            derived["regime"] = "stressed"
        elif vix > 18:
            derived["regime"] = "cautious"
        elif vix > 12:
            derived["regime"] = "calm"
        else:
            derived["regime"] = "complacent"
        derived["vix_level"] = round(vix, 2)

    return MacroDashboardResponse(
        series=points, derived=derived,
        as_of=date.today().isoformat(),
    )


class SectorRow(BaseModel):
    ticker: str
    sector: str
    last: Optional[float] = None
    pct_1m: Optional[float] = None
    pct_3m: Optional[float] = None
    pct_6m: Optional[float] = None
    pct_ytd: Optional[float] = None


class SectorRotationResponse(BaseModel):
    rows: List[SectorRow]
    leadership: Dict[str, Any]
    as_of: str


@router.get("/sector-rotation", response_model=SectorRotationResponse)
def sector_rotation() -> SectorRotationResponse:
    """Per-sector ETF returns over 1m / 3m / 6m / YTD.

    Sorted by 3-month return descending — the rotation story is
    typically clearest at the 3m horizon. Frontend renders as a
    heatmap by default but the data is also usable as a sortable
    table.
    """
    import pandas as pd
    from datetime import datetime
    out: List[SectorRow] = []
    today_ts = pd.Timestamp(date.today())
    yr_start = pd.Timestamp(date(date.today().year, 1, 1))

    for ticker, label in SECTOR_ETFS:
        closes = _fetch_closes(ticker, period="1y")
        if closes is None or closes.empty:
            out.append(SectorRow(ticker=ticker, sector=label))
            continue
        last = float(closes.iloc[-1])
        # YTD anchor: first close on or after Jan 1 this year
        ytd_anchor_rows = closes.index[closes.index >= yr_start]
        ytd_anchor = float(closes.loc[ytd_anchor_rows[0]]) if len(ytd_anchor_rows) > 0 else None
        ytd_pct = (last / ytd_anchor - 1) * 100.0 if ytd_anchor and ytd_anchor > 0 else None
        out.append(SectorRow(
            ticker=ticker, sector=label,
            last=round(last, 2),
            pct_1m=round(p, 2) if (p := _hist_return_pct(closes, 21)) is not None else None,
            pct_3m=round(p, 2) if (p := _hist_return_pct(closes, 63)) is not None else None,
            pct_6m=round(p, 2) if (p := _hist_return_pct(closes, 126)) is not None else None,
            pct_ytd=round(ytd_pct, 2) if ytd_pct is not None else None,
        ))

    # Sort by 3m descending so leadership is obvious
    out.sort(key=lambda r: -(r.pct_3m or -9999))

    leaders = [r.sector for r in out[:3] if r.pct_3m is not None]
    laggards = [r.sector for r in reversed(out[-3:]) if r.pct_3m is not None]
    leadership = {
        "top_3_3m": leaders,
        "bottom_3_3m": laggards,
        "spread_3m_pct": round(
            (out[0].pct_3m - out[-1].pct_3m) if (out and out[0].pct_3m is not None and out[-1].pct_3m is not None) else 0,
            2,
        ) if out else None,
    }

    return SectorRotationResponse(
        rows=out, leadership=leadership,
        as_of=date.today().isoformat(),
    )
