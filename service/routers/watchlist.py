"""Watchlist CRUD + a /watchlist/quotes batch fetch endpoint."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gui import storage
from service.streaming import broadcaster

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistEntry(BaseModel):
    id: int
    ticker: str
    added_at: str
    notes: Optional[str] = None
    next_earnings_date: Optional[str] = None  # YYYY-MM-DD via yfinance cache
    days_until_earnings: Optional[int] = None


class WatchlistAddRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    notes: Optional[str] = None


def _enrich_with_earnings(entry: dict) -> dict:
    """Add next_earnings_date + days_until_earnings to a watchlist row,
    using the shared 15-min cache so we don't hit yfinance per request."""
    from datetime import date as _date
    try:
        ne = storage._next_earnings_date(entry["ticker"])
    except Exception:
        ne = None
    out = dict(entry)
    if ne is not None:
        out["next_earnings_date"] = ne.isoformat()
        out["days_until_earnings"] = (ne - _date.today()).days
    else:
        out["next_earnings_date"] = None
        out["days_until_earnings"] = None
    return out


@router.get("", response_model=List[WatchlistEntry])
def list_watchlist() -> List[WatchlistEntry]:
    return [WatchlistEntry(**_enrich_with_earnings(e))
            for e in storage.list_watchlist()]


@router.post("", response_model=WatchlistEntry)
async def add_to_watchlist(req: WatchlistAddRequest) -> WatchlistEntry:
    entry = storage.add_to_watchlist(req.ticker, req.notes)
    # Pre-warm: register the ticker with the broadcaster so the next poll
    # picks it up. The first browser subscription would do this anyway,
    # but doing it now means a snapshot is ready by the time the UI loads.
    await broadcaster.subscribe("price", entry["ticker"])
    return WatchlistEntry(**entry)


@router.delete("/{ticker}")
def remove_from_watchlist(ticker: str) -> dict:
    storage.remove_from_watchlist(ticker)
    return {"removed": ticker.upper()}


@router.get("/quotes")
def watchlist_quotes() -> dict:
    """Last-known quote snapshot for every ticker in the watchlist —
    cheap REST endpoint suitable for periodic UI refresh fallback when
    a client doesn't want to maintain live WebSockets per row."""
    out = {}
    for entry in storage.list_watchlist():
        ticker = entry["ticker"]
        st = broadcaster._state.get(ticker)
        if st and st.last_price is not None:
            out[ticker] = {
                "price": st.last_price,
                "change": st.last_change,
                "change_pct": st.last_change_pct,
                "polled_at": st.last_polled,
            }
        else:
            out[ticker] = None
    return out
