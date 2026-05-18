"""Strategy backtesting — realized returns + hit-rate tracking.

For every completed run with a decision, compute:
- Realized total return at +5d, +30d, +60d, +180d post-trade-date
- SPY benchmark return for the same windows
- Alpha = realized − benchmark

Then aggregate to expose:
- Per-decision hit rate: % of Buy / Overweight calls that gained ≥ 0;
  % of Sell / Underweight calls that fell ≤ 0
- Per-(provider, model) mean alpha — which configurations actually pick
  winners on YOUR portfolio specifically

Caching: the per-run computation lands as a ``.backtest.json`` sidecar
next to the archive so re-renders of the /backtest page don't re-fetch
yfinance every time. Recompute is forced via ``?force=true``.

Endpoints
---------
GET /backtest/{run_id}            — single-run scoreboard (computes on demand)
GET /backtest/                    — across-portfolio aggregates by decision /
                                    provider / model
POST /backtest/recompute-all      — recompute every status='done' run
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from gui import sidecars as sidecars_helpers
from gui import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/backtest", tags=["backtest"])


WINDOWS_DAYS = [5, 30, 60, 180]


class BacktestWindow(BaseModel):
    days: int
    end_date: Optional[str] = None
    ticker_return_pct: Optional[float] = None
    benchmark_return_pct: Optional[float] = None
    alpha_pct: Optional[float] = None
    horizon_reached: bool = False
    win: Optional[bool] = None


class BacktestResult(BaseModel):
    run_id: str
    ticker: str
    trade_date: str
    decision: Optional[str] = None
    provider: Optional[str] = None
    deep_model: Optional[str] = None
    benchmark: str = "SPY"
    windows: List[BacktestWindow]
    computed_at: str
    note: Optional[str] = None


def _classify_win(decision: Optional[str], ticker_return: Optional[float]) -> Optional[bool]:
    """Map a decision + realized return into a binary win/lose flag.

    - Buy / Overweight: win if ticker_return > 0
    - Hold: not counted (returns None)
    - Underweight / Sell: win if ticker_return < 0 (avoiding the loss is the win)
    """
    if decision is None or ticker_return is None:
        return None
    d = decision.lower()
    if d in ("buy", "overweight"):
        return ticker_return > 0
    if d in ("sell", "underweight"):
        return ticker_return < 0
    return None


def _fetch_returns(ticker: str, trade_date: date, benchmark: str = "SPY") -> Dict[int, Dict[str, Any]]:
    """For each window in WINDOWS_DAYS, compute ticker and benchmark return.

    Uses yfinance split+dividend adjusted Close. Windows that haven't
    completed yet still return a partial return but ``horizon_reached``
    is False so the aggregator can choose to exclude them.
    """
    out: Dict[int, Dict[str, Any]] = {}
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        for d in WINDOWS_DAYS:
            out[d] = {"end_date": None, "ticker_return_pct": None,
                      "benchmark_return_pct": None, "horizon_reached": False}
        return out

    end = min(date.today(), trade_date + timedelta(days=max(WINDOWS_DAYS) + 7))
    start = trade_date - timedelta(days=7)
    try:
        ticker_df = yf.Ticker(ticker.upper()).history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
        )
        bench_df = yf.Ticker(benchmark).history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
        )
    except Exception as e:
        logger.warning(f"backtest fetch failed for {ticker}: {e}")
        for d in WINDOWS_DAYS:
            out[d] = {"end_date": None, "ticker_return_pct": None,
                      "benchmark_return_pct": None, "horizon_reached": False}
        return out

    if ticker_df is None or ticker_df.empty:
        for d in WINDOWS_DAYS:
            out[d] = {"end_date": None, "ticker_return_pct": None,
                      "benchmark_return_pct": None, "horizon_reached": False}
        return out

    if ticker_df.index.tz is not None:
        ticker_df.index = ticker_df.index.tz_localize(None)
    if bench_df is not None and not bench_df.empty and bench_df.index.tz is not None:
        bench_df.index = bench_df.index.tz_localize(None)

    trade_ts = pd.Timestamp(trade_date)
    anchor_rows = ticker_df.index[ticker_df.index >= trade_ts]
    if len(anchor_rows) == 0:
        for d in WINDOWS_DAYS:
            out[d] = {"end_date": None, "ticker_return_pct": None,
                      "benchmark_return_pct": None, "horizon_reached": False}
        return out
    anchor_ticker = float(ticker_df.loc[anchor_rows[0], "Close"])
    bench_anchor_rows = bench_df.index[bench_df.index >= trade_ts] if bench_df is not None else []
    anchor_bench = (
        float(bench_df.loc[bench_anchor_rows[0], "Close"]) if len(bench_anchor_rows) > 0 else None
    )

    for days in WINDOWS_DAYS:
        target_ts = pd.Timestamp(trade_date + timedelta(days=days))
        candidates = ticker_df.index[ticker_df.index >= target_ts]
        if len(candidates) > 0:
            end_idx = candidates[0]
            horizon_reached = True
        else:
            end_idx = ticker_df.index[-1]
            horizon_reached = False
        ticker_end = float(ticker_df.loc[end_idx, "Close"])
        ticker_ret = (ticker_end / anchor_ticker - 1) * 100 if anchor_ticker > 0 else None

        bench_ret = None
        if anchor_bench and bench_df is not None and not bench_df.empty:
            bench_candidates = bench_df.index[bench_df.index >= target_ts]
            bench_end_idx = bench_candidates[0] if len(bench_candidates) > 0 else bench_df.index[-1]
            bench_end = float(bench_df.loc[bench_end_idx, "Close"])
            bench_ret = (bench_end / anchor_bench - 1) * 100 if anchor_bench > 0 else None

        out[days] = {
            "end_date": end_idx.date().isoformat(),
            "ticker_return_pct": round(ticker_ret, 2) if ticker_ret is not None else None,
            "benchmark_return_pct": round(bench_ret, 2) if bench_ret is not None else None,
            "horizon_reached": bool(horizon_reached),
        }
    return out


def _cache_path(archive_path: str) -> Optional[Path]:
    if not archive_path:
        return None
    return sidecars_helpers.sidecar_path(archive_path, "backtest.json")


def _compute(run_row: Dict[str, Any], force: bool = False) -> BacktestResult:
    archive_path = run_row.get("log_path") or ""
    cache_path = _cache_path(archive_path)
    if cache_path and cache_path.exists() and not force:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return BacktestResult(**cached)
        except Exception:
            pass

    ticker = run_row["ticker"]
    try:
        trade_date = datetime.fromisoformat(run_row["trade_date"]).date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"invalid trade_date {run_row.get('trade_date')!r}")

    returns = _fetch_returns(ticker, trade_date)
    decision = run_row.get("decision")

    windows: List[BacktestWindow] = []
    for days in WINDOWS_DAYS:
        r = returns.get(days, {})
        tr = r.get("ticker_return_pct")
        br = r.get("benchmark_return_pct")
        alpha = (tr - br) if (tr is not None and br is not None) else None
        win = _classify_win(decision, tr)
        windows.append(BacktestWindow(
            days=days,
            end_date=r.get("end_date"),
            ticker_return_pct=tr,
            benchmark_return_pct=br,
            alpha_pct=round(alpha, 2) if alpha is not None else None,
            horizon_reached=r.get("horizon_reached", False),
            win=win,
        ))

    result = BacktestResult(
        run_id=run_row["run_id"],
        ticker=ticker,
        trade_date=run_row["trade_date"],
        decision=decision,
        provider=run_row.get("provider"),
        deep_model=run_row.get("deep_model"),
        windows=windows,
        computed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    if cache_path:
        try:
            cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning(f"backtest cache write failed: {e}")

    return result


# NOTE: /attribution must be declared BEFORE /{run_id} below — FastAPI
# evaluates routes in declaration order and would otherwise match the
# string "attribution" as a run_id parameter.

class TickerAttributionRow(BaseModel):
    ticker: str
    runs: int
    counted: int
    wins: int
    losses: int
    hit_rate_pct: Optional[float] = None
    mean_alpha_pct: Optional[float] = None
    best_alpha_pct: Optional[float] = None
    best_run_id: Optional[str] = None
    worst_alpha_pct: Optional[float] = None
    worst_run_id: Optional[str] = None


class AttributionResponse(BaseModel):
    window_days: int
    rows: List[TickerAttributionRow]


@router.get("/attribution", response_model=AttributionResponse)
def attribution(
    window_days: int = Query(30, ge=5, le=365),
    limit: int = Query(500, ge=10, le=2000),
) -> AttributionResponse:
    """Per-ticker performance attribution rollup."""
    rows = [r for r in storage.list_runs(limit=limit) if (r.get("status") or "").lower() == "done"]
    results: List[BacktestResult] = []
    for r in rows:
        try:
            results.append(_compute(r, force=False))
        except Exception as e:
            logger.warning(f"attribution skipped run {r['run_id']}: {e}")

    by_ticker: Dict[str, List[BacktestResult]] = {}
    for res in results:
        by_ticker.setdefault(res.ticker, []).append(res)

    out_rows: List[TickerAttributionRow] = []
    for ticker, runs in by_ticker.items():
        counted = 0
        wins = losses = 0
        alphas: List[tuple] = []
        for res in runs:
            w = next((w for w in res.windows if w.days == window_days), None)
            if w is None or not w.horizon_reached or w.win is None:
                continue
            counted += 1
            if w.win:
                wins += 1
            else:
                losses += 1
            if w.alpha_pct is not None:
                alphas.append((w.alpha_pct, res.run_id))
        hit = (wins / counted * 100) if counted > 0 else None
        mean_alpha = (sum(a for a, _ in alphas) / len(alphas)) if alphas else None
        best = max(alphas, default=(None, None))
        worst = min(alphas, default=(None, None))
        out_rows.append(TickerAttributionRow(
            ticker=ticker, runs=len(runs), counted=counted,
            wins=wins, losses=losses,
            hit_rate_pct=round(hit, 1) if hit is not None else None,
            mean_alpha_pct=round(mean_alpha, 2) if mean_alpha is not None else None,
            best_alpha_pct=round(best[0], 2) if best[0] is not None else None,
            best_run_id=best[1],
            worst_alpha_pct=round(worst[0], 2) if worst[0] is not None else None,
            worst_run_id=worst[1],
        ))

    out_rows.sort(key=lambda r: (
        r.counted == 0,
        -(r.mean_alpha_pct if r.mean_alpha_pct is not None else -9999),
        r.ticker,
    ))
    return AttributionResponse(window_days=window_days, rows=out_rows)


@router.get("/{run_id}", response_model=BacktestResult)
def get_backtest(run_id: str, force: bool = Query(False)) -> BacktestResult:
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    if (row.get("status") or "").lower() != "done":
        raise HTTPException(status_code=409, detail="run is not done")
    return _compute(row, force=force)


class HitRateCell(BaseModel):
    label: str
    runs: int
    wins: int
    losses: int
    skipped: int
    hit_rate_pct: Optional[float] = None
    mean_alpha_pct: Optional[float] = None


class BacktestSummaryResponse(BaseModel):
    window_days: int
    overall: HitRateCell
    by_decision: List[HitRateCell]
    by_provider: List[HitRateCell]
    by_model: List[HitRateCell]
    sample_rows: List[Dict[str, Any]] = []


def _hit_cell(label: str, results: List[BacktestResult], window_days: int) -> HitRateCell:
    runs = wins = losses = skipped = 0
    alphas: List[float] = []
    for r in results:
        w = next((w for w in r.windows if w.days == window_days), None)
        if w is None or not w.horizon_reached:
            skipped += 1
            continue
        if w.win is None:
            skipped += 1
            continue
        runs += 1
        if w.win:
            wins += 1
        else:
            losses += 1
        if w.alpha_pct is not None:
            alphas.append(w.alpha_pct)
    hit = (wins / runs * 100) if runs > 0 else None
    mean_alpha = (sum(alphas) / len(alphas)) if alphas else None
    return HitRateCell(
        label=label,
        runs=runs, wins=wins, losses=losses, skipped=skipped,
        hit_rate_pct=round(hit, 1) if hit is not None else None,
        mean_alpha_pct=round(mean_alpha, 2) if mean_alpha is not None else None,
    )


@router.get("/", response_model=BacktestSummaryResponse)
def summary(
    window_days: int = Query(30, ge=5, le=365),
    limit: int = Query(500, ge=10, le=2000),
) -> BacktestSummaryResponse:
    rows = [r for r in storage.list_runs(limit=limit) if (r.get("status") or "").lower() == "done"]
    results: List[BacktestResult] = []
    for r in rows:
        try:
            results.append(_compute(r, force=False))
        except Exception as e:
            logger.warning(f"backtest skipped run {r['run_id']}: {e}")

    by_decision: Dict[str, List[BacktestResult]] = {}
    by_provider: Dict[str, List[BacktestResult]] = {}
    by_model: Dict[str, List[BacktestResult]] = {}
    for res in results:
        if res.decision:
            by_decision.setdefault(res.decision, []).append(res)
        if res.provider:
            by_provider.setdefault(res.provider, []).append(res)
        if res.deep_model:
            by_model.setdefault(res.deep_model, []).append(res)

    sample = []
    for res in results[:50]:
        w = next((w for w in res.windows if w.days == window_days), None)
        if w is None:
            continue
        sample.append({
            "run_id": res.run_id,
            "ticker": res.ticker,
            "trade_date": res.trade_date,
            "decision": res.decision,
            "provider": res.provider,
            "deep_model": res.deep_model,
            "ticker_return_pct": w.ticker_return_pct,
            "benchmark_return_pct": w.benchmark_return_pct,
            "alpha_pct": w.alpha_pct,
            "horizon_reached": w.horizon_reached,
            "win": w.win,
        })

    return BacktestSummaryResponse(
        window_days=window_days,
        overall=_hit_cell("All decisions", results, window_days),
        by_decision=[_hit_cell(k, v, window_days) for k, v in sorted(by_decision.items())],
        by_provider=[_hit_cell(k, v, window_days) for k, v in sorted(by_provider.items())],
        by_model=[_hit_cell(k, v, window_days) for k, v in sorted(by_model.items())],
        sample_rows=sample,
    )


@router.post("/recompute-all")
def recompute_all(limit: int = Query(500, ge=10, le=2000)) -> dict:
    rows = [r for r in storage.list_runs(limit=limit) if (r.get("status") or "").lower() == "done"]
    ok = err = 0
    errors: List[str] = []
    for r in rows:
        try:
            _compute(r, force=True)
            ok += 1
        except Exception as e:
            err += 1
            errors.append(f"{r['run_id']}: {e}")
    return {"computed": ok, "errors": err, "error_details": errors[:10]}


# (attribution endpoint moved above /{run_id} earlier in the file for
# routing-order correctness.)
