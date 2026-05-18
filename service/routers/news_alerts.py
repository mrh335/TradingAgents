"""News alerts — scored news items surfaced as alerts.

Read endpoints + status mutations + force-poll. The actual fetch + score
loop lives in ``service.news_alerts_poller`` (spawned at app startup).

Endpoints
---------
GET    /news-alerts                              — list (filter by ticker/status/impact)
GET    /news-alerts/unread-count                 — {ticker → count} for the dashboard badge
POST   /news-alerts/{id}/mark-read
POST   /news-alerts/{id}/dismiss
POST   /news-alerts/mark-all-read                — optionally by ticker
POST   /news-alerts/refresh                      — force a poller tick (admin/manual)
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from gui import storage
from service import news_alerts_poller

router = APIRouter(prefix="/news-alerts", tags=["news-alerts"])


class NewsAlert(BaseModel):
    id: int
    ticker: str
    headline: str
    url: Optional[str] = None
    published_at: Optional[str] = None
    source: Optional[str] = None
    impact: str
    impact_score: int
    keywords: Optional[str] = None
    status: str
    fetched_at: str


def _row(d: dict) -> NewsAlert:
    return NewsAlert(
        id=d["id"], ticker=d["ticker"], headline=d["headline"],
        url=d.get("url"), published_at=d.get("published_at"),
        source=d.get("source"), impact=d.get("impact") or "low",
        impact_score=int(d.get("impact_score") or 0),
        keywords=d.get("keywords"), status=d.get("status") or "unread",
        fetched_at=d["fetched_at"],
    )


@router.get("", response_model=List[NewsAlert])
def list_alerts(
    ticker: Optional[str] = None,
    status: Optional[str] = None,
    impact: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
) -> List[NewsAlert]:
    rows = storage.list_news_alerts(
        ticker=ticker, status=status, impact=impact, limit=limit,
    )
    return [_row(r) for r in rows]


@router.get("/unread-count")
def unread_count() -> Dict[str, int]:
    return storage.news_alerts_unread_count()


@router.post("/{alert_id}/mark-read")
def mark_read(alert_id: int) -> dict:
    if not storage.update_news_alert_status(alert_id, "read"):
        raise HTTPException(status_code=404, detail="alert not found")
    return {"status": "read", "id": alert_id}


@router.post("/{alert_id}/dismiss")
def dismiss(alert_id: int) -> dict:
    if not storage.update_news_alert_status(alert_id, "dismissed"):
        raise HTTPException(status_code=404, detail="alert not found")
    return {"status": "dismissed", "id": alert_id}


@router.post("/mark-all-read")
def mark_all_read(ticker: Optional[str] = None) -> dict:
    n = storage.mark_all_news_alerts_read(ticker=ticker)
    return {"marked_read": n, "ticker": ticker}


@router.post("/refresh")
async def refresh() -> dict:
    """Force one poller tick right now. Useful after adding a new ticker
    to the watchlist. Runs the yfinance fetch in a worker thread so the
    request handler doesn't block — large polls can take 30-60s."""
    import asyncio
    n = await asyncio.to_thread(news_alerts_poller._tick)
    return {"new_alerts": n}
