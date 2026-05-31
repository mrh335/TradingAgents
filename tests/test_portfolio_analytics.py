"""Unit tests for the portfolio analytics engine (service/portfolio_analytics.py).

Pure-math module — numpy/pandas only, no network — so every statistic is
verified locally against hand-computable expected values. This is the safety
net for the /sim/backtest + /sim/montecarlo endpoints.
"""

import math

import numpy as np
import pandas as pd
import pytest

from service import portfolio_analytics as pa


def _dates(n):
    return pd.bdate_range("2020-01-01", periods=n)


# ---- helpers -------------------------------------------------------------

def test_inv_norm_cdf_known_quantiles():
    assert pa._inv_norm_cdf(0.5) == pytest.approx(0.0, abs=1e-6)
    assert pa._inv_norm_cdf(0.975) == pytest.approx(1.959964, abs=1e-4)
    assert pa._inv_norm_cdf(0.025) == pytest.approx(-1.959964, abs=1e-4)
    assert pa._inv_norm_cdf(0.05) == pytest.approx(-1.644854, abs=1e-4)


def test_safe_filters_nan_inf():
    assert pa._safe(float("nan")) is None
    assert pa._safe(float("inf")) is None
    assert pa._safe(1.23456789) == 1.234568
    assert pa._safe(None) is None


def test_normalize_weights():
    w = pa.normalize_weights({"A": 2, "B": 2, "C": 0})
    assert w == {"A": 0.5, "B": 0.5}
    assert sum(w.values()) == pytest.approx(1.0)


def test_normalize_weights_rejects_all_zero():
    with pytest.raises(ValueError):
        pa.normalize_weights({"A": 0, "B": -1})


# ---- returns + curve -----------------------------------------------------

def test_equity_curve_and_cagr_exact():
    # +10% per year for exactly 1 trading-year (252 days), constant daily rate.
    daily = (1.10) ** (1 / 252) - 1
    rets = pd.Series([daily] * 252, index=_dates(252))
    curve = pa.equity_curve(rets, initial=100_000)
    assert curve.iloc[-1] == pytest.approx(110_000, rel=1e-4)
    # cagr over 252 days should recover ~10%.
    assert pa.cagr(curve) == pytest.approx(0.10, rel=1e-3)


def test_max_drawdown_exact():
    # Up to 100, down to 60 (=-40%), back up.
    curve = pd.Series([100, 120, 60, 90], index=_dates(4))
    assert pa.max_drawdown(curve) == pytest.approx(-0.5)  # 120 -> 60 = -50%


def test_portfolio_returns_daily_rebalance_is_weighted_avg():
    rets = pd.DataFrame(
        {"A": [0.10, 0.0], "B": [0.0, 0.20]}, index=_dates(2)
    )
    port = pa.portfolio_returns(rets, {"A": 0.5, "B": 0.5}, rebalance="daily")
    assert port.iloc[0] == pytest.approx(0.05)  # (0.10+0)/2
    assert port.iloc[1] == pytest.approx(0.10)  # (0+0.20)/2


def test_portfolio_returns_buy_and_hold_curve():
    # Two assets, 50/50, buy-and-hold. A: +10% then 0; B: 0 then +20%.
    rets = pd.DataFrame(
        {"A": [0.10, 0.0], "B": [0.0, 0.20]}, index=_dates(2)
    )
    port = pa.portfolio_returns(rets, {"A": 0.5, "B": 0.5}, rebalance="none")
    curve = pa.equity_curve(port, initial=1.0)
    # A sleeve: 0.5 -> 0.55 -> 0.55 ; B sleeve: 0.5 -> 0.5 -> 0.6 ; total -> 1.15
    assert curve.iloc[-1] == pytest.approx(1.15, rel=1e-9)


def test_stat_pack_volatility_and_positive_days():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0005, 0.01, 2520), index=_dates(2520))
    stats = pa.stat_pack(r)
    # annualized vol should be ~ daily std * sqrt(252) ~ 0.01*15.87 ~ 0.1587
    assert stats["volatility"] == pytest.approx(0.1587, abs=0.02)
    assert 0.4 < stats["pct_positive_days"] < 0.6
    assert stats["n_days"] == 2520


