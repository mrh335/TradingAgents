"""Portfolio risk metrics — VaR, max drawdown, Sharpe ratio.

Standard portfolio-management measures computed from yfinance daily
returns over a configurable lookback window. Exposed at the position
level (per ticker) and rolled up to the portfolio (weighted by cost
basis).

Endpoints
---------
GET /risk/portfolio?lookback_days=365  — book-level + per-position metrics

Math:
- **Sharpe** (annualized): mean(daily_returns) / stddev(daily_returns) × √252,
  with a default risk-free rate of 0. A value of 1.0+ is typical for a
  decent equity portfolio; 2.0+ is excellent. Below 0.5 is mediocre.
- **Max drawdown**: largest peak-to-trough decline (%) over the window.
  Always negative or zero. A reading of -40% means the worst stretch
  saw you lose 40% before recovering.
- **Daily VaR (5%)**: 5th percentile of daily returns — a one-day loss
  this big has happened (about) once every 20 trading days in the
  window. ‘Historical' VaR, not parametric.
- **Annualized volatility**: stddev(daily_returns) × √252.

Caching: the per-ticker daily-returns fetch is the slow part. The
endpoint runs in a single shot (no per-ticker sidecar yet) since the
position list is short and yfinance handles it in <5s for typical
portfolios.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from gui import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/risk", tags=["risk"])


TRADING_DAYS_PER_YEAR = 252


class PositionRisk(BaseModel):
    ticker: str
    weight_pct: float                       # % of book at cost basis
    annualized_volatility_pct: Optional[float] = None
    annualized_return_pct: Optional[float] = None
    sharpe: Optional[float] = None
    max_drawdown_pct: Optional[float] = None       # negative
    var_5pct_daily: Optional[float] = None          # negative
    var_5pct_dollar: Optional[float] = None         # negative — dollar loss at 5% threshold


class PortfolioRiskResponse(BaseModel):
    lookback_days: int
    benchmark: str
    portfolio: PositionRisk
    benchmark_risk: PositionRisk
    positions: List[PositionRisk]
    correlation_avg: Optional[float] = None  # average pairwise correlation (heads-up flag)
    note: Optional[str] = None


def _fetch_returns_matrix(tickers: List[str], lookback_days: int):
    """Return (pd.DataFrame of daily returns, fetched_tickers).

    Tickers that yfinance can't price are silently dropped — the rollup
    proceeds with whatever data is available.
    """
    try:
        import pandas as pd
        import yfinance as yf
    except ImportError:
        return None, []
    end = date.today()
    start = end - timedelta(days=lookback_days + 14)
    series: Dict[str, "pd.Series"] = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(
                start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                auto_adjust=True,
            )
            if df is None or df.empty:
                continue
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            series[t] = df["Close"].pct_change().dropna()
        except Exception as e:
            logger.warning(f"risk fetch {t}: {e}")
            continue
    if not series:
        return None, []
    returns_df = pd.concat(series, axis=1).dropna(how="all")
    return returns_df, list(returns_df.columns)


def _single_ticker_metrics(returns_series, current_value: Optional[float]) -> Dict[str, Optional[float]]:
    """Compute Sharpe / max DD / VaR for one ticker's daily return series."""
    try:
        import numpy as np
    except ImportError:
        return {k: None for k in (
            "annualized_volatility_pct", "annualized_return_pct", "sharpe",
            "max_drawdown_pct", "var_5pct_daily", "var_5pct_dollar",
        )}
    if returns_series is None or returns_series.empty or len(returns_series.dropna()) < 5:
        return {k: None for k in (
            "annualized_volatility_pct", "annualized_return_pct", "sharpe",
            "max_drawdown_pct", "var_5pct_daily", "var_5pct_dollar",
        )}
    r = returns_series.dropna().to_numpy()
    mean_daily = float(np.mean(r))
    std_daily = float(np.std(r, ddof=1))
    ann_vol = std_daily * np.sqrt(TRADING_DAYS_PER_YEAR) * 100
    ann_ret = mean_daily * TRADING_DAYS_PER_YEAR * 100
    sharpe = (mean_daily / std_daily * np.sqrt(TRADING_DAYS_PER_YEAR)) if std_daily > 0 else None
    var_5 = float(np.percentile(r, 5))                # negative
    var_dollar = (current_value * var_5) if current_value else None

    # Max drawdown — walk the cumulative return curve.
    cum = (1 + r).cumprod()
    running_peak = np.maximum.accumulate(cum)
    dd = (cum - running_peak) / running_peak
    max_dd = float(dd.min()) if len(dd) else None

    return {
        "annualized_volatility_pct": round(ann_vol, 2),
        "annualized_return_pct": round(ann_ret, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2) if max_dd is not None else None,
        "var_5pct_daily": round(var_5 * 100, 2),
        "var_5pct_dollar": round(var_dollar, 2) if var_dollar is not None else None,
    }


