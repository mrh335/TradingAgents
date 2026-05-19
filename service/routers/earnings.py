"""Earnings — latest release card + revisions + AI-summary queue handoff.

Per ticker, surfaces:

1. **Latest reported quarter** — date, EPS actual vs estimate, revenue
   actual vs estimate, surprise %, last 4 quarters trend.

2. **Forward analyst estimates** — current EPS estimate for next-Q vs
   the estimate 7/30/60/90 days ago. The DELTA tells you whether
   analysts are revising up or down (the "revision breadth" signal).

3. **Recommendation mix** — current count of strong-buy/buy/hold/sell/
   strong-sell ratings.

4. **AI-generated summary** — sidecar-style plain-English digest of
   the press release. Populated by Claude Desktop via the run_queue
   (mode='earnings_summary'); the webapp drops a request, the skill
   fetches the press release + structured data, generates bullets +
   structured comparison, POSTs back.

Endpoints
---------
GET  /earnings/{ticker}             — combined card + revisions + recs
GET  /earnings/{ticker}/summary     — latest AI summary (404 if none)
POST /earnings/{ticker}/summary     — submit a summary (used by Claude Desktop)
POST /earnings/{ticker}/queue-summary — queue an AI summary generation
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gui import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/earnings", tags=["earnings"])


# ──────────────────────────────────────────────────────────────────────
# Response models
# ──────────────────────────────────────────────────────────────────────


class EarningsQuarter(BaseModel):
    report_date: str
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None
    eps_surprise_pct: Optional[float] = None
    revenue_actual: Optional[float] = None  # USD
    revenue_estimate: Optional[float] = None


class EstimateRevision(BaseModel):
    """Analyst EPS estimate state for one forecast horizon.

    yfinance gives us two pieces:
      - ``earnings_estimate`` row: current avg, low, high, analyst count,
        growth rate, year-ago EPS
      - ``eps_revisions`` row: COUNT of analysts revising up vs down
        over the last 7 and 30 days

    The revision-count data is the actual "revision breadth" signal that
    academic research identifies as a near-term direction predictor.
    """
    horizon: str  # 'current_quarter' | 'next_quarter' | 'current_year' | 'next_year'
    current_estimate: Optional[float] = None
    low_estimate: Optional[float] = None
    high_estimate: Optional[float] = None
    analyst_count: Optional[int] = None
    growth_pct: Optional[float] = None       # expected growth vs year-ago
    year_ago_eps: Optional[float] = None
    up_last_7d: Optional[int] = None
    down_last_7d: Optional[int] = None
    up_last_30d: Optional[int] = None
    down_last_30d: Optional[int] = None
    net_30d: Optional[int] = None             # up_30d - down_30d
    direction: Optional[str] = None           # 'up' | 'down' | 'flat'


class RecommendationMix(BaseModel):
    period: str  # '0m' (current) | '-1m' | '-2m' | '-3m'
    strong_buy: Optional[int] = None
    buy: Optional[int] = None
    hold: Optional[int] = None
    sell: Optional[int] = None
    strong_sell: Optional[int] = None


class EarningsSummaryCard(BaseModel):
    report_date: str
    bullets_md: Optional[str] = None
    structured_json: Optional[Dict[str, Any]] = None
    source: str
    status: str
    updated_at: str


class EarningsResponse(BaseModel):
    ticker: str
    next_earnings_date: Optional[str] = None
    days_until_next: Optional[int] = None
    latest_quarter: Optional[EarningsQuarter] = None
    history: List[EarningsQuarter] = []
    revisions: List[EstimateRevision] = []
    recommendations: List[RecommendationMix] = []
    summary: Optional[EarningsSummaryCard] = None
    summary_pending: bool = False  # True if a queue item is in flight
    note: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────
# yfinance fetch helpers
# ──────────────────────────────────────────────────────────────────────


def _safe_float(v: Any) -> Optional[float]:
    """Convert yfinance NaN / pd.NA / None / numbers into Optional[float]."""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN check (NaN != NaN)
            return None
        return f
    except (TypeError, ValueError):
        return None


def _fetch_earnings_history(ticker: str) -> tuple[List[EarningsQuarter], Optional[EarningsQuarter]]:
    """Return (history, latest) — last 4-8 quarters of reported EPS/revenue."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper())
    except ImportError:
        return [], None

    history: List[EarningsQuarter] = []
    latest: Optional[EarningsQuarter] = None

    # ticker.earnings_dates — DataFrame indexed by datetime with columns
    # EPS Estimate, Reported EPS, Surprise(%). Includes future scheduled
    # dates as rows with all-NaN values; we filter those out for "history".
    try:
        ed = t.earnings_dates
    except Exception as e:
        logger.warning(f"earnings_dates fetch failed {ticker}: {e}")
        ed = None

    if ed is not None and not ed.empty:
        # Sort descending by index (most recent first)
        ed_sorted = ed.sort_index(ascending=False)
        today = date.today()
        for idx, row in ed_sorted.iterrows():
            report_dt = idx.date() if hasattr(idx, "date") else None
            if report_dt is None:
                continue
            reported_eps = _safe_float(row.get("Reported EPS") or row.get("reportedEPS"))
            est_eps = _safe_float(row.get("EPS Estimate") or row.get("epsEstimate"))
            surprise = _safe_float(row.get("Surprise(%)") or row.get("surprisePct"))
            if reported_eps is None and report_dt > today:
                # Future scheduled date — skip from "history"
                continue
            q = EarningsQuarter(
                report_date=report_dt.isoformat(),
                eps_actual=reported_eps,
                eps_estimate=est_eps,
                eps_surprise_pct=surprise,
            )
            if latest is None and reported_eps is not None:
                latest = q
            history.append(q)
            if len(history) >= 8:
                break

    # Try to enrich revenue from quarterly_income_stmt for the latest 4
    # quarters when available. yfinance's structure has changed over
    # versions, so we defensively access via dict/dataframe.
    try:
        q_inc = t.quarterly_income_stmt
        if q_inc is not None and not q_inc.empty:
            # Columns are quarter-end timestamps; rows are line items.
            for col in q_inc.columns:
                try:
                    col_date = col.date() if hasattr(col, "date") else None
                    if not col_date:
                        continue
                    rev = _safe_float(q_inc.loc["Total Revenue", col]) if "Total Revenue" in q_inc.index else None
                    # Match this quarter into history by closest report_date
                    for h in history:
                        try:
                            h_dt = datetime.fromisoformat(h.report_date).date()
                            if abs((h_dt - col_date).days) <= 95:  # quarter window
                                if rev is not None and h.revenue_actual is None:
                                    h.revenue_actual = rev
                                break
                        except (ValueError, TypeError):
                            continue
                except Exception:
                    continue
    except Exception:
        pass

    return history, latest


