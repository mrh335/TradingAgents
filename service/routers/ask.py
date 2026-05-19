"""Portfolio Q&A — free-form questions about positions, runs, trades, alerts.

Two execution modes:

- **queue** (default, recommended): drops a `run_queue` item with
  ``mode='ask_portfolio'`` that Claude Desktop drains. Free; async.
  Answer arrives whenever the next drain processes it.

- **sync**: calls the Anthropic API directly with the full context
  snapshot + question. Instant response but burns tokens. Falls back
  to queue mode if the Anthropic SDK or ANTHROPIC_API_KEY isn't
  available.

Context snapshot captures everything the answer might need: positions
+ latest run summaries + trades + restrictions + active news alerts +
top 13F overlaps. The snapshot is JSON-serialized and stored on the
question row so the answer is reproducible.

Endpoints
---------
POST /ask                                  — submit a question
GET  /ask/conversations                    — list recent threads
GET  /ask/conversation/{conversation_id}   — full thread of Q+As
GET  /ask/{question_id}                    — one Q+A (used for polling)
POST /ask/{question_id}/answer             — Claude Desktop POSTs back here
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gui import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ask", tags=["ask"])


# ──────────────────────────────────────────────────────────────────────
# Context snapshot — fed to the LLM (queue or sync) so it can answer
# grounded in the user's actual data instead of guessing.
# ──────────────────────────────────────────────────────────────────────


def _build_context_snapshot() -> Dict[str, Any]:
    """Capture a moment-in-time view of the portfolio + recent activity
    that the LLM uses to answer questions.

    Conservative on size: we don't dump full archives, just the
    structured summary of each. The full archive is one API call away
    if the model needs to drill in (Claude Desktop can hit
    /sidecars/run/{id} for that)."""
    snapshot: Dict[str, Any] = {
        "as_of": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }

    # ── Open positions ──
    try:
        positions = storage.list_positions(include_closed=False)
        snapshot["positions"] = [
            {
                "ticker": p["ticker"],
                "shares": p["shares"],
                "cost_basis_per_share": p["cost_basis_per_share"],
                "opened_at": p.get("opened_at"),
                "account": p.get("account"),
            }
            for p in positions
        ]
    except Exception as e:
        snapshot["positions_error"] = str(e)

    # ── Watchlist ──
    try:
        snapshot["watchlist"] = [
            w["ticker"] for w in storage.list_watchlist()
        ]
    except Exception:
        snapshot["watchlist"] = []

    # ── Recent runs + their decisions ──
    try:
        recent_runs = storage.list_runs(limit=40)
        snapshot["recent_runs"] = []
        for r in recent_runs:
            if (r.get("status") or "").lower() != "done":
                continue
            snapshot["recent_runs"].append({
                "run_id": r["run_id"],
                "ticker": r["ticker"],
                "trade_date": r["trade_date"],
                "decision": r.get("decision"),
                "provider": r.get("provider"),
                "deep_model": r.get("deep_model"),
                "completed_at": r.get("completed_at"),
            })
    except Exception as e:
        snapshot["recent_runs_error"] = str(e)

    # ── Recent trades ──
    try:
        snapshot["recent_trades"] = [
            {
                "ticker": t["ticker"],
                "action": t["action"],
                "shares": t["shares"],
                "price": t.get("price"),
                "executed_at": t["executed_at"],
                "linked_run_id": t.get("linked_run_id"),
            }
            for t in storage.list_trades(limit=50)
        ]
    except Exception:
        snapshot["recent_trades"] = []

    # ── Active restrictions ──
    try:
        today_iso = date.today().isoformat()
        snapshot["active_restrictions"] = [
            {
                "ticker": r["ticker"],
                "kind": r.get("kind"),
                "earnings_window_open_offset_days": r.get("earnings_window_open_offset_days"),
                "earnings_window_duration_days": r.get("earnings_window_duration_days"),
                "earnings_days_before": r.get("earnings_days_before"),
                "earnings_days_after": r.get("earnings_days_after"),
                "reason": r.get("reason"),
                "_resolved_start": r.get("_resolved_start"),
                "_resolved_end": r.get("_resolved_end"),
            }
            for r in storage.list_restrictions(active_on=today_iso)
        ]
    except Exception:
        snapshot["active_restrictions"] = []

    # ── Active news alerts (high impact only, last week) ──
    try:
        alerts = storage.list_news_alerts(impact="high", limit=30)
        # Filter to last week.
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        snapshot["high_impact_news"] = [
            {
                "ticker": a["ticker"],
                "headline": a["headline"],
                "published_at": a.get("published_at"),
                "impact_score": a.get("impact_score"),
                "url": a.get("url"),
            }
            for a in alerts
            if (a.get("published_at") or "") > cutoff
        ][:20]
    except Exception:
        snapshot["high_impact_news"] = []

    # ── Smart-money overlaps for held tickers ──
    try:
        snapshot["smart_money_overlaps"] = []
        for p in snapshot.get("positions", []):
            t = p["ticker"]
            try:
                s = storage.smart_money_summary_for_ticker(t)
                if s.get("manager_count", 0) > 0:
                    snapshot["smart_money_overlaps"].append({
                        "ticker": t,
                        "manager_count": s["manager_count"],
                        "total_value": s["total_value"],
                        "top_managers": s.get("top_managers", [])[:3],
                    })
            except Exception:
                continue
    except Exception:
        pass

    return snapshot


def _render_context_md(snapshot: Dict[str, Any]) -> str:
    """Turn the JSON snapshot into a compact markdown block suitable
    for pasting into an LLM prompt. Keeps the LLM grounded in the
    actual data."""
    lines: List[str] = []
    lines.append(f"# Portfolio context snapshot — as of {snapshot.get('as_of', 'unknown')}")
    lines.append("")

    pos = snapshot.get("positions", []) or []
    if pos:
        lines.append(f"## Open positions ({len(pos)})")
        for p in pos:
            cost = p["shares"] * p["cost_basis_per_share"]
            lines.append(
                f"- **{p['ticker']}** — {p['shares']:g} sh @ ${p['cost_basis_per_share']:.2f}"
                f" cost (${cost:,.0f}), opened {p.get('opened_at') or 'n/a'}"
                f"{', ' + p['account'] if p.get('account') else ''}"
            )
        lines.append("")

    wl = snapshot.get("watchlist", []) or []
    if wl:
        lines.append(f"## Watchlist: {', '.join(wl)}")
        lines.append("")

    runs = snapshot.get("recent_runs", []) or []
    if runs:
        lines.append(f"## Recent analysis runs ({len(runs)})")
        for r in runs[:20]:
            lines.append(
                f"- {r['trade_date']} **{r['ticker']}** → {r.get('decision') or 'no decision'}"
                f" · {r.get('provider') or '?'}/{r.get('deep_model') or '?'}"
                f" · run_id={r['run_id'][:8]}…"
            )
        lines.append("")

    trades = snapshot.get("recent_trades", []) or []
    if trades:
        lines.append(f"## Recent trades ({len(trades)})")
        for t in trades[:15]:
            price_s = f"@ ${t['price']:.2f}" if t.get("price") else ""
            link_s = f" (linked to run {t['linked_run_id'][:8]}…)" if t.get("linked_run_id") else ""
            lines.append(
                f"- {t['executed_at']} {t['action']} {t['shares']:g} {t['ticker']} {price_s}{link_s}"
            )
        lines.append("")

    restr = snapshot.get("active_restrictions", []) or []
    if restr:
        lines.append(f"## Currently restricted ({len(restr)})")
        for r in restr:
            if r.get("kind") == "earnings_window":
                lines.append(
                    f"- {r['ticker']}: earnings_window — open"
                    f" {r.get('earnings_window_open_offset_days', '?')}d post-earnings"
                    f" for {r.get('earnings_window_duration_days', '?')}d"
                )
            elif r.get("kind") == "earnings_blackout":
                lines.append(
                    f"- {r['ticker']}: earnings_blackout"
                    f" ({r.get('earnings_days_before', 0)}d pre, {r.get('earnings_days_after', 0)}d post)"
                )
            else:
                lines.append(f"- {r['ticker']}: {r.get('kind') or 'restriction'} — {r.get('reason') or ''}")
        lines.append("")

    news = snapshot.get("high_impact_news", []) or []
    if news:
        lines.append(f"## High-impact news (last 7 days, {len(news)} items)")
        for n in news[:10]:
            lines.append(f"- {n['ticker']}: {n['headline'][:120]}")
        lines.append("")

    sm = snapshot.get("smart_money_overlaps", []) or []
    if sm:
        lines.append(f"## Smart-money overlap with your holdings ({len(sm)})")
        for s in sm:
            mgrs = ", ".join(m["name"] for m in s.get("top_managers", []))
            lines.append(
                f"- {s['ticker']}: {s['manager_count']} institutional holders"
                f" (${s['total_value']/1e9:.1f}B combined) — top: {mgrs}"
            )
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# Sync mode — direct Anthropic API call
# ──────────────────────────────────────────────────────────────────────


def _try_sync_answer(question: str, context_md: str) -> tuple[Optional[str], Optional[int], Optional[int]]:
    """Attempt a sync answer via the Anthropic SDK. Returns
    (answer, tokens_in, tokens_out) on success, (None, None, None) on
    any failure (missing SDK, missing key, API error). Caller should
    fall back to queue mode if this returns None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("sync /ask: no ANTHROPIC_API_KEY set; falling back to queue mode")
        return None, None, None
    try:
        import anthropic
    except ImportError:
        logger.info("sync /ask: anthropic SDK not installed; falling back to queue mode")
        return None, None, None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        # Use the cheaper Haiku model by default since these are short
        # context-grounded answers, not deep research. The system
        # prompt enforces engineer-friendly vocabulary consistent with
        # the rest of the app.
        system_prompt = (
            "You are answering questions about a self-managed personal portfolio. "
            "The audience is a mechanical engineer — they understand percentages, "
            "ratios, units, and stats but NOT Wall Street jargon. When using terms "
            "like alpha / beta / EV/EBITDA, put a plain-English translation in "
            "parentheses immediately after. Concrete numbers always; ground every "
            "claim in the context snapshot below. If the snapshot doesn't contain "
            "what's needed, say so honestly rather than guessing.\n\n"
            "Format the answer as concise markdown — short paragraphs and bullet "
            "points. Never disclaim 'not financial advice' — the app already says "
            "that once at the top level."
        )
        user_prompt = f"{context_md}\n\n---\n\n**Question:** {question}"
        resp = client.messages.create(
            model=os.environ.get("ASK_SYNC_MODEL", "claude-haiku-4-5"),
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # Extract text + token counts
        answer = "".join(
            block.text for block in resp.content if hasattr(block, "text")
        )
        tokens_in = getattr(resp.usage, "input_tokens", None)
        tokens_out = getattr(resp.usage, "output_tokens", None)
        return answer, tokens_in, tokens_out
    except Exception as e:
        logger.warning(f"sync /ask Anthropic call failed: {e}")
        return None, None, None


# ──────────────────────────────────────────────────────────────────────
# Models + endpoints
# ──────────────────────────────────────────────────────────────────────


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    conversation_id: Optional[str] = Field(
        default=None,
        description="Pass to continue an existing thread. Omit to start a new one.",
    )
    mode: str = Field(
        default="queue",
        description="'queue' (free, async, Claude Desktop) or 'sync' "
                    "(Anthropic API, instant, costs tokens; falls back to "
                    "queue if SDK/key unavailable)",
    )


class QuestionRow(BaseModel):
    id: int
    conversation_id: str
    question: str
    answer_md: Optional[str] = None
    mode: str
    status: str
    source: Optional[str] = None
    queue_id: Optional[str] = None
    error_message: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    requested_at: str
    answered_at: Optional[str] = None


def _row(d: dict) -> QuestionRow:
    return QuestionRow(
        id=d["id"], conversation_id=d["conversation_id"],
        question=d["question"], answer_md=d.get("answer_md"),
        mode=d["mode"], status=d["status"], source=d.get("source"),
        queue_id=d.get("queue_id"), error_message=d.get("error_message"),
        tokens_in=d.get("tokens_in"), tokens_out=d.get("tokens_out"),
        requested_at=d["requested_at"], answered_at=d.get("answered_at"),
    )


@router.post("", response_model=QuestionRow)
def ask(req: AskRequest) -> QuestionRow:
    """Submit a question. Queue mode (default) returns immediately with
    status='pending'; the answer appears later via Claude Desktop drain.
    Sync mode blocks for ~2-10s and returns the answer inline."""
    if req.mode not in ("queue", "sync"):
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {req.mode!r}; must be 'queue' or 'sync'",
        )

    # Snapshot the portfolio state at ask-time.
    snapshot = _build_context_snapshot()
    snapshot_json = json.dumps(snapshot)
    context_md = _render_context_md(snapshot)

    # Resolve conversation_id.
    conv_id = req.conversation_id or storage.new_conversation_id()

    if req.mode == "sync":
        # Insert as pending first so we have an ID even on failure.
        row = storage.insert_question(
            conversation_id=conv_id, question=req.question,
            mode="sync", context_snapshot_json=snapshot_json,
        )
        answer, tokens_in, tokens_out = _try_sync_answer(req.question, context_md)
        if answer:
            completed = storage.complete_question(
                row["id"], answer_md=answer, source="anthropic-api",
                tokens_in=tokens_in, tokens_out=tokens_out,
            )
            return _row(completed)
        else:
            # Fall through to queue mode.
            logger.info(
                "sync /ask falling back to queue mode for question id=%s", row["id"]
            )
            queued = storage.queue_request(
                ticker="_PORTFOLIO",   # synthetic, not a real ticker
                trade_date=date.today().isoformat(),
                mode="ask_portfolio",
                options={
                    "question_id": row["id"],
                    "question": req.question,
                    "conversation_id": conv_id,
                    "context_md": context_md,
                },
                requested_by="web-ui:ask",
                priority=5,
            )
            storage.attach_queue_id(row["id"], queued["id"])
            # Update mode to reflect the fallback.
            with storage._conn() as c:
                c.execute(
                    "UPDATE portfolio_questions SET mode='queue' WHERE id=?",
                    (row["id"],),
                )
            return _row(storage.get_question(row["id"]))

    # Queue mode (default).
    row = storage.insert_question(
        conversation_id=conv_id, question=req.question,
        mode="queue", context_snapshot_json=snapshot_json,
    )
    queued = storage.queue_request(
        ticker="_PORTFOLIO",
        trade_date=date.today().isoformat(),
        mode="ask_portfolio",
        options={
            "question_id": row["id"],
            "question": req.question,
            "conversation_id": conv_id,
            "context_md": context_md,
        },
        requested_by="web-ui:ask",
        priority=5,
    )
    storage.attach_queue_id(row["id"], queued["id"])
    return _row(storage.get_question(row["id"]))


