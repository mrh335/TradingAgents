"""compute_indicators.py — derive the technical indicators the market analyst expects.

Takes the market_data JSON produced by fetch_market_data.py, computes the
indicator set the technical-analyst persona references (close_50_sma,
close_200_sma, close_10_ema, macd/macds/macdh, rsi, boll/boll_ub/boll_lb,
atr, vwma), and writes them back into a new JSON file with an `indicators`
block added.

Usage:
    python compute_indicators.py <market_data.json> [--output <path>]

Output JSON has the same shape as input plus:
    "indicators": {
        "as_of": "<last bar date>",
        "current": { close_50_sma: 198.40, rsi: 62.5, ... },
        "trailing_60": [ { date, close_50_sma, rsi, ... }, ... ]
    }

Falls back to raw pandas math if stockstats isn't installed.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _to_dataframe(price_history: list[dict]):
    import pandas as pd

    rows = []
    for r in price_history:
        rows.append({
            "date": r["date"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
        })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _compute_stockstats(df):
    """Try stockstats first — matches the framework's indicator names exactly."""
    try:
        from stockstats import wrap as ss_wrap
    except ImportError:
        return None

    _dates = df["date"].copy()
    sdf = ss_wrap(df.drop(columns=["date"]).copy())
    # Touch each column to force stockstats to compute it.
    needed = ["close_50_sma", "close_200_sma", "close_10_ema",
              "macd", "macds", "macdh",
              "rsi_14",  # stockstats default RSI period is 14
              "boll", "boll_ub", "boll_lb",
              "atr_14",
              "vwma_20"]
    for col in needed:
        try:
            _ = sdf[col]
        except Exception as e:
            _eprint(f"  stockstats: failed {col}: {e}")

    out = sdf.copy()
    # Rename to match the framework's catalogue (drops _14 / _20 suffixes).
    out = out.rename(columns={
        "rsi_14": "rsi",
        "atr_14": "atr",
        "vwma_20": "vwma",
    })
    out["date"] = _dates.values
    return out


def _compute_pandas(df):
    """Pure-pandas fallback for when stockstats isn't available."""
    import pandas as pd

    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    df["close_50_sma"] = close.rolling(50).mean()
    df["close_200_sma"] = close.rolling(200).mean()
    df["close_10_ema"] = close.ewm(span=10, adjust=False).mean()

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macds"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macdh"] = df["macd"] - df["macds"]

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    df["rsi"] = 100 - (100 / (1 + rs))

    df["boll"] = close.rolling(20).mean()
    std = close.rolling(20).std()
    df["boll_ub"] = df["boll"] + 2 * std
    df["boll_lb"] = df["boll"] - 2 * std

    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    pv = close * vol
    df["vwma"] = pv.rolling(20).sum() / vol.rolling(20).sum()

    return df


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("market_data_json", help="Path to market_data JSON from fetch_market_data.py")
    p.add_argument("--output", "-o", default=None, help="Output JSON path (default: temp file)")
    args = p.parse_args()

    in_path = Path(args.market_data_json)
    if not in_path.exists():
        _eprint(f"ERROR: input file not found: {in_path}")
        return 2

    data = json.loads(in_path.read_text(encoding="utf-8"))
    price_history = data.get("price_history", [])
    if not price_history:
        _eprint("ERROR: market_data has no price_history")
        return 2

    df = _to_dataframe(price_history)
    _eprint(f"loaded {len(df)} bars; computing indicators…")

    enriched = _compute_stockstats(df)
    if enriched is None:
        _eprint("  stockstats unavailable — using pandas fallback")
        enriched = _compute_pandas(df)

    cols = ["close_50_sma", "close_200_sma", "close_10_ema",
            "macd", "macds", "macdh", "rsi",
            "boll", "boll_ub", "boll_lb", "atr", "vwma"]
    present_cols = [c for c in cols if c in enriched.columns]

    last = enriched.iloc[-1]
    current = {c: (float(last[c]) if last[c] == last[c] else None) for c in present_cols}
    current["close"] = float(last["close"])
    current["as_of"] = enriched.iloc[-1]["date"].isoformat()

    trailing = []
    for _, row in enriched.tail(60).iterrows():
        entry = {"date": row["date"].isoformat(), "close": float(row["close"])}
        for c in present_cols:
            v = row[c]
            entry[c] = float(v) if v == v else None  # NaN check
        trailing.append(entry)

    data["indicators"] = {
        "as_of": current["as_of"],
        "current": current,
        "trailing_60": trailing,
    }

    if args.output:
        out_path = Path(args.output)
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"market_data_with_indicators_",
            suffix=".json", delete=False, mode="w", encoding="utf-8",
        )
        out_path = Path(tmp.name)
        tmp.close()

    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _eprint(f"OK: wrote {out_path} (indicators: {', '.join(present_cols)})")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
