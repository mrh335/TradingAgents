"""Runs: create, list, drilldown, cancel, import, and live-streaming WebSocket."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from gui import sidecars as sidecars_helpers
from gui import storage
from gui.log_browser import discover_logs, load_archive_full, load_log
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.utils import safe_ticker_component
from service.runner_pool import pool
from service.schemas import RunCreateRequest, RunDetail, RunImportRequest, RunSummary

router = APIRouter(prefix="/runs", tags=["runs"])


# Conservative chars-per-token estimate. tiktoken cl100k_base averages
# ~4 chars/token on English prose; we use 4 for a back-of-envelope read.
_CHARS_PER_TOKEN = 4.0


def _estimate_tokens_from_archive(archive: dict) -> tuple[int, int]:
    """Rough server-side fallback for clients that don't send token counts.

    Splits archive content into "input-like" (analyst reports + debates that
    were re-fed to downstream phases) and "output-like" (final-stage outputs
    the model produced once). Both are estimated at 4 chars/token (English
    prose, tiktoken cl100k_base ballpark). Off by 10-30% but better than 0
    on the /tokens chart.
    """
    state = (archive.get("state") or {}) if isinstance(archive, dict) else {}
    if not isinstance(state, dict):
        return 0, 0

    # Output-like: prose the model emitted at distinct phases.
    output_fields = (
        "market_report", "sentiment_report", "news_report",
        "fundamentals_report", "trader_investment_plan", "final_trade_decision",
    )
    output_chars = sum(
        len(state.get(f) or "") for f in output_fields if isinstance(state.get(f), str)
    )
    debate = state.get("investment_debate_state") or {}
    risk = state.get("risk_debate_state") or {}
    for k in ("bull_history", "bear_history", "judge_decision"):
        v = debate.get(k) if isinstance(debate, dict) else None
        if isinstance(v, str):
            output_chars += len(v)
    for k in ("aggressive_history", "conservative_history", "neutral_history", "judge_decision"):
        v = risk.get(k) if isinstance(risk, dict) else None
        if isinstance(v, str):
            output_chars += len(v)

    # Input-like: every output gets fed into every downstream phase that
    # references it. A rough heuristic: input is ~2x the output (each
    # report is read by multiple later agents on average). Skill-side
    # tiktoken numbers typically show input/output ratios in the 1.5-3x
    # range, so 2.0 is a reasonable midpoint.
    input_chars = int(output_chars * 2.0)

    return (
        int(input_chars / _CHARS_PER_TOKEN),
        int(output_chars / _CHARS_PER_TOKEN),
    )


# ---------------------------------------------------------------------------
# CRUD-shaped endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=RunSummary)
def create_run(req: RunCreateRequest) -> RunSummary:
    """Start a new analysis run. Returns immediately; the client should
    connect to ``/runs/{run_id}/stream`` to follow."""
    safe_ticker_component(req.ticker)  # validate

    run_id = storage.new_run_id()
    storage.create_run(
        run_id=run_id,
        ticker=req.ticker,
        trade_date=req.trade_date,
        provider=req.llm_provider,
        deep_model=req.deep_think_llm,
        quick_model=req.quick_think_llm,
        debate_rounds=req.max_debate_rounds,
        risk_rounds=req.max_risk_discuss_rounds,
        vendors=req.data_vendors,
    )

    job = req.model_dump()
    pool.start(run_id=run_id, job=job)

    db_row = storage.get_run(run_id) or {}
    return RunSummary(**db_row)


@router.post("/import", response_model=RunSummary)
def import_run(req: RunImportRequest) -> RunSummary:
    """Import a complete run produced outside the framework.

    Writes the archive + (optional) brief sidecar to the same on-disk
    location the framework writes its own runs to, and INSERTs a row into
    the ``runs`` table with status='done'. After this returns, the run is
    indistinguishable from a framework-generated one in every read path —
    History page, search, sidecar API, brief API, etc.

    Use this from external clients (e.g. a Claude Code skill) that have
    run the multi-agent pipeline themselves and want to publish the result
    into the same UI surface as framework runs.

    See ``RunImportRequest`` for the payload contract.
    """
    archive = req.archive
    metadata = (archive.get("metadata") or {}) if isinstance(archive, dict) else {}

    run_id = metadata.get("run_id")
    ticker = metadata.get("ticker")
    trade_date = metadata.get("trade_date")
    if not run_id or not ticker or not trade_date:
        raise HTTPException(
            status_code=400,
            detail="archive.metadata must include run_id, ticker, trade_date",
        )

    if not isinstance(archive.get("state"), dict):
        raise HTTPException(
            status_code=400,
            detail="archive.state must be a dict (per schema_version 1)",
        )

    if archive.get("schema_version") not in (None, 1):
        raise HTTPException(
            status_code=400,
            detail=f"unsupported schema_version: {archive.get('schema_version')!r}",
        )

    if storage.get_run(run_id):
        raise HTTPException(
            status_code=409,
            detail=f"run_id {run_id!r} already exists; delete it first or use a fresh id",
        )

    # Path-traversal protection — same helper the regular create endpoint uses.
    safe_ticker = safe_ticker_component(ticker)

    # Compose the on-disk path identically to how the framework does it.
    results_dir = Path(DEFAULT_CONFIG["results_dir"])
    runs_dir = results_dir / safe_ticker / "TradingAgentsStrategy_logs" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_filename = f"{run_id}__{trade_date}__{ts}.json"
    archive_path = runs_dir / archive_filename

    try:
        archive_path.write_text(
            json.dumps(archive, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not write archive: {e}")

    # Optional brief sidecar.
    if req.brief is not None:
        brief_sidecar = sidecars_helpers.sidecar_path(archive_path, "brief.json")
        try:
            brief_sidecar.write_text(
                req.brief.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            # Don't roll back the archive — partial publish is still useful.
            raise HTTPException(status_code=500, detail=f"could not write brief: {e}")

    # Optional free-form markdown brief.
    if req.brief_markdown:
        brief_md_sidecar = sidecars_helpers.sidecar_path(archive_path, "brief.md")
        try:
            brief_md_sidecar.write_text(req.brief_markdown, encoding="utf-8")
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"could not write brief.md: {e}")

    # Register in SQLite. Use the framework's create→update→finalize sequence so
    # downstream code paths that filter by status='done' Just Work.
    vendors = (metadata.get("vendors") or metadata.get("data_vendors") or {
        "core_stock_apis": "external",
        "technical_indicators": "external",
        "fundamental_data": "external",
        "news_data": "external",
    })
    storage.create_run(
        run_id=run_id,
        ticker=safe_ticker,
        trade_date=trade_date,
        provider=metadata.get("provider", "external"),
        deep_model=metadata.get("deep_model", "external"),
        quick_model=metadata.get("quick_model", "external"),
        debate_rounds=int(metadata.get("debate_rounds", 0) or 0),
        risk_rounds=int(metadata.get("risk_rounds", 0) or 0),
        vendors=vendors,
    )
    # Token counts — prefer the values the client sent, fall back to a
    # rough server-side estimate based on the archived prose. Caller-
    # supplied numbers (e.g. tiktoken from the Claude Desktop skill) are
    # always more accurate; the estimate exists so legacy / minimal clients
    # that don't post token data still get a non-zero number on the
    # /tokens chart and per-run pages.
    tokens_in = int(metadata.get("tokens_in", 0) or 0)
    tokens_out = int(metadata.get("tokens_out", 0) or 0)
    llm_calls = int(metadata.get("llm_calls", 0) or 0)
    tool_calls = int(metadata.get("tool_calls", 0) or 0)
    if tokens_in == 0 and tokens_out == 0:
        est_in, est_out = _estimate_tokens_from_archive(archive)
        tokens_in, tokens_out = est_in, est_out
    storage.update_run_stats(
        run_id,
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    decision = req.brief.decision if req.brief is not None else None
    storage.finalize_run(run_id, decision=decision, log_path=str(archive_path))

    row = storage.get_run(run_id) or {}
    return RunSummary(**row)


@router.get("", response_model=List[RunSummary])
def list_runs(ticker: Optional[str] = None, limit: int = 200) -> List[RunSummary]:
    rows = storage.list_runs(ticker=ticker, limit=limit)
    return [RunSummary(**r) for r in rows]


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str) -> RunDetail:
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")

    state: dict = {}
    tool_trace: list = []
    log_path = row.get("log_path")
    if log_path and Path(log_path).exists():
        full = load_archive_full(log_path) or {}
        state = full.get("state") or {}
        tool_trace = full.get("tool_trace") or []

    return RunDetail(**row, state=state, tool_trace=tool_trace)


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    if not pool.cancel(run_id):
        raise HTTPException(status_code=404, detail="run not running")
    return {"cancelled": True}


@router.delete("/{run_id}")
def delete_run(run_id: str, delete_files: bool = True) -> dict:
    """Delete a run row + (optionally) its on-disk archive and sidecars.

    Archives are never overwritten — they accumulate over time as you
    re-run the same ticker/date. This endpoint lets the History page
    surface a Delete button so you can prune deliberately. Default is
    to delete the SQLite row AND any on-disk archive + sidecars; pass
    ``delete_files=false`` to keep the files and only purge the row.
    """
    import sqlite3
    from pathlib import Path
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")

    archive_path = row.get("log_path") or ""
    files_deleted: list[str] = []
    if delete_files and archive_path:
        ap = Path(archive_path)
        # Delete the archive + every sidecar that shares its basename.
        if ap.exists():
            try:
                ap.unlink()
                files_deleted.append(str(ap))
            except OSError:
                pass
            # Sidecars: <basename>.brief.json, .brief.md, .brief.request.md, .analysis.md, .chat.md
            stem_base = ap.with_suffix("")  # strip .json
            for sidecar in stem_base.parent.glob(stem_base.name + ".*"):
                if sidecar == ap:
                    continue
                try:
                    sidecar.unlink()
                    files_deleted.append(str(sidecar))
                except OSError:
                    pass

    # Also delete chat messages associated with this run.
    try:
        storage.clear_chat(run_id)
    except Exception:
        pass

    with sqlite3.connect(storage.DB_PATH) as c:
        c.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
        c.commit()

    return {
        "deleted_run": run_id,
        "files_deleted": files_deleted,
    }


# ---------------------------------------------------------------------------
# History from disk (legacy CLI runs + GUI archives)
# ---------------------------------------------------------------------------

@router.get("/disk/index")
def list_disk_logs() -> JSONResponse:
    """Return every state log discoverable on disk, joined with DB rows."""
    db_by_run_id = {r["run_id"]: r for r in storage.list_runs(limit=10_000)}
    db_by_key = {(r["ticker"], r["trade_date"]): r for r in db_by_run_id.values()}
    entries = discover_logs()
    out = []
    for entry in entries:
        rid = entry.get("run_id", "")
        db = db_by_run_id.get(rid) or db_by_key.get((entry["ticker"], entry["trade_date"])) or {}
        out.append({**entry, "db": db})
    return JSONResponse(out)


# ---------------------------------------------------------------------------
# WebSocket — live stream of agent output
# ---------------------------------------------------------------------------

@router.websocket("/{run_id}/stream")
async def stream_run(ws: WebSocket, run_id: str) -> None:
    await ws.accept()
    q = await pool.subscribe(run_id)
    try:
        while True:
            ev = await q.get()
            if ev.get("type") == "_eof":
                break
            await ws.send_text(json.dumps(ev))
    except WebSocketDisconnect:
        pass
    finally:
        pool.unsubscribe(run_id, q)
        try:
            await ws.close()
        except RuntimeError:
            pass