def _fetch_revisions(ticker: str) -> List[EstimateRevision]:
    """Pull yfinance.Ticker.earnings_estimate + .eps_revisions.

    Two distinct DataFrames keyed by the same period index ('0q', '+1q',
    '0y', '+1y'):

    - **earnings_estimate** has the current consensus (avg, low, high)
      + analyst count + expected growth + year-ago EPS for context.

    - **eps_revisions** has the COUNT of analysts revising up vs down
      in the last 7 and 30 days. This is the actual revision-breadth
      signal — academic research identifies the net up-down count as
      a near-term direction predictor.

    We merge both into one EstimateRevision per horizon and compute a
    direction flag from the 30-day net count (with a 50% margin to
    avoid flapping on tie-ish counts).
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper())
    except ImportError:
        return []

    horizon_labels = {
        "0q": "current_quarter",
        "+1q": "next_quarter",
        "0y": "current_year",
        "+1y": "next_year",
    }

    # --- earnings_estimate ---
    ee = None
    try:
        ee = t.earnings_estimate
    except Exception as e:
        logger.warning(f"earnings_estimate fetch failed {ticker}: {e}")

    # --- eps_revisions ---
    er = None
    try:
        er = t.eps_revisions
    except Exception as e:
        logger.warning(f"eps_revisions fetch failed {ticker}: {e}")

    if (ee is None or ee.empty) and (er is None or er.empty):
        return []

    def _safe_int(v: Any) -> Optional[int]:
        try:
            if v is None:
                return None
            f = float(v)
            if f != f:  # NaN
                return None
            return int(f)
        except (TypeError, ValueError):
            return None

    out: List[EstimateRevision] = []
    for raw_idx, label in horizon_labels.items():
        ee_row = ee.loc[raw_idx] if (ee is not None and raw_idx in ee.index) else None
        er_row = er.loc[raw_idx] if (er is not None and raw_idx in er.index) else None

        if ee_row is None and er_row is None:
            continue

        cur = _safe_float(ee_row.get("avg")) if ee_row is not None else None
        low = _safe_float(ee_row.get("low")) if ee_row is not None else None
        high = _safe_float(ee_row.get("high")) if ee_row is not None else None
        analyst_count = _safe_int(ee_row.get("numberOfAnalysts")) if ee_row is not None else None
        growth = _safe_float(ee_row.get("growth")) if ee_row is not None else None
        # yfinance reports growth as a decimal (0.20 = 20%)
        growth_pct = growth * 100.0 if growth is not None else None
        year_ago = _safe_float(ee_row.get("yearAgoEps")) if ee_row is not None else None

        # eps_revisions columns are inconsistently-cased across versions:
        # ``upLast7days``, ``downLast7Days``, ``upLast30days``, ``downLast30days``.
        # Look up case-insensitively to be safe.
        def _col(row, *names) -> Optional[int]:
            if row is None:
                return None
            lower_map = {str(k).lower(): k for k in row.index}
            for n in names:
                hit = lower_map.get(n.lower())
                if hit is not None:
                    return _safe_int(row.get(hit))
            return None

        up7 = _col(er_row, "upLast7days", "upLast7Days")
        down7 = _col(er_row, "downLast7Days", "downLast7days")
        up30 = _col(er_row, "upLast30days", "upLast30Days")
        down30 = _col(er_row, "downLast30Days", "downLast30days")

        net30 = None
        direction = None
        if up30 is not None and down30 is not None:
            net30 = up30 - down30
            # 50% margin to avoid noise — needs a clear lead to flag a direction
            if up30 > down30 * 1.5 and up30 > 0:
                direction = "up"
            elif down30 > up30 * 1.5 and down30 > 0:
                direction = "down"
            else:
                direction = "flat"

        out.append(EstimateRevision(
            horizon=label,
            current_estimate=round(cur, 4) if cur is not None else None,
            low_estimate=round(low, 4) if low is not None else None,
            high_estimate=round(high, 4) if high is not None else None,
            analyst_count=analyst_count,
            growth_pct=round(growth_pct, 2) if growth_pct is not None else None,
            year_ago_eps=round(year_ago, 4) if year_ago is not None else None,
            up_last_7d=up7,
            down_last_7d=down7,
            up_last_30d=up30,
            down_last_30d=down30,
            net_30d=net30,
            direction=direction,
        ))
    return out


def _fetch_recommendations(ticker: str) -> List[RecommendationMix]:
    """yfinance.Ticker.recommendations gives a DataFrame indexed by
    period offsets (0m, -1m, -2m, -3m) with strong_buy/buy/hold/sell/
    strong_sell counts. Returns up to 4 most recent periods."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper())
    except ImportError:
        return []

    out: List[RecommendationMix] = []
    try:
        rec = t.recommendations
    except Exception as e:
        logger.warning(f"recommendations fetch failed {ticker}: {e}")
        return []

    if rec is None or rec.empty:
        return []

    # Period column patterns vary across versions: sometimes 'period',
    # sometimes the index is the period label, sometimes there's just
    # a row per ratings analyst (newer yfinance).
    if "period" in rec.columns:
        for _, row in rec.iterrows():
            period = str(row["period"])
            out.append(RecommendationMix(
                period=period,
                strong_buy=int(row.get("strongBuy") or row.get("strong_buy") or 0),
                buy=int(row.get("buy") or 0),
                hold=int(row.get("hold") or 0),
                sell=int(row.get("sell") or 0),
                strong_sell=int(row.get("strongSell") or row.get("strong_sell") or 0),
            ))
            if len(out) >= 4:
                break
    else:
        # Index-based shape — older API
        for idx in rec.index[:4]:
            row = rec.loc[idx]
            try:
                out.append(RecommendationMix(
                    period=str(idx),
                    strong_buy=int(row.get("strongBuy") or row.get("strong_buy") or 0),
                    buy=int(row.get("buy") or 0),
                    hold=int(row.get("hold") or 0),
                    sell=int(row.get("sell") or 0),
                    strong_sell=int(row.get("strongSell") or row.get("strong_sell") or 0),
                ))
            except Exception:
                continue
    return out


