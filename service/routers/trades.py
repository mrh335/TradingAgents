"""Trade journal — actual executed trades.

Separate from /portfolio/positions (current holdings snapshot); this
table is the chronological history of buy/sell/dividend/split/transfer
activity that produced those positions. Lets the user:

1. Track P&L precisely (cost basis + realized gains)
2. Link a trade to a recommended run (was this trade following the
   framework's call or going against it?)
3. Feed future "actual vs notional" backtest comparison

Endpoints
---------
GET    /trades              — list all (newest first)
GET    /trades?ticker=AAPL  — filter
POST   /trades              — log a new trade
PUT    /trades/{id}         — edit
DELETE /trades/{id}         — remove
GET    /trades/summary      — realized P&L per ticker
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from gui import storage

router = APIRouter(prefix="/trades", tags=["trades"])

ALLOWED_ACTIONS = set(storage.ALLOWED_TRADE_ACTIONS)


class TradeEntry(BaseModel):
    id: int
    ticker: str
    action: str
    shares: float
    price: Optional[float] = None
    executed_at: str
    account: Optional[str] = None
    notes: Optional[str] = None
    linked_run_id: Optional[str] = None
    fees: float = 0.0
    created_at: str
    updated_at: str


class TradeCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    action: str
    shares: float = Field(gt=0)
    price: Optional[float] = Field(default=None, ge=0)
    executed_at: str = Field(description="YYYY-MM-DD")
    account: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=500)
    linked_run_id: Optional[str] = None
    fees: float = Field(default=0.0, ge=0)

    @field_validator("action")
    @classmethod
    def _v_action(cls, v: str) -> str:
        if v not in ALLOWED_ACTIONS:
            raise ValueError(f"invalid action {v!r}; allowed: {sorted(ALLOWED_ACTIONS)}")
        return v


class TradeUpdateRequest(BaseModel):
    action: Optional[str] = None
    shares: Optional[float] = None
    price: Optional[float] = None
    executed_at: Optional[str] = None
    account: Optional[str] = None
    notes: Optional[str] = None
    linked_run_id: Optional[str] = None
    fees: Optional[float] = None

    @field_validator("action")
    @classmethod
    def _v_action(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALLOWED_ACTIONS:
            raise ValueError(f"invalid action {v!r}")
        return v


def _row(d: dict) -> TradeEntry:
    return TradeEntry(
        id=d["id"], ticker=d["ticker"], action=d["action"],
        shares=d["shares"], price=d.get("price"),
        executed_at=d["executed_at"], account=d.get("account"),
        notes=d.get("notes"), linked_run_id=d.get("linked_run_id"),
        fees=d.get("fees") or 0.0,
        created_at=d["created_at"], updated_at=d["updated_at"],
    )


@router.get("", response_model=List[TradeEntry])
def list_trades_endpoint(
    ticker: Optional[str] = None,
    limit: int = Query(1000, ge=1, le=5000),
) -> List[TradeEntry]:
    rows = storage.list_trades(ticker=ticker, limit=limit)
    return [_row(r) for r in rows]


@router.post("", response_model=TradeEntry)
def create_trade(req: TradeCreateRequest) -> TradeEntry:
    row = storage.add_trade(
        ticker=req.ticker, action=req.action, shares=req.shares,
        price=req.price, executed_at=req.executed_at, account=req.account,
        notes=req.notes, linked_run_id=req.linked_run_id, fees=req.fees,
    )
    return _row(row)


@router.put("/{trade_id}", response_model=TradeEntry)
def update_trade_endpoint(trade_id: int, req: TradeUpdateRequest) -> TradeEntry:
    if not storage.get_trade(trade_id):
        raise HTTPException(status_code=404, detail="trade not found")
    row = storage.update_trade(
        trade_id,
        action=req.action, shares=req.shares, price=req.price,
        executed_at=req.executed_at, account=req.account, notes=req.notes,
        linked_run_id=req.linked_run_id, fees=req.fees,
    )
    return _row(row)


@router.delete("/{trade_id}")
def delete_trade_endpoint(trade_id: int) -> dict:
    if not storage.delete_trade(trade_id):
        raise HTTPException(status_code=404, detail="trade not found")
    return {"deleted": trade_id}


class TickerPnL(BaseModel):
    ticker: str
    buys: int
    sells: int
    shares_acquired: float
    shares_disposed: float
    capital_in: float            # total $ spent on buys (+ fees)
    capital_out: float           # total $ received on sells (− fees)
    dividends: float
    net_pnl_realized: float      # capital_out − capital_in + dividends
    trade_count: int


@router.get("/summary")
def trades_summary() -> Dict[str, list]:
    """Realized P&L per ticker, computed from the trade journal alone.

    Note: this is *realized* only — open positions' unrealized P&L lives
    on /portfolio/summary. Dividends count as positive cash flow.
    """
    all_trades = storage.list_trades(limit=10000)
    by_ticker: Dict[str, Dict[str, float]] = {}
    counts: Dict[str, Dict[str, int]] = {}
    for t in all_trades:
        ticker = t["ticker"]
        bucket = by_ticker.setdefault(ticker, {
            "shares_acquired": 0.0, "shares_disposed": 0.0,
            "capital_in": 0.0, "capital_out": 0.0, "dividends": 0.0,
        })
        cnt = counts.setdefault(ticker, {"buys": 0, "sells": 0, "trade_count": 0})
        cnt["trade_count"] += 1
        action = t["action"]
        shares = float(t["shares"] or 0)
        price = float(t["price"] or 0)
        fees = float(t.get("fees") or 0)
        gross = shares * price
        if action == "buy":
            cnt["buys"] += 1
            bucket["shares_acquired"] += shares
            bucket["capital_in"] += gross + fees
        elif action == "sell":
            cnt["sells"] += 1
            bucket["shares_disposed"] += shares
            bucket["capital_out"] += gross - fees
        elif action == "dividend":
            # Convention: shares=dividend per share, price=number of shares
            # held, OR shares*price = total dividend received. We just use
            # gross as the dividend amount.
            bucket["dividends"] += gross if gross > 0 else shares
        elif action == "short":
            cnt["sells"] += 1
            bucket["shares_disposed"] += shares
            bucket["capital_out"] += gross - fees
        elif action == "cover":
            cnt["buys"] += 1
            bucket["shares_acquired"] += shares
            bucket["capital_in"] += gross + fees
        # split + transfer don't affect cash flow

    rows: List[TickerPnL] = []
    for ticker, b in by_ticker.items():
        c = counts[ticker]
        net = round(b["capital_out"] - b["capital_in"] + b["dividends"], 2)
        rows.append(TickerPnL(
            ticker=ticker,
            buys=c["buys"], sells=c["sells"],
            shares_acquired=round(b["shares_acquired"], 4),
            shares_disposed=round(b["shares_disposed"], 4),
            capital_in=round(b["capital_in"], 2),
            capital_out=round(b["capital_out"], 2),
            dividends=round(b["dividends"], 2),
            net_pnl_realized=net,
            trade_count=c["trade_count"],
        ))
    rows.sort(key=lambda r: -r.net_pnl_realized)
    grand = {
        "total_capital_in": round(sum(r.capital_in for r in rows), 2),
        "total_capital_out": round(sum(r.capital_out for r in rows), 2),
        "total_dividends": round(sum(r.dividends for r in rows), 2),
        "total_realized_pnl": round(sum(r.net_pnl_realized for r in rows), 2),
        "trade_count": sum(r.trade_count for r in rows),
        "ticker_count": len(rows),
    }
    return {"by_ticker": [r.model_dump() for r in rows], "totals": grand}
