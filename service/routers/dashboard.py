"""Dashboard endpoints — portfolio-level cross-cutting views.

Lives outside the per-resource routers (runs, briefs, queue, etc.)
because it joins multiple sources to answer "what's the state of my
book and how stale is the analysis."

Endpoints
---------
GET /dashboard/freshness        — per-ticker last-run timestamp + days since
GET /dashboard/recommendations  — synthesizes positions + latest briefs +
                                  restrictions into a portfolio-level action
                                  plan (deterministic rules, no LLM call).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gui import sidecars as sidecars_helpers
from gui import storage

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Ticker → sector map for the most-common tickers we expect in the book.
# Skips a yfinance roundtrip per page load. Unknown tickers fall back to
# "Other" so the totals still add up.
# ---------------------------------------------------------------------------
_SECTOR_MAP: Dict[str, str] = {
    # Technology
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AVGO": "Technology", "AMD": "Technology", "INTC": "Technology",
    "ASML": "Technology", "ARM": "Technology", "ORCL": "Technology",
    "CSCO": "Technology", "ADBE": "Technology", "CRM": "Technology",
    "QCOM": "Technology", "TXN": "Technology", "MU": "Technology",
    # Communication Services
    "GOOGL": "Communication Services", "GOOG": "Communication Services",
    "META": "Communication Services", "NFLX": "Communication Services",
    "DIS": "Communication Services", "T": "Communication Services",
    "VZ": "Communication Services",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "MCD": "Consumer Discretionary",
    "NKE": "Consumer Discretionary", "RIVN": "Consumer Discretionary",
    "LCID": "Consumer Discretionary",
    # Consumer Staples
    "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples",
    "WMT": "Consumer Staples", "COST": "Consumer Staples",
    # Financials
    "JPM": "Financials", "BAC": "Financials", "GS": "Financials",
    "MS": "Financials", "V": "Financials", "MA": "Financials",
    "PYPL": "Financials",
    # Healthcare
    "UNH": "Healthcare", "JNJ": "Healthcare", "LLY": "Healthcare",
    "PFE": "Healthcare", "ABBV": "Healthcare", "TMO": "Healthcare",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    # Industrials
    "MOD": "Industrials", "CAT": "Industrials", "BA": "Industrials",
    "GE": "Industrials", "RTX": "Industrials",
    # Utilities
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities",
    # Real Estate
    "PLD": "Real Estate", "AMT": "Real Estate",
    # Materials
    "LIN": "Materials", "APD": "Materials",
}


def _sector_for(ticker: str) -> str:
    return _SECTOR_MAP.get(ticker.upper(), "Other")


class FreshnessRow(BaseModel):
    ticker: str
    shares: float
    last_run_id: Optional[str] = None
    last_run_date: Optional[str] = None       # ISO date the analysis covered
    last_run_completed_at: Optional[str] = None  # actual run completion timestamp
    days_since: Optional[int] = None          # whole days since the last run
    last_decision: Optional[str] = None       # decision from the most recent run
    last_provider: Optional[str] = None
    runs_total: int = 0                       # how many runs we've done on this ticker total


def _days_between(iso_str: Optional[str]) -> Optional[int]:
    """Days between today (UTC) and an ISO timestamp/date string."""
    if not iso_str:
        return None
    try:
        # Accept full ISO timestamps or YYYY-MM-DD.
        if "T" in iso_str:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return int(delta.total_seconds() // 86400)
    except (ValueError, TypeError):
        return None


@router.get("/freshness", response_model=List[FreshnessRow])
def freshness(
    include_watchlist: bool = True,
    include_positions: bool = True,
) -> List[FreshnessRow]:
    """Per-ticker view of last-run age.

    Builds the ticker universe from current open positions + watchlist
    (configurable), then joins the latest done run per ticker. Tickers
    with no runs yet show ``last_run_*=None`` and ``days_since=None`` so
    the UI can highlight them as "never analyzed".
    """
    tickers: set[str] = set()
    pos_by_ticker: dict[str, float] = {}
    if include_positions:
        for p in storage.list_positions(include_closed=False):
            t = (p.get("ticker") or "").upper()
            if t:
                tickers.add(t)
                pos_by_ticker[t] = pos_by_ticker.get(t, 0.0) + float(p.get("shares") or 0)
    if include_watchlist:
        try:
            for w in storage.list_watchlist():
                t = (w.get("ticker") or "").upper()
                if t:
                    tickers.add(t)
        except Exception:
            pass

    rows: List[FreshnessRow] = []
    for ticker in sorted(tickers):
        all_runs = [
            r for r in storage.list_runs(ticker=ticker, limit=200)
            if (r.get("status") or "").lower() == "done"
        ]
        if all_runs:
            latest = all_runs[0]  # list_runs is ORDER BY started_at DESC
            completed = latest.get("completed_at") or latest.get("started_at")
            rows.append(FreshnessRow(
                ticker=ticker,
                shares=pos_by_ticker.get(ticker, 0.0),
                last_run_id=latest["run_id"],
                last_run_date=latest.get("trade_date"),
                last_run_completed_at=completed,
                days_since=_days_between(completed),
                last_decision=latest.get("decision"),
                last_provider=latest.get("provider"),
                runs_total=len(all_runs),
            ))
        else:
            rows.append(FreshnessRow(
                ticker=ticker,
                shares=pos_by_ticker.get(ticker, 0.0),
                runs_total=0,
            ))

    # Sort: positions first (by shares desc), then watchlist-only (by ticker).
    rows.sort(key=lambda r: (
        0 if r.shares > 0 else 1,
        -r.shares if r.shares > 0 else 0,
        r.ticker,
    ))
    return rows


# ---------------------------------------------------------------------------
# Recommendations — synthesise positions + briefs + restrictions into a
# portfolio-level action plan via deterministic rules.
# ---------------------------------------------------------------------------

class PositionAction(BaseModel):
    ticker: str
    shares: float
    cost_basis: float                  # weighted average $/share (NOT total)
    cost_basis_total: float            # shares * cost_basis (estimate of book value)
    sector: str
    weight_pct: float                  # % of total book value (at cost basis)
    latest_decision: Optional[str] = None
    latest_action_plain: Optional[str] = None
    latest_tldr: Optional[str] = None
    latest_run_id: Optional[str] = None
    days_since: Optional[int] = None
    restriction_active: bool = False
    restriction_reason: Optional[str] = None
    action: str                        # maintain | trim | add | exit | refresh | blocked
    priority: str                      # high | medium | low | info
    rationale: str                     # plain-English explanation


class PortfolioObservation(BaseModel):
    kind: str                          # concentration | sector_gap | stale | restriction | cash
    priority: str
    summary: str
    detail: Optional[str] = None


class RecommendationsResponse(BaseModel):
    generated_at: str
    portfolio_summary: Dict[str, Any]
    positions: List[PositionAction]
    sector_mix: Dict[str, float]       # sector → % of book at cost basis
    observations: List[PortfolioObservation]
    action_priority: List[Dict[str, str]]   # ordered list for the top of the page


def _read_brief_sidecar(archive_path: str) -> Optional[Dict[str, Any]]:
    """Best-effort read of a run's brief.json sidecar."""
    if not archive_path:
        return None
    try:
        path = sidecars_helpers.sidecar_path(archive_path, "brief.json")
        if not Path(path).exists():
            return None
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _days_since(iso_str: Optional[str]) -> Optional[int]:
    return _days_between(iso_str)


