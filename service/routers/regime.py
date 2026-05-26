"""Market regime endpoints — current state, history, framework hit-rate by regime.

Three tiers exposed:
  - Tier 1 + 2: GET /regime/snapshot — rule-based regime + Markov transition + 30d forecast
  - Tier 3:    GET /regime/hmm     — HMM-fitted regime assignments + comparison vs tier 1
  - Combined:  GET /regime/runs-by-regime — framework hit rate stratified by regime

All endpoints are read-only and cheap (tier 1/2 cached 1h, tier 3 cached 24h).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from gui import storage
from service import regime as regime_module

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/regime", tags=["regime"])


# ───────────────────────────────────────────────────────────────────────
# Response models
# ───────────────────────────────────────────────────────────────────────


class RegimeSnapshot(BaseModel):
    available: bool
    as_of: Optional[str] = None
    current_regime: Optional[str] = None
    current_label: Optional[str] = None
    current_blurb: Optional[str] = None
    current_spy: Optional[float] = None
    current_vix: Optional[float] = None
    current_sma_200: Optional[float] = None
    regime_order: List[str] = []
    # Tier 2 outputs
    transition_matrix: List[List[float]] = []   # rows = from, cols = to
    stationary: Dict[str, float] = {}
    forecast_30d: Dict[str, float] = {}
    n_days_observed: Optional[int] = None
    error: Optional[str] = None


class HmmSnapshot(BaseModel):
    available: bool
    n_states: Optional[int] = None
    as_of: Optional[str] = None
    current_regime: Optional[str] = None
    regime_order: List[str] = []
    hmm_transition_matrix: List[List[float]] = []   # reordered to REGIMES axis order
    tier1_agreement_pct: Optional[float] = None
    n_days_observed: Optional[int] = None
    error: Optional[str] = None


class RegimePerformanceRow(BaseModel):
    regime: str
    n_runs: int                   # how many completed runs fell in this regime
    n_with_decision: int          # excludes runs without a decision
    decisions: Dict[str, int]     # {"Buy": 5, "Hold": 3, ...}
    mean_alpha_pct: Optional[float] = None     # average +30d alpha across runs in this regime
    hit_rate_pct: Optional[float] = None       # % of directional calls that won


class RegimePerformanceResponse(BaseModel):
    window_days: int               # the evaluation horizon (default 30)
    lookback_days: int             # how far back we scanned runs
    rows: List[RegimePerformanceRow]
    baseline_hit_rate_pct: Optional[float] = None   # framework's overall hit rate (un-stratified)
    baseline_mean_alpha_pct: Optional[float] = None


# ───────────────────────────────────────────────────────────────────────
# Tier 1 + 2 endpoint
# ───────────────────────────────────────────────────────────────────────


@router.get("/snapshot", response_model=RegimeSnapshot)
def get_snapshot(
    lookback_days: int = Query(365 * 5, ge=365, le=365 * 20),
) -> RegimeSnapshot:
    """Tier 1 (rule-based current regime) + Tier 2 (Markov transition matrix,
    stationary distribution, 30-day forecast)."""
    snap = regime_module.get_regime_snapshot(lookback_days)
    if not snap.get("available"):
        return RegimeSnapshot(
            available=False,
            error=snap.get("error") or "snapshot unavailable",
            regime_order=list(regime_module.REGIMES),
        )
    current = snap.get("current_regime")
    return RegimeSnapshot(
        available=True,
        as_of=snap.get("as_of"),
        current_regime=current,
        current_label=regime_module.REGIME_LABELS.get(current) if current else None,
        current_blurb=regime_module.REGIME_BLURB.get(current) if current else None,
        current_spy=snap.get("current_spy"),
        current_vix=snap.get("current_vix"),
        current_sma_200=snap.get("current_sma_200"),
        regime_order=list(regime_module.REGIMES),
        transition_matrix=snap.get("transition_matrix", []),
        stationary=snap.get("stationary", {}),
        forecast_30d=snap.get("forecast_30d", {}),
        n_days_observed=snap.get("n_days_observed"),
    )


# ───────────────────────────────────────────────────────────────────────
# Tier 3 endpoint
# ───────────────────────────────────────────────────────────────────────


@router.get("/hmm", response_model=HmmSnapshot)
def get_hmm(
    lookback_days: int = Query(365 * 5, ge=365, le=365 * 20),
    n_states: int = Query(4, ge=2, le=6),
) -> HmmSnapshot:
    """Tier 3 — HMM-fitted regime classification.

    Same axis order as tier 2 so the transition matrices can be compared
    side-by-side. ``tier1_agreement_pct`` tells you how often the
    learned HMM agrees with the deterministic rule-based classifier."""
    snap = regime_module.get_hmm_snapshot(lookback_days, n_states)
    if not snap.get("available"):
        return HmmSnapshot(
            available=False,
            error=snap.get("error") or "HMM fit unavailable",
            regime_order=list(regime_module.REGIMES),
        )
    return HmmSnapshot(
        available=True,
        n_states=snap.get("n_states"),
        as_of=snap.get("as_of"),
        current_regime=snap.get("current_regime"),
        regime_order=list(regime_module.REGIMES),
        hmm_transition_matrix=snap.get("hmm_transition_matrix", []),
        tier1_agreement_pct=snap.get("tier1_agreement_pct"),
        n_days_observed=snap.get("n_days_observed"),
    )


# ───────────────────────────────────────────────────────────────────────
# Hit rate stratified by regime
# ───────────────────────────────────────────────────────────────────────


@router.get("/runs-by-regime", response_model=RegimePerformanceResponse)
def get_runs_by_regime(
    window_days: int = Query(30, ge=5, le=365),
    lookback_days: int = Query(365, ge=30, le=3650),
    limit: int = Query(2000, ge=10, le=10000),
) -> RegimePerformanceResponse:
    """For every completed run with a reached +window_days horizon, look
    up the regime that was active on its trade_date, then aggregate by
    regime to get hit rate + mean alpha per regime.

    Answers the question: 'In which market regimes does the framework
    actually add value?' If Buy calls made in VOLATILE_BEAR have 40%
    hit rate vs 70% in CALM_BULL, you down-weight them in real life
    when the regime indicator says VOLATILE_BEAR is active."""
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    rows = [
        r for r in storage.list_runs(limit=limit)
        if (r.get("status") or "").lower() == "done"
        and (r.get("trade_date") or "") >= cutoff
    ]

    # Resolve regime per trade_date (single batch call, then in-memory map).
    trade_dates = sorted({r["trade_date"] for r in rows if r.get("trade_date")})
    regime_map = regime_module.regime_for_run_dates(trade_dates, lookback_days=365 * 5)

    # Pull the cached +window_days backtest result per run.
    from service.routers.backtest import _compute as compute_backtest

    by_regime_acc: Dict[str, Dict[str, Any]] = {}
    baseline_wins = baseline_counted = 0
    baseline_alphas: List[float] = []

    for r in rows:
        reg = regime_map.get(r.get("trade_date") or "")
        if reg is None:
            continue
        try:
            res = compute_backtest(r, force=False)
        except Exception:
            continue
        w = next((w for w in res.windows if w.days == window_days), None)
        if w is None or not w.horizon_reached:
            continue
        decision = res.decision or "—"

        bucket = by_regime_acc.setdefault(
            reg,
            {"n": 0, "with_dec": 0, "decisions": {}, "alphas": [], "wins": 0, "counted": 0},
        )
        bucket["n"] += 1
        if res.decision:
            bucket["with_dec"] += 1
            bucket["decisions"][decision] = bucket["decisions"].get(decision, 0) + 1
        if w.alpha_pct is not None:
            bucket["alphas"].append(w.alpha_pct)
            baseline_alphas.append(w.alpha_pct)
        if w.win is True:
            bucket["wins"] += 1
            baseline_wins += 1
        if w.win is not None:
            bucket["counted"] += 1
            baseline_counted += 1

    out_rows: List[RegimePerformanceRow] = []
    for reg in regime_module.REGIMES:
        b = by_regime_acc.get(reg)
        if b is None:
            out_rows.append(RegimePerformanceRow(
                regime=reg, n_runs=0, n_with_decision=0,
                decisions={}, mean_alpha_pct=None, hit_rate_pct=None,
            ))
            continue
        mean_alpha = (sum(b["alphas"]) / len(b["alphas"])) if b["alphas"] else None
        hit_rate = (b["wins"] / b["counted"] * 100.0) if b["counted"] else None
        out_rows.append(RegimePerformanceRow(
            regime=reg,
            n_runs=b["n"],
            n_with_decision=b["with_dec"],
            decisions=b["decisions"],
            mean_alpha_pct=round(mean_alpha, 2) if mean_alpha is not None else None,
            hit_rate_pct=round(hit_rate, 1) if hit_rate is not None else None,
        ))

    baseline_hit = (baseline_wins / baseline_counted * 100.0) if baseline_counted else None
    baseline_mean_alpha = (sum(baseline_alphas) / len(baseline_alphas)) if baseline_alphas else None

    return RegimePerformanceResponse(
        window_days=window_days,
        lookback_days=lookback_days,
        rows=out_rows,
        baseline_hit_rate_pct=round(baseline_hit, 1) if baseline_hit is not None else None,
        baseline_mean_alpha_pct=round(baseline_mean_alpha, 2) if baseline_mean_alpha is not None else None,
    )


# ───────────────────────────────────────────────────────────────────────
# Per-run regime lookup (used by brief panel)
# ───────────────────────────────────────────────────────────────────────


class RunRegimeResponse(BaseModel):
    run_id: str
    trade_date: str
    regime: Optional[str] = None
    regime_label: Optional[str] = None
    regime_blurb: Optional[str] = None
    regime_hit_rate_pct: Optional[float] = None
    baseline_hit_rate_pct: Optional[float] = None
    regime_calls_count: Optional[int] = None


@router.get("/run/{run_id}", response_model=RunRegimeResponse)
def get_run_regime(run_id: str) -> RunRegimeResponse:
    """For a specific run, return the regime that was active on its
    trade_date + how the framework historically performed in that regime."""
    run = storage.get_run(run_id)
    if not run:
        return RunRegimeResponse(run_id=run_id, trade_date="")
    trade_date = run.get("trade_date") or ""
    regime_map = regime_module.regime_for_run_dates([trade_date])
    reg = regime_map.get(trade_date)
    if reg is None:
        return RunRegimeResponse(run_id=run_id, trade_date=trade_date)

    # Fetch the regime-stratified performance to surface this regime's hit rate.
    perf = get_runs_by_regime()  # uses defaults: 30d window, 365d lookback
    row = next((r for r in perf.rows if r.regime == reg), None)
    return RunRegimeResponse(
        run_id=run_id,
        trade_date=trade_date,
        regime=reg,
        regime_label=regime_module.REGIME_LABELS.get(reg),
        regime_blurb=regime_module.REGIME_BLURB.get(reg),
        regime_hit_rate_pct=row.hit_rate_pct if row else None,
        baseline_hit_rate_pct=perf.baseline_hit_rate_pct,
        regime_calls_count=row.n_runs if row else None,
    )
