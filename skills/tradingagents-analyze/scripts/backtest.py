"""backtest.py — compute realised return for a past run and post as a sidecar.

Given a run_id, this script:
1. GETs the run's metadata (trade_date, decision) from the framework API.
2. Pulls the ticker's price at `trade_date` and `trade_date + horizon` via
   yfinance.
3. Pulls SPY's price at the same two dates (for benchmark).
4. Computes the raw return and the alpha vs SPY.
5. Renders the result as a `backtest.md` sidecar and POSTs it back to the
   framework.

The result helps you grade past decisions and feeds into a future
decision-log / memory-log mechanism (where the framework already has
the plumbing — see TRADINGAGENTS_MEMORY_LOG_PATH).

Usage:
    python backtest.py <run_id> [--horizon-days N] [--config <path>]
                                 [--dry-run]

Default horizon: 30 calendar days after the trade_date. Common values:
14 (short-term setup), 30 (medium), 90 (quarterly review).

NOTE: A horizon that hasn't elapsed yet will fail gracefully ("not enough
data — wait until <future_date>"). No fabrication.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG = SKILL_DIR / "config" / "defaults.yaml"


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _http(method: str, url: str, payload: dict | None = None, timeout: int = 30):
    body = json.dumps(payload).encode("utf-8") if payload else None
    headers = {"Accept": "application/json"}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return 0, str(e.reason)


def _close_on_or_after(ticker, target_date_iso: str) -> tuple[str, float] | None:
    """Return (actual_date, close) for the first trading day on/after target."""
    import yfinance as yf

    target = datetime.fromisoformat(target_date_iso).date()
    # Pull a ±10-day window to ensure we get a trading day even if target is a weekend.
    start = (target - timedelta(days=3)).isoformat()
    end = (target + timedelta(days=10)).isoformat()
    df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
    if df is None or df.empty:
        return None
    df = df.reset_index()
    df["date"] = df["Date"].dt.tz_localize(None) if hasattr(df["Date"].dt, "tz_localize") else df["Date"]
    on_or_after = df[df["date"].dt.date >= target]
    if on_or_after.empty:
        return None
    first = on_or_after.iloc[0]
    return first["date"].date().isoformat(), float(first["Close"])


def _render_markdown(report: dict) -> str:
    horizon = report["horizon_days"]
    entry = report["entry"]
    exit_ = report["exit"]
    return f"""# Backtest — {report['ticker']} ({report['trade_date']} → {exit_['date']})

**Decision at trade_date:** {report.get('decision') or '?'}
**Horizon:** {horizon} calendar days
**Run ID:** `{report['run_id']}`

## Levels

| | Date | Close |
|---|---|---|
| Entry | {entry['date']} | ${entry['close']:.2f} |
| Exit  | {exit_['date']} | ${exit_['close']:.2f} |
| SPY entry | {report['spy_entry']['date']} | ${report['spy_entry']['close']:.2f} |
| SPY exit  | {report['spy_exit']['date']} | ${report['spy_exit']['close']:.2f} |

## Returns

- **Raw return:** {report['raw_return_pct']:+.2f}%
- **SPY benchmark:** {report['spy_return_pct']:+.2f}%
- **Alpha vs SPY:** {report['alpha_pct']:+.2f}%

## Verdict

