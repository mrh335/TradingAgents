"""Model A/B comparisons — submit N runs against the same ticker/date
with only the LLM model varying, see side-by-side results.

Use case: "given the same data, does Sonnet say Buy and Opus say Hold?"
Useful for sanity-checking the framework's decisions, picking which
model to trust for a given ticker class, or just exploring how each
model reasons differently.

Implementation
--------------
- POST /compare creates N queue items, all with the same ticker +
  trade_date but different (provider, deep_model, quick_model)
  combos. All N share a comparison_id (uuid). The webapp scheduler
  doesn't fire these — they're explicit user submissions, so the
  Claude Desktop / Windows Scheduled Task drainer picks them up
  like any other analyze item.

- GET /compare/{id} reads the queue + run rows tagged with that
  comparison_id and returns a side-by-side payload: per-model
  decision, brief tldr, trigger count, key risks, token cost.
  Includes an agreement summary at the top ("3/4 say Buy").

- GET /compare lists recent comparisons (paginated).

Tradeoff: each model runs the FULL pipeline including analyst
summarization. So technically the analyst phases are LLM-dependent.
For pure "model only varies in the reasoning phase" testing, we'd
snapshot the analyst outputs and replay — that's a future
enhancement. The simpler full-pipeline approach gives meaningful
comparison at much lower complexity.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gui import sidecars as sidecars_helpers
from gui import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/compare", tags=["compare"])


class ModelCombo(BaseModel):
    """One model configuration to include in the comparison."""
    provider: str = Field(min_length=1, max_length=32, description="anthropic | openai | ollama | etc.")
    deep_model: str = Field(min_length=1, max_length=128, description="Model used for deep-reasoning agents")
    quick_model: Optional[str] = Field(default=None, max_length=128, description="Lighter model for fast helpers; defaults to deep_model")
    label: Optional[str] = Field(default=None, max_length=80, description="Optional display label (e.g. 'Sonnet 4.5'); auto-generated if omitted")


class CompareCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    trade_date: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD; defaults to today",
    )
    analysis_mode: str = Field(
        default="fresh",
        description="'fresh' (no memory injection) is recommended for true comparison; "
                    "'incremental' biases each model toward the same prior decision context",
    )
    combos: List[ModelCombo] = Field(
        min_length=2, max_length=6,
        description="2-6 model combos to compare against the same data",
    )
    execution_mode: str = Field(
        default="server",
        description=(
            "'server' (recommended): launch each combo immediately on the "
            "server-side runner_pool using the per-combo provider+model. "
            "Anthropic/OpenAI combos cost API tokens; Ollama is free + local. "
            "'queue': create run_queue items for Claude Desktop / Windows "
            "Task drainer to pick up. NOTE: CD cannot be programmatically "
            "told which model to use — it always uses whatever model your "
            "chat session is currently set to, so queue-mode comparisons "
            "of multiple Anthropic models will all run with the same CD "
            "model. Useful for sanity-checking; not useful for real A/B "
            "testing across models."
        ),
    )
    notes: Optional[str] = Field(default=None, max_length=500)


class CompareCreateResponse(BaseModel):
    comparison_id: str
    queue_ids: List[str]
    run_ids: List[str]
    execution_mode: str
    ticker: str
    trade_date: str
    combo_count: int


@router.post("", response_model=CompareCreateResponse)
def create_comparison(req: CompareCreateRequest) -> CompareCreateResponse:
    """Submit a comparison. Returns a comparison_id you can use to fetch
    /compare/{id} and watch the side-by-side build as each combo finishes.

    Execution mode 'server' (default) kicks each combo off immediately via
    the framework's runner_pool with the per-combo provider+model — this
    is the ONLY way to get a fair multi-model comparison because the
    runner respects whichever provider you specified. CD can't be
    programmatically forced to a specific model, so queue-mode is only
    useful for 'what does my current CD setup say'.
    """
    if req.execution_mode not in ("server", "queue"):
        raise HTTPException(
            status_code=400,
            detail=f"invalid execution_mode {req.execution_mode!r}; must be 'server' or 'queue'",
        )

    trade_date = req.trade_date or date.today().isoformat()
    comparison_id = storage.new_comparison_id()

    queue_ids: List[str] = []
    run_ids: List[str] = []

    if req.execution_mode == "server":
        # Server-side: kick off each combo immediately via runner_pool.
        # Each pool.start creates a run_id; we tag each new run with
        # comparison_id so /compare/{id} can group them.
        from service.runner_pool import pool

        for combo in req.combos:
            label = combo.label or f"{combo.provider}/{combo.deep_model}"
            run_id = storage.new_run_id()
            # Stamp the comparison_id on the run row immediately so the
            # group view can find it even before the run completes.
            storage.create_run(
                run_id=run_id,
                ticker=req.ticker.upper(),
                trade_date=trade_date,
                provider=combo.provider,
                deep_model=combo.deep_model,
                quick_model=combo.quick_model or combo.deep_model,
                debate_rounds=1,
                risk_rounds=1,
                vendors={},
            )
            storage.attach_comparison_id(run_id, comparison_id)

            job = {
                "ticker": req.ticker.upper(),
                "trade_date": trade_date,
                "llm_provider": combo.provider,
                "deep_think_llm": combo.deep_model,
                "quick_think_llm": combo.quick_model or combo.deep_model,
                "max_debate_rounds": 1,
                "max_risk_discuss_rounds": 1,
                "analysis_mode": req.analysis_mode,
                "comparison_id": comparison_id,
                "comparison_label": label,
            }
            try:
                pool.start(run_id=run_id, job=job)
                run_ids.append(run_id)
            except Exception as e:
                logger.warning(f"compare: pool.start failed for {label}: {e}")
                # Mark this run as failed but keep the others going
                try:
                    storage.finalize_run(
                        run_id,
                        decision=None,
                        log_path=None,
                        error=f"pool.start failed: {e}",
                    )
                except Exception:
                    pass
        return CompareCreateResponse(
            comparison_id=comparison_id,
            queue_ids=[],
            run_ids=run_ids,
            execution_mode="server",
            ticker=req.ticker.upper(),
            trade_date=trade_date,
            combo_count=len(req.combos),
        )

    # Queue mode: create run_queue items for the external drainer.
    for combo in req.combos:
        label = combo.label or f"{combo.provider}/{combo.deep_model}"
        options = {
            "comparison_id": comparison_id,
            "comparison_label": label,
            "provider": combo.provider,
            "deep_model": combo.deep_model,
            "quick_model": combo.quick_model or combo.deep_model,
            "analysis_mode": req.analysis_mode,
            "notes": req.notes,
        }
        queued = storage.queue_request(
            ticker=req.ticker,
            trade_date=trade_date,
            mode="analyze",
            options=options,
            requested_by="web-ui:compare",
            priority=10,
        )
        queue_ids.append(queued["id"])

    return CompareCreateResponse(
        comparison_id=comparison_id,
        queue_ids=queue_ids,
        run_ids=[],
        execution_mode="queue",
        ticker=req.ticker.upper(),
        trade_date=trade_date,
        combo_count=len(req.combos),
    )


class CompareRowState(BaseModel):
    """One column of the comparison view."""
    # Identity
    label: str
    provider: Optional[str] = None
    deep_model: Optional[str] = None
    quick_model: Optional[str] = None

    # Queue + run linkage
    queue_id: Optional[str] = None
    queue_status: Optional[str] = None  # 'pending' | 'claimed' | 'done' | 'error' | 'cancelled'
    queue_error: Optional[str] = None
    run_id: Optional[str] = None

    # Run-derived
    decision: Optional[str] = None
    run_status: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    llm_calls: Optional[int] = None
    tool_calls: Optional[int] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    log_path: Optional[str] = None

    # Brief excerpt (read from sidecar if exists)
    brief_tldr: Optional[str] = None
    brief_timeframe: Optional[str] = None
    brief_position_size: Optional[str] = None
    brief_entry_strategy: Optional[str] = None
    brief_stop_loss: Optional[str] = None
    brief_take_profit: Optional[str] = None
    trigger_count: Optional[int] = None
    risk_count: Optional[int] = None
    brief_benchmark_view: Optional[str] = None


class AgreementSummary(BaseModel):
    total_runs: int
    completed_runs: int
    decisions: Dict[str, int]    # {"Buy": 3, "Hold": 1}
    consensus: Optional[str] = None    # The most common decision, or None if split
    consensus_strength_pct: Optional[float] = None  # how dominant the consensus is
    outliers: List[str] = []   # labels of runs whose decision differs from consensus


class CompareDetailResponse(BaseModel):
    comparison_id: str
    ticker: str
    trade_date: str
    rows: List[CompareRowState]
    agreement: AgreementSummary
    overall_status: str   # 'pending' | 'in_progress' | 'partial' | 'complete'
    created_at: Optional[str] = None


def _safe_json_load(s: Optional[str]) -> Dict[str, Any]:
    if not s:
        return {}
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return {}


def _read_brief_for_run(log_path: Optional[str]) -> Dict[str, Any]:
    """Pull the structured brief sidecar (if present) so the comparison
    view can show tldr + entry/stop/targets without the user needing to
    drill into /history/{run_id}."""
    if not log_path:
        return {}
    try:
        sidecar = sidecars_helpers.sidecar_path(log_path, "brief.json")
        if not sidecar or not sidecar.exists():
            return {}
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"compare: brief read failed for {log_path}: {e}")
        return {}


def _row_state(queue_item: Optional[Dict[str, Any]],
               run_row: Optional[Dict[str, Any]]) -> CompareRowState:
    """Merge a queue item + (possibly) a run row into one column of the
    comparison view."""
    opts = _safe_json_load(queue_item.get("options_json") if queue_item else None)
    label = opts.get("comparison_label") or (
        f"{opts.get('provider', '?')}/{opts.get('deep_model', '?')}"
    )
    brief = _read_brief_for_run(run_row.get("log_path") if run_row else None)
    triggers = brief.get("triggers") or []
    risks = brief.get("key_risks") or []

    return CompareRowState(
        label=label,
        provider=opts.get("provider") or (run_row or {}).get("provider"),
        deep_model=opts.get("deep_model") or (run_row or {}).get("deep_model"),
        quick_model=opts.get("quick_model") or (run_row or {}).get("quick_model"),
        queue_id=queue_item.get("id") if queue_item else None,
        queue_status=queue_item.get("status") if queue_item else None,
        queue_error=queue_item.get("error_message") if queue_item else None,
        run_id=(run_row or {}).get("run_id") or (queue_item or {}).get("result_run_id"),
        decision=(run_row or {}).get("decision"),
        run_status=(run_row or {}).get("status"),
        started_at=(run_row or {}).get("started_at"),
        completed_at=(run_row or {}).get("completed_at"),
        llm_calls=(run_row or {}).get("llm_calls"),
        tool_calls=(run_row or {}).get("tool_calls"),
        tokens_in=(run_row or {}).get("tokens_in"),
        tokens_out=(run_row or {}).get("tokens_out"),
        log_path=(run_row or {}).get("log_path"),
        brief_tldr=brief.get("tldr"),
        brief_timeframe=brief.get("timeframe"),
        brief_position_size=brief.get("position_size"),
        brief_entry_strategy=brief.get("entry_strategy"),
        brief_stop_loss=brief.get("stop_loss"),
        brief_take_profit=brief.get("take_profit"),
        trigger_count=len(triggers) if isinstance(triggers, list) else None,
        risk_count=len(risks) if isinstance(risks, list) else None,
        brief_benchmark_view=brief.get("benchmark_view"),
    )


def _agreement(rows: List[CompareRowState]) -> AgreementSummary:
    """Compute the consensus + outliers across the completed rows."""
    decisions = [r.decision for r in rows if r.decision]
    counter = Counter(decisions)
    consensus = None
    consensus_strength = None
    outliers: List[str] = []
    if counter:
        top = counter.most_common(1)[0]
        # Consensus only if the most-common decision is strictly > half the completed runs
        if top[1] * 2 > len(decisions):
            consensus = top[0]
            consensus_strength = round(top[1] / len(decisions) * 100, 1)
            outliers = [r.label for r in rows if r.decision and r.decision != consensus]
    return AgreementSummary(
        total_runs=len(rows),
        completed_runs=len(decisions),
        decisions=dict(counter),
        consensus=consensus,
        consensus_strength_pct=consensus_strength,
        outliers=outliers,
    )


def _overall_status(rows: List[CompareRowState]) -> str:
    if not rows:
        return "pending"
    queue_statuses = [r.queue_status for r in rows if r.queue_status]
    run_statuses = [r.run_status for r in rows if r.run_status]
    # A row is "finished" when the queue item is done/error/cancelled.
    finished = sum(1 for s in queue_statuses if s in ("done", "error", "cancelled"))
    if finished == 0 and not any(s == "claimed" for s in queue_statuses):
        return "pending"
    if finished == len(rows):
        return "complete"
    if finished > 0:
        return "partial"
    return "in_progress"


@router.get("/{comparison_id}", response_model=CompareDetailResponse)
def get_comparison(comparison_id: str) -> CompareDetailResponse:
    """Side-by-side view of all runs in a comparison group."""
    queue_items = storage.list_queue_items_for_comparison(comparison_id)
    if not queue_items:
        raise HTTPException(
            status_code=404,
            detail=f"no comparison with id {comparison_id!r}",
        )

    # Group runs by queue_id (via result_run_id) so we can pair each
    # queue item with its eventual run row.
    runs_by_queue: Dict[str, Dict[str, Any]] = {}
    for q in queue_items:
        if q.get("result_run_id"):
            run = storage.get_run(q["result_run_id"])
            if run:
                runs_by_queue[q["id"]] = run

    rows: List[CompareRowState] = []
    ticker: Optional[str] = None
    trade_date: Optional[str] = None
    created_at: Optional[str] = None
    for q in queue_items:
        ticker = ticker or q.get("ticker")
        trade_date = trade_date or q.get("trade_date")
        created_at = created_at or q.get("created_at")
        rows.append(_row_state(q, runs_by_queue.get(q["id"])))

    return CompareDetailResponse(
        comparison_id=comparison_id,
        ticker=ticker or "?",
        trade_date=trade_date or "?",
        rows=rows,
        agreement=_agreement(rows),
        overall_status=_overall_status(rows),
        created_at=created_at,
    )


class CompareListRow(BaseModel):
    comparison_id: str
    ticker: str
    trade_date: str
    combo_count: int
    completed_count: int
    overall_status: str
    consensus: Optional[str] = None
    created_at: str


@router.get("", response_model=List[CompareListRow])
def list_comparisons(limit: int = 50) -> List[CompareListRow]:
    """List recent comparison groups, newest first. Scans the run_queue
    options_json for distinct comparison_id values — slightly more
    expensive than a dedicated table but avoids another schema migration."""
    # Pull queue items that mention a comparison_id; group in Python.
    with storage._conn() as c:
        rows = c.execute(
            """SELECT * FROM run_queue
               WHERE options_json LIKE '%"comparison_id":%'
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit * 8,),  # over-fetch since N rows per comparison
        ).fetchall()

    by_cid: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        opts = _safe_json_load(d.get("options_json"))
        cid = opts.get("comparison_id")
        if not cid:
            continue
        by_cid.setdefault(cid, []).append(d)

    out: List[CompareListRow] = []
    for cid, items in by_cid.items():
        items.sort(key=lambda x: x["created_at"])
        first = items[0]
        # Build a temporary list of CompareRowState to reuse the agreement helper
        runs_by_queue: Dict[str, Dict[str, Any]] = {}
        for q in items:
            if q.get("result_run_id"):
                run = storage.get_run(q["result_run_id"])
                if run:
                    runs_by_queue[q["id"]] = run
        states = [_row_state(q, runs_by_queue.get(q["id"])) for q in items]
        agr = _agreement(states)
        out.append(CompareListRow(
            comparison_id=cid,
            ticker=first["ticker"],
            trade_date=first["trade_date"],
            combo_count=len(items),
            completed_count=agr.completed_runs,
            overall_status=_overall_status(states),
            consensus=agr.consensus,
            created_at=first["created_at"],
        ))

    out.sort(key=lambda r: r.created_at, reverse=True)
    return out[:limit]
