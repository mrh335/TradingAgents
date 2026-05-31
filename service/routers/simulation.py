"""Portfolio simulation engine + REST.

The model is intentionally simple — we're not pretending to be Monte Carlo
quant infrastructure. Given a scenario:

    {
        "starting_capital": 10000,
        "trades": [
            {"ticker": "NVDA", "shares": 10, "entry_price": 198,
             "exit_strategy": {"hold_days": 30}}
        ],
    }

…we estimate trailing return + volatility from yfinance for each ticker,
project forward for the holding period (linear drift with a normal-noise
band), and compare to a SPY-only baseline over the same window.

POST /sim/run         — run a scenario, return result (also saved)
GET  /sim             — list saved simulations
GET  /sim/{id}        — fetch one saved simulation
DELETE /sim/{id}      — delete
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gui import storage
from service import portfolio_analytics as pa

router = APIRouter(prefix="/sim", tags=["simulation"])

# Cache yfinance pulls briefly — backtest + montecarlo + correlation on the
# same page hit the same tickers repeatedly. Keyed by (sorted tickers, period).
_PRICE_CACHE: Dict[str, "tuple[float, pd.DataFrame]"] = {}
_PRICE_TTL = 600.0  # 10 min
import threading as _threading
import time as _time
_PRICE_LOCK = _threading.Lock()


def _fetch_prices(tickers: List[str], period: str = "6y") -> pd.DataFrame:
    """Auto-adjusted (total-return) daily closes for tickers. Cached + locked."""
    key = ",".join(sorted(set(tickers))) + "|" + period
    with _PRICE_LOCK:
        hit = _PRICE_CACHE.get(key)
        if hit and (_time.time() - hit[0]) < _PRICE_TTL:
            return hit[1]
    raw = yf.download(sorted(set(tickers)), period=period, interval="1d",
                      auto_adjust=True, progress=False)
    if raw is None or len(raw) == 0:
        raise HTTPException(status_code=502, detail="no price data returned from yfinance")
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    # Single-ticker download returns a Series-like; normalize to DataFrame.
    if isinstance(close, pd.Series):
        close = close.to_frame(name=sorted(set(tickers))[0])
    close = close.dropna(how="all")
    with _PRICE_LOCK:
        _PRICE_CACHE[key] = (_time.time(), close)
    return close


# ---- Schemas ---------------------------------------------------------

class SimTrade(BaseModel):
    ticker: str
    shares: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    hold_days: int = Field(default=30, ge=1, le=365 * 3)


class SimRunRequest(BaseModel):
    name: Optional[str] = None
    base_run_id: Optional[str] = None
    starting_capital: float = Field(default=10000.0)
    trades: List[SimTrade]
    # Look-back for drift / vol estimate.
    history_days: int = Field(default=180, ge=30, le=365 * 5)


class SimPoint(BaseModel):
    day: int
    portfolio: float
    baseline_spy: float
    portfolio_low: float
    portfolio_high: float


class SimResult(BaseModel):
    name: str
    starting_capital: float
    expected_final_value: float
    expected_return_pct: float
    baseline_final_value: float
    baseline_return_pct: float
    alpha_pct: float
    horizon_days: int
    points: List[SimPoint]
    per_trade: List[Dict[str, Any]]


class SimRow(BaseModel):
    id: int
    name: Optional[str] = None
    base_run_id: Optional[str] = None
    ticker: Optional[str] = None
    created_at: str


class SimDetail(SimRow):
    scenario: Dict[str, Any]
    result: SimResult


# ---- Engine ---------------------------------------------------------

def _stats(ticker: str, days: int) -> tuple[float, float]:
    """Return (annualised mu, annualised sigma) from daily log returns."""
    end = date.today()
    start = end - timedelta(days=int(days * 1.5) + 10)  # buffer for non-trading days
    df = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat(),
                                   auto_adjust=True)
    if df.empty or len(df) < 5:
        return 0.0, 0.20  # 20% vol fallback
    close = df["Close"].astype(float)
    log_returns = np.log(close / close.shift(1)).dropna()
    if len(log_returns) < 2:
        return 0.0, 0.20
    daily_mu = float(log_returns.mean())
    daily_sigma = float(log_returns.std(ddof=1))
    # Annualise (252 trading days).
    return daily_mu * 252, daily_sigma * np.sqrt(252)


def _simulate(req: SimRunRequest) -> SimResult:
    horizon = max(t.hold_days for t in req.trades)

    # Per-trade stats.
    per_trade_stats: List[Dict[str, Any]] = []
    for t in req.trades:
        mu, sigma = _stats(t.ticker, req.history_days)
        per_trade_stats.append({
            "ticker": t.ticker, "shares": t.shares, "entry_price": t.entry_price,
            "hold_days": t.hold_days, "mu_annual": mu, "sigma_annual": sigma,
            "cost": t.shares * t.entry_price,
        })
    spy_mu, spy_sigma = _stats("SPY", req.history_days)

    # Daily projection.
    daily_factor = 1.0 / 252
    points: List[SimPoint] = []
    total_invested = sum(s["cost"] for s in per_trade_stats)
    cash = max(0.0, req.starting_capital - total_invested)

    for day in range(horizon + 1):
        # Each trade's expected price after `day` days (or hold_days if shorter).
        port_value = cash
        port_low = cash
        port_high = cash
        for t, s in zip(req.trades, per_trade_stats):
            d = min(day, t.hold_days)
            drift = np.exp(s["mu_annual"] * d * daily_factor)
            band = s["sigma_annual"] * np.sqrt(d * daily_factor)  # 1-sigma band
            mid_price = t.entry_price * drift
            low_price = t.entry_price * drift * np.exp(-band)
            high_price = t.entry_price * drift * np.exp(band)
            port_value += t.shares * mid_price
            port_low += t.shares * low_price
            port_high += t.shares * high_price

        baseline = req.starting_capital * np.exp(spy_mu * day * daily_factor)
        points.append(SimPoint(
            day=day,
            portfolio=round(port_value, 2),
            baseline_spy=round(baseline, 2),
            portfolio_low=round(port_low, 2),
            portfolio_high=round(port_high, 2),
        ))

    final = points[-1]
    expected_return_pct = (final.portfolio / req.starting_capital - 1) * 100
    baseline_return_pct = (final.baseline_spy / req.starting_capital - 1) * 100

    return SimResult(
        name=req.name or f"sim @ {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        starting_capital=req.starting_capital,
        expected_final_value=final.portfolio,
        expected_return_pct=expected_return_pct,
        baseline_final_value=final.baseline_spy,
        baseline_return_pct=baseline_return_pct,
        alpha_pct=expected_return_pct - baseline_return_pct,
        horizon_days=horizon,
        points=points,
        per_trade=per_trade_stats,
    )


# ---- Endpoints ------------------------------------------------------

@router.post("/run", response_model=SimDetail)
def run_simulation(req: SimRunRequest) -> SimDetail:
    if not req.trades:
        raise HTTPException(status_code=400, detail="at least one trade required")
    result = _simulate(req)
    sid = storage.add_simulation(
        name=result.name,
        base_run_id=req.base_run_id,
        ticker=req.trades[0].ticker if req.trades else None,
        scenario_json=req.model_dump_json(),
        result_json=result.model_dump_json(),
    )
    row = storage.get_simulation(sid)
    return SimDetail(
        id=sid,
        name=row["name"],
        base_run_id=row.get("base_run_id"),
        ticker=row.get("ticker"),
        created_at=row["created_at"],
        scenario=req.model_dump(),
        result=result,
    )


@router.get("", response_model=List[SimRow])
def list_sims() -> List[SimRow]:
    return [SimRow(**r) for r in storage.list_simulations()]


@router.get("/{sid:int}", response_model=SimDetail)
def get_sim(sid: int) -> SimDetail:
    row = storage.get_simulation(sid)
    if not row:
        raise HTTPException(status_code=404, detail="simulation not found")
    return SimDetail(
        id=row["id"],
        name=row["name"],
        base_run_id=row.get("base_run_id"),
        ticker=row.get("ticker"),
        created_at=row["created_at"],
        scenario=json.loads(row["scenario_json"]),
        result=SimResult.model_validate_json(row["result_json"]),
    )


@router.delete("/{sid:int}")
def delete_sim(sid: int) -> dict:
    storage.delete_simulation(sid)
    return {"deleted": sid}


# =====================================================================
# Historical backtest + risk statistics  (NEW)
# =====================================================================

class ScenarioSpec(BaseModel):
    name: str
    # ticker -> weight (need not sum to 1; engine normalizes). Non-positive
    # weights are dropped.
    weights: Dict[str, float]


class BacktestRequest(BaseModel):
    scenarios: List[ScenarioSpec]
    benchmark: str = "SPY"
    period: str = Field(default="6y", description="yfinance period, e.g. 5y/6y/10y")
    initial: float = Field(default=100_000.0, gt=0)
    rebalance: str = Field(default="none", description="'none' (buy & hold) or 'daily'")
    windows: List[int] = Field(default=[1, 2, 3, 5])


@router.post("/backtest")
def backtest(req: BacktestRequest) -> dict:
    """Real historical equity curves + a professional risk-stat pack for each
    allocation scenario, all over the same window for apples-to-apples."""
    if not req.scenarios:
        raise HTTPException(status_code=400, detail="at least one scenario required")

    tickers = sorted({t for s in req.scenarios for t in s.weights} | {req.benchmark})
    prices = _fetch_prices(tickers, req.period)
    if req.benchmark not in prices.columns:
        raise HTTPException(status_code=502,
                            detail=f"no price data for benchmark {req.benchmark}")
    rets = pa.daily_returns(prices)
    bench_rets = rets[req.benchmark].dropna()

    # Downsample equity curve to <=180 points to keep payloads small.
    def thin(curve: pd.Series, n: int = 180) -> list:
        step = max(1, len(curve) // n)
        pts = []
        for i in range(0, len(curve), step):
            pts.append({"date": str(curve.index[i].date()),
                        "value": pa._safe(float(curve.iloc[i]))})
        if pts and pts[-1]["date"] != str(curve.index[-1].date()):
            pts.append({"date": str(curve.index[-1].date()),
                        "value": pa._safe(float(curve.iloc[-1]))})
        return pts

    results = []
    for s in req.scenarios:
        try:
            pr = pa.portfolio_returns(rets, s.weights, rebalance=req.rebalance)
        except ValueError as e:
            results.append({"name": s.name, "error": str(e)})
            continue
        curve = pa.equity_curve(pr, req.initial)
        stats = pa.stat_pack(pr, benchmark_rets=bench_rets, initial=req.initial)
        # Windowed returns from the scenario's own equity curve.
        win = pa.windowed_returns(curve, years=req.windows)
        results.append({
            "name": s.name,
            "weights": pa.normalize_weights(s.weights),
            "stats": stats,
            "windows": win,
            "curve": thin(curve),
        })

    # Benchmark curve + stats for the chart overlay.
    bench_curve = pa.equity_curve(bench_rets, req.initial)
    bench_stats = pa.stat_pack(bench_rets, benchmark_rets=bench_rets, initial=req.initial)

    # Correlation matrix across all individual tickers used.
    indiv = [t for t in tickers if t != req.benchmark]
    corr = pa.correlation_matrix(rets[indiv].dropna(how="any")) if len(indiv) >= 2 else {}

    return {
        "as_of": str(prices.index[-1].date()),
        "start": str(prices.index[0].date()),
        "benchmark": req.benchmark,
        "initial": req.initial,
        "rebalance": req.rebalance,
        "scenarios": results,
        "benchmark_curve": thin(bench_curve),
        "benchmark_stats": bench_stats,
        "correlation": corr,
    }


# =====================================================================
# Monte Carlo forward simulation  (NEW)
# =====================================================================

class MonteCarloRequest(BaseModel):
    weights: Dict[str, float]
    benchmark: str = "SPY"
    period: str = Field(default="5y", description="history window to learn the return distribution")
    horizon_days: int = Field(default=252, ge=5, le=252 * 10)
    n_paths: int = Field(default=5000, ge=200, le=50_000)
    method: str = Field(default="bootstrap", description="'bootstrap' or 'normal'")
    initial: float = Field(default=100_000.0, gt=0)
    rebalance: str = Field(default="none")


@router.post("/montecarlo")
def montecarlo(req: MonteCarloRequest) -> dict:
    tickers = sorted(set(req.weights) | {req.benchmark})
    prices = _fetch_prices(tickers, req.period)
    rets = pa.daily_returns(prices)
    try:
        pr = pa.portfolio_returns(rets, req.weights, rebalance=req.rebalance)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    bench_rets = rets[req.benchmark].dropna() if req.benchmark in rets.columns else None

    try:
        mc = pa.monte_carlo(
            pr, horizon_days=req.horizon_days, n_paths=req.n_paths,
            method=req.method, initial=req.initial, benchmark_rets=bench_rets,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    mc["weights"] = pa.normalize_weights(req.weights)
    mc["history_start"] = str(prices.index[0].date())
    mc["history_end"] = str(prices.index[-1].date())
    return mc


# =====================================================================
# Prefill helper — "your actual mix" from the live portfolio  (NEW)
# =====================================================================

@router.get("/portfolio-actual")
def portfolio_actual() -> dict:
    """Return current real positions as a weights dict (by live value), so the
    UI can seed a 'your actual mix' scenario in one click."""
    positions = storage.list_positions(include_closed=False)
    weights: Dict[str, float] = {}
    total = 0.0
    # Pull live prices in one batch for valuation.
    tickers = [p["ticker"] for p in positions]
    px: Dict[str, float] = {}
    if tickers:
        try:
            raw = yf.download(sorted(set(tickers)), period="5d", interval="1d",
                              auto_adjust=True, progress=False)
            close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
            if isinstance(close, pd.Series):
                close = close.to_frame(name=sorted(set(tickers))[0])
            last = close.ffill().iloc[-1]
            for t in set(tickers):
                try:
                    px[t] = float(last[t])
                except Exception:
                    px[t] = 0.0
        except Exception:
            px = {}
    rows = []
    for p in positions:
        t = p["ticker"]
        price = px.get(t) or 0.0
        value = price * float(p["shares"]) if price else float(p.get("cost") or 0.0)
        weights[t] = weights.get(t, 0.0) + value
        total += value
        rows.append({"ticker": t, "shares": p["shares"], "value": round(value, 2)})
    if total > 0:
        weights = {k: round(v / total, 4) for k, v in weights.items()}
    return {"weights": weights, "positions": rows, "total_value": round(total, 2)}
