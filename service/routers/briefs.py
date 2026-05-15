"""Brief generation + cache + Claude Code sidecar handoff."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from gui import brief as brief_mod
from gui import sidecars, storage
from gui.log_browser import load_log
from service.schemas import BriefResponse

router = APIRouter(prefix="/runs", tags=["briefs"])


def _build_response(run_id: str, archive_path: Optional[str]) -> BriefResponse:
    """Resolve the brief layering: sidecar > SQLite cache > nothing.

    Markdown-only sidecars are surfaced as ``markdown`` plus ``source =
    'markdown_sidecar'`` so the UI can render them even without a
    structured Brief object.
    """
    sidecar_brief = sidecars.read_brief_sidecar(archive_path) if archive_path else None
    if sidecar_brief is not None:
        return BriefResponse(
            run_id=run_id,
            brief=sidecar_brief,
            cached=True,
            source="sidecar",
            markdown=None,
            request_pending=sidecars.request_exists(archive_path, "brief") if archive_path else False,
        )

    md_sidecar = sidecars.read_brief_markdown(archive_path) if archive_path else None
    if md_sidecar:
        return BriefResponse(
            run_id=run_id,
            brief=None,
            cached=True,
            source="markdown_sidecar",
            markdown=md_sidecar,
            request_pending=sidecars.request_exists(archive_path, "brief") if archive_path else False,
        )

    cached = brief_mod.get_cached_brief(run_id)
    return BriefResponse(
        run_id=run_id,
        brief=cached,
        cached=cached is not None,
        source="llm" if cached else None,
        markdown=None,
        request_pending=sidecars.request_exists(archive_path, "brief") if archive_path else False,
    )


@router.get("/{run_id}/brief", response_model=BriefResponse)
def get_brief(run_id: str) -> BriefResponse:
    row = storage.get_run(run_id)
    archive_path = (row or {}).get("log_path")
    return _build_response(run_id, archive_path)


@router.post("/{run_id}/brief", response_model=BriefResponse)
def generate_brief(run_id: str, force: bool = False) -> BriefResponse:
    """Generate (or regenerate) the brief via the quick-think LLM.

    ``force=true`` skips both the sidecar AND the SQLite cache and re-runs
    the LLM call. Default behaviour returns whatever's already on hand:
    a brief.json sidecar wins over a SQLite cache, which wins over a
    new LLM call.
    """
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    archive_path = row.get("log_path")

    if not force:
        existing = _build_response(run_id, archive_path)
        if existing.brief is not None or existing.markdown:
            return existing

    if not archive_path or not Path(archive_path).exists():
        raise HTTPException(
            status_code=409,
            detail="run has no on-disk transcript yet — wait for it to finish",
        )
    state = load_log(archive_path)
    if state is None:
        raise HTTPException(status_code=500, detail="could not parse run state log")

    meta = {
        "ticker": row["ticker"],
        "trade_date": row["trade_date"],
        "decision": row.get("decision"),
        "run_id": run_id,
    }
    try:
        new_brief = brief_mod.generate_brief(state, meta)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"brief generation failed: {e}")
    brief_mod.store_brief(run_id, new_brief)
    return BriefResponse(
        run_id=run_id,
        brief=new_brief,
        cached=False,
        source="llm",
        request_pending=sidecars.request_exists(archive_path, "brief") if archive_path else False,
    )


# ---------------------------------------------------------------------------
# Sidecar control plane — "Claude Code please look at this run"
# ---------------------------------------------------------------------------

@router.get("/{run_id}/files")
def list_run_files(run_id: str) -> dict:
    """Enumerate sidecar files next to this run's archive.

    Surfaces what Claude Code has produced for this run so the UI can show
    badges like "brief.md available, analysis.md available".
    """
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    archive_path = row.get("log_path")
    if not archive_path:
        return {"archive": None, "sidecars": []}
    return {
        "archive": archive_path,
        "sidecars": sidecars.list_sidecars(archive_path),
    }


_BRIEF_REQUEST_TEMPLATE = """\
# Brief request for {ticker} ({trade_date})

The web app would like a plain-English brief generated for this run
**without** spending API tokens. Claude Code: please read the archive
and write the structured brief next to it.

## Archive

```
{archive_path}
```

## What to do

1. Open the archive JSON.
2. Build a ``Brief`` object matching the schema in
   ``gui/brief.py`` (Brief + Trigger Pydantic models). Required fields:
   ``decision``, ``tldr``, ``timeframe``, ``position_size``,
   ``entry_strategy``, ``stop_loss``, ``take_profit``, ``triggers``
   (list of {{condition, action}}), ``key_risks`` (list[str]),
   ``benchmark_view``.
3. Write the JSON to ``{brief_sidecar}``.
4. Delete this request marker file (``{request_marker}``).

The web app will pick it up automatically on the next page load.

## Conventions

- Quote specific prices/levels from the analysis when given.
- ``decision`` should be one of: Buy, Overweight, Hold, Underweight, Sell.
- Keep ``tldr`` to 2-3 sentences a non-investor can understand.
- 3-7 trigger points in if-then form. Concrete numbers > vague language.
- Plain language throughout — no jargon in tldr / key_risks.

## Run summary

- Ticker: **{ticker}**
- Trade date: **{trade_date}**
- Decision label from framework: **{decision}**
- Provider used for the analysis: **{provider}** ({deep_model} / {quick_model})

See ``CLAUDE.md`` at the repo root for the full sidecar pattern.
"""


@router.post("/{run_id}/request-claude-code-analysis", response_model=BriefResponse)
def request_claude_code_brief(run_id: str) -> BriefResponse:
    """Drop a ``*.brief.request.md`` marker next to the run archive asking
    Claude Code to produce a brief offline (no API tokens spent here).

    The user opens Claude Code in the repo, runs the workflow described
    in CLAUDE.md, and a ``*.brief.json`` appears next to the archive.
    The GET /brief endpoint will pick it up on the next call.
    """
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    archive_path = row.get("log_path")
    if not archive_path or not Path(archive_path).exists():
        raise HTTPException(
            status_code=409,
            detail="run has no on-disk transcript yet — wait for it to finish",
        )
    prompt = _BRIEF_REQUEST_TEMPLATE.format(
        ticker=row["ticker"],
        trade_date=row["trade_date"],
        decision=row.get("decision") or "—",
        provider=row.get("provider") or "—",
        deep_model=row.get("deep_model") or "—",
        quick_model=row.get("quick_model") or "—",
        archive_path=archive_path,
        brief_sidecar=str(sidecars.sidecar_path(archive_path, "brief.json")),
        request_marker=str(sidecars.sidecar_path(archive_path, "brief.request.md")),
    )
    sidecars.write_request(archive_path, kind="brief", prompt=prompt)
    return _build_response(run_id, archive_path)


@router.delete("/{run_id}/brief/request")
def cancel_claude_code_request(run_id: str) -> dict:
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    archive_path = row.get("log_path")
    if not archive_path:
        return {"cleared": False}
    return {"cleared": sidecars.clear_request(archive_path, "brief")}
