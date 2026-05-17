"""Dashboard endpoints — portfolio-level cross-cutting views.

Lives outside the per-resource routers (runs, briefs, queue, etc.)
because it joins multiple sources to answer "what's the state of my
book and how stale is the analysis."

Endpoints
---------
GET /dashboard/freshness     — per-ticker last-run timestamp + days since
GET /dashboard/portfolio     — current positions + latest brief decision
                                + days-since-last-run, in one shot
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from gui import storage

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class FreshnessRow(BaseModel):
    ticker: str
    shares: float
    last_run_id: Optional[str] = None
    last_run_date: Optional[str] = None       # ISO date the analysis covered
    last_run_completed_at: Optional[str] = None  # actual run completion timestamp
    days_since: Optional[int] = None          # whole days since the last run
    last_decision: Optional[str] = None       # decision from the most recent run
    last_provider: Optional[str] = None
    runs_total: int = 0                       # how many runs we've done on this ticker total


def _days_between(iso_str: Optional[str]) -> Optional[int]:
    """Days between today (UTC) and an ISO timestamp/date string."""
    if not iso_str:
        return None
    try:
        # Accept full ISO timestamps or YYYY-MM-DD.
        if "T" in iso_str:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return int(delta.total_seconds() // 86400)
    except (ValueError, TypeError):
        return None


@router.get("/freshness", response_model=List[FreshnessRow])
def freshness(
    include_watchlist: bool = True,
    include_positions: bool = True,
) -> List[FreshnessRow]:
    """Per-ticker view of last-run age.

    Builds the ticker universe from current open positions + watchlist
    (configurable), then joins the latest done run per ticker. Tickers
    with no runs yet show ``last_run_*=None`` and ``days_since=None`` so
    the UI can highlight them as "never analyzed".
    """
    tickers: set[str] = set()
    pos_by_ticker: dict[str, float] = {}
    if include_positions:
        for p in storage.list_positions(include_closed=False):
            t = (p.get("ticker") or "").upper()
            if t:
                tickers.add(t)
                pos_by_ticker[t] = pos_by_ticker.get(t, 0.0) + float(p.get("shares") or 0)
    if include_watchlist:
        try:
            for w in storage.list_watchlist():
                t = (w.get("ticker") or "").upper()
                if t:
                    tickers.add(t)
        except Exception:
            pass

    rows: List[FreshnessRow] = []
    for ticker in sorted(tickers):
        all_runs = [
            r for r in storage.list_runs(ticker=ticker, limit=200)
            if (r.get("status") or "").lower() == "done"
        ]
        if all_runs:
            latest = all_runs[0]  # list_runs is ORDER BY started_at DESC
            completed = latest.get("completed_at") or latest.get("started_at")
            rows.append(FreshnessRow(
                ticker=ticker,
                shares=pos_by_ticker.get(ticker, 0.0),
                last_run_id=latest["run_id"],
                last_run_date=latest.get("trade_date"),
                last_run_completed_at=completed,
                days_since=_days_between(completed),
                last_decision=latest.get("decision"),
                last_provider=latest.get("provider"),
                runs_total=len(all_runs),
            ))
        else:
            rows.append(FreshnessRow(
                ticker=ticker,
                shares=pos_by_ticker.get(ticker, 0.0),
                runs_total=0,
            ))

    # Sort: positions first (by shares desc), then watchlist-only (by ticker).
    rows.sort(key=lambda r: (
        0 if r.shares > 0 else 1,
        -r.shares if r.shares > 0 else 0,
        r.ticker,
    ))
    return rows
