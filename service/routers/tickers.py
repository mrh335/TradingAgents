"""Per-ticker snapshot — technical metrics for ANY ticker, not just
held positions.

The existing /portfolio/metrics endpoint computes 52-wk range + MA
distance + SPY comparison, but only for tickers in the user's actual
portfolio. This endpoint is the same math for arbitrary tickers so the
new /ticker/[ticker] detail page (linked from /watchlist, /discover,
/recommendations, etc.) can show price + trend health for any name.

Endpoints
---------
GET /tickers/{ticker}/snapshot  — current price, 52-wk range, MA distance
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tickers", tags=["tickers"])


class TickerSnapshot(BaseModel):
    ticker: str
    available: bool
    current_price: Optional[float] = None
    change_pct_today: Optional[float] = None  # vs previous close
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    range_position_pct: Optional[float] = None     # 0 = at low, 100 = at high
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    pct_vs_sma_50: Optional[float] = None
    pct_vs_sma_200: Optional[float] = None
    golden_cross: Optional[bool] = None            # 50d > 200d → uptrend
    error: Optional[str] = None


@router.get("/{ticker}/snapshot", response_model=TickerSnapshot)
def get_snapshot(ticker: str) -> TickerSnapshot:
    """Lightweight technical snapshot — no caching, single yfinance call."""
    t = (ticker or "").strip().upper()
    if not t:
        raise HTTPException(status_code=400, detail="ticker required")

    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        return TickerSnapshot(ticker=t, available=False, error="yfinance not installed")

    try:
        # 1y of history is enough for 52-wk range + 200d SMA.
        hist = yf.Ticker(t).history(period="1y", auto_adjust=True)
    except Exception as e:
        return TickerSnapshot(ticker=t, available=False, error=f"yfinance fetch: {e}")

    if hist is None or hist.empty:
        return TickerSnapshot(ticker=t, available=False, error="no price history")

    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    closes = hist["Close"].dropna()
    if closes.empty:
        return TickerSnapshot(ticker=t, available=False, error="no close prices")

    current = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) > 1 else None
    change_pct = ((current / prev - 1) * 100.0) if (prev and prev > 0) else None

    # 52-wk range from the past year of trading days.
    high = float(closes.max())
    low = float(closes.min())
    span = high - low
    range_pos = ((current - low) / span * 100.0) if span > 0 else None

    sma_50 = float(closes.tail(50).mean()) if len(closes) >= 50 else None
    sma_200 = float(closes.tail(200).mean()) if len(closes) >= 200 else None
    pct_vs_50 = ((current / sma_50 - 1) * 100.0) if sma_50 and sma_50 > 0 else None
    pct_vs_200 = ((current / sma_200 - 1) * 100.0) if sma_200 and sma_200 > 0 else None
    golden = (sma_50 > sma_200) if (sma_50 and sma_200) else None

    return TickerSnapshot(
        ticker=t,
        available=True,
        current_price=round(current, 2),
        change_pct_today=round(change_pct, 2) if change_pct is not None else None,
        high_52w=round(high, 2),
        low_52w=round(low, 2),
        range_position_pct=round(range_pos, 1) if range_pos is not None else None,
        sma_50=round(sma_50, 2) if sma_50 else None,
        sma_200=round(sma_200, 2) if sma_200 else None,
        pct_vs_sma_50=round(pct_vs_50, 2) if pct_vs_50 is not None else None,
        pct_vs_sma_200=round(pct_vs_200, 2) if pct_vs_200 is not None else None,
        golden_cross=golden,
    )