def _portfolio_metrics(returns_df, weights: Dict[str, float], total_value: Optional[float]) -> Dict[str, Optional[float]]:
    """Compute portfolio-level metrics by weighting per-ticker returns."""
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        return {k: None for k in (
            "annualized_volatility_pct", "annualized_return_pct", "sharpe",
            "max_drawdown_pct", "var_5pct_daily", "var_5pct_dollar",
        )}
    cols = [c for c in returns_df.columns if c in weights]
    if not cols:
        return {k: None for k in (
            "annualized_volatility_pct", "annualized_return_pct", "sharpe",
            "max_drawdown_pct", "var_5pct_daily", "var_5pct_dollar",
        )}
    # Normalize weights across the columns we have data for.
    w_vec = np.array([weights[c] for c in cols])
    w_vec = w_vec / w_vec.sum() if w_vec.sum() > 0 else w_vec
    sub = returns_df[cols].fillna(0)
    portfolio_returns = sub.dot(w_vec)
    return _single_ticker_metrics(portfolio_returns, total_value)


@router.get("/portfolio", response_model=PortfolioRiskResponse)
def portfolio_risk(
    lookback_days: int = Query(365, ge=30, le=1825),
    benchmark: str = Query("SPY"),
) -> PortfolioRiskResponse:
    """Portfolio + per-position risk metrics over the lookback window.

    Returns a uniform shape for both the book-level rollup and each held
    ticker, plus the benchmark for context. Average pairwise correlation
    is surfaced as a heads-up — a high average (>0.7) means the book
    behaves like one bet.
    """
    positions = storage.list_positions(include_closed=False)
    if not positions:
        return PortfolioRiskResponse(
            lookback_days=lookback_days,
            benchmark=benchmark,
            portfolio=PositionRisk(ticker="(book)", weight_pct=0.0),
            benchmark_risk=PositionRisk(ticker=benchmark, weight_pct=0.0),
            positions=[],
            note="No open positions.",
        )

    # Aggregate to per-ticker weights (cost basis fraction).
    by_ticker: Dict[str, Dict[str, float]] = {}
    total_basis = 0.0
    for p in positions:
        t = (p["ticker"] or "").upper()
        basis = float(p["shares"]) * float(p["cost_basis_per_share"])
        b = by_ticker.setdefault(t, {"basis": 0.0, "shares": 0.0})
        b["basis"] += basis
        b["shares"] += float(p["shares"])
        total_basis += basis

    weights = {t: b["basis"] / total_basis for t, b in by_ticker.items()} if total_basis > 0 else {}
    tickers = sorted(by_ticker.keys())

    returns_df, available = _fetch_returns_matrix(tickers + [benchmark], lookback_days)
    if returns_df is None or returns_df.empty:
        return PortfolioRiskResponse(
            lookback_days=lookback_days,
            benchmark=benchmark,
            portfolio=PositionRisk(ticker="(book)", weight_pct=100.0),
            benchmark_risk=PositionRisk(ticker=benchmark, weight_pct=0.0),
            positions=[],
            note="Could not fetch price data.",
        )

    # Per-position metrics (use cost basis as "current value" stand-in
    # for the VaR-dollar number; for real current-value we'd hit the
    # live-price broadcaster which would slow this endpoint).
    pos_rows: List[PositionRisk] = []
    for t in tickers:
        if t not in available:
            pos_rows.append(PositionRisk(
                ticker=t,
                weight_pct=round(weights.get(t, 0) * 100, 1),
                annualized_volatility_pct=None,
                annualized_return_pct=None,
                sharpe=None,
                max_drawdown_pct=None,
                var_5pct_daily=None,
                var_5pct_dollar=None,
            ))
            continue
        basis_value = by_ticker[t]["basis"]
        m = _single_ticker_metrics(returns_df[t], basis_value)
        pos_rows.append(PositionRisk(
            ticker=t,
            weight_pct=round(weights.get(t, 0) * 100, 1),
            **m,
        ))

    # Sort by weight descending.
    pos_rows.sort(key=lambda r: -r.weight_pct)

    portfolio_m = _portfolio_metrics(
        returns_df[[c for c in returns_df.columns if c in weights]],
        weights, total_basis,
    )

    # Benchmark
    if benchmark in available:
        bench_m = _single_ticker_metrics(returns_df[benchmark], total_basis)
    else:
        bench_m = {k: None for k in (
            "annualized_volatility_pct", "annualized_return_pct", "sharpe",
            "max_drawdown_pct", "var_5pct_daily", "var_5pct_dollar",
        )}

    # Avg pairwise correlation (excluding self + benchmark)
    corr_avg: Optional[float] = None
    try:
        import numpy as np
        ticker_cols = [c for c in returns_df.columns if c in weights]
        if len(ticker_cols) >= 2:
            corr = returns_df[ticker_cols].corr().to_numpy()
            mask = ~np.eye(len(ticker_cols), dtype=bool)
            corr_avg = round(float(np.mean(corr[mask])), 3)
    except Exception:
        corr_avg = None

    return PortfolioRiskResponse(
        lookback_days=lookback_days,
        benchmark=benchmark,
        portfolio=PositionRisk(ticker="(book)", weight_pct=100.0, **portfolio_m),
        benchmark_risk=PositionRisk(ticker=benchmark, weight_pct=0.0, **bench_m),
        positions=pos_rows,
        correlation_avg=corr_avg,
    )
