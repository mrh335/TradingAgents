"""Queue drainer — server-side worker that auto-processes light-mode
queue items via the Anthropic API.

Why this exists
---------------
The web app's scheduler PUSHES items into ``run_queue`` (e.g. via cron
schedules or /ask submissions). Claude Desktop PULLS them when invoked.
Without an active CD session, items sit indefinitely. This drainer
solves that for the lightweight modes — ones where the LLM call is
short, cheap, and well-bounded:

- ``ask_portfolio`` — already has a pre-rendered ``context_md`` block;
  one Anthropic call with system prompt + question. ~5k tokens in,
  ~1k tokens out. Cents per call on Haiku.

- ``earnings_summary`` — fetch /earnings/{ticker} for the structured
  context, build a digest via one Anthropic call. Similar cost.

Heavy modes (``analyze``, ``deep_dive``, etc.) are intentionally
LEFT for Claude Desktop or the per-item "Process now" button — those
multi-agent pipelines are expensive enough that always-on auto-drain
would be a meaningful cost.

Configuration
-------------
Reads ``defaults.auto_drain_enabled`` from gui/config.json (set via
PUT /settings or the /queue UI toggle). Default: false — opt-in.

Reads ``ANTHROPIC_API_KEY`` from env. If unset, the drainer logs a
warning and idles (re-checks every tick).

Reads ``ASK_SYNC_MODEL`` from env for the model name. Default:
``claude-haiku-4-5`` (cheapest + fast enough for these tasks).

Concurrency
-----------
Uses ``storage.claim_queue_items`` which atomically updates rows from
'pending' to 'claimed' — safe against parallel CD-skill invocations.
A claim names the worker ``server-drainer:{hostname}`` so the user
can tell server-side claims from CD claims in /queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Any, Dict, List, Optional

from gui import storage

logger = logging.getLogger(__name__)

LIGHT_MODES = ("ask_portfolio", "earnings_summary")


def _worker_id() -> str:
    """Identifier stamped on claimed_by — distinguishes server drains
    from Claude Desktop drains in the UI."""
    return f"server-drainer:{socket.gethostname()[:32]}"


def _auto_drain_enabled() -> bool:
    """Read the user's auto-drain toggle from gui/config.json."""
    try:
        from gui.config import load
        cfg = load()
        return bool(cfg.get("defaults", {}).get("auto_drain_enabled", False))
    except Exception as e:
        logger.warning(f"auto_drain config read failed: {e}")
        return False


def _api_key() -> Optional[str]:
    return os.environ.get("ANTHROPIC_API_KEY") or None


def _model_name() -> str:
    return os.environ.get("ASK_SYNC_MODEL", "claude-haiku-4-5")


# ──────────────────────────────────────────────────────────────────────
# Per-mode handlers — each takes one claimed queue row, processes it,
# POSTs the result back, returns (success: bool, error_msg: str|None).
# ──────────────────────────────────────────────────────────────────────


def _call_anthropic(system: str, user: str,
                     max_tokens: int = 2048) -> tuple[Optional[str], Optional[int], Optional[int]]:
    """Wrap an Anthropic Messages call. Returns (text, in, out) on
    success, (None, None, None) on any failure (SDK missing, network,
    rate limit). Caller is responsible for marking the queue item
    failed if this returns None."""
    try:
        import anthropic
    except ImportError:
        logger.error("queue_drainer: anthropic SDK not installed")
        return None, None, None
    try:
        client = anthropic.Anthropic(api_key=_api_key())
        resp = client.messages.create(
            model=_model_name(),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in resp.content if hasattr(block, "text"))
        return text, getattr(resp.usage, "input_tokens", None), getattr(resp.usage, "output_tokens", None)
    except Exception as e:
        logger.warning(f"queue_drainer Anthropic call failed: {e}")
        return None, None, None


