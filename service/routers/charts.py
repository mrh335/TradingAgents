"""Ticker vs index comparison charts (using existing gui.charts module)."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from gui import charts as charts_mod
from gui import storage
from tradingagents.dataflows.utils import safe_ticker_component
from service.schemas import ChartComparisonResponse, ChartPoint

router = APIRouter(prefix="/charts", tags=["charts"])

# Colour palette matches the 5-tier rating vocabulary. Kept in this
# module so both the JSON shape and the PNG renderer agree on the
# legend semantics.
_DECISION_COLOR = {
    "Buy":         "#16a34a",
    "Overweight":  "#84cc16",
    "Hold":        "#a3a3a3",
    "Underweight": "#f59e0b",
    "Sell":        "#dc2626",
}


@router.get("/comparison", response_model=ChartComparisonResponse)
def comparison(
    ticker: str = Query(...),
    trade_date: str = Query(..., description="YYYY-MM-DD"),
    days_back: int = 90,
    days_forward: int = 180,
    benchmarks: List[str] = Query(default=["SPY", "QQQ"]),
) -> ChartComparisonResponse:
    df = charts_mod.build_comparison_frame(
        ticker, trade_date,
        days_back=days_back, days_forward=days_forward,
        benchmarks=tuple(benchmarks),
    )
    points: list[ChartPoint] = []
    if df is not None and not df.empty:
        for ts, row in df.iterrows():
            points.append(ChartPoint(
                date=ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts),
                values={k: float(v) for k, v in row.items() if v == v},  # filters NaN
            ))

    rt_df = charts_mod.realised_returns_table(ticker, trade_date)
    rt_records = rt_df.to_dict(orient="records") if rt_df is not None else None

    return ChartComparisonResponse(
        ticker=ticker,
        trade_date=trade_date,
        benchmarks=benchmarks,
        points=points,
        realised_returns=rt_records,
    )


# ---------------------------------------------------------------------------
# Decision history — every past run for a ticker, overlaid on price
# ---------------------------------------------------------------------------

def _collect_decision_history(ticker: str, lookback_days: int) -> dict:
    """Build the JSON payload used by both the data + PNG endpoints.

    Returns:
        {
          "ticker", "lookback_days", "fetched_at",
          "decisions":    [{trade_date, run_id, decision, provider}, ...],
          "price_series": [{date, close}, ...]    split-adjusted absolute dollars
          "splits":       [{date, ratio}, ...]    stock split events in the window
          "dividends":    [{date, amount}, ...]   cash dividend events in the window
        }

    Prices are **split-adjusted** so the line is continuous through stock
    splits (no fake 50% drop the day NVDA did its 10-for-1) but **NOT
    dividend-adjusted** — historical prices reflect what the stock
    actually traded at, with dividend payouts surfaced as separate events
    on the chart. See ``charts_mod.build_absolute_price_history``.
    """
    ticker = safe_ticker_component(ticker)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()

    # Decisions from the runs table (filters status=done; uses SQLite directly
    # to avoid scanning 200 rows via list_runs's default cap).
    rows = storage.list_runs(ticker=ticker, limit=2000)
    decisions = []
    for r in rows:
        if (r.get("status") or "").lower() != "done":
            continue
        td = r.get("trade_date") or ""
        try:
            d = datetime.fromisoformat(td).date()
        except ValueError:
            continue
        if d < cutoff:
            continue
        decisions.append({
            "trade_date": td,
            "run_id": r["run_id"],
            "decision": r.get("decision"),
            "provider": r.get("provider"),
        })
    decisions.sort(key=lambda x: x["trade_date"])

    # Absolute split-adjusted prices + split/dividend event lists.
    try:
        price_data = charts_mod.build_absolute_price_history(ticker, lookback_days)
    except Exception:
        price_data = {"price_series": [], "splits": [], "dividends": []}

    return {
        "ticker": ticker,
        "lookback_days": lookback_days,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decisions": decisions,
        "price_series": price_data.get("price_series", []),
        "splits": price_data.get("splits", []),
        "dividends": price_data.get("dividends", []),
    }


# NOTE: the JSON `/decisions/{ticker}` route is declared at the END of this
# file, AFTER `/decisions/{ticker}.png`. Starlette matches routes in
# registration order and the bare `{ticker}` param (regex `[^/]+`) would
# otherwise greedily match `NVDA.png` and shadow the image route.


@router.get("/decisions/{ticker}.png")
def decisions_png(ticker: str, lookback_days: int = 180, width: int = 1100,
                  height: int = 500) -> Response:
    """Server-rendered PNG of the decision-history chart.

    Embed directly via ``<img src="…/charts/decisions/NVDA.png">``, or
    open in a browser. The matplotlib dependency lives in the ``service``
    extras (see pyproject.toml). Returns 503 if matplotlib isn't
    importable for some reason — deployment issue, not a data issue.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"matplotlib not available on server: {e}. "
                   f"Install with `pip install 'tradingagents[service]'`.",
        )

    payload = _collect_decision_history(ticker, lookback_days)
    decisions = payload["decisions"]
    price_series = payload["price_series"]
    splits = payload.get("splits") or []
    dividends = payload.get("dividends") or []

    if not decisions and not price_series:
        raise HTTPException(
            status_code=404,
            detail=f"no decisions or price data for {ticker} in the last "
                   f"{lookback_days} days",
        )

    dpi = 110
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)

    # Build a lookup from date string → close (so we can place decision
    # markers on top of the price line).
    close_by_date = {p["date"]: p["close"] for p in price_series}
    dates = [datetime.fromisoformat(p["date"]).date() for p in price_series]
    closes = [p["close"] for p in price_series]
    if dates:
        ax.plot(dates, closes, color="#1f2937", linewidth=1.3,
                label=f"{ticker} close (split-adjusted)")

    # Vertical band for each stock split (rare, big visual impact).
    split_label_used = False
    for s in splits:
        try:
            day = datetime.fromisoformat(s["date"]).date()
        except ValueError:
            continue
        lbl = "stock split" if not split_label_used else None
        split_label_used = True
        ax.axvline(day, color="#7c3aed", linestyle="-", linewidth=1.4,
                   alpha=0.55, label=lbl)
        # Annotate the ratio at the top of the chart.
        ax.annotate(
            f"{s['ratio']:g}×",
            xy=(day, 1.0), xycoords=("data", "axes fraction"),
            xytext=(0, -12), textcoords="offset points",
            ha="center", fontsize=8, color="#7c3aed", fontweight="bold",
        )

    # Small green triangles at the bottom for each dividend payout.
    dividend_label_used = False
    if dividends and closes:
        ymin = min(closes)
        for div in dividends:
            try:
                day = datetime.fromisoformat(div["date"]).date()
            except ValueError:
                continue
            lbl = "dividend" if not dividend_label_used else None
            dividend_label_used = True
            ax.scatter([day], [ymin], s=60, c="#059669", marker="^",
                       edgecolors="black", linewidths=0.5, zorder=8,
                       label=lbl)

    seen = set()
    for d in decisions:
        try:
            day = datetime.fromisoformat(d["trade_date"]).date()
        except ValueError:
            continue
        # Find closest known close on or after this date
        y = close_by_date.get(d["trade_date"])
        if y is None and closes:
            # Fall back to the nearest available close to this date
            try:
                later = [(dt, c) for dt, c in zip(dates, closes) if dt >= day]
                if later:
                    y = later[0][1]
            except Exception:
                y = None
        if y is None:
            continue
        decision = d.get("decision") or "Hold"
        colour = _DECISION_COLOR.get(decision, "#6b7280")
        label = decision if decision not in seen else None
        seen.add(decision)
        ax.scatter([day], [y], s=140, c=colour, edgecolors="black",
                   linewidths=0.8, zorder=10, label=label)

    today = datetime.now(timezone.utc).date()
    ax.axvline(today, color="#6b7280", linestyle="--", linewidth=0.8,
               label="today")

    ax.set_title(f"{ticker} — price & decision history ({lookback_days}d)")
    ax.set_ylabel("Price ($, split-adjusted)")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate()
    ax.grid(alpha=0.3)
    ax.legend(loc="best", fontsize=8, framealpha=0.9)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)

    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={
            # Lightly cache so quick page refreshes don't re-render. New
            # runs invalidate naturally — most viewers will hit the URL
            # after they know there's a new run.
            "Cache-Control": "public, max-age=60",
            "X-Decisions-Count": str(len(decisions)),
        },
    )


# Declared LAST (see note near the top) so the bare `{ticker}` param doesn't
# shadow `/decisions/{ticker}.png`.
@router.get("/decisions/{ticker}")
def decisions_data(ticker: str, lookback_days: int = 180) -> dict:
    """Return the raw data the decision-history chart visualises.

    JSON shape:
        {
          "ticker": "NVDA", "lookback_days": 180,
          "fetched_at": "...",
          "decisions": [{trade_date, run_id, decision, provider}, ...],
          "price_series": [{date, close}, ...]
        }

    Use this for client-side rendering (Recharts / Chart.js in the Next.js
    webapp). For an immediately-viewable image, hit
    ``/charts/decisions/{ticker}.png`` instead.
    """
    return _collect_decision_history(ticker, lookback_days)
