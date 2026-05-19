"""Portfolio: positions CRUD + summary with live-price valuation."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gui import storage
from service.streaming import broadcaster

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class Position(BaseModel):
    id: int
    ticker: str
    shares: float
    cost_basis_per_share: float
    opened_at: str
    closed_at: Optional[str] = None
    closing_price: Optional[float] = None
    account: Optional[str] = None
    notes: Optional[str] = None
    next_earnings_date: Optional[str] = None
    days_until_earnings: Optional[int] = None


def _with_earnings(row: dict) -> dict:
    """Attach next_earnings_date + days_until_earnings to a position row.
    Uses the shared 15-min cache in storage._next_earnings_date so we
    don't hit yfinance per request."""
    from datetime import date as _date
    out = dict(row)
    try:
        ne = storage._next_earnings_date(row["ticker"])
    except Exception:
        ne = None
    out["next_earnings_date"] = ne.isoformat() if ne else None
    out["days_until_earnings"] = (ne - _date.today()).days if ne else None
    return out


class PositionCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    shares: float = Field(gt=0)
    cost_basis_per_share: float = Field(gt=0)
    opened_at: Optional[str] = None
    account: Optional[str] = None
    notes: Optional[str] = None


class PositionUpdateRequest(BaseModel):
    shares: Optional[float] = None
    cost_basis_per_share: Optional[float] = None
    account: Optional[str] = None
    notes: Optional[str] = None


class PositionCloseRequest(BaseModel):
    closing_price: float = Field(gt=0)
    closed_at: Optional[str] = None


@router.get("/positions", response_model=List[Position])
def list_positions(include_closed: bool = False) -> List[Position]:
    return [Position(**_with_earnings(p))
            for p in storage.list_positions(include_closed=include_closed)]


@router.post("/positions", response_model=Position)
async def create_position(req: PositionCreateRequest) -> Position:
    pid = storage.add_position(
        ticker=req.ticker,
        shares=req.shares,
        cost_basis_per_share=req.cost_basis_per_share,
        opened_at=req.opened_at,
        account=req.account,
        notes=req.notes,
    )
    # Warm the price stream so summary shows live value immediately.
    try:
        await broadcaster.subscribe("price", req.ticker)
    except Exception:
        pass
    row = storage.get_position(pid)
    if not row:
        raise HTTPException(status_code=500, detail="position not retrievable")
    return Position(**row)


@router.put("/positions/{pid}", response_model=Position)
def update_position(pid: int, req: PositionUpdateRequest) -> Position:
    if not storage.get_position(pid):
        raise HTTPException(status_code=404, detail="position not found")
    storage.update_position(
        pid,
        shares=req.shares,
        cost_basis_per_share=req.cost_basis_per_share,
        account=req.account,
        notes=req.notes,
    )
    return Position(**storage.get_position(pid))


@router.post("/positions/{pid}/close", response_model=Position)
def close_position(pid: int, req: PositionCloseRequest) -> Position:
    if not storage.get_position(pid):
        raise HTTPException(status_code=404, detail="position not found")
    storage.close_position(pid, closing_price=req.closing_price, closed_at=req.closed_at)
    return Position(**storage.get_position(pid))


@router.delete("/positions/{pid}")
def delete_position(pid: int) -> dict:
    storage.delete_position(pid)
    return {"deleted": pid}


@router.get("/summary")
def summary() -> dict:
    """Aggregate open positions with live-price valuation.

    Returns total cost, current value, unrealized P&L (+ %), and a per-position
    breakdown. Closed positions get realized P&L summed separately.
    """
    open_positions = storage.list_positions(include_closed=False)
    closed_positions = [
        p for p in storage.list_positions(include_closed=True)
        if p.get("closed_at")
    ]

    rows = []
    total_cost = 0.0
    total_value = 0.0
    for p in open_positions:
        ticker = p["ticker"]
        cost = p["shares"] * p["cost_basis_per_share"]
        st = broadcaster._state.get(ticker)
        live_price = st.last_price if st else None
        value = (p["shares"] * live_price) if live_price else None
        unreal = (value - cost) if value is not None else None
        unreal_pct = (unreal / cost * 100) if (unreal is not None and cost) else None
        rows.append({
            **p,
            "cost": cost,
            "live_price": live_price,
            "value": value,
            "unrealized": unreal,
            "unrealized_pct": unreal_pct,
        })
        total_cost += cost
        if value is not None:
            total_value += value

    realized = 0.0
    for p in closed_positions:
        if p.get("closing_price") is None:
            continue
        cost = p["shares"] * p["cost_basis_per_share"]
        proceeds = p["shares"] * p["closing_price"]
        realized += (proceeds - cost)

    return {
        "open_positions": rows,
        "total_cost": total_cost,
        "total_value": total_value,
        "unrealized_pnl": total_value - total_cost if total_value else None,
        "unrealized_pnl_pct": ((total_value - total_cost) / total_cost * 100) if total_cost else None,
        "realized_pnl": realized,
        "open_count": len(open_positions),
        "closed_count": len(closed_positions),
    }


# ───────────────────────────────────────────────────────────────────────
# Multi-account rollup — group positions by account label.
# ───────────────────────────────────────────────────────────────────────

