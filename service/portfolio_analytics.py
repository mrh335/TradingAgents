"""Portfolio analytics engine — historical backtest + risk statistics +
Monte Carlo, on real total-return price history.

Design goals:
* **Pure, testable core.** All math lives in functions that take a pandas
  price/return frame and return plain dicts — no network, no FastAPI. The
  router layer does the yfinance fetch and hands frames in. This means the
  whole stat pack is unit-tested locally (tests/test_portfolio_analytics.py)
  without hitting the network or the container.
* **numpy/pandas only.** No scipy/statsmodels, so the API image needs no
  new dependency. The normal-quantile helper uses a rational approximation.
* **Total return.** Callers pass auto-adjusted (dividends reinvested) close
  prices, so dividends are included.

Vocabulary (this app's audience is an engineer, not a finance person):
* CAGR — compound annual growth rate (geometric mean annual return).
* Volatility — annualized standard deviation of returns (like vibration
  amplitude: bigger = bumpier ride, not necessarily worse return).
* Sharpe — return per unit of total volatility (higher = more reward per
  unit of bumpiness). Sortino — same but only counts *downside* bumpiness.
* Max drawdown — worst peak-to-trough drop along the way (the deepest hole).
* Beta — sensitivity to the market (1.0 = moves with SPY, 2.0 = twice as
  swingy). Alpha — annualized return left over after accounting for beta.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

TRADING_DAYS = 252
DEFAULT_RF = 0.04  # annual risk-free rate assumption for Sharpe/Sortino


# --------------------------------------------------------------------------
# Small numeric helpers (no scipy)
# --------------------------------------------------------------------------

def _inv_norm_cdf(p: float) -> float:
    """Inverse standard-normal CDF (probit) via Acklam's rational approx.

    Max abs error ~1.15e-9 over (0,1). Used for parametric VaR percentiles so
    we don't pull in scipy.stats just for one quantile.
    """
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _safe(x: float) -> Optional[float]:
    """JSON-safe float: turn NaN/inf into None so responses don't break."""
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(xf) or math.isinf(xf):
        return None
    return round(xf, 6)


# --------------------------------------------------------------------------
# Returns + weighting
# --------------------------------------------------------------------------

