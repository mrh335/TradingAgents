"""Token-usage stats — aggregate the per-run token counts already in
``runs.tokens_in`` / ``runs.tokens_out`` into time-series shapes the
``/tokens`` page can plot.

No new storage table needed — the runs table already captures token
usage for every completed run, including ones imported via
``/runs/import`` from the Claude Desktop / Claude Code skill (the skill
posts ``metadata.tokens_in`` and ``metadata.tokens_out`` which the
import endpoint pipes into ``storage.update_run_stats``).

Endpoints
---------
GET /tokens/events   — raw per-run rows (date, ticker, provider, in/out, …)
GET /tokens/summary  — bucketed time-series, optionally by provider/ticker
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from gui import storage

router = APIRouter(prefix="/tokens", tags=["tokens"])


# Default rough $/1M tokens (cost estimate). Updated when providers
# publish new pricing — this is for in-app cost projection, not for
# accounting. Values are roughly the cheaper deep-think tier at each
# provider; quick-think tier is typically 5-10x cheaper but we don't
# distinguish in the runs table.
DEFAULT_RATES_USD_PER_M = {
    "anthropic": {"in": 3.00, "out": 15.00},   # sonnet-class
    "openai":    {"in": 2.50, "out": 10.00},   # gpt-4o-class
    "google":    {"in": 1.25, "out": 5.00},
    "xai":       {"in": 2.00, "out": 10.00},
    "deepseek":  {"in": 0.27, "out": 1.10},
    "qwen":      {"in": 0.40, "out": 1.20},
    "glm":       {"in": 0.50, "out": 1.50},
    "openrouter":{"in": 3.00, "out": 15.00},   # varies — approx
    "ollama":    {"in": 0.00, "out": 0.00},    # local = free
    "claude-desktop-skill": {"in": 0.00, "out": 0.00},  # flat sub
}


def _estimate_cost(provider: Optional[str], tokens_in: int, tokens_out: int) -> float:
    rates = DEFAULT_RATES_USD_PER_M.get(
        (provider or "").lower(), {"in": 0.0, "out": 0.0}
    )
    return (tokens_in / 1_000_000) * rates["in"] + (tokens_out / 1_000_000) * rates["out"]


class TokenEvent(BaseModel):
    run_id: str
    ticker: str
    trade_date: str
    provider: Optional[str] = None
    deep_model: Optional[str] = None
    completed_at: Optional[str] = None
    tokens_in: int
    tokens_out: int
    llm_calls: int = 0
    tool_calls: int = 0
    estimated_cost_usd: float


@router.get("/events", response_model=List[TokenEvent])
def list_events(
    since_iso: Optional[str] = Query(
        default=None,
        description="Return only events on or after this ISO timestamp (UTC)",
    ),
    ticker: Optional[str] = None,
    limit: int = 5000,
) -> List[TokenEvent]:
    rows = storage.list_token_events(
        since_iso=since_iso, ticker=ticker, limit=limit,
    )
    return [
        TokenEvent(
            run_id=r["run_id"],
            ticker=r["ticker"],
            trade_date=r["trade_date"],
            provider=r.get("provider"),
            deep_model=r.get("deep_model"),
            completed_at=r.get("completed_at"),
            tokens_in=int(r.get("tokens_in") or 0),
            tokens_out=int(r.get("tokens_out") or 0),
            llm_calls=int(r.get("llm_calls") or 0),
            tool_calls=int(r.get("tool_calls") or 0),
            estimated_cost_usd=round(
                _estimate_cost(
                    r.get("provider"),
                    int(r.get("tokens_in") or 0),
                    int(r.get("tokens_out") or 0),
                ),
                4,
            ),
        )
        for r in rows
    ]


class TokenBucket(BaseModel):
    date: str
    provider: Optional[str] = None
    tokens_in: int
    tokens_out: int
    runs: int
    estimated_cost_usd: float


class TokenSummary(BaseModel):
    buckets: List[TokenBucket]
    totals: Dict[str, float]
    providers: List[str]


@router.get("/summary", response_model=TokenSummary)
def summary(
    days: int = Query(default=30, ge=1, le=730),
    group_by_provider: bool = Query(
        default=True,
        description="If true, returns one bucket per (day, provider). If false, totals across all providers per day.",
    ),
) -> TokenSummary:
    """Daily-bucketed token + cost time series over the last ``days``.

    Used by the /tokens page to render the stacked-bar / line chart.
    All times in the underlying ``runs`` table are UTC ISO strings;
    bucketing is by the date prefix of ``completed_at``.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds"
    ) + "Z"
    rows = storage.list_token_events(since_iso=cutoff, limit=20000)

    # bucket_key -> bucket aggregator
    buckets: Dict[tuple, dict] = {}
    providers_seen: set = set()
    grand_tokens_in = 0
    grand_tokens_out = 0
    grand_cost = 0.0
    grand_runs = 0

    for r in rows:
        completed = r.get("completed_at") or r.get("started_at") or ""
        day = (completed or "")[:10]  # YYYY-MM-DD
        if not day:
            continue
        provider = r.get("provider") or "unknown"
        providers_seen.add(provider)
        key = (day, provider) if group_by_provider else (day, None)

        ti = int(r.get("tokens_in") or 0)
        to = int(r.get("tokens_out") or 0)
        cost = _estimate_cost(provider, ti, to)

        b = buckets.setdefault(
            key,
            {"date": day, "provider": provider if group_by_provider else None,
             "tokens_in": 0, "tokens_out": 0, "runs": 0, "estimated_cost_usd": 0.0},
        )
        b["tokens_in"] += ti
        b["tokens_out"] += to
        b["runs"] += 1
        b["estimated_cost_usd"] += cost

        grand_tokens_in += ti
        grand_tokens_out += to
        grand_cost += cost
        grand_runs += 1

    # Sort by date asc, then provider for deterministic stacking.
    sorted_buckets = sorted(
        buckets.values(),
        key=lambda b: (b["date"], b["provider"] or ""),
    )
    # Round cost for clean JSON.
    for b in sorted_buckets:
        b["estimated_cost_usd"] = round(b["estimated_cost_usd"], 4)

    return TokenSummary(
        buckets=[TokenBucket(**b) for b in sorted_buckets],
        totals={
            "tokens_in": float(grand_tokens_in),
            "tokens_out": float(grand_tokens_out),
            "runs": float(grand_runs),
            "estimated_cost_usd": round(grand_cost, 4),
            "days": float(days),
        },
        providers=sorted(providers_seen),
    )
