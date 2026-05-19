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


class ActualPnL(BaseModel):
    """Realized + unrealized P&L from the user's actual trades linked
    to this run via ``trade_journal.linked_run_id``. None across the
    board when no trades are linked.
    """
    trade_count: int = 0
    shares_bought: float = 0.0
    shares_sold: float = 0.0
    shares_held_end: float = 0.0
    cost_basis: float = 0.0           # $ deployed (gross + fees, FIFO)
    proceeds: float = 0.0             # $ received from sells (net of fees)
    dividends: float = 0.0
    unrealized_value_end: float = 0.0 # shares_held_end × end-of-window price
    realized_pnl: float = 0.0
    total_pnl: float = 0.0            # realized + unrealized + dividends
    total_return_pct: Optional[float] = None  # total_pnl / cost_basis × 100
    actual_alpha_pct: Optional[float] = None  # actual return minus benchmark return
    end_price: Optional[float] = None
    notes: Optional[str] = None


class BacktestResult(BaseModel):
    run_id: str
    ticker: str
    trade_date: str
    decision: Optional[str] = None
    provider: Optional[str] = None
    deep_model: Optional[str] = None
    benchmark: str = "SPY"
    windows: List[BacktestWindow]
    actual: Optional[ActualPnL] = None  # populated when include_actual=true and linked trades exist
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


def _compute_actual(run_id: str, ticker: str, trade_date: date,
                     end_date: Optional[date], benchmark_return_pct: Optional[float]) -> Optional[ActualPnL]:
    """For the trades the user has linked to this run, compute realized
    + unrealized P&L through the end of the +30d window.

    FIFO cost-basis accounting:
      - Each buy adds a (shares, total_cost_with_fees) layer
      - Each sell pops from the front, generating realized P&L
      - Dividends accrue as positive cash flow with no share impact
      - Splits multiply remaining lots; transfers move shares without
        cash impact

    Unrealized = remaining shares × end-of-window closing price.

    Returns None if there are no trades linked to this run, so the
    UI can hide the "actual" column for un-traded recommendations.
    """
    trades = storage.trades_for_run(run_id)
    if not trades:
        return None

    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        return ActualPnL(notes="yfinance unavailable — actual P&L not computed")

    # End-of-window closing price for unrealized valuation.
    end_price: Optional[float] = None
    if end_date is not None:
        try:
            window = yf.Ticker(ticker.upper()).history(
                start=(end_date - timedelta(days=7)).isoformat(),
                end=(end_date + timedelta(days=2)).isoformat(),
                auto_adjust=True,
            )
            if window is not None and not window.empty:
                end_price = float(window["Close"].iloc[-1])
        except Exception as e:
            logger.warning(f"actual P&L end-price fetch failed for {ticker}: {e}")

    # FIFO lot stack: list of [shares_remaining, cost_per_share_with_fees].
    lots: List[List[float]] = []
    shares_bought = 0.0
    shares_sold = 0.0
    cost_basis = 0.0   # total $ deployed (gross + fees on buys)
    proceeds = 0.0     # total $ received (net of fees on sells)
    realized = 0.0
    dividends = 0.0

    for t in trades:
        action = t.get("action") or "buy"
        shares = float(t.get("shares") or 0)
        price = float(t.get("price") or 0)
        fees = float(t.get("fees") or 0)

        if action == "buy" or action == "cover":
            shares_bought += shares
            gross = shares * price + fees
            cost_basis += gross
            unit_cost = (shares * price + fees) / shares if shares > 0 else price
            lots.append([shares, unit_cost])

        elif action == "sell" or action == "short":
            shares_sold += shares
            gross = shares * price - fees
            proceeds += gross
            # Pop from FIFO front until we've sold ``shares`` worth.
            to_sell = shares
            while to_sell > 1e-9 and lots:
                lot_shares, lot_cost = lots[0]
                take = min(lot_shares, to_sell)
                realized += take * (price - lot_cost)
                lot_shares -= take
                to_sell -= take
                if lot_shares <= 1e-9:
                    lots.pop(0)
                else:
                    lots[0][0] = lot_shares
            if to_sell > 1e-9:
                # Sold more than ever owned — happens for shorts. Treat
                # the surplus as a short open at this price; no realized
                # change until covered.
                # For our purposes we just leave it as "sold without
                # cost basis" and the realized number stays as-is. The
                # unrealized side picks this up as negative shares-held.
                lots.append([-to_sell, price])

        elif action == "dividend":
            # Convention from /trades/summary: shares × price is the total
            # dividend cash. If price=0 we fall back to shares as the $.
            cash = shares * price if (shares > 0 and price > 0) else shares
            dividends += cash

        elif action == "split":
            # shares field holds the split ratio (2.0 = 2-for-1).
            if shares > 0 and lots:
                for lot in lots:
                    lot[0] *= shares
                    lot[1] /= shares

        # 'transfer' leaves the position alone

    shares_held = sum(l[0] for l in lots)
    unrealized_value = shares_held * end_price if (end_price is not None) else 0.0

    # Total P&L treatment:
    #   realized (cumulative buy → sell pairs)
    # + unrealized (current shares × end-of-window price − remaining cost basis)
    # + dividends
    remaining_cost = sum(l[0] * l[1] for l in lots)
    unrealized = unrealized_value - remaining_cost if end_price is not None else 0.0
    total_pnl = realized + unrealized + dividends

    total_return = (total_pnl / cost_basis * 100.0) if cost_basis > 0 else None
    actual_alpha: Optional[float] = None
    if total_return is not None and benchmark_return_pct is not None:
        actual_alpha = total_return - benchmark_return_pct

    return ActualPnL(
        trade_count=len(trades),
        shares_bought=round(shares_bought, 4),
        shares_sold=round(shares_sold, 4),
        shares_held_end=round(shares_held, 4),
        cost_basis=round(cost_basis, 2),
        proceeds=round(proceeds, 2),
        dividends=round(dividends, 2),
        unrealized_value_end=round(unrealized_value, 2),
        realized_pnl=round(realized, 2),
        total_pnl=round(total_pnl, 2),
        total_return_pct=round(total_return, 2) if total_return is not None else None,
        actual_alpha_pct=round(actual_alpha, 2) if actual_alpha is not None else None,
        end_price=round(end_price, 2) if end_price is not None else None,
        notes=None if end_price is not None else "end-of-window price unavailable; unrealized excluded",
    )