# ──────────────────────────────────────────────────────────────────────
# Main combined endpoint
# ──────────────────────────────────────────────────────────────────────


@router.get("/{ticker}", response_model=EarningsResponse)
def get_earnings(ticker: str) -> EarningsResponse:
    """One-shot earnings card: latest reported, history, forward
    estimate revisions, analyst rec mix, AI summary if available."""
    t = ticker.strip().upper()
    if not t:
        raise HTTPException(status_code=400, detail="ticker required")

    history, latest = _fetch_earnings_history(t)
    revisions = _fetch_revisions(t)
    recs = _fetch_recommendations(t)

    # Next earnings date
    try:
        ne = storage._next_earnings_date(t)
    except Exception:
        ne = None
    days_until = (ne - date.today()).days if ne else None

    # Latest summary (if any). Tries to match the latest reported
    # quarter first; falls back to the most recent summary on file.
    summary: Optional[EarningsSummaryCard] = None
    summary_pending = False
    summary_row = None
    if latest:
        summary_row = storage.get_earnings_summary(t, latest.report_date)
    if summary_row is None:
        summary_row = storage.latest_earnings_summary(t)
    if summary_row:
        struct = None
        if summary_row.get("structured_json"):
            try:
                struct = json.loads(summary_row["structured_json"])
            except (ValueError, TypeError):
                struct = None
        summary = EarningsSummaryCard(
            report_date=summary_row["report_date"],
            bullets_md=summary_row.get("bullets_md"),
            structured_json=struct,
            source=summary_row.get("source") or "claude-desktop",
            status=summary_row.get("status") or "complete",
            updated_at=summary_row["updated_at"],
        )
        summary_pending = (summary_row.get("status") == "pending")

    return EarningsResponse(
        ticker=t,
        next_earnings_date=ne.isoformat() if ne else None,
        days_until_next=days_until,
        latest_quarter=latest,
        history=history,
        revisions=revisions,
        recommendations=recs,
        summary=summary,
        summary_pending=summary_pending,
    )


