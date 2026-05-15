"""Sidecar workflow API — lets Claude Code (or any client) process
pending brief requests without touching the NAS filesystem directly.

The web app drops ``*.brief.request.md`` files next to each run archive
when the user clicks "Request via Claude Code". This router exposes:

    GET    /sidecars/pending           — every pending request, full body
    GET    /sidecars/run/{run_id}      — sidecars for one run + archive content
    POST   /sidecars/run/{run_id}/brief — accept a Brief JSON, write sidecar
    POST   /sidecars/run/{run_id}/brief/markdown — accept free-form markdown
    DELETE /sidecars/run/{run_id}/request — clear the brief.request.md marker

Claude Code workflow:
    1. GET /sidecars/pending → list of {run_id, request_body, archive_url}
    2. For each: GET /sidecars/run/{run_id} to fetch the full archive
    3. Build a Brief, POST /sidecars/run/{run_id}/brief
    4. The marker is deleted automatically as a side-effect of POST.

This removes the filesystem-mounting dependency — Claude Code on any
machine that can reach the API (LAN-mounted) handles everything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from gui import sidecars
from gui import storage
from gui.brief import Brief
from gui.log_browser import discover_logs, load_log

router = APIRouter(prefix="/sidecars", tags=["sidecars"])


# ---------------------------------------------------------------------------
# Discovery — what's pending?
# ---------------------------------------------------------------------------

class PendingRequest(BaseModel):
    run_id: str
    ticker: str
    trade_date: str
    archive_path: str
    request_path: str
    request_body: str
    has_brief_already: bool  # if a .brief.json sidecar already exists


@router.get("/pending", response_model=List[PendingRequest])
def list_pending() -> List[PendingRequest]:
    """Walk every archived run and surface those with a pending request marker.

    Claude Code should call this first to find work to do. The
    ``request_body`` field contains the full templated prompt the web
    app wrote — has the archive path, the brief sidecar destination, and
    the run summary inline.
    """
    out: List[PendingRequest] = []
    # Map run_id → archive path via the discover_logs helper (handles both
    # archive-format and legacy canonical files).
    runs_by_id = {r["run_id"]: r for r in storage.list_runs(limit=10_000) if r.get("log_path")}
    for run_id, row in runs_by_id.items():
        archive_path = row["log_path"]
        if not archive_path or not Path(archive_path).exists():
            continue
        req_path = sidecars.sidecar_path(archive_path, "brief.request.md")
        if not req_path.exists():
            continue
        try:
            body = req_path.read_text(encoding="utf-8")
        except OSError:
            body = ""
        out.append(PendingRequest(
            run_id=run_id,
            ticker=row.get("ticker") or "",
            trade_date=row.get("trade_date") or "",
            archive_path=str(archive_path),
            request_path=str(req_path),
            request_body=body,
            has_brief_already=sidecars.read_brief_sidecar(archive_path) is not None,
        ))
    return out


# ---------------------------------------------------------------------------
# Per-run sidecar bundle — gives Claude Code everything it needs in one call.
# ---------------------------------------------------------------------------

class SidecarBundle(BaseModel):
    run_id: str
    ticker: str
    trade_date: str
    archive_path: str
    archive: Dict[str, Any]            # the full archive JSON (state + metadata + tool_trace)
    existing_sidecars: List[Dict[str, Any]]
    request_pending: bool
    request_body: Optional[str] = None


@router.get("/run/{run_id}", response_model=SidecarBundle)
def get_bundle(run_id: str) -> SidecarBundle:
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    archive_path = row.get("log_path")
    if not archive_path or not Path(archive_path).exists():
        raise HTTPException(status_code=409, detail="run has no on-disk archive yet")

    # Load the archive as JSON (full envelope, not unwrapped state).
    import json
    try:
        with open(archive_path, "r", encoding="utf-8") as f:
            archive_doc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"could not read archive: {e}")

    req_path = sidecars.sidecar_path(archive_path, "brief.request.md")
    request_body: Optional[str] = None
    if req_path.exists():
        try:
            request_body = req_path.read_text(encoding="utf-8")
        except OSError:
            pass

    return SidecarBundle(
        run_id=run_id,
        ticker=row.get("ticker") or "",
        trade_date=row.get("trade_date") or "",
        archive_path=str(archive_path),
        archive=archive_doc,
        existing_sidecars=sidecars.list_sidecars(archive_path),
        request_pending=req_path.exists(),
        request_body=request_body,
    )


# ---------------------------------------------------------------------------
# Write — accept a Brief from Claude Code, save the sidecar, drop the marker
# ---------------------------------------------------------------------------

@router.post("/run/{run_id}/brief")
def submit_brief(run_id: str, brief: Brief) -> dict:
    """Accept a structured Brief from Claude Code and write it as a sidecar.

    Clears the pending request marker on success so the run stops showing
    up under ``/sidecars/pending``.
    """
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    archive_path = row.get("log_path")
    if not archive_path or not Path(archive_path).exists():
        raise HTTPException(status_code=409, detail="run has no on-disk archive yet")

    # Write the structured sidecar.
    sidecar_path = sidecars.sidecar_path(archive_path, "brief.json")
    sidecar_path.write_text(brief.model_dump_json(indent=2), encoding="utf-8")

    # Clear the request marker (if any).
    sidecars.clear_request(archive_path, "brief")

    return {
        "saved": str(sidecar_path),
        "request_cleared": True,
        "ticker": row.get("ticker"),
        "trade_date": row.get("trade_date"),
    }


class MarkdownBrief(BaseModel):
    markdown: str


@router.post("/run/{run_id}/brief/markdown")
def submit_brief_markdown(run_id: str, body: MarkdownBrief) -> dict:
    """Accept a free-form markdown brief if Claude Code couldn't fit the
    analysis into the structured schema. The web app renders this with a
    ``markdown_sidecar`` source badge."""
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    archive_path = row.get("log_path")
    if not archive_path or not Path(archive_path).exists():
        raise HTTPException(status_code=409, detail="run has no on-disk archive yet")

    sidecar_path = sidecars.sidecar_path(archive_path, "brief.md")
    sidecar_path.write_text(body.markdown, encoding="utf-8")
    sidecars.clear_request(archive_path, "brief")
    return {"saved": str(sidecar_path), "request_cleared": True}


@router.delete("/run/{run_id}/request")
def clear_request_marker(run_id: str) -> dict:
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    archive_path = row.get("log_path")
    if not archive_path:
        return {"cleared": False}
    return {"cleared": sidecars.clear_request(archive_path, "brief")}


# ---------------------------------------------------------------------------
# Bulk-request — drop markers across many runs in one call
# ---------------------------------------------------------------------------

class BulkRequestResult(BaseModel):
    requested: List[str]      # run_ids the marker was dropped on
    skipped: List[str]        # run_ids that already had a brief AND we didn't include them
    no_archive: List[str]     # run_ids whose archive is missing


_REQUEST_TEMPLATE_BULK = """\
# Brief request for {ticker} ({trade_date})