def _compute(run_row: Dict[str, Any], force: bool = False,
              include_actual: bool = False) -> BacktestResult:
    """Compute (or load cached) per-window returns for a single run.

    ``include_actual=True`` additionally walks the trade_journal entries
    linked to this run and computes the actual realized + unrealized
    P&L. Result is anchored to the +30d window's end date — that's
    the canonical "did the recommendation work" horizon.
    """
    archive_path = run_row.get("log_path") or ""
    cache_path = _cache_path(archive_path)
    cached_result: Optional[BacktestResult] = None
    if cache_path and cache_path.exists() and not force:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached_result = BacktestResult(**cached)
        except Exception:
            pass

    if cached_result and not include_actual:
        # Cached result is sufficient when caller doesn't need fresh
        # actual P&L (which depends on user trades that may have been
        # added after the cache was written).
        return cached_result

    ticker = run_row["ticker"]
    try:
        trade_date = datetime.fromisoformat(run_row["trade_date"]).date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"invalid trade_date {run_row.get('trade_date')!r}")

    if cached_result is not None:
        # Reuse the cached window math; we only need to compute the
        # actual side fresh.
        windows = cached_result.windows
    else:
        returns = _fetch_returns(ticker, trade_date)
        decision = run_row.get("decision")
        windows = []
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

    actual: Optional[ActualPnL] = None
    if include_actual:
        # Anchor the actual-P&L window to +30d (the canonical comparison
        # horizon). Caller can re-ask with a different window if needed
        # later; we keep the API simple for now.
        w30 = next((w for w in windows if w.days == 30), None)
        end_date = None
        bench_pct: Optional[float] = None
        if w30 and w30.end_date:
            try:
                end_date = datetime.fromisoformat(w30.end_date).date()
            except (TypeError, ValueError):
                end_date = None
            bench_pct = w30.benchmark_return_pct
        try:
            actual = _compute_actual(run_row["run_id"], ticker, trade_date, end_date, bench_pct)
        except Exception as e:
            logger.warning(f"actual P&L compute failed for {run_row['run_id']}: {e}")

    result = BacktestResult(
        run_id=run_row["run_id"],
        ticker=ticker,
        trade_date=run_row["trade_date"],
        decision=run_row.get("decision"),
        provider=run_row.get("provider"),
        deep_model=run_row.get("deep_model"),
        windows=windows,
        actual=actual,
        computed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    # Only cache the notional half (windows). actual depends on
    # mutable trade_journal entries and must always be recomputed.
    if cache_path and cached_result is None:
        try:
            to_cache = BacktestResult(
                run_id=result.run_id, ticker=result.ticker,
                trade_date=result.trade_date, decision=result.decision,
                provider=result.provider, deep_model=result.deep_model,
                benchmark=result.benchmark, windows=result.windows,
                actual=None, computed_at=result.computed_at,
            )
            cache_path.write_text(to_cache.model_dump_json(indent=2), encoding="utf-8")
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


# ---------------------------------------------------------------------------
# Actual-vs-notional aggregate — only runs that the user has actually traded.
# Declared BEFORE /{run_id} (FastAPI evaluates routes in declaration order
# and would otherwise match "actual-vs-notional" as a run_id parameter).
# ---------------------------------------------------------------------------

class ActualVsNotionalRow(BaseModel):
    run_id: str
    ticker: str
    trade_date: str
    decision: Optional[str] = None
    notional_return_pct: Optional[float] = None      # at +30d, the framework's notional
    notional_alpha_pct: Optional[float] = None       # vs SPY at +30d
    actual_return_pct: Optional[float] = None        # what the user's trades actually returned
    actual_alpha_pct: Optional[float] = None         # actual return vs SPY at +30d
    actual_minus_notional_pct: Optional[float] = None  # behaviour gap (positive = you beat the framework)
    trade_count: int = 0
    cost_basis: float = 0.0
    total_pnl: float = 0.0


class ActualVsNotionalResponse(BaseModel):
    window_days: int = 30
    runs: List[ActualVsNotionalRow]
    aggregate: Dict[str, Any]


@router.get("/actual-vs-notional", response_model=ActualVsNotionalResponse)
def actual_vs_notional(limit: int = Query(500, ge=10, le=2000)) -> ActualVsNotionalResponse:
    """Compare what the framework predicted vs what your trades actually
    realized, only for runs you actually traded against.

    Implicitly anchored to the +30d window — that's where notional and
    actual share a comparable end-of-window benchmark return for the
    alpha math to line up.
    """
    rows = [r for r in storage.list_runs(limit=limit) if (r.get("status") or "").lower() == "done"]
    out_rows: List[ActualVsNotionalRow] = []
    total_actual_pnl = 0.0
    total_cost_basis = 0.0
    behaviour_gap_pcs: List[float] = []
    you_beat_framework = 0

    for r in rows:
        try:
            res = _compute(r, force=False, include_actual=True)
        except Exception as e:
            logger.warning(f"actual-vs-notional skipped {r.get('run_id')}: {e}")
            continue
        if res.actual is None or (res.actual.trade_count or 0) == 0:
            continue
        w30 = next((w for w in res.windows if w.days == 30), None)
        notional = w30.ticker_return_pct if w30 else None
        notional_alpha = w30.alpha_pct if w30 else None
        actual = res.actual.total_return_pct
        actual_alpha = res.actual.actual_alpha_pct
        gap = None
        if actual is not None and notional is not None:
            gap = actual - notional
            behaviour_gap_pcs.append(gap)
            if gap > 0:
                you_beat_framework += 1
        out_rows.append(ActualVsNotionalRow(
            run_id=res.run_id, ticker=res.ticker,
            trade_date=res.trade_date, decision=res.decision,
            notional_return_pct=notional,
            notional_alpha_pct=notional_alpha,
            actual_return_pct=actual,
            actual_alpha_pct=actual_alpha,
            actual_minus_notional_pct=round(gap, 2) if gap is not None else None,
            trade_count=res.actual.trade_count,
            cost_basis=res.actual.cost_basis,
            total_pnl=res.actual.total_pnl,
        ))
        total_actual_pnl += res.actual.total_pnl
        total_cost_basis += res.actual.cost_basis

    out_rows.sort(key=lambda r: -(r.actual_minus_notional_pct or -9999))
    mean_gap = (sum(behaviour_gap_pcs) / len(behaviour_gap_pcs)) if behaviour_gap_pcs else None
    blended_return = (total_actual_pnl / total_cost_basis * 100.0) if total_cost_basis else None

    aggregate: Dict[str, Any] = {
        "runs_with_actual_trades": len(out_rows),
        "you_beat_framework": you_beat_framework,
        "mean_behaviour_gap_pct": round(mean_gap, 2) if mean_gap is not None else None,
        "total_cost_basis": round(total_cost_basis, 2),
        "total_realized_plus_unrealized_pnl": round(total_actual_pnl, 2),
        "blended_actual_return_pct": round(blended_return, 2) if blended_return is not None else None,
    }
    return ActualVsNotionalResponse(window_days=30, runs=out_rows, aggregate=aggregate)


@router.get("/{run_id}", response_model=BacktestResult)
def get_backtest(
    run_id: str,
    force: bool = Query(False),
    include_actual: bool = Query(
        False,
        description="Also compute realized + unrealized P&L from trade_journal "
                    "entries linked to this run via linked_run_id.",
    ),
) -> BacktestResult:
    row = storage.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    if (row.get("status") or "").lower() != "done":
        raise HTTPException(status_code=409, detail="run is not done")
    return _compute(row, force=force, include_actual=include_actual)


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