@router.get("/recommendations", response_model=RecommendationsResponse)
def recommendations() -> RecommendationsResponse:
    """Synthesise positions + latest briefs + restrictions into a
    portfolio-level action sheet.

    Pure deterministic rules — no LLM call. Idea is to surface the most
    obvious actions (concentration trims, exit-on-Sell, refresh-on-stale)
    so the user knows where to focus when they sit down with the book.
    """
    positions = storage.list_positions(include_closed=False)
    today_iso = date.today().isoformat()
    restrictions_today = storage.list_restrictions(active_on=today_iso)
    restriction_map: Dict[str, Dict[str, Any]] = {}
    for r in restrictions_today:
        restriction_map.setdefault(r["ticker"].upper(), r)

    # Aggregate positions by ticker (a ticker can have multiple lots / accounts).
    by_ticker: Dict[str, Dict[str, Any]] = {}
    for p in positions:
        t = (p["ticker"] or "").upper()
        if not t:
            continue
        b = by_ticker.setdefault(t, {"shares": 0.0, "basis_total": 0.0, "lots": []})
        b["shares"] += float(p["shares"])
        b["basis_total"] += float(p["shares"]) * float(p["cost_basis_per_share"])
        b["lots"].append(p)

    total_basis = sum(b["basis_total"] for b in by_ticker.values())

    # Per-ticker action.
    action_rows: List[PositionAction] = []
    for ticker, bucket in by_ticker.items():
        sector = _sector_for(ticker)
        weight = (bucket["basis_total"] / total_basis * 100) if total_basis > 0 else 0
        avg_basis = bucket["basis_total"] / bucket["shares"] if bucket["shares"] > 0 else 0

        # Find latest done run for this ticker + read its brief sidecar.
        latest_run = None
        for r in storage.list_runs(ticker=ticker, limit=20):
            if (r.get("status") or "").lower() == "done":
                latest_run = r
                break

        days = _days_since(latest_run.get("completed_at") if latest_run else None)
        decision = latest_run.get("decision") if latest_run else None

        brief = _read_brief_sidecar(latest_run.get("log_path") if latest_run else "")
        action_plain = brief.get("action_plain") if brief else None
        tldr = brief.get("tldr") if brief else None

        restriction = restriction_map.get(ticker)
        restriction_active = restriction is not None
        restriction_reason = (
            f"{(restriction.get('kind') or 'blackout').replace('_', ' ')}: "
            f"{(restriction.get('reason') or 'no reason given')}"
            if restriction else None
        )

        # Decision tree (cheap deterministic rules).
        action = "maintain"
        priority = "low"
        rationale_parts: List[str] = []

        if restriction_active:
            action = "blocked"
            priority = "info"
            rationale_parts.append(
                "Trading restriction active — agent forced to Hold regardless of signal."
            )
        elif weight > 25:
            action = "trim"
            priority = "high"
            rationale_parts.append(
                f"Concentration alert: {weight:.0f}% of book in a single name. "
                "Consider trimming to ≤15% to cap idiosyncratic risk."
            )
        elif weight > 15 and decision in ("Underweight", "Sell"):
            action = "trim"
            priority = "high"
            rationale_parts.append(
                f"Latest brief says {decision} and you hold {weight:.0f}%. "
                "Reduce exposure."
            )
        elif decision == "Sell":
            action = "exit"
            priority = "high"
            rationale_parts.append(f"Latest brief says Sell — exit the position.")
        elif decision == "Underweight":
            action = "trim"
            priority = "medium"
            rationale_parts.append(f"Latest brief says Underweight — trim ~half.")
        elif decision == "Buy" and weight < 3:
            action = "add"
            priority = "medium"
            rationale_parts.append(
                f"Latest brief says Buy but position is small ({weight:.1f}%). "
                "Consider adding."
            )
        elif decision == "Overweight" and weight < 8:
            action = "add"
            priority = "medium"
            rationale_parts.append(
                f"Latest brief says Overweight but position is light ({weight:.1f}%). "
                "Consider adding."
            )
        elif decision == "Hold":
            action = "maintain"
            priority = "low"
            rationale_parts.append("Latest brief says Hold — maintain current size.")
        elif latest_run is None:
            action = "refresh"
            priority = "medium"
            rationale_parts.append("Never analyzed. Queue a fresh run.")

        # Staleness flag added separately so it can co-occur with above.
        if days is not None and days > 14:
            if action == "maintain":
                action = "refresh"
                priority = "medium"
            rationale_parts.append(
                f"Analysis is {days} days old — refresh recommended."
            )

        if not rationale_parts:
            rationale_parts.append("No clear signal; maintain current size.")

        action_rows.append(PositionAction(
            ticker=ticker,
            shares=bucket["shares"],
            cost_basis=avg_basis,
            cost_basis_total=bucket["basis_total"],
            sector=sector,
            weight_pct=weight,
            latest_decision=decision,
            latest_action_plain=action_plain,
            latest_tldr=tldr,
            latest_run_id=latest_run.get("run_id") if latest_run else None,
            days_since=days,
            restriction_active=restriction_active,
            restriction_reason=restriction_reason,
            action=action,
            priority=priority,
            rationale=" ".join(rationale_parts),
        ))

    # Sort: high priority first, then by weight desc.
    priority_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    action_rows.sort(key=lambda a: (priority_rank.get(a.priority, 9), -a.weight_pct))

    # Sector mix.
    sector_mix: Dict[str, float] = {}
    for a in action_rows:
        sector_mix[a.sector] = sector_mix.get(a.sector, 0.0) + a.weight_pct
    sector_mix = {k: round(v, 1) for k, v in sorted(sector_mix.items(), key=lambda kv: -kv[1])}

    # Cross-portfolio observations.
    observations: List[PortfolioObservation] = []
    for a in action_rows:
        if a.weight_pct > 25:
            observations.append(PortfolioObservation(
                kind="concentration", priority="high",
                summary=f"{a.ticker} is {a.weight_pct:.0f}% of book — single-name concentration",
                detail=f"Sector: {a.sector}. Consider trimming to ≤15% to cap idiosyncratic risk.",
            ))
    # Sector concentration (>60% in one sector).
    for sector, pct in sector_mix.items():
        if pct > 60 and sector != "Other":
            observations.append(PortfolioObservation(
                kind="sector", priority="medium",
                summary=f"{pct:.0f}% concentrated in {sector}",
                detail="Sector beta tends to dominate — consider diversification toward Healthcare, Financials, or Utilities.",
            ))
    stale_count = sum(1 for a in action_rows if (a.days_since is None or a.days_since > 14))
    if stale_count > 0:
        observations.append(PortfolioObservation(
            kind="stale", priority="medium",
            summary=f"{stale_count} position(s) have stale or missing analysis",
            detail="Queue refreshes from /dashboard or /schedules.",
        ))
    blocked = [a.ticker for a in action_rows if a.restriction_active]
    if blocked:
        observations.append(PortfolioObservation(
            kind="restriction", priority="info",
            summary=f"Trading blocked on: {', '.join(blocked)}",
            detail="Active blackout windows force Hold regardless of signal. Manage at /restrictions.",
        ))

    # Action priority list (top of page).
    action_priority: List[Dict[str, str]] = []
    for a in action_rows:
        if a.priority in ("high", "medium") and a.action != "maintain":
            verb = {
                "trim": "Trim",
                "exit": "Exit",
                "add": "Add",
                "refresh": "Refresh analysis for",
                "blocked": "(blocked)",
            }.get(a.action, a.action.title())
            action_priority.append({
                "priority": a.priority,
                "ticker": a.ticker,
                "verb": verb,
                "summary": a.rationale,
            })

    return RecommendationsResponse(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        portfolio_summary={
            "position_count": len(action_rows),
            "total_value_at_basis": round(total_basis, 2),
            "high_priority_actions": sum(1 for a in action_rows if a.priority == "high"),
            "blocked_tickers": len(blocked),
        },
        positions=action_rows,
        sector_mix=sector_mix,
        observations=observations,
        action_priority=action_priority,
    )