{report['verdict']}
"""


def _verdict(decision: str | None, raw_pct: float, alpha_pct: float) -> str:
    """Plain-English assessment of whether the call was right."""
    decision = (decision or "").lower()
    if decision in ("buy", "overweight"):
        if raw_pct > 0 and alpha_pct > 0:
            return f"The bullish call worked — position gained {raw_pct:+.1f}% and beat SPY by {alpha_pct:+.1f}%."
        if raw_pct > 0:
            return f"The position gained {raw_pct:+.1f}% but lagged SPY by {-alpha_pct:.1f}% — call was right on direction, wrong on magnitude."
        return f"The bullish call missed — position lost {raw_pct:.1f}% over the horizon."
    if decision in ("sell", "underweight"):
        if raw_pct < 0:
            return f"The bearish call worked — would have avoided a {-raw_pct:.1f}% drawdown."
        return f"The bearish call missed — the stock gained {raw_pct:+.1f}% over the horizon."
    if decision == "hold":
        return f"Hold call — stock moved {raw_pct:+.1f}% vs SPY {alpha_pct:+.1f}%. Holding was {'a reasonable' if abs(alpha_pct) < 2 else 'a costly'} choice."
    return f"Decision {decision!r} — stock moved {raw_pct:+.1f}%, alpha {alpha_pct:+.1f}%."


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("run_id")
    p.add_argument("--horizon-days", type=int, default=30)
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    cfg = _load_config(Path(args.config))
    api = (cfg.get("api_base_url") or "http://192.168.2.34:8001").rstrip("/")

    # 1. Fetch the run summary
    status, run = _http("GET", f"{api}/runs/{args.run_id}")
    if status != 200 or not isinstance(run, dict):
        _eprint(f"ERROR: GET /runs/{args.run_id} → {status}: {run}")
        return 2

    ticker = run.get("ticker")
    trade_date = run.get("trade_date")
    decision = run.get("decision")
    if not ticker or not trade_date:
        _eprint("ERROR: run missing ticker / trade_date")
        return 2

    # 2. Compute exit date
    exit_date = (datetime.fromisoformat(trade_date) + timedelta(days=args.horizon_days)).date()
    if exit_date > datetime.now(timezone.utc).date():
        _eprint(f"ERROR: horizon hasn't elapsed yet. "
                f"Wait until {exit_date.isoformat()} (or pick a shorter --horizon-days).")
        return 3

    # 3. Fetch prices
    try:
        entry = _close_on_or_after(ticker, trade_date)
        exit_ = _close_on_or_after(ticker, exit_date.isoformat())
        spy_entry = _close_on_or_after("SPY", trade_date)
        spy_exit = _close_on_or_after("SPY", exit_date.isoformat())
    except Exception as e:
        _eprint(f"ERROR: price fetch failed: {e}")
        return 4

    if not all([entry, exit_, spy_entry, spy_exit]):
        _eprint(f"ERROR: insufficient price data — entry={entry}, exit={exit_}, "
                f"spy_entry={spy_entry}, spy_exit={spy_exit}")
        return 4

    raw_pct = (exit_[1] / entry[1] - 1) * 100
    spy_pct = (spy_exit[1] / spy_entry[1] - 1) * 100
    alpha = raw_pct - spy_pct

    report = {
        "run_id": args.run_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "decision": decision,
        "horizon_days": args.horizon_days,
        "entry": {"date": entry[0], "close": entry[1]},
        "exit": {"date": exit_[0], "close": exit_[1]},
        "spy_entry": {"date": spy_entry[0], "close": spy_entry[1]},
        "spy_exit": {"date": spy_exit[0], "close": spy_exit[1]},
        "raw_return_pct": round(raw_pct, 3),
        "spy_return_pct": round(spy_pct, 3),
        "alpha_pct": round(alpha, 3),
        "verdict": _verdict(decision, raw_pct, alpha),
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    md = _render_markdown(report)
    _eprint(f"Backtest computed: raw={raw_pct:+.2f}% alpha={alpha:+.2f}%")
    _eprint(report["verdict"])

    if args.dry_run:
        _eprint("DRY RUN — would POST as backtest.md sidecar")
        print(md)
        return 0

    status, resp = _http(
        "POST",
        f"{api}/sidecars/run/{args.run_id}/sidecar/markdown",
        {"kind": "backtest.md", "content": md},
    )
    if status == 200:
        web_base = (cfg.get("webapp_base_url") or "http://192.168.2.34:3001").rstrip("/")
        _eprint(f"OK: posted backtest.md")
        print(f"{web_base}/history/{args.run_id}")
        return 0
    if status == 404:
        _eprint(f"  generic sidecar endpoint not deployed on the server.")
        return 5
    _eprint(f"ERROR: POST → {status}: {resp}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