def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Simple daily returns from a price frame (cols = tickers)."""
    return prices.pct_change().dropna(how="all")


def normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """Drop non-positive weights and renormalize to sum to 1.0."""
    pos = {k: float(v) for k, v in weights.items() if v and float(v) > 0}
    total = sum(pos.values())
    if total <= 0:
        raise ValueError("weights must contain at least one positive value")
    return {k: v / total for k, v in pos.items()}


def portfolio_returns(
    rets: pd.DataFrame, weights: Dict[str, float], *, rebalance: str = "none"
) -> pd.Series:
    """Daily return series for a weighted portfolio.

    rebalance:
      * "none"  — buy-and-hold; weights drift with performance (the realistic
        default for "what if I'd bought this basket and never touched it").
      * "daily" — constant-weight (rebalanced every day); the textbook
        weighted-average-returns model.
    """
    w = normalize_weights(weights)
    tickers = [t for t in w if t in rets.columns]
    if not tickers:
        raise ValueError("none of the weighted tickers are in the price data")
    sub = rets[tickers].dropna(how="any")
    wvec = np.array([w[t] for t in tickers])
    wvec = wvec / wvec.sum()

    if rebalance == "daily":
        return pd.Series(sub.values @ wvec, index=sub.index)

    # Buy-and-hold: grow each sleeve from $1*weight and sum the equity curves.
    growth = (1 + sub).cumprod()
    equity = growth.mul(wvec, axis=1).sum(axis=1)
    port = equity.pct_change()
    # First day's return relative to the $1 starting basket.
    first = equity.iloc[0] - 1.0
    port.iloc[0] = first
    return port


def equity_curve(port_rets: pd.Series, initial: float = 100_000.0) -> pd.Series:
    return initial * (1 + port_rets).cumprod()


# --------------------------------------------------------------------------
# Risk statistics
# --------------------------------------------------------------------------

def max_drawdown(curve: pd.Series) -> float:
    """Worst peak-to-trough fractional drop (negative number, e.g. -0.42)."""
    if curve.empty:
        return 0.0
    running_max = curve.cummax()
    dd = curve / running_max - 1.0
    return float(dd.min())


def cagr(curve: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    if len(curve) < 2:
        return 0.0
    total = curve.iloc[-1] / curve.iloc[0]
    years = (len(curve) - 1) / periods_per_year
    if years <= 0 or total <= 0:
        return 0.0
    return float(total ** (1 / years) - 1)


def stat_pack(
    port_rets: pd.Series,
    *,
    benchmark_rets: Optional[pd.Series] = None,
    rf_annual: float = DEFAULT_RF,
    initial: float = 100_000.0,
) -> Dict[str, Optional[float]]:
    """Full professional statistics for one return series."""
    r = port_rets.dropna()
    if r.empty:
        return {}
    curve = equity_curve(r, initial)
    n = len(r)
    rf_daily = rf_annual / TRADING_DAYS

    mean_d = float(r.mean())
    std_d = float(r.std(ddof=1)) if n > 1 else 0.0
    downside = r[r < 0]
    downside_std_d = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0

    ann_return = cagr(curve)
    ann_vol = std_d * math.sqrt(TRADING_DAYS)
    sharpe = ((mean_d - rf_daily) / std_d * math.sqrt(TRADING_DAYS)) if std_d > 0 else None
    sortino = ((mean_d - rf_daily) / downside_std_d * math.sqrt(TRADING_DAYS)) if downside_std_d > 0 else None

    out = {
        "total_return": _safe(curve.iloc[-1] / curve.iloc[0] - 1),
        "cagr": _safe(ann_return),
        "volatility": _safe(ann_vol),
        "sharpe": _safe(sharpe),
        "sortino": _safe(sortino),
        "max_drawdown": _safe(max_drawdown(curve)),
        "best_day": _safe(r.max()),
        "worst_day": _safe(r.min()),
        "pct_positive_days": _safe((r > 0).mean()),
        "final_value": _safe(curve.iloc[-1]),
        "n_days": n,
    }

    # Calmar — CAGR over the depth of the worst hole. Reward vs worst pain.
    mdd = max_drawdown(curve)
    out["calmar"] = _safe(ann_return / abs(mdd)) if mdd < 0 else None

    if benchmark_rets is not None:
        aligned = pd.concat([r, benchmark_rets], axis=1, join="inner").dropna()
        if len(aligned) > 2:
            pr = aligned.iloc[:, 0].values
            br = aligned.iloc[:, 1].values
            var_b = float(np.var(br, ddof=1))
            beta = float(np.cov(pr, br, ddof=1)[0, 1] / var_b) if var_b > 0 else None
            # Annualized alpha (CAPM): a = Rp - [Rf + beta*(Rm - Rf)]
            rp_ann = float(np.mean(pr)) * TRADING_DAYS
            rm_ann = float(np.mean(br)) * TRADING_DAYS
            alpha = (rp_ann - (rf_annual + (beta or 0) * (rm_ann - rf_annual))) if beta is not None else None
            corr = float(np.corrcoef(pr, br)[0, 1])
            out["beta"] = _safe(beta)
            out["alpha"] = _safe(alpha)
            out["correlation_to_benchmark"] = _safe(corr)
    return out


def windowed_returns(
    prices: pd.Series, years: Sequence[int] = (1, 2, 3, 5)
) -> Dict[str, Optional[float]]:
    """Trailing total return over each lookback window (from a price series)."""
    s = prices.dropna()
    if s.empty:
        return {f"{y}y": None for y in years}
    last_date = s.index[-1]
    last_px = float(s.iloc[-1])
    out: Dict[str, Optional[float]] = {}
    for y in years:
        target = last_date - pd.DateOffset(years=y)
        prior = s.loc[:target]
        if prior.empty:
            out[f"{y}y"] = None
        else:
            p0 = float(prior.iloc[-1])
            out[f"{y}y"] = _safe(last_px / p0 - 1) if p0 > 0 else None
    return out


def correlation_matrix(rets: pd.DataFrame) -> Dict[str, Dict[str, Optional[float]]]:
    corr = rets.corr()
    return {
        row: {col: _safe(corr.loc[row, col]) for col in corr.columns}
        for row in corr.index
    }


# --------------------------------------------------------------------------
# Monte Carlo
# --------------------------------------------------------------------------

def monte_carlo(
    port_rets: pd.Series,
    *,
    horizon_days: int,
    n_paths: int = 5000,
    method: str = "bootstrap",
    initial: float = 100_000.0,
    seed: int = 42,
    benchmark_rets: Optional[pd.Series] = None,
) -> Dict[str, object]:
    """Forward Monte Carlo from the historical return distribution.

    method:
      * "bootstrap" — resample actual historical daily returns with
        replacement (keeps fat tails / skew; makes no normality assumption).
      * "normal"    — draw from a normal fit to the daily mean/std (classic
        GBM-style). Faster, smoother, but understates tail risk.

    Returns percentile fan over time + an ending-value distribution +
    headline probabilities (loss, beating the benchmark) and VaR/CVaR.
    """
    r = port_rets.dropna().values
    if len(r) < 2:
        raise ValueError("need at least 2 return observations for Monte Carlo")

    rng = np.random.default_rng(seed)
    mu_d = float(np.mean(r))
    sd_d = float(np.std(r, ddof=1))

    if method == "normal":
        draws = rng.normal(mu_d, sd_d, size=(n_paths, horizon_days))
    else:
        idx = rng.integers(0, len(r), size=(n_paths, horizon_days))
        draws = r[idx]

    # Equity paths: initial * cumprod(1+r) along the horizon.
    paths = initial * np.cumprod(1.0 + draws, axis=1)
    paths = np.concatenate([np.full((n_paths, 1), initial), paths], axis=1)

    pct_levels = [5, 25, 50, 75, 95]
    # Downsample the time axis to <=60 points to keep the payload small.
    T = horizon_days + 1
    step = max(1, T // 60)
    cols = list(range(0, T, step))
    if cols[-1] != T - 1:
        cols.append(T - 1)

    fan = []
    for c in cols:
        col = paths[:, c]
        entry = {"day": int(c)}
        for p in pct_levels:
            entry[f"p{p}"] = _safe(np.percentile(col, p))
        entry["mean"] = _safe(float(np.mean(col)))
        fan.append(entry)

    ending = paths[:, -1]
    ending_ret = ending / initial - 1.0

    # Histogram of ending returns (for the distribution chart).
    hist_counts, hist_edges = np.histogram(ending_ret, bins=30)
    histogram = [
        {"low": _safe(hist_edges[i]), "high": _safe(hist_edges[i + 1]),
         "count": int(hist_counts[i])}
        for i in range(len(hist_counts))
    ]

    # 95% VaR / CVaR on ending return (loss is negative).
    var95_level = float(np.percentile(ending_ret, 5))
    cvar95 = float(ending_ret[ending_ret <= var95_level].mean()) if np.any(ending_ret <= var95_level) else var95_level

    out: Dict[str, object] = {
        "method": method,
        "n_paths": n_paths,
        "horizon_days": horizon_days,
        "initial": initial,
        "fan": fan,
        "histogram": histogram,
        "ending": {
            "mean": _safe(float(np.mean(ending))),
            "median": _safe(float(np.median(ending))),
            "p5": _safe(float(np.percentile(ending, 5))),
            "p95": _safe(float(np.percentile(ending, 95))),
            "min": _safe(float(np.min(ending))),
            "max": _safe(float(np.max(ending))),
        },
        "prob_loss": _safe(float(np.mean(ending_ret < 0))),
        "prob_double": _safe(float(np.mean(ending_ret >= 1.0))),
        "var_95_pct": _safe(var95_level),
        "cvar_95_pct": _safe(cvar95),
        "expected_return_pct": _safe(float(np.mean(ending_ret))),
        "median_return_pct": _safe(float(np.median(ending_ret))),
    }

    # Probability of beating a benchmark path simulated the same way.
    if benchmark_rets is not None:
        br = benchmark_rets.dropna().values
        if len(br) >= 2:
            if method == "normal":
                bmu, bsd = float(np.mean(br)), float(np.std(br, ddof=1))
                bdraws = rng.normal(bmu, bsd, size=(n_paths, horizon_days))
            else:
                bidx = rng.integers(0, len(br), size=(n_paths, horizon_days))
                bdraws = br[bidx]
            bpaths_end = initial * np.prod(1.0 + bdraws, axis=1)
            out["prob_beat_benchmark"] = _safe(float(np.mean(ending > bpaths_end)))
    return out
