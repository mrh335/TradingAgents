"""Paper trading: positions that mimic the real `positions` table but never
touch the real brokerage book.

Endpoints (mirrors the structure of service.routers.portfolio):
    GET    /paper/positions[?include_closed=true]
    POST   /paper/positions                 — open a paper position
    GET    /paper/positions/{pid}
    POST   /paper/positions/{pid}/close     — close (mark exit price)
    DELETE /paper/positions/{pid}           — hard delete (use sparingly)
    GET    /paper/summary                   — mark-to-market across open positions
    GET    /paper/history                   — closed paper trades w/ realized P&L

Live prices come from the same broadcaster the real portfolio summary uses,
with a yfinance fallback so the summary works even when the price stream
hasn't subscribed yet (e.g. a paper-only ticker that isn't in the real book).
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gui import storage
from service.streaming import broadcaster

router = APIRouter(prefix="/paper", tags=["paper-trading"])


# ───────────────────────────────────────────────────────────────────────────
# Schemas
# ───────────────────────────────────────────────────────────────────────────

class PaperPosition(BaseModel):
    id: int
    ticker: str
    shares: float
    cost_basis_per_share: float
    opened_at: str
    closed_at: Optional[str] = None
    closing_price: Optional[float] = None
    notes: Optional[str] = None
    related_run_id: Optional[str] = None
    created_by: Optional[str] = None


class PaperOpenRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    shares: float = Field(gt=0)
    # If omitted, the server fetches the current price via yfinance and
    # uses that as the entry. Pass an explicit price to backdate a trade
    # to a specific level (e.g. matching an analysis's tranche-1 entry).
    entry_price: Optional[float] = Field(default=None, gt=0)
    opened_at: Optional[str] = None
    notes: Optional[str] = None
    related_run_id: Optional[str] = None
    created_by: Optional[str] = None


class PaperCloseRequest(BaseModel):
    # If omitted, server fetches current price via yfinance.
    exit_price: Optional[float] = Field(default=None, gt=0)
    closed_at: Optional[str] = None


# ───────────────────────────────────────────────────────────────────────────
# Price helper — broadcaster first, yfinance fallback
# ───────────────────────────────────────────────────────────────────────────

def _live_price(ticker: str) -> Optional[float]:
    """Return the current price for `ticker` or None if unavailable.

    Tries the streaming broadcaster's last_price first (no network), falls
    back to a one-shot yfinance fetch. Paper trading needs to work even for
    tickers the real book doesn't hold, so a fresh fetch is fine here.
    """
    st = broadcaster._state.get(ticker.upper())
    if st and st.last_price:
        return float(st.last_price)
    try:
        import yfinance as yf
        info = yf.Ticker(ticker.upper()).fast_info
        # fast_info.last_price is the post-trade close; fall back to
        # regular_market_price if last_price is None on a brand-new session.
        for attr in ("last_price", "regular_market_price", "previous_close"):
            v = getattr(info, attr, None)
            if v:
                return float(v)
    except Exception:
        return None
    return None


# ───────────────────────────────────────────────────────────────────────────
# CRUD
# ───────────────────────────────────────────────────────────────────────────

@router.get("/positions", response_model=List[PaperPosition])
def list_paper_positions(include_closed: bool = False) -> List[PaperPosition]:
    return [PaperPosition(**p)
            for p in storage.list_paper_positions(include_closed=include_closed)]


@router.post("/positions", response_model=PaperPosition)
async def open_paper_position(req: PaperOpenRequest) -> PaperPosition:
    entry = req.entry_price
    if entry is None:
        # _live_price() does a synchronous yfinance fetch on a cache miss;
        # offload it so it doesn't block the event loop (this handler must
        # stay async for the broadcaster.subscribe await below).
        entry = await asyncio.to_thread(_live_price, req.ticker)
        if entry is None:
            raise HTTPException(
                status_code=400,
                detail=f"could not fetch live price for {req.ticker!r}; "
                       "pass entry_price explicitly",
            )
    pid = storage.add_paper_position(
        ticker=req.ticker,
        shares=req.shares,
        cost_basis_per_share=entry,
        opened_at=req.opened_at,
        notes=req.notes,
        related_run_id=req.related_run_id,
        created_by=req.created_by or "api",
    )
    # Warm the price stream so subsequent summary calls show fresh value.
    try:
        await broadcaster.subscribe("price", req.ticker.upper())
    except Exception:
        pass
    row = storage.get_paper_position(pid)
    if not row:
        raise HTTPException(status_code=500, detail="paper position not retrievable")
    return PaperPosition(**row)


@router.get("/positions/{pid}", response_model=PaperPosition)
def get_one_paper_position(pid: int) -> PaperPosition:
    row = storage.get_paper_position(pid)
    if not row:
        raise HTTPException(status_code=404, detail="paper position not found")
    return PaperPosition(**row)


@router.post("/positions/{pid}/close", response_model=PaperPosition)
def close_one_paper_position(pid: int, req: PaperCloseRequest) -> PaperPosition:
    row = storage.get_paper_position(pid)
    if not row:
        raise HTTPException(status_code=404, detail="paper position not found")
    if row.get("closed_at"):
        raise HTTPException(status_code=409, detail="paper position already closed")
    exit_p = req.exit_price
    if exit_p is None:
        exit_p = _live_price(row["ticker"])
        if exit_p is None:
            raise HTTPException(
                status_code=400,
                detail=f"could not fetch live price for {row['ticker']!r}; "
                       "pass exit_price explicitly",
            )
    storage.close_paper_position(pid, closing_price=exit_p, closed_at=req.closed_at)
    return PaperPosition(**storage.get_paper_position(pid))


@router.delete("/positions/{pid}")
def delete_one_paper_position(pid: int) -> dict:
    if not storage.get_paper_position(pid):
        raise HTTPException(status_code=404, detail="paper position not found")
    storage.delete_paper_position(pid)
    return {"deleted": pid}


# ───────────────────────────────────────────────────────────────────────────
# Summary + history
# ───────────────────────────────────────────────────────────────────────────

@router.get("/summary")
def paper_summary() -> dict:
    """Mark-to-market across all open paper positions.

    Returns total cost, current value, unrealized P&L, per-position rows
    with their live price + unrealized — same shape as /portfolio/summary
    for UI parity.
    """
    open_rows = storage.list_paper_positions(include_closed=False)
    closed_rows = [p for p in storage.list_paper_positions(include_closed=True)
                   if p.get("closed_at")]

    rows = []
    total_cost = 0.0
    total_value = 0.0
    for p in open_rows:
        ticker = p["ticker"]
        cost = p["shares"] * p["cost_basis_per_share"]
        live = _live_price(ticker)
        value = (p["shares"] * live) if live else None
        unreal = (value - cost) if value is not None else None
        unreal_pct = (unreal / cost * 100) if (unreal is not None and cost) else None
        rows.append({
            **p,
            "cost": cost,
            "live_price": live,
            "value": value,
            "unrealized": unreal,
            "unrealized_pct": unreal_pct,
        })
        total_cost += cost
        if value is not None:
            total_value += value

    realized = 0.0
    for p in closed_rows:
        if p.get("closing_price") is None:
            continue
        cost = p["shares"] * p["cost_basis_per_share"]
        proceeds = p["shares"] * p["closing_price"]
        realized += (proceeds - cost)

    return {
        "open_positions": rows,
        "total_cost": total_cost,
        "total_value": total_value if total_value else None,
        "unrealized_pnl": (total_value - total_cost) if total_value else None,
        "unrealized_pnl_pct": (
            (total_value - total_cost) / total_cost * 100 if total_cost else None
        ),
        "realized_pnl": realized,
        "open_count": len(open_rows),
        "closed_count": len(closed_rows),
    }


@router.get("/history")
def paper_history(limit: int = 50) -> List[dict]:
    """Closed paper trades with realized P&L per trade. Most recent first."""
    closed = [p for p in storage.list_paper_positions(include_closed=True)
              if p.get("closed_at") and p.get("closing_price") is not None]
    out = []
    for p in closed[:limit]:
        cost = p["shares"] * p["cost_basis_per_share"]
        proceeds = p["shares"] * p["closing_price"]
        realized = proceeds - cost
        realized_pct = (realized / cost * 100) if cost else None
        out.append({
            **p,
            "cost": cost,
            "proceeds": proceeds,
            "realized_pnl": realized,
            "realized_pnl_pct": realized_pct,
        })
    return out