@router.get("/conversations")
def list_conversations(limit: int = 50) -> List[Dict[str, Any]]:
    """Recent threads, newest first. Each row has the first question
    as a preview + a total turn count."""
    return storage.list_conversations(limit=limit)


@router.get("/conversation/{conversation_id}", response_model=List[QuestionRow])
def get_conversation(conversation_id: str) -> List[QuestionRow]:
    return [_row(r) for r in storage.list_conversation(conversation_id)]


@router.get("/{question_id}", response_model=QuestionRow)
def get_question(question_id: int) -> QuestionRow:
    row = storage.get_question(question_id)
    if not row:
        raise HTTPException(status_code=404, detail="question not found")
    return _row(row)


class AnswerSubmission(BaseModel):
    answer_md: str = Field(min_length=1)
    source: str = Field(default="claude-desktop")
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None


@router.post("/{question_id}/answer", response_model=QuestionRow)
def submit_answer(question_id: int, req: AnswerSubmission) -> QuestionRow:
    """Used by Claude Desktop after it processes an ask_portfolio queue
    item. Marks the question complete and writes the answer."""
    existing = storage.get_question(question_id)
    if not existing:
        raise HTTPException(status_code=404, detail="question not found")
    row = storage.complete_question(
        question_id,
        answer_md=req.answer_md, source=req.source,
        tokens_in=req.tokens_in, tokens_out=req.tokens_out,
    )
    # Mark the linked run_queue item complete as well so it disappears
    # from /queue pending. The skill normally does this directly via
    # POST /run-queue/{id}/complete but we wire it here too as a
    # belt-and-braces.
    if existing.get("queue_id"):
        try:
            storage.complete_queue_item(existing["queue_id"], result_run_id=None)
        except Exception:
            pass
    return _row(row)
