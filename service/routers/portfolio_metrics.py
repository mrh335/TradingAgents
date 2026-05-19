"""Portfolio metrics — per-position trend health + index comparison.

Surfaces three things for every open position, all derived from yfinance
daily price history:

1. **52-week range position** — where the stock sits in its 1-year H/L
   range, as a 0-100 percentile. Useful for sizing decisions: a 90+
   reading means "you're buying near the high"; sub-20 means "near the
   low" (which can be opportunity or trap depending on fundamentals).

2. **MA distance** — % above/below the 50-day and 200-day simple moving
   averages. Trend health at a glance:
   - +5% above 200d + 50d above 200d ("golden cross") = healthy uptrend
   - Price below 200d = downtrend; consider whether to defend
   - 50d below 200d ("death cross") = weakening

3. **Index comparison** — for each holding, compute what the position
   would be worth had you put the same $$ into SPY on your opened_at
   date instead. Lets you see per-position whether you're beating the
   index. Most-actionable single column on the portfolio page.

Endpoints
---------
GET /portfolio/metrics  — one row per open position with all 3 metrics
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from gui import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class PositionMetrics(BaseModel):
    ticker: str
    position_id: int
    shares: float
    cost_basis_per_share: float
    opened_at: str

    # Current state
    current_price: Optional[float] = None
    current_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None

    # 52-week range
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    range_position_pct: Optional[float] = None  # 0=at low, 100=at high

    # MA distances (signed %)
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    pct_vs_sma_50: Optional[float] = None
    pct_vs_sma_200: Optional[float] = None
    golden_cross: Optional[bool] = None  # 50 > 200 (uptrend)

    # Index comparison (vs SPY from opened_at to today)
    position_return_pct: Optional[float] = None
    spy_return_same_period_pct: Optional[float] = None
    alpha_vs_spy_pct: Optional[float] = None
    spy_equivalent_value: Optional[float] = None  # what $ this same $ would be in SPY


class PortfolioMetricsResponse(BaseModel):
    rows: List[PositionMetrics]
    summary: Dict[str, Any]


def _compute_metrics(p: Dict[str, Any], spy_history: Any) -> PositionMetrics:
    """For one position row, compute all three metric groups. Falls back
    gracefully when yfinance is unavailable or a series is empty."""
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        return PositionMetrics(
            ticker=p["ticker"], position_id=p["id"],
            shares=p["shares"], cost_basis_per_share=p["cost_basis_per_share"],
            opened_at=p["opened_at"],
        )

    ticker = p["ticker"].upper()
    shares = float(p["shares"] or 0)
    cost_per_share = float(p["cost_basis_per_share"] or 0)
    cost_basis = shares * cost_per_share

    try:
        opened = datetime.fromisoformat(p["opened_at"][:10]).date()
    except (TypeError, ValueError):
        opened = date.today() - timedelta(days=365)

    # Fetch enough history to compute 200-day SMA and 52-week range.
    # 300 calendar days gives us ~210 trading days, enough for SMA-200.
    start = min(opened, date.today() - timedelta(days=380))
    try:
        hist = yf.Ticker(ticker).history(
            start=start.isoformat(),
            end=(date.today() + timedelta(days=1)).isoformat(),
            auto_adjust=True,
        )
    except Exception as e:
        logger.warning(f"metrics fetch failed {ticker}: {e}")
        hist = None

    if hist is None or hist.empty:
        return PositionMetrics(
            ticker=ticker, position_id=p["id"],
            shares=shares, cost_basis_per_share=cost_per_share,
            opened_at=p["opened_at"],
        )

    # Strip tz for clean indexing.
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    closes = hist["Close"].dropna()
    if closes.empty:
        return PositionMetrics(
            ticker=ticker, position_id=p["id"],
            shares=shares, cost_basis_per_share=cost_per_share,
            opened_at=p["opened_at"],
        )

    current_price = float(closes.iloc[-1])
    current_value = current_price * shares
    unrealized = current_value - cost_basis
    unrealized_pct = (unrealized / cost_basis * 100.0) if cost_basis > 0 else None

    # 52-week range — take last ~252 trading days
    one_year_ago = pd.Timestamp(date.today() - timedelta(days=365))
    closes_1y = closes[closes.index >= one_year_ago]
    if closes_1y.empty:
        closes_1y = closes
    high_52w = float(closes_1y.max())
    low_52w = float(closes_1y.min())
    range_span = high_52w - low_52w
    range_pos = (
        (current_price - low_52w) / range_span * 100.0
        if range_span > 0 else None
    )

    # Moving averages
    sma_50 = float(closes.tail(50).mean()) if len(closes) >= 50 else None
    sma_200 = float(closes.tail(200).mean()) if len(closes) >= 200 else None
    pct_vs_50 = (
        (current_price / sma_50 - 1) * 100.0 if sma_50 and sma_50 > 0 else None
    )
    pct_vs_200 = (
        (current_price / sma_200 - 1) * 100.0 if sma_200 and sma_200 > 0 else None
    )
    golden = (sma_50 > sma_200) if (sma_50 and sma_200) else None

    # Index comparison — what would the same $$ in SPY have done?
    position_return_pct = None
    spy_return_pct = None
    spy_equivalent_value = None
    alpha = None
    try:
        opened_ts = pd.Timestamp(opened)
        # First trading day on or after opened_at for both ticker + SPY.
        anchor_rows = closes.index[closes.index >= opened_ts]
        anchor_price = float(closes.loc[anchor_rows[0]]) if len(anchor_rows) > 0 else None
        if anchor_price and anchor_price > 0:
            position_return_pct = (current_price / anchor_price - 1) * 100.0

        if spy_history is not None and not spy_history.empty:
            spy = spy_history["Close"]
            if spy.index.tz is not None:
                spy.index = spy.index.tz_localize(None)
            spy_anchor_rows = spy.index[spy.index >= opened_ts]
            if len(spy_anchor_rows) > 0:
                spy_anchor = float(spy.loc[spy_anchor_rows[0]])
                spy_now = float(spy.iloc[-1])
                if spy_anchor > 0:
                    spy_return_pct = (spy_now / spy_anchor - 1) * 100.0
                    # What would your $cost_basis be worth had you put it
                    # in SPY on opened_at?
                    spy_shares_equiv = cost_basis / spy_anchor
                    spy_equivalent_value = spy_shares_equiv * spy_now
        if position_return_pct is not None and spy_return_pct is not None:
            alpha = position_return_pct - spy_return_pct
    except Exception as e:
        logger.warning(f"metrics SPY compare failed {ticker}: {e}")

    return PositionMetrics(
        ticker=ticker, position_id=p["id"],
        shares=shares, cost_basis_per_share=cost_per_share,
        opened_at=p["opened_at"],
        current_price=round(current_price, 2),
        current_value=round(current_value, 2),
        unrealized_pnl=round(unrealized, 2),
        unrealized_pnl_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
        high_52w=round(high_52w, 2),
        low_52w=round(low_52w, 2),
        range_position_pct=round(range_pos, 1) if range_pos is not None else None,
        sma_50=round(sma_50, 2) if sma_50 else None,
        sma_200=round(sma_200, 2) if sma_200 else None,
        pct_vs_sma_50=round(pct_vs_50, 2) if pct_vs_50 is not None else None,
        pct_vs_sma_200=round(pct_vs_200, 2) if pct_vs_200 is not None else None,
        golden_cross=golden,
        position_return_pct=round(position_return_pct, 2) if position_return_pct is not None else None,
        spy_return_same_period_pct=round(spy_return_pct, 2) if spy_return_pct is not None else None,
        alpha_vs_spy_pct=round(alpha, 2) if alpha is not None else None,
        spy_equivalent_value=round(spy_equivalent_value, 2) if spy_equivalent_value is not None else None,
    )


@router.get("/metrics", response_model=PortfolioMetricsResponse)
def portfolio_metrics() -> PortfolioMetricsResponse:
    """Trend health + index comparison per open position.

    Fetches yfinance daily price history once for SPY and once per ticker.
    Heavy when the portfolio is large (10 tickers ≈ 5-10s), but the
    output is small + cacheable on the client side.
    """
    positions = storage.list_positions(include_closed=False)
    if not positions:
        return PortfolioMetricsResponse(
            rows=[],
            summary={
                "position_count": 0,
                "total_cost_basis": 0.0,
                "total_current_value": 0.0,
                "total_spy_equivalent": 0.0,
                "blended_return_pct": None,
                "blended_spy_return_pct": None,
                "blended_alpha_pct": None,
                "winners_vs_spy": 0,
                "losers_vs_spy": 0,
            },
        )

    # Fetch SPY once for everyone (the index-comparison benchmark).
    spy_hist = None
    try:
        import yfinance as yf
        spy_hist = yf.Ticker("SPY").history(period="3y", auto_adjust=True)
    except Exception as e:
        logger.warning(f"SPY history fetch failed: {e}")

    rows: List[PositionMetrics] = []
    for p in positions:
        try:
            rows.append(_compute_metrics(p, spy_hist))
        except Exception as e:
            logger.warning(f"metrics failed for {p.get('ticker')}: {e}")

    # Aggregate summary
    total_cost = sum(r.shares * r.cost_basis_per_share for r in rows)
    total_value = sum(r.current_value or 0 for r in rows)
    total_spy_equiv = sum(r.spy_equivalent_value or 0 for r in rows)
    blended_ret = (
        (total_value - total_cost) / total_cost * 100.0 if total_cost > 0 else None
    )
    blended_spy = (
        (total_spy_equiv - total_cost) / total_cost * 100.0
        if (total_cost > 0 and total_spy_equiv > 0) else None
    )
    blended_alpha = (
        (blended_ret - blended_spy)
        if (blended_ret is not None and blended_spy is not None) else None
    )
    winners = sum(1 for r in rows if (r.alpha_vs_spy_pct or 0) > 0)
    losers = sum(1 for r in rows if (r.alpha_vs_spy_pct or 0) < 0)

    return PortfolioMetricsResponse(
        rows=rows,
        summary={
            "position_count": len(rows),
            "total_cost_basis": round(total_cost, 2),
            "total_current_value": round(total_value, 2),
            "total_spy_equivalent": round(total_spy_equiv, 2),
            "blended_return_pct": round(blended_ret, 2) if blended_ret is not None else None,
            "blended_spy_return_pct": round(blended_spy, 2) if blended_spy is not None else None,
            "blended_alpha_pct": round(blended_alpha, 2) if blended_alpha is not None else None,
            "winners_vs_spy": winners,
            "losers_vs_spy": losers,
        },
    )