Auto-batched. Please process via the ``/sidecars/pending`` workflow
described in CLAUDE.md — no API tokens to be spent here.

## Archive

```
{archive_path}
```

## Run summary

- Ticker: **{ticker}**
- Trade date: **{trade_date}**
- Decision label from framework: **{decision}**
- Provider used for the analysis: **{provider}** ({deep_model} / {quick_model})

See CLAUDE.md for the schema and conventions.
"""


@router.post("/request-all-missing", response_model=BulkRequestResult)
def request_all_missing(include_existing: bool = False) -> BulkRequestResult:
    """Drop a ``*.brief.request.md`` next to every completed run that
    doesn't have a ``*.brief.json`` sidecar yet.

    ``include_existing=true`` re-requests briefs even where one already
    exists — useful when you've improved CLAUDE.md or want fresh briefs
    on previously-briefed runs.
    """
    requested: List[str] = []
    skipped: List[str] = []
    no_archive: List[str] = []

    for row in storage.list_runs(limit=10_000):
        # Only act on completed runs.
        if (row.get("status") or "").lower() != "done":
            continue
        archive_path = row.get("log_path") or ""
        if not archive_path or not Path(archive_path).exists():
            no_archive.append(row["run_id"])
            continue

        has_brief = sidecars.read_brief_sidecar(archive_path) is not None
        if has_brief and not include_existing:
            skipped.append(row["run_id"])
            continue

        # Drop the marker (idempotent — overwrites if already there).
        prompt = _REQUEST_TEMPLATE_BULK.format(
            ticker=row["ticker"],
            trade_date=row["trade_date"],
            decision=row.get("decision") or "—",
            provider=row.get("provider") or "—",
            deep_model=row.get("deep_model") or "—",
            quick_model=row.get("quick_model") or "—",
            archive_path=archive_path,
        )
        sidecars.write_request(archive_path, kind="brief", prompt=prompt)
        requested.append(row["run_id"])

    return BulkRequestResult(
        requested=requested,
        skipped=skipped,
        no_archive=no_archive,
    )
