"""Run queue — async handoff between the webapp and an external worker.

This is the back-end half of the "queue from the web, process from Claude
Desktop" workflow. The webapp's Run / Batch pages can POST work here
instead of running synchronously against the local LLM provider. A poller
(typically the ``tradingagents-analyze`` skill running in Claude Code or
Claude Desktop) periodically claims queued items, runs the full pipeline
with its own pre-paid LLM access, and POSTs the result archive back via
``/runs/import`` — at which point the queue item is auto-completed.

The advantage over the synchronous run path:
- Token costs flow through the poller's flat subscription (not the
  webapp's pay-per-call API key).
- The poller has access to richer tools (MCP servers, web search,
  sandboxed Python) than the in-process framework can use.
- The webapp doesn't need any LLM credentials at all.
- Decouples "user wants analysis" from "LLM client runs it" — multiple
  workers can pull from the same queue.

Endpoints
---------
POST   /run-queue              — webapp / API client adds an item
GET    /run-queue              — list items (filter by status)
GET    /run-queue/pending      — convenience: status='pending', oldest first
POST   /run-queue/claim        — worker claims up to N pending items
POST   /run-queue/{id}/complete — worker reports success (optionally link result)
POST   /run-queue/{id}/fail    — worker reports failure with error message
POST   /run-queue/{id}/cancel  — user cancels via the UI
DELETE /run-queue/{id}         — purge a completed/cancelled item
POST   /run-queue/reclaim-stale — janitor: re-pending any claims older than 30 min
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gui import storage

router = APIRouter(prefix="/run-queue", tags=["run-queue"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

ALLOWED_MODES = {
    # Full multi-agent analysis (Phase 0 → Phase 10 of tradingagents-analyze)
    "analyze",
    # Brief regeneration only — reuses an existing run's analysis
    "brief",
    # Incremental refresh — analyze with analysis_mode=incremental forced
    "refresh",
    # News pulse — pull latest news + sentiment for a ticker, post a sidecar
    "news_fetch",
    # Deep dive — assemble a long-form research memo on a ticker (uses
    # multiple analyst tools without running the full debate)
    "deep_dive",
    # Earnings recap — summarise the most recent earnings call + reaction
    "earnings_recap",
    # Earnings summary — plain-English digest of the latest press release
    # written for an engineer, POSTed back to /earnings/{ticker}/summary
    "earnings_summary",
    # Portfolio Q&A — free-form question with a context snapshot, answer
    # POSTed back to /ask/{question_id}/answer
    "ask_portfolio",
    # Screener query — run a custom screen against the universe
    "screener_query",
    # Portfolio review — periodic across-book health check (synthesizes
    # current holdings + briefs + restrictions)
    "portfolio_review",
}


class QueueItem(BaseModel):
    id: str
    ticker: str
    trade_date: str
    mode: str
    options: Dict[str, Any] = Field(default_factory=dict)
    requested_by: Optional[str] = None
    priority: int = 0
    status: str
    claimed_by: Optional[str] = None
    claimed_at: Optional[str] = None
    completed_at: Optional[str] = None
    result_run_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str


def _row_to_item(row: Dict[str, Any]) -> QueueItem:
    options_raw = row.get("options_json") or "{}"
    try:
        options = json.loads(options_raw) if isinstance(options_raw, str) else {}
    except json.JSONDecodeError:
        options = {}
    return QueueItem(
        id=row["id"],
        ticker=row["ticker"],
        trade_date=row["trade_date"],
        mode=row["mode"],
        options=options,
        requested_by=row.get("requested_by"),
        priority=row.get("priority") or 0,
        status=row["status"],
        claimed_by=row.get("claimed_by"),
        claimed_at=row.get("claimed_at"),
        completed_at=row.get("completed_at"),
        result_run_id=row.get("result_run_id"),
        error_message=row.get("error_message"),
        created_at=row["created_at"],
    )


class QueueCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    trade_date: str = Field(description="YYYY-MM-DD")
    mode: str = Field(default="analyze", description="analyze | brief | refresh")
    options: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Mode-specific options. For mode='analyze': "
            "{provider, deep_model, quick_model, debate_rounds, risk_rounds, "
            "data_vendors, notes}. Passed verbatim to the worker."
        ),
    )
    requested_by: Optional[str] = Field(
        default="web-ui",
        max_length=64,
        description="Label for who/what queued this — for audit + filtering.",
    )
    priority: int = Field(default=0, ge=-10, le=10)


class QueueClaimRequest(BaseModel):
    claimed_by: str = Field(
        min_length=1, max_length=128,
        description="Worker identifier (e.g. 'claude-desktop:markhoehne@home').",
    )
    max_items: int = Field(default=1, ge=1, le=20)
    mode: Optional[str] = Field(
        default=None,
        description="Optional filter — only claim items with this mode.",
    )


class QueueCompleteRequest(BaseModel):
    result_run_id: Optional[str] = Field(
        default=None,
        description=(
            "If the worker imported the result via /runs/import, the "
            "resulting run_id. Lets the UI link queue item → final run."
        ),
    )


class QueueFailRequest(BaseModel):
    error: str = Field(min_length=1, max_length=2000)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=QueueItem)
def create_queue_item(req: QueueCreateRequest) -> QueueItem:
    """Add a new work item to the queue. Called by the webapp's Run / Batch
    pages when the user picks "Queue for Claude Desktop" instead of "Run now".
    """
    if req.mode not in ALLOWED_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {req.mode!r}; allowed: {sorted(ALLOWED_MODES)}",
        )
    row = storage.queue_request(
        ticker=req.ticker,
        trade_date=req.trade_date,
        mode=req.mode,
        options=req.options,
        requested_by=req.requested_by,
        priority=req.priority,
    )
    return _row_to_item(row)


@router.get("", response_model=List[QueueItem])
def list_queue_items(
    status: Optional[str] = None,
    limit: int = 200,
) -> List[QueueItem]:
    """List queue items. ``status`` filter accepts pending / claimed / done
    / error / cancelled. Default is all statuses, newest first."""
    rows = storage.list_queue(status=status, limit=limit)
    return [_row_to_item(r) for r in rows]


@router.get("/pending", response_model=List[QueueItem])
def list_pending() -> List[QueueItem]:
    """Convenience endpoint — every unclaimed item, oldest first by priority.
    The poller GETs this to know if there's work to do at all (cheaper than
    POST /claim if it just wants to peek)."""
    rows = storage.list_queue(status="pending", limit=200)
    return [_row_to_item(r) for r in rows]


@router.post("/claim", response_model=List[QueueItem])
def claim_items(req: QueueClaimRequest) -> List[QueueItem]:
    """Worker calls this to atomically take ownership of up to ``max_items``.

    Once claimed, items have status='claimed' and won't be handed to other
    workers. The worker should then run each one and POST back to /complete
    or /fail. If the worker dies mid-job, ``reclaim-stale`` will revert the
    claim after 30 minutes.
    """
    rows = storage.claim_queue_items(
        claimed_by=req.claimed_by,
        max_items=req.max_items,
        mode=req.mode,
    )
    return [_row_to_item(r) for r in rows]


@router.post("/{queue_id}/complete", response_model=QueueItem)
def complete_item(queue_id: str, req: QueueCompleteRequest) -> QueueItem:
    """Worker reports a queue item as successfully finished.

    Note: the result archive should already have been POSTed to /runs/import
    before calling this — pass the resulting ``run_id`` here so the UI can
    link the queue row to the final run record.
    """
    row = storage.get_queue_item(queue_id)
    if not row:
        raise HTTPException(status_code=404, detail="queue item not found")
    storage.complete_queue_item(queue_id, result_run_id=req.result_run_id)
    # Belt-and-braces: if this queue item carried a comparison_id in its
    # options AND a result_run_id was supplied, attach the comparison_id
    # to the run. /runs/import does this too via metadata, but doing it
    # here covers the case where a worker didn't put the id in metadata.
    if req.result_run_id:
        try:
            opts = json.loads(row.get("options_json") or "{}")
            cmp_id = opts.get("comparison_id")
            if cmp_id:
                storage.attach_comparison_id(req.result_run_id, str(cmp_id))
        except Exception:
            pass
    updated = storage.get_queue_item(queue_id) or row
    return _row_to_item(updated)


@router.post("/{queue_id}/fail", response_model=QueueItem)
def fail_item(queue_id: str, req: QueueFailRequest) -> QueueItem:
    """Worker reports a queue item as failed. The error message surfaces in
    the UI so the user can decide whether to re-queue or investigate."""
    row = storage.get_queue_item(queue_id)
    if not row:
        raise HTTPException(status_code=404, detail="queue item not found")
    storage.fail_queue_item(queue_id, error=req.error)
    updated = storage.get_queue_item(queue_id) or row
    return _row_to_item(updated)


@router.post("/{queue_id}/cancel", response_model=QueueItem)
def cancel_item(queue_id: str) -> QueueItem:
    """User cancels a pending or claimed item via the UI."""
    row = storage.get_queue_item(queue_id)
    if not row:
        raise HTTPException(status_code=404, detail="queue item not found")
    ok = storage.cancel_queue_item(queue_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"cannot cancel item in status {row['status']!r}",
        )
    updated = storage.get_queue_item(queue_id) or row
    return _row_to_item(updated)


@router.delete("/{queue_id}")
def delete_item(queue_id: str) -> dict:
    """Purge a queue row. Use after you've reviewed a done/error/cancelled
    item and want it off the dashboard."""
    ok = storage.delete_queue_item(queue_id)
    if not ok:
        raise HTTPException(status_code=404, detail="queue item not found")
    return {"deleted": queue_id}


@router.post("/{queue_id}/process-now")
def process_now(queue_id: str) -> dict:
    """User-triggered immediate processing via Anthropic API. Bypasses
    the auto_drain_enabled flag (user explicitly clicked) but still
    needs ANTHROPIC_API_KEY set on the api container.

    Heavy modes (analyze, deep_dive) are rejected — those require the
    multi-agent pipeline that only runs locally via Claude Desktop.
    For light modes (ask_portfolio, earnings_summary), the call takes
    a few seconds + a few thousand tokens on Haiku.

    Returns immediately on success/failure (synchronous call, no queue
    polling needed)."""
    from service import queue_drainer
    try:
        return queue_drainer.process_one(queue_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"unexpected error: {e}")


@router.get("/drainer-status")
def drainer_status() -> dict:
    """Used by the UI to show the auto-drain toggle + status."""
    from service import queue_drainer
    return {
        "enabled": queue_drainer._auto_drain_enabled(),
        "anthropic_key_present": bool(queue_drainer._api_key()),
        "model": queue_drainer._model_name(),
        "supported_models": list(queue_drainer.SUPPORTED_DRAINER_MODELS),
        "light_modes": list(queue_drainer.LIGHT_MODES),
        "interval_seconds": 300,
    }


class DrainerToggleRequest(BaseModel):
    enabled: bool


@router.post("/drainer-toggle")
def drainer_toggle(req: DrainerToggleRequest) -> dict:
    """Flip the auto-drain enable flag in gui/config.json."""
    from gui.config import load, save
    cfg = load()
    cfg.setdefault("defaults", {})["auto_drain_enabled"] = bool(req.enabled)
    save(cfg)
    return {"enabled": bool(req.enabled)}


class DrainerModelRequest(BaseModel):
    model: str


@router.post("/drainer-model")
def drainer_model(req: DrainerModelRequest) -> dict:
    """Pick the model the auto-drainer + Process-now buttons use when
    calling the Anthropic API. Haiku is cheapest; Sonnet is the
    balanced default; Opus is the smartest but most expensive.
    Doesn't affect Claude Desktop drains — those use whatever model
    is configured in the run, not this setting."""
    from service import queue_drainer
    from gui.config import load, save
    if req.model not in queue_drainer.SUPPORTED_DRAINER_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported model {req.model!r}; "
                   f"allowed: {list(queue_drainer.SUPPORTED_DRAINER_MODELS)}",
        )
    cfg = load()
    cfg.setdefault("defaults", {})["auto_drain_model"] = req.model
    save(cfg)
    return {"model": req.model}


@router.post("/reclaim-stale")
def reclaim_stale(older_than_seconds: int = 1800) -> dict:
    """Revert claims older than ``older_than_seconds`` back to pending so
    another worker can pick them up. Run as a janitor cron or as part of
    the poller's wake-up routine."""
    if older_than_seconds < 60:
        raise HTTPException(
            status_code=400,
            detail="older_than_seconds must be at least 60 (don't reclaim mid-job)",
        )
    n = storage.reclaim_stale_queue_items(older_than_seconds=older_than_seconds)
    return {"reclaimed": n, "older_than_seconds": older_than_seconds}
