"""Market regime classification — three tiers.

The TradingAgents framework's recommendations are LLM-generated and don't
explicitly know what kind of market they're being made in. This module
augments them with regime context so the user can calibrate confidence:

> "This Buy was made in a VOLATILE_BEAR regime. Historically the framework
>  has 47% hit rate in this regime vs 64% baseline — read skeptically."

Three tiers, all computed from yfinance SPY + VIX:

**Tier 1 — Rule-based classifier** (the headline number)
    Simple, deterministic, interpretable. Each day → exactly one of
    4 regimes based on:
      - Trend: SPY above/below its 200-day SMA
      - Volatility: VIX above/below 20
    Pros: zero training, easy to explain, always agrees with intuition.
    Cons: 4 hard buckets miss nuance like "we're at the BULL/BEAR
    transition" (currently classified as one or the other).

**Tier 2 — Markov chain on tier-1 regimes**
    Empirical transition matrix between the 4 regimes from history,
    its stationary distribution (long-run % time in each regime),
    and 30-day forecast (matrix^30 from current state).
    Pros: tells you which regime is "sticky" (high diagonal probability)
    vs "transitional" (off-diagonal probability mass).
    Use case: "current state is VOLATILE_BULL. Transition probabilities:
    35% stay VOLATILE_BULL, 30% to CALM_BULL, 25% to VOLATILE_BEAR..."

**Tier 3 — Hidden Markov Model (HMM)**
    GaussianHMM with 4 latent states, EM-fit (Baum-Welch) on (SPY daily
    log return, VIX level) features. Lets the model discover its own
    regime boundaries rather than us hand-coding the VIX-20 threshold.
    Pros: data-driven regimes, soft probability assignment per day
    (rather than hard bucket).
    Cons: more compute (refit weekly), harder to interpret why a
    specific day got a specific state.

All three are cached: tier 1/2 in process memory for 1 hour, tier 3
(HMM) fit once per day and pickled.

Public API:
    get_regime_snapshot()           → tier 1 current + tier 2 transition + forecast
    get_regime_history(days)        → daily regime tags
    get_regime_for_date(yyyy_mm_dd) → tier 1 regime for one specific date
    get_hmm_snapshot()              → tier 3 fitted state assignments
    regime_for_run_dates(dates)     → batch lookup for runs[].trade_date
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────
# Regime taxonomy (shared across all three tiers)
# ───────────────────────────────────────────────────────────────────────

REGIMES = ("CALM_BULL", "VOLATILE_BULL", "VOLATILE_BEAR", "CALM_BEAR")
N_REGIMES = len(REGIMES)


# Plain-English descriptions, surfaced to the user in the UI.
REGIME_LABELS: Dict[str, str] = {
    "CALM_BULL": "Calm bull market",
    "VOLATILE_BULL": "Volatile bull market",
    "VOLATILE_BEAR": "Volatile bear market",
    "CALM_BEAR": "Calm bear market (rare)",
}

REGIME_BLURB: Dict[str, str] = {
    "CALM_BULL": (
        "Steady uptrend with low fear (VIX under 20). Historically the easiest "
        "regime to make money in — trend-following strategies work."
    ),
    "VOLATILE_BULL": (
        "Uptrend but choppy (VIX 20+). Drawdowns happen fast even though the "
        "longer trend is up. Smaller positions and wider stops make sense."
    ),
    "VOLATILE_BEAR": (
        "Downtrend with elevated fear. Most painful regime for long-only "
        "investors. Defensive positioning warranted; selective shorts can work."
    ),
    "CALM_BEAR": (
        "Slow grinding decline at low VIX (uncommon). Often a 'walking-dead' "
        "market — no clear panic, no rebound. Defensive cash / quality bias."
    ),
}

# Tone classes for UI rendering (Tailwind text colors).
REGIME_TONE: Dict[str, str] = {
    "CALM_BULL": "text-success",
    "VOLATILE_BULL": "text-warning",
    "VOLATILE_BEAR": "text-danger",
    "CALM_BEAR": "text-muted",
}


# ───────────────────────────────────────────────────────────────────────
# Tier 1: rule-based classifier
# ───────────────────────────────────────────────────────────────────────

# Thresholds. Adjust if backtests show better separation at different
# cutoffs. Defaults are the "textbook" boundaries from the VIX
# distribution: <20 = calm, 20+ = volatile.
SMA_WINDOW = 200      # days for the SPY trend baseline
VIX_VOLATILE = 20.0   # VIX level that splits "calm" from "volatile"


def classify_day(spy_close: Optional[float],
                  spy_sma_200: Optional[float],
                  vix_level: Optional[float]) -> Optional[str]:
    """Rule-based regime for a single trading day.

    Returns None if any input is missing — the caller is expected to
    forward-fill or skip days where market data wasn't published yet.
    """
    if spy_close is None or spy_sma_200 is None or vix_level is None:
        return None
    bull = spy_close > spy_sma_200
    volatile = vix_level >= VIX_VOLATILE
    if bull and not volatile:
        return "CALM_BULL"
    if bull and volatile:
        return "VOLATILE_BULL"
    if (not bull) and volatile:
        return "VOLATILE_BEAR"
    return "CALM_BEAR"


# ───────────────────────────────────────────────────────────────────────
# Tier 2: Markov math on the tier-1 daily regime sequence
# ───────────────────────────────────────────────────────────────────────

def transition_matrix(regime_sequence: List[Optional[str]]) -> "Any":
    """Empirical day-over-day transition probability matrix.

    Rows = "from" regime, columns = "to" regime. Each row sums to 1.0
    (a probability distribution over the next state given the current
    state). Drops any None entries from the sequence first so missing
    days don't poison the counts.
    """
    import numpy as np
    seq = [r for r in regime_sequence if r in REGIMES]
    n = N_REGIMES
    counts = np.zeros((n, n), dtype=float)
    for prev, curr in zip(seq[:-1], seq[1:]):
        i = REGIMES.index(prev)
        j = REGIMES.index(curr)
        counts[i][j] += 1.0
    # Row-normalize. A regime never seen as "from" gets a uniform fallback
    # so the matrix stays a valid stochastic operator.
    row_sums = counts.sum(axis=1, keepdims=True)
    safe_rows = np.where(row_sums == 0, 1.0, row_sums)
    matrix = counts / safe_rows
    # Force uniform fallback for empty rows.
    for i in range(n):
        if row_sums[i, 0] == 0:
            matrix[i, :] = 1.0 / n
    return matrix


def stationary_distribution(matrix: "Any") -> "Any":
    """Long-run frequency of each regime under the given transition matrix.

    Computed as the left eigenvector of the matrix with eigenvalue 1
    (the Perron-Frobenius vector for an irreducible stochastic matrix).
    Falls back to row-averaging if the eigendecomposition is degenerate
    (e.g. some regime never appears in the history).
    """
    import numpy as np
    try:
        evals, evecs = np.linalg.eig(matrix.T)
        # Find the index where eigenvalue is closest to 1.
        idx = int(np.argmin(np.abs(evals - 1.0)))
        v = evecs[:, idx].real
        s = v.sum()
        if abs(s) < 1e-12:
            raise ValueError("degenerate stationary vector")
        return v / s
    except Exception as e:
        logger.warning("stationary_distribution fallback: %s", e)
        # Fall back: the marginal distribution of the input matrix.
        col_avg = matrix.mean(axis=0)
        return col_avg / col_avg.sum()


def forecast_distribution(current_regime: str, matrix: "Any",
                           horizon_days: int) -> Dict[str, float]:
    """Probability of being in each regime ``horizon_days`` from now,
    assuming we're in ``current_regime`` today.

    Math: initial state vector × matrix^horizon_days. Approximates the
    "where might the market be in N days" question.
    """
    import numpy as np
    if current_regime not in REGIMES:
        return {r: 0.0 for r in REGIMES}
    initial = np.zeros(N_REGIMES)
    initial[REGIMES.index(current_regime)] = 1.0
    powered = np.linalg.matrix_power(matrix, max(1, horizon_days))
    result = initial @ powered
    return {REGIMES[i]: float(result[i]) for i in range(N_REGIMES)}


# ───────────────────────────────────────────────────────────────────────
# Market data fetch + cache
# ───────────────────────────────────────────────────────────────────────

_SNAPSHOT_CACHE: Dict[str, Any] = {}
_SNAPSHOT_LOCK = threading.Lock()
_SNAPSHOT_TTL_SEC = 3600   # 1 hour


def _fetch_market_data(lookback_days: int = 365 * 5) -> Optional["Any"]:
    """Pull SPY closes + VIX levels from yfinance, return a joined
    DataFrame indexed by date with columns: spy_close, vix_level,
    spy_sma_200, spy_log_return.

    Cached for 1 hour because both series only change once a day.
    """
    try:
        import yfinance as yf
        import pandas as pd
        import numpy as np
    except ImportError:
        logger.warning("regime: numpy/pandas/yfinance not available")
        return None

    try:
        end = date.today() + timedelta(days=1)
        start = date.today() - timedelta(days=lookback_days)
        spy = yf.Ticker("SPY").history(
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
        )
        vix = yf.Ticker("^VIX").history(
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=False,
        )
    except Exception as e:
        logger.warning("regime: yfinance fetch failed: %s", e)
        return None

    if spy is None or spy.empty or vix is None or vix.empty:
        return None

    # Strip tz for clean join.
    if spy.index.tz is not None:
        spy.index = spy.index.tz_localize(None)
    if vix.index.tz is not None:
        vix.index = vix.index.tz_localize(None)

    df = pd.DataFrame({
        "spy_close": spy["Close"],
        "vix_level": vix["Close"],
    }).dropna()

    df["spy_sma_200"] = df["spy_close"].rolling(SMA_WINDOW, min_periods=20).mean()
    df["spy_log_return"] = np.log(df["spy_close"] / df["spy_close"].shift(1))
    return df


def _build_snapshot(lookback_days: int) -> Dict[str, Any]:
    """Compute the tier-1 + tier-2 snapshot from fresh market data.

    Cached upstream; this does the actual work."""
    df = _fetch_market_data(lookback_days)
    if df is None or df.empty:
        return {"available": False, "error": "no market data"}

    # Tier 1: classify every day.
    regimes_by_date: Dict[str, str] = {}
    sequence: List[Optional[str]] = []
    for ts, row in df.iterrows():
        d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
        regime = classify_day(
            row.get("spy_close"),
            row.get("spy_sma_200"),
            row.get("vix_level"),
        )
        regimes_by_date[d] = regime
        sequence.append(regime)

    # Current regime: the latest day's classification.
    current = sequence[-1] if sequence else None

    # Tier 2: transition matrix + stationary distribution + 30d forecast.
    matrix = transition_matrix(sequence)
    stationary = stationary_distribution(matrix)
    forecast_30d = forecast_distribution(current, matrix, 30) if current else {}

    return {
        "available": True,
        "as_of": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else None,
        "current_regime": current,
        "current_spy": float(df["spy_close"].iloc[-1]),
        "current_vix": float(df["vix_level"].iloc[-1]),
        "current_sma_200": float(df["spy_sma_200"].iloc[-1]) if df["spy_sma_200"].notna().iloc[-1] else None,
        "transition_matrix": matrix.tolist(),
        "stationary": {REGIMES[i]: float(stationary[i]) for i in range(N_REGIMES)},
        "forecast_30d": forecast_30d,
        "regimes_by_date": regimes_by_date,
        "n_days_observed": len(sequence),
    }


def get_regime_snapshot(lookback_days: int = 365 * 5) -> Dict[str, Any]:
    """Cached entry point for tier 1 + tier 2 data. Refreshes hourly."""
    key = f"snapshot_{lookback_days}"
    now = time.time()
    with _SNAPSHOT_LOCK:
        cached = _SNAPSHOT_CACHE.get(key)
        if cached and (now - cached["_ts"] < _SNAPSHOT_TTL_SEC):
            return cached["data"]
        data = _build_snapshot(lookback_days)
        _SNAPSHOT_CACHE[key] = {"_ts": now, "data": data}
        return data


def get_regime_history(lookback_days: int = 365 * 2) -> Dict[str, str]:
    """Map of YYYY-MM-DD → regime label, for every trading day in the window.

    Useful for tagging historical runs by the regime that was active on
    their trade_date."""
    snap = get_regime_snapshot(lookback_days)
    return snap.get("regimes_by_date", {})


def regime_for_run_dates(trade_dates: List[str],
                          lookback_days: int = 365 * 5) -> Dict[str, Optional[str]]:
    """For each YYYY-MM-DD trade_date, return the regime that was active
    on (or the most recent trading day before) that date.

    Returns None for dates older than the lookback window or after the
    last trading day we have data for.
    """
    history = get_regime_history(lookback_days)
    if not history:
        return {d: None for d in trade_dates}
    # Build a sorted list of (date, regime) and do a binary search per
    # query date so non-trading-day requests (weekends/holidays) fall
    # back to the previous trading day.
    sorted_dates = sorted(history.keys())
    out: Dict[str, Optional[str]] = {}
    for q in trade_dates:
        # Find the largest sorted_dates[i] that is <= q.
        idx = _bisect_le(sorted_dates, q)
        if idx is None:
            out[q] = None
        else:
            out[q] = history.get(sorted_dates[idx])
    return out


def _bisect_le(sorted_list: List[str], target: str) -> Optional[int]:
    """Return the index of the largest element <= target, or None."""
    import bisect
    i = bisect.bisect_right(sorted_list, target)
    return (i - 1) if i > 0 else None


# ───────────────────────────────────────────────────────────────────────
# Tier 3: Hidden Markov Model (Baum-Welch fit, GaussianHMM)
# ───────────────────────────────────────────────────────────────────────

# HMM cache — fit once per day, hold in memory.
_HMM_CACHE: Dict[str, Any] = {}
_HMM_LOCK = threading.Lock()
_HMM_TTL_SEC = 24 * 3600


def get_hmm_snapshot(lookback_days: int = 365 * 5,
                      n_states: int = 4) -> Dict[str, Any]:
    """Tier 3 — fit a Gaussian HMM on (SPY log return, VIX level) and
    return per-day state assignments + transition + emission stats.

    Caches the fitted model for 24 hours. If hmmlearn isn't installed
    or the fit fails, returns {"available": False, "error": "..."}.
    """
    key = f"hmm_{lookback_days}_{n_states}"
    now = time.time()
    with _HMM_LOCK:
        cached = _HMM_CACHE.get(key)
        if cached and (now - cached["_ts"] < _HMM_TTL_SEC):
            return cached["data"]
        try:
            data = _fit_hmm_and_score(lookback_days, n_states)
        except Exception as e:
            logger.exception("regime: HMM fit failed")
            data = {"available": False, "error": str(e)[:300]}
        _HMM_CACHE[key] = {"_ts": now, "data": data}
        return data


def _fit_hmm_and_score(lookback_days: int, n_states: int) -> Dict[str, Any]:
    """Fit a GaussianHMM and return the structured snapshot."""
    try:
        import numpy as np
    except ImportError:
        return {"available": False, "error": "numpy not installed"}
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError:
        return {
            "available": False,
            "error": "hmmlearn not installed — add to requirements + redeploy",
        }

    df = _fetch_market_data(lookback_days)
    if df is None or df.empty:
        return {"available": False, "error": "no market data"}

    # Build the 2-feature observation matrix. Drop the first row (NaN
    # log return) and any rows where features are NaN.
    feat = df[["spy_log_return", "vix_level"]].dropna().to_numpy()
    if len(feat) < 250:
        return {
            "available": False,
            "error": f"need at least 250 days of data; have {len(feat)}",
        }

    # Standardize features so VIX doesn't dominate variance.
    means = feat.mean(axis=0)
    stds = feat.std(axis=0)
    stds[stds == 0] = 1.0
    feat_z = (feat - means) / stds

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="full",
        n_iter=200,
        tol=1e-3,
        random_state=42,
    )
    model.fit(feat_z)
    state_seq = model.predict(feat_z)  # one state index per day

    # Label each latent state with a human regime name based on its
    # mean SPY return + emission variance. Highest mean return = bullish,
    # lowest = bearish; higher variance = volatile, lower = calm.
    state_means_return = model.means_[:, 0]  # standardized — relative ordering is what matters
    state_vars_return = np.array([model.covars_[k][0, 0] for k in range(n_states)])

    state_to_label = _label_hmm_states(state_means_return, state_vars_return, n_states)

    # Map state sequence back to dated labels.
    feat_dates = df.dropna(subset=["spy_log_return", "vix_level"]).index
    hmm_by_date: Dict[str, str] = {}
    for d, s in zip(feat_dates, state_seq):
        date_str = d.date().isoformat() if hasattr(d, "date") else str(d)[:10]
        hmm_by_date[date_str] = state_to_label.get(int(s), f"STATE_{s}")

    # Transition matrix of the HMM directly from learned parameters.
    hmm_matrix = model.transmat_.tolist()
    # Reorder rows/cols to match REGIMES order for consistent UI.
    hmm_matrix_labeled = _reorder_to_regime_axes(model.transmat_, state_to_label)

    # Agreement with tier 1 — what % of days do they agree?
    tier1 = get_regime_history(lookback_days)
    agree = total = 0
    for d, hmm_lbl in hmm_by_date.items():
        t1 = tier1.get(d)
        if t1 is None:
            continue
        total += 1
        if hmm_lbl == t1:
            agree += 1
    agreement_pct = round(agree / total * 100.0, 1) if total else None

    return {
        "available": True,
        "n_states": n_states,
        "as_of": list(hmm_by_date.keys())[-1] if hmm_by_date else None,
        "current_regime": list(hmm_by_date.values())[-1] if hmm_by_date else None,
        "state_to_label": {str(k): v for k, v in state_to_label.items()},
        "hmm_transition_matrix": hmm_matrix_labeled,
        "raw_transition_matrix": hmm_matrix,
        "regimes_by_date": hmm_by_date,
        "tier1_agreement_pct": agreement_pct,
        "n_days_observed": len(state_seq),
        "feature_means": [float(x) for x in means],
        "feature_stds": [float(x) for x in stds],
    }


def _label_hmm_states(means_return, vars_return, n_states: int) -> Dict[int, str]:
    """Assign canonical regime labels to learned HMM states.

    Strategy: rank states by mean return (desc), split into "bull half"
    and "bear half". Within each half, the lower-variance state is the
    "calm" version and the higher-variance is the "volatile" version.

    For n_states == 4 this maps cleanly to CALM_BULL / VOLATILE_BULL /
    VOLATILE_BEAR / CALM_BEAR. For other counts, falls back to STATE_N.
    """
    import numpy as np
    if n_states != 4:
        return {i: f"STATE_{i}" for i in range(n_states)}
    order = np.argsort(-means_return)  # descending by mean return
    bull_top, bull_bot = int(order[0]), int(order[1])
    bear_top, bear_bot = int(order[2]), int(order[3])
    labels: Dict[int, str] = {}
    # Bull pair — lower variance is the calm bull.
    if vars_return[bull_top] <= vars_return[bull_bot]:
        labels[bull_top] = "CALM_BULL"
        labels[bull_bot] = "VOLATILE_BULL"
    else:
        labels[bull_top] = "VOLATILE_BULL"
        labels[bull_bot] = "CALM_BULL"
    # Bear pair — lower variance is the calm bear (rarer).
    if vars_return[bear_top] <= vars_return[bear_bot]:
        labels[bear_top] = "CALM_BEAR"
        labels[bear_bot] = "VOLATILE_BEAR"
    else:
        labels[bear_top] = "VOLATILE_BEAR"
        labels[bear_bot] = "CALM_BEAR"
    return labels


def _reorder_to_regime_axes(matrix, state_to_label: Dict[int, str]) -> List[List[float]]:
    """Reshape the HMM's raw NxN transition matrix to use REGIMES as the
    axis labels in canonical order, so it can be compared directly with
    the tier-2 matrix in the UI."""
    import numpy as np
    label_to_state = {v: k for k, v in state_to_label.items()}
    n = len(REGIMES)
    out = np.zeros((n, n))
    for i, from_label in enumerate(REGIMES):
        src = label_to_state.get(from_label)
        if src is None:
            out[i] = 1.0 / n  # fallback: uniform
            continue
        for j, to_label in enumerate(REGIMES):
            dst = label_to_state.get(to_label)
            if dst is None:
                continue
            out[i][j] = matrix[src][dst]
    return out.tolist()
