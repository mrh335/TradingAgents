"""Batch processing — submit a list of tickers, get one run per ticker.

    POST   /runs/batch         — create a batch + queue runs, kick off first
    GET    /runs/batch         — list recent batches
    GET    /runs/batch/{id}    — batch detail with per-run statuses
    POST   /runs/batch/{id}/cancel — cancel remaining queued runs
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gui import storage
from service.runner_pool import pool
from service.schemas import RunSummary

router = APIRouter(prefix="/runs/batch", tags=["batch"])


class BatchCreateRequest(BaseModel):
    name: Optional[str] = None
    tickers: List[str] = Field(min_length=1, max_length=200)
    trade_date: str
    llm_provider: str = "openai"
    deep_think_llm: str
    quick_think_llm: str
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1
    data_vendors: Dict[str, str] = Field(
        default_factory=lambda: {
            "core_stock_apis": "yfinance",
            "technical_indicators": "yfinance",
            "fundamental_data": "yfinance",
            "news_data": "yfinance",
        }
    )


class BatchSummary(BaseModel):
    id: str
    name: Optional[str] = None
    trade_date: str
    total: int
    provider: Optional[str] = None
    deep_model: Optional[str] = None
    quick_model: Optional[str] = None
    debate_rounds: Optional[int] = None
    risk_rounds: Optional[int] = None
    status: str
    started_at: str
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


class BatchDetail(BatchSummary):
    runs: List[RunSummary]
    counts: Dict[str, int]


def _normalise_tickers(raw: List[str]) -> List[str]:
    """Uppercase + dedupe (preserving order) + drop blanks."""
    seen = set()
    out: List[str] = []
    for t in raw:
        clean = (t or "").strip().upper()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


@router.post("", response_model=BatchDetail)
def create_batch(req: BatchCreateRequest) -> BatchDetail:
    tickers = _normalise_tickers(req.tickers)
    if not tickers:
        raise HTTPException(status_code=400, detail="no valid tickers in request")

    batch_id = storage.new_batch_id()
    storage.create_batch(
        batch_id=batch_id,
        name=req.name,
        trade_date=req.trade_date,
        total=len(tickers),
        provider=req.llm_provider,
        deep_model=req.deep_think_llm,
        quick_model=req.quick_think_llm,
        debate_rounds=req.max_debate_rounds,
        risk_rounds=req.max_risk_discuss_rounds,
    )

    base_job = {
        "trade_date": req.trade_date,
        "llm_provider": req.llm_provider,
        "deep_think_llm": req.deep_think_llm,
        "quick_think_llm": req.quick_think_llm,
        "max_debate_rounds": req.max_debate_rounds,
        "max_risk_discuss_rounds": req.max_risk_discuss_rounds,
        "data_vendors": req.data_vendors,
    }
    pool.start_batch(batch_id=batch_id, tickers=tickers, base_job=base_job)

    return _build_detail(batch_id)


@router.get("", response_model=List[BatchSummary])
def list_batches(limit: int = 50) -> List[BatchSummary]:
    return [BatchSummary(**b) for b in storage.list_batches(limit=limit)]


@router.get("/{batch_id}", response_model=BatchDetail)
def get_batch(batch_id: str) -> BatchDetail:
    if not storage.get_batch(batch_id):
        raise HTTPException(status_code=404, detail="batch not found")
    return _build_detail(batch_id)


@router.post("/{batch_id}/cancel", response_model=BatchDetail)
def cancel_batch(batch_id: str) -> BatchDetail:
    if not storage.get_batch(batch_id):
        raise HTTPException(status_code=404, detail="batch not found")
    pool.cancel_batch(batch_id)
    return _build_detail(batch_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_detail(batch_id: str) -> BatchDetail:
    batch = storage.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch not found")
    runs = storage.runs_in_batch(batch_id)
    counts: Dict[str, int] = {}
    for r in runs:
        s = r.get("status") or "unknown"
        counts[s] = counts.get(s, 0) + 1
    return BatchDetail(
        **batch,
        runs=[RunSummary(**r) for r in runs],
        counts=counts,
    )