# ──────────────────────────────────────────────────────────────────────
# Summary GET/POST + queue handoff
# ──────────────────────────────────────────────────────────────────────


@router.get("/{ticker}/summary", response_model=Optional[EarningsSummaryCard])
def get_summary(ticker: str, report_date: Optional[str] = None) -> Optional[EarningsSummaryCard]:
    """Latest summary on file, or for a specific report_date if provided."""
    t = ticker.strip().upper()
    if report_date:
        row = storage.get_earnings_summary(t, report_date)
    else:
        row = storage.latest_earnings_summary(t)
    if not row:
        raise HTTPException(status_code=404, detail="no summary on file")
    struct = None
    if row.get("structured_json"):
        try:
            struct = json.loads(row["structured_json"])
        except (ValueError, TypeError):
            struct = None
    return EarningsSummaryCard(
        report_date=row["report_date"],
        bullets_md=row.get("bullets_md"),
        structured_json=struct,
        source=row.get("source") or "claude-desktop",
        status=row.get("status") or "complete",
        updated_at=row["updated_at"],
    )


class SummarySubmission(BaseModel):
    report_date: str = Field(description="YYYY-MM-DD of the earnings call this summarizes")
    bullets_md: Optional[str] = None
    structured: Optional[Dict[str, Any]] = None
    source: str = Field(default="claude-desktop")


@router.post("/{ticker}/summary", response_model=EarningsSummaryCard)
def submit_summary(ticker: str, req: SummarySubmission) -> EarningsSummaryCard:
    """Submit a generated summary. Called by Claude Desktop after
    processing an earnings_summary queue item; can also be called
    directly for manual entry."""
    t = ticker.strip().upper()
    struct_json = json.dumps(req.structured) if req.structured else None
    row = storage.upsert_earnings_summary(
        ticker=t, report_date=req.report_date,
        bullets_md=req.bullets_md, structured_json=struct_json,
        source=req.source, status="complete",
    )
    return EarningsSummaryCard(
        report_date=row["report_date"],
        bullets_md=row.get("bullets_md"),
        structured_json=req.structured,
        source=row.get("source") or "claude-desktop",
        status=row.get("status") or "complete",
        updated_at=row["updated_at"],
    )


@router.post("/{ticker}/queue-summary")
def queue_summary(ticker: str) -> Dict[str, Any]:
    """Queue an AI summary generation for this ticker's most recent
    earnings. Inserts a placeholder 'pending' row + drops a
    run_queue item with mode='earnings_summary' that Claude Desktop
    will pick up."""
    t = ticker.strip().upper()

    # Resolve the most recent reported quarter so the queue item knows
    # which earnings to summarize.
    history, latest = _fetch_earnings_history(t)
    if not latest:
        raise HTTPException(
            status_code=400,
            detail="no reported earnings found for this ticker — nothing to summarize",
        )

    # Mark a 'pending' placeholder in earnings_summaries so the UI can
    # show "summary requested" without waiting for the queue worker.
    storage.upsert_earnings_summary(
        ticker=t, report_date=latest.report_date,
        bullets_md=None, structured_json=None,
        source="claude-desktop", status="pending",
    )

    # Drop the run_queue item. queue_request() generates its own id.
    queued = storage.queue_request(
        ticker=t,
        trade_date=latest.report_date,
        mode="earnings_summary",
        options={"report_date": latest.report_date, "ticker": t},
        requested_by="web-ui",
        priority=5,
    )
    return {
        "queue_id": queued["id"],
        "ticker": t,
        "report_date": latest.report_date,
        "status": "queued",
        "message": "Earnings summary queued. Claude Desktop will pick it up on its next run.",
    }