@router.get("/by-account")
def positions_by_account() -> dict:
    """Group open positions by account label and return per-account
    aggregates: position count, total cost basis, total current value,
    per-ticker breakdown.

    Useful for "what's in my joint brokerage vs my IRA vs my Stock Plan."
    Account label comes from positions.account (set by the planner sync
    or by manual entry).
    """
    open_positions = storage.list_positions(include_closed=False)
    by_account: dict = {}
    for p in open_positions:
        acct = p.get("account") or "(unspecified)"
        bucket = by_account.setdefault(acct, {
            "account": acct,
            "positions": 0,
            "total_cost": 0.0,
            "total_value": 0.0,
            "tickers": [],
        })
        cost = float(p["shares"]) * float(p["cost_basis_per_share"])
        st = broadcaster._state.get(p["ticker"])
        live_price = st.last_price if st else None
        value = (float(p["shares"]) * live_price) if live_price else None
        bucket["positions"] += 1
        bucket["total_cost"] += cost
        if value is not None:
            bucket["total_value"] += value
        bucket["tickers"].append({
            "ticker": p["ticker"],
            "shares": p["shares"],
            "cost": round(cost, 2),
            "value": round(value, 2) if value is not None else None,
            "cost_basis_per_share": p["cost_basis_per_share"],
            "live_price": live_price,
        })

    # Sort accounts by total cost desc so the biggest book surfaces first.
    accounts = sorted(by_account.values(), key=lambda a: -a["total_cost"])
    for a in accounts:
        a["total_cost"] = round(a["total_cost"], 2)
        a["total_value"] = round(a["total_value"], 2) if a["total_value"] else None
        a["unrealized_pnl"] = (
            round(a["total_value"] - a["total_cost"], 2)
            if a["total_value"] is not None else None
        )
        a["unrealized_pnl_pct"] = (
            round((a["unrealized_pnl"] / a["total_cost"]) * 100, 2)
            if a["unrealized_pnl"] is not None and a["total_cost"] > 0 else None
        )

    grand_cost = sum(a["total_cost"] for a in accounts)
    grand_value = sum((a["total_value"] or 0) for a in accounts)
    return {
        "accounts": accounts,
        "totals": {
            "account_count": len(accounts),
            "total_cost": round(grand_cost, 2),
            "total_value": round(grand_value, 2) if grand_value else None,
        },
    }


# ───────────────────────────────────────────────────────────────────────
# Correlation matrix — pairwise return correlation across held tickers
# ───────────────────────────────────────────────────────────────────────

class CorrelationCell(BaseModel):
    a: str
    b: str
    correlation: float


class CorrelationResponse(BaseModel):
    tickers: List[str]
    lookback_days: int
    matrix: List[List[Optional[float]]]      # symmetric N×N with 1.0 on diagonal
    pairs_high_correlation: List[CorrelationCell]   # ρ > 0.7 pairs to flag
    note: Optional[str] = None


@router.get("/correlation", response_model=CorrelationResponse)
def correlation(
    lookback_days: int = 90,
    include_benchmark: bool = True,
) -> CorrelationResponse:
    """Pairwise Pearson correlation of daily returns across all currently-
    held tickers (optionally including SPY as a row).

    Useful for spotting hidden concentration: if NVDA + AVGO + AMD all
    correlate at 0.9, you don't have 3 semis bets, you have one bet 3x.
    """
    import pandas as pd
    try:
        import yfinance as yf
    except ImportError:
        return CorrelationResponse(
            tickers=[], lookback_days=lookback_days, matrix=[],
            pairs_high_correlation=[], note="yfinance not installed",
        )

    open_positions = storage.list_positions(include_closed=False)
    tickers = sorted({p["ticker"].upper() for p in open_positions if p.get("ticker")})
    if include_benchmark and "SPY" not in tickers:
        tickers.append("SPY")

    if len(tickers) < 2:
        return CorrelationResponse(
            tickers=tickers, lookback_days=lookback_days, matrix=[],
            pairs_high_correlation=[],
            note="Need at least 2 open positions to compute correlation.",
        )

    from datetime import date as _date, timedelta as _td
    end = _date.today()
    start = end - _td(days=lookback_days + 7)

    series_map: dict = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(
                start=start.isoformat(), end=(end + _td(days=1)).isoformat(),
                auto_adjust=True,
            )
            if df is None or df.empty:
                continue
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            series_map[t] = df["Close"].pct_change().dropna()
        except Exception:
            continue

    if len(series_map) < 2:
        return CorrelationResponse(
            tickers=tickers, lookback_days=lookback_days, matrix=[],
            pairs_high_correlation=[],
            note="Could not fetch price data for enough tickers.",
        )

    # Align on common dates
    returns_df = pd.concat(series_map, axis=1).dropna()
    corr_df = returns_df.corr()

    tickers_in_order = list(corr_df.columns)
    matrix: List[List[Optional[float]]] = []
    high_corr_pairs: List[CorrelationCell] = []
    for i, a in enumerate(tickers_in_order):
        row: List[Optional[float]] = []
        for j, b in enumerate(tickers_in_order):
            try:
                val = float(corr_df.iloc[i, j])
                row.append(round(val, 3))
                # Surface high-correlation pairs (excluding self and SPY)
                if i < j and val > 0.7 and a != "SPY" and b != "SPY":
                    high_corr_pairs.append(CorrelationCell(
                        a=a, b=b, correlation=round(val, 3),
                    ))
            except (ValueError, TypeError):
                row.append(None)
        matrix.append(row)

    # Sort high-correlation pairs by strength descending.
    high_corr_pairs.sort(key=lambda p: -p.correlation)

    return CorrelationResponse(
        tickers=tickers_in_order,
        lookback_days=lookback_days,
        matrix=matrix,
        pairs_high_correlation=high_corr_pairs,
    )