def _handle_ask_portfolio(item: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Process an ask_portfolio queue item: read the question + pre-built
    context_md from options, call Anthropic, POST the answer back via
    storage.complete_question, mark the queue item done."""
    try:
        opts = json.loads(item.get("options_json") or "{}")
    except (TypeError, ValueError):
        opts = {}
    question_id = opts.get("question_id")
    question = opts.get("question")
    context_md = opts.get("context_md")
    if not question_id or not question:
        return False, "missing question_id or question in queue options"

    system = (
        "You are answering questions about a self-managed personal portfolio. "
        "The audience is a mechanical engineer — they understand percentages, "
        "ratios, units, and stats but NOT Wall Street jargon. When using terms "
        "like alpha / beta / EV/EBITDA, put a plain-English translation in "
        "parentheses immediately after. Concrete numbers always; ground every "
        "claim in the context snapshot below. If the snapshot doesn't contain "
        "what's needed, say so honestly rather than guessing. Format the "
        "answer as concise markdown."
    )
    user = f"{context_md or ''}\n\n---\n\n**Question:** {question}"
    answer, tin, tout = _call_anthropic(system, user, max_tokens=2048)
    if not answer:
        return False, "Anthropic call failed (see api logs)"

    try:
        storage.complete_question(
            int(question_id), answer_md=answer,
            source="server-drainer", tokens_in=tin, tokens_out=tout,
        )
    except Exception as e:
        return False, f"complete_question failed: {e}"
    return True, None


def _handle_earnings_summary(item: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Process an earnings_summary queue item: fetch the structured
    earnings data, call Anthropic to generate bullets + structured
    comparison, upsert to earnings_summaries."""
    try:
        opts = json.loads(item.get("options_json") or "{}")
    except (TypeError, ValueError):
        opts = {}
    ticker = (opts.get("ticker") or item["ticker"]).upper()
    report_date = opts.get("report_date") or item.get("trade_date")
    if not ticker or not report_date:
        return False, "missing ticker or report_date"

    # Pull the structured data the same way the /earnings endpoint does.
    try:
        from service.routers.earnings import (
            _fetch_earnings_history,
            _fetch_revisions,
            _fetch_recommendations,
        )
        history, latest = _fetch_earnings_history(ticker)
        revisions = _fetch_revisions(ticker)
        recs = _fetch_recommendations(ticker)
    except Exception as e:
        return False, f"earnings data fetch failed: {e}"

    if not latest:
        return False, f"no reported earnings found for {ticker}"

    # Build a tight context block for the LLM.
    parts: List[str] = []
    parts.append(f"# {ticker} — earnings context")
    parts.append(f"Latest reported: {latest.report_date}")
    parts.append(
        f"- EPS actual {latest.eps_actual} vs estimate {latest.eps_estimate}"
        f" (surprise {latest.eps_surprise_pct}%)"
    )
    if latest.revenue_actual:
        parts.append(
            f"- Revenue ${latest.revenue_actual/1e9:.2f}B"
            f" vs est ${(latest.revenue_estimate or 0)/1e9:.2f}B"
        )
    parts.append("\nRecent quarters:")
    for h in history[:4]:
        parts.append(
            f"- {h.report_date}: EPS {h.eps_actual} (est {h.eps_estimate},"
            f" surprise {h.eps_surprise_pct}%)"
        )
    parts.append("\nAnalyst EPS estimate revisions:")
    for r in revisions:
        parts.append(
            f"- {r.horizon}: current ${r.current_estimate},"
            f" {r.up_last_30d or 0} up / {r.down_last_30d or 0} down in 30d"
            f" → direction: {r.direction}"
        )
    parts.append("\nRecommendation mix (most recent):")
    if recs:
        r0 = recs[0]
        parts.append(
            f"- strong_buy={r0.strong_buy}, buy={r0.buy}, hold={r0.hold},"
            f" sell={r0.sell}, strong_sell={r0.strong_sell}"
        )
    context = "\n".join(parts)

    system = (
        "You are summarizing a stock's most recent earnings report for a "
        "mechanical engineer who doesn't know Wall Street vocabulary. "
        "Output TWO blocks:\n\n"
        "BLOCK 1: 3-5 plain-English markdown bullets. Lead with the "
        "headline result. Concrete numbers always. Final bullet says "
        "what this means for the stock direction.\n\n"
        "BLOCK 2: A JSON object inside ```json ... ``` fences with keys: "
        "headline (eps_actual, eps_estimate, eps_surprise_pct, "
        "revenue_actual_usd, revenue_estimate_usd), vs_prior_quarter "
        "(revenue_growth_qoq_pct, eps_growth_qoq_pct if computable), "
        "vs_year_ago (revenue_growth_yoy_pct, eps_growth_yoy_pct if "
        "computable), analyst_revisions (direction). Skip fields you "
        "can't compute from the context.\n\n"
        "No 'not financial advice' disclaimer."
    )
    answer, tin, tout = _call_anthropic(system, context, max_tokens=2048)
    if not answer:
        return False, "Anthropic call failed"

    # Split bullets from the JSON block.
    bullets_md = answer
    structured: Optional[Dict[str, Any]] = None
    if "```json" in answer:
        try:
            before, rest = answer.split("```json", 1)
            json_str, after = rest.split("```", 1)
            bullets_md = (before + after).strip()
            structured = json.loads(json_str.strip())
        except (ValueError, json.JSONDecodeError):
            pass

    try:
        storage.upsert_earnings_summary(
            ticker=ticker, report_date=report_date,
            bullets_md=bullets_md,
            structured_json=json.dumps(structured) if structured else None,
            source="server-drainer", status="complete",
        )
    except Exception as e:
        return False, f"upsert_earnings_summary failed: {e}"
    return True, None


HANDLERS = {
    "ask_portfolio": _handle_ask_portfolio,
    "earnings_summary": _handle_earnings_summary,
}


# ──────────────────────────────────────────────────────────────────────
# Tick + run loop
# ──────────────────────────────────────────────────────────────────────


def _tick() -> Dict[str, Any]:
    """One drain pass. Returns a summary for logging."""
    summary: Dict[str, Any] = {
        "checked": 0, "processed": 0, "failed": 0, "skipped_no_key": 0,
        "skipped_disabled": 0,
    }
    if not _auto_drain_enabled():
        summary["skipped_disabled"] += 1
        return summary
    if not _api_key():
        summary["skipped_no_key"] += 1
        logger.warning("queue_drainer: ANTHROPIC_API_KEY not set; idle")
        return summary

    worker = _worker_id()
    # Reclaim stale claims (CD crashed mid-process > 30 min ago)
    try:
        storage.reclaim_stale_queue_items(older_than_seconds=1800)
    except Exception as e:
        logger.warning(f"reclaim_stale failed: {e}")

    for mode in LIGHT_MODES:
        try:
            claimed = storage.claim_queue_items(claimed_by=worker, max_items=5, mode=mode)
        except Exception as e:
            logger.warning(f"claim {mode} failed: {e}")
            continue
        for item in claimed:
            summary["checked"] += 1
            handler = HANDLERS.get(mode)
            if not handler:
                storage.fail_queue_item(item["id"], error=f"no handler for mode {mode}")
                summary["failed"] += 1
                continue
            try:
                ok, err = handler(item)
                if ok:
                    storage.complete_queue_item(item["id"], result_run_id=None)
                    summary["processed"] += 1
                else:
                    storage.fail_queue_item(item["id"], error=err or "unknown error")
                    summary["failed"] += 1
            except Exception as e:
                logger.exception(f"queue_drainer handler crashed on {item.get('id')}: {e}")
                try:
                    storage.fail_queue_item(item["id"], error=f"handler crash: {e}")
                except Exception:
                    pass
                summary["failed"] += 1
    return summary


def process_one(queue_id: str) -> Dict[str, Any]:
    """Synchronously process a SINGLE queue item by id. Used by the
    per-item 'Process now' button in /queue UI — bypasses the
    enabled-flag check (user explicitly asked) but still requires
    ANTHROPIC_API_KEY.

    Handles light AND heavy modes; for analyze/deep_dive the user is
    explicitly opting in to API cost.
    """
    if not _api_key():
        raise RuntimeError("ANTHROPIC_API_KEY is not set in the api container env")

    # Claim this specific item atomically. We do the claim with a
    # status-conditioned UPDATE to avoid racing with a CD drain.
    item = storage.get_queue_item(queue_id)
    if not item:
        raise RuntimeError(f"queue item {queue_id} not found")
    if item["status"] != "pending":
        raise RuntimeError(
            f"queue item {queue_id} is already {item['status']} — can't re-claim"
        )

    worker = _worker_id() + ":manual"
    # The atomic claim — relies on storage._conn() under the hood.
    with storage._conn() as c:
        cur = c.execute(
            """UPDATE run_queue SET status='claimed', claimed_by=?, claimed_at=?
               WHERE id=? AND status='pending'""",
            (worker, storage._now(), queue_id),
        )
        if cur.rowcount == 0:
            raise RuntimeError(
                f"queue item {queue_id} was claimed by someone else between read and claim"
            )
    item = storage.get_queue_item(queue_id)
    if item is None:
        raise RuntimeError("post-claim re-read returned None")

    mode = item["mode"]
    handler = HANDLERS.get(mode)
    if not handler:
        # Heavy mode (analyze etc.) — explicitly opt-in. We don't have
        # a light handler. Raise so the user knows we can't auto-process
        # this via the drainer (it'd need the full multi-agent pipeline).
        storage.fail_queue_item(
            queue_id,
            error=f"no server-side handler for mode {mode!r} — heavy modes "
                  "require Claude Desktop or the local CLI.",
        )
        raise RuntimeError(
            f"Cannot process mode {mode!r} server-side. Heavy modes "
            "(analyze, deep_dive) require the multi-agent pipeline that "
            "only runs locally via Claude Desktop. Open CD and invoke the "
            "tradingagents-analyze skill to drain this item."
        )

    ok, err = handler(item)
    if not ok:
        storage.fail_queue_item(queue_id, error=err or "unknown")
        raise RuntimeError(err or "processing failed")
    storage.complete_queue_item(queue_id)
    return {"queue_id": queue_id, "mode": mode, "status": "done"}


async def run(interval_seconds: int = 300) -> None:
    """Run the drainer loop forever. Spawn as an asyncio task in app
    startup.

    Tick cadence: 5 minutes by default. Tighter = faster auto-drain
    but more wasted polls (DB query every N seconds even when idle).
    """
    logger.info("queue_drainer started (interval=%ds)", interval_seconds)
    # Short initial delay so app fully boots before we start polling.
    await asyncio.sleep(20)
    while True:
        try:
            summary = await asyncio.to_thread(_tick)
            if summary.get("processed") or summary.get("failed"):
                logger.info(
                    "queue_drainer tick: processed=%d failed=%d",
                    summary["processed"], summary["failed"],
                )
        except Exception as e:
            logger.exception("queue_drainer tick crashed: %s", e)
        await asyncio.sleep(interval_seconds)