def test_stat_pack_beta_alpha_self_is_one_and_zero():
    rng = np.random.default_rng(1)
    bench = pd.Series(rng.normal(0.0004, 0.009, 1000), index=_dates(1000))
    # Portfolio identical to benchmark => beta 1, alpha 0, corr 1.
    stats = pa.stat_pack(bench.copy(), benchmark_rets=bench)
    assert stats["beta"] == pytest.approx(1.0, abs=1e-6)
    assert stats["alpha"] == pytest.approx(0.0, abs=1e-6)
    assert stats["correlation_to_benchmark"] == pytest.approx(1.0, abs=1e-9)


def test_stat_pack_beta_two_when_double_market():
    rng = np.random.default_rng(2)
    bench = pd.Series(rng.normal(0.0, 0.01, 1500), index=_dates(1500))
    port = bench * 2.0  # exactly twice the market, no idiosyncratic noise
    stats = pa.stat_pack(port, benchmark_rets=bench)
    assert stats["beta"] == pytest.approx(2.0, abs=1e-6)
    assert stats["correlation_to_benchmark"] == pytest.approx(1.0, abs=1e-6)


def test_windowed_returns():
    # Span a real 6 CALENDAR years (daily) so the 5y lookback has data.
    idx = pd.bdate_range("2020-01-01", "2026-01-01")
    px = pd.Series(100 * (1.0 + np.linspace(0, 1.0, len(idx))), index=idx)
    w = pa.windowed_returns(px, years=(1, 2, 3, 5))
    assert w["1y"] is not None and w["1y"] > 0
    assert w["5y"] is not None and w["5y"] > w["1y"]  # longer window, more growth


def test_windowed_returns_none_when_history_too_short():
    # Only ~1.5y of data: the 5y window must be None, not a wrong number.
    idx = pd.bdate_range("2025-01-01", periods=380)
    px = pd.Series(np.linspace(100, 150, len(idx)), index=idx)
    w = pa.windowed_returns(px, years=(1, 5))
    assert w["1y"] is not None
    assert w["5y"] is None


def test_correlation_matrix_perfectly_correlated():
    idx = _dates(100)
    base = pd.Series(np.linspace(0, 1, 100), index=idx)
    rets = pd.DataFrame({"A": base, "B": base, "C": -base}, index=idx)
    cm = pa.correlation_matrix(rets)
    assert cm["A"]["B"] == pytest.approx(1.0, abs=1e-9)
    assert cm["A"]["C"] == pytest.approx(-1.0, abs=1e-9)


# ---- Monte Carlo ---------------------------------------------------------

def test_monte_carlo_structure_and_determinism():
    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0005, 0.01, 1000), index=_dates(1000))
    mc1 = pa.monte_carlo(r, horizon_days=252, n_paths=2000, seed=42)
    mc2 = pa.monte_carlo(r, horizon_days=252, n_paths=2000, seed=42)
    # Deterministic under fixed seed.
    assert mc1["ending"]["median"] == mc2["ending"]["median"]
    # Fan percentiles are ordered p5 <= p50 <= p95 at the horizon.
    last = mc1["fan"][-1]
    assert last["p5"] <= last["p50"] <= last["p95"]
    # Probabilities are valid.
    assert 0.0 <= mc1["prob_loss"] <= 1.0
    assert mc1["horizon_days"] == 252
    # Histogram counts sum to n_paths.
    assert sum(b["count"] for b in mc1["histogram"]) == 2000


def test_monte_carlo_positive_drift_beats_loss_half():
    rng = np.random.default_rng(4)
    # Strong positive drift => low probability of loss over a year.
    r = pd.Series(rng.normal(0.001, 0.008, 1000), index=_dates(1000))
    mc = pa.monte_carlo(r, horizon_days=252, n_paths=3000, method="normal", seed=7)
    assert mc["prob_loss"] < 0.4
    assert mc["expected_return_pct"] > 0


def test_monte_carlo_bootstrap_vs_normal_both_run():
    rng = np.random.default_rng(5)
    r = pd.Series(rng.normal(0.0003, 0.012, 800), index=_dates(800))
    for method in ("bootstrap", "normal"):
        mc = pa.monte_carlo(r, horizon_days=60, n_paths=1000, method=method, seed=1)
        assert mc["method"] == method
        assert mc["var_95_pct"] is not None
        assert mc["cvar_95_pct"] <= mc["var_95_pct"]  # CVaR is worse than VaR


def test_monte_carlo_needs_data():
    with pytest.raises(ValueError):
        pa.monte_carlo(pd.Series([0.01]), horizon_days=10)
