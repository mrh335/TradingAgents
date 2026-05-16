"""Pydantic schemas for the FastAPI service.

Reuses ``gui.brief.Brief`` and ``gui.brief.Trigger`` directly (single
source of truth for the brief shape). Everything else is defined here
so the API surface is stable independent of GUI changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from gui.brief import Brief, Trigger  # noqa: F401  (re-exported)


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------

class RunCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    trade_date: str = Field(description="YYYY-MM-DD")
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


class RunImportRequest(BaseModel):
    """Import a complete run produced outside the framework.

    Use case: an external client (e.g. a Claude Code skill) has run the full
    multi-agent analysis itself and wants the result to surface in the same
    History UI as framework-generated runs. POST the assembled archive +
    optional brief; the server writes the archive file under
    ``<results_dir>/<TICKER>/TradingAgentsStrategy_logs/runs/`` (the same
    location the framework writes), writes any brief as a sidecar next to
    it, and INSERTs a ``runs`` row marked status='done' so it shows up
    everywhere a framework run does (History page, search, sidecar API).

    The archive must conform to schema_version 1 (see ``gui/log_browser.py``
    ``load_log()``). The brief, if provided, is validated against the
    ``Brief`` model. ``brief_markdown`` is the free-form fallback when the
    structured shape can't be filled cleanly.

    Idempotency: the endpoint rejects (409) if ``metadata.run_id`` already
    exists in the DB. To overwrite an existing run, delete it first via
    DELETE /runs/{run_id}.
    """

    archive: Dict[str, Any] = Field(
        description=(
            "schema_version 1 archive envelope: "
            "{schema_version, kind, metadata, state, tool_trace}. "
            "metadata must include run_id, ticker, trade_date."
        ),
    )
    brief: Optional[Brief] = Field(
        default=None,
        description="Structured Brief sidecar. Validated; written as <basename>.brief.json.",
    )
    brief_markdown: Optional[str] = Field(
        default=None,
        description="Free-form markdown brief. Written as <basename>.brief.md.",
    )


class RunSummary(BaseModel):
    """One row of the runs table — what the History page lists."""
    run_id: str
    ticker: str
    trade_date: str
    provider: Optional[str] = None
    deep_model: Optional[str] = None
    quick_model: Optional[str] = None
    debate_rounds: Optional[int] = None
    risk_rounds: Optional[int] = None
    status: str
    decision: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    llm_calls: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    log_path: Optional[str] = None
    error_message: Optional[str] = None


class RunDetail(RunSummary):
    """Full run state for the per-run drilldown view."""
    state: Dict[str, Any] = Field(default_factory=dict)
    tool_trace: List[Dict[str, Any]] = Field(default_factory=list)


class RunEvent(BaseModel):
    """Server-sent event over the WebSocket while a run streams."""
    type: str  # start | section | debate | risk | chunk | tool_start | tool_end | stats | warning | done | error
    data: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

class NoteCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    ticker: Optional[str] = None
    run_id: Optional[str] = None
    tags: Optional[str] = None


class NoteUpdateRequest(BaseModel):
    title: str
    body: str
    tags: Optional[str] = None


class Note(BaseModel):
    id: int
    title: str
    body: str
    ticker: Optional[str] = None
    run_id: Optional[str] = None
    tags: Optional[str] = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    id: int
    run_id: str
    role: str  # user | assistant
    content: str
    created_at: str
    model: Optional[str] = None


class ChatAskRequest(BaseModel):
    question: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class ProviderKey(BaseModel):
    provider: str
    env_name: str
    label: str
    set_in_env: bool
    set_in_config: bool


class SettingsResponse(BaseModel):
    api_keys: List[ProviderKey]
    defaults: Dict[str, Any]
    config_path: str


class SettingsUpdateRequest(BaseModel):
    api_keys: Optional[Dict[str, str]] = None  # env_name -> value (empty value clears)
    defaults: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Memory log
# ---------------------------------------------------------------------------

class MemoryEntry(BaseModel):
    raw: str
    resolved: bool


class MemoryResponse(BaseModel):
    path: str
    entries: List[MemoryEntry]
    total: int
    resolved_count: int
    pending_count: int


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

class ChartPoint(BaseModel):
    date: str
    values: Dict[str, float]


class ChartComparisonResponse(BaseModel):
    ticker: str
    trade_date: str
    benchmarks: List[str]
    points: List[ChartPoint]
    realised_returns: Optional[List[Dict[str, str]]] = None


# ---------------------------------------------------------------------------
# Briefs
# ---------------------------------------------------------------------------

class BriefResponse(BaseModel):
    run_id: str
    brief: Optional[Brief] = None
    cached: bool
    # Where the brief came from:
    #   "sidecar" — read from <archive>.brief.json next to the archive
    #               (no API tokens used; produced by Claude Code or hand-edited)
    #   "llm"     — generated by the quick-think model (current default)
    #   "markdown_sidecar" — free-form brief.md exists but isn't structured
    #   None      — no brief available yet
    source: Optional[str] = None
    # If a brief sidecar exists in markdown form (free-form), expose the text.
    markdown: Optional[str] = None
    # Whether a Claude-Code-please-brief-this request marker is sitting next
    # to the archive waiting to be picked up.
    request_pending: bool = False
