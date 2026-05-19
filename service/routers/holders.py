"""13F institutional holdings — "smart money" view.

Surfaces the latest 13F-HR filings from a curated list of institutional
managers (Berkshire, Burry's Scion, Klarman/Baupost, Ackman/Pershing,
etc.) so the user can see who owns the tickers they hold or watch.

Endpoints
---------
GET    /holders/managers                 — list tracked managers + status
POST   /holders/managers                 — add a new manager by CIK
PATCH  /holders/managers/{cik}           — enable/disable an existing one
GET    /holders/manager/{cik}            — that manager's latest filing
GET    /holders/ticker/{ticker}          — who holds this ticker
GET    /holders/ticker/{ticker}/summary  — one-card summary (dashboard widget)
POST   /holders/refresh                  — force one poll cycle now

The actual fetch + parse runs in ``service.holdings_13f_poller`` (spawned
at app startup, weekly cadence). The /refresh endpoint triggers an
out-of-band tick via asyncio.to_thread so the request doesn't block.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from gui import storage
from service import edgar_client, holdings_13f_poller

router = APIRouter(prefix="/holders", tags=["holders"])


class ManagerRow(BaseModel):
    cik: str
    name: str
    enabled: bool
    last_refreshed_at: Optional[str] = None
    last_filing_date: Optional[str] = None
    last_report_date: Optional[str] = None
    last_accession_no: Optional[str] = None
    total_value: Optional[float] = None
    position_count: Optional[int] = None
    last_error: Optional[str] = None


def _mgr_row(d: dict) -> ManagerRow:
    return ManagerRow(
        cik=d["cik"], name=d["name"], enabled=bool(d.get("enabled", 1)),
        last_refreshed_at=d.get("last_refreshed_at"),
        last_filing_date=d.get("last_filing_date"),
        last_report_date=d.get("last_report_date"),
        last_accession_no=d.get("last_accession_no"),
        total_value=d.get("total_value"),
        position_count=d.get("position_count"),
        last_error=d.get("last_error"),
    )


class HoldingRow(BaseModel):
    manager_cik: str
    manager_name: str
    accession_no: str
    filing_date: str
    report_date: str
    cusip: str
    ticker: Optional[str] = None
    name_of_issuer: Optional[str] = None
    title_of_class: Optional[str] = None
    shares: int
    value: float
    put_call: Optional[str] = None
    prev_shares: Optional[int] = None
    qoq_change_pct: Optional[float] = None
    pct_of_manager_aum: Optional[float] = None


def _holding_row(d: dict) -> HoldingRow:
    return HoldingRow(
        manager_cik=d["manager_cik"],
        manager_name=d.get("manager_name") or d.get("manager_name_current") or "",
        accession_no=d["accession_no"],
        filing_date=d["filing_date"], report_date=d["report_date"],
        cusip=d["cusip"], ticker=d.get("ticker"),
        name_of_issuer=d.get("name_of_issuer"),
        title_of_class=d.get("title_of_class"),
        shares=int(d.get("shares") or 0),
        value=float(d.get("value") or 0),
        put_call=d.get("put_call"),
        prev_shares=d.get("prev_shares"),
        qoq_change_pct=d.get("qoq_change_pct"),
        pct_of_manager_aum=d.get("pct_of_manager_aum"),
    )


@router.get("/managers", response_model=List[ManagerRow])
def list_managers(enabled_only: bool = Query(False)) -> List[ManagerRow]:
    rows = storage.list_smart_money_managers(enabled_only=enabled_only)
    return [_mgr_row(r) for r in rows]


class AddManagerRequest(BaseModel):
    cik: str = Field(min_length=1, max_length=20, description="SEC CIK (10-digit, with or without leading zeros)")
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True


@router.post("/managers", response_model=ManagerRow)
def add_manager(req: AddManagerRequest) -> ManagerRow:
    row = storage.upsert_smart_money_manager(
        cik=req.cik, name=req.name, enabled=req.enabled,
    )
    return _mgr_row(row)


class ToggleRequest(BaseModel):
    enabled: bool


@router.patch("/managers/{cik}", response_model=ManagerRow)
def toggle_manager(cik: str, req: ToggleRequest) -> ManagerRow:
    if not storage.set_smart_money_manager_enabled(cik, req.enabled):
        raise HTTPException(status_code=404, detail="manager not found")
    # Return the updated row.
    rows = [r for r in storage.list_smart_money_managers()
            if r["cik"].lstrip("0") == cik.lstrip("0")]
    if not rows:
        raise HTTPException(status_code=404, detail="manager not found post-update")
    return _mgr_row(rows[0])


@router.get("/manager/{cik}", response_model=List[HoldingRow])
def get_manager_holdings(
    cik: str, limit: int = Query(100, ge=1, le=1000),
) -> List[HoldingRow]:
    """Latest 13F-HR filing for a single manager, top positions first by value."""
    rows = storage.list_holdings_by_manager(cik, limit=limit)
    return [_holding_row(r) for r in rows]


@router.get("/ticker/{ticker}", response_model=List[HoldingRow])
def get_ticker_holders(
    ticker: str, limit: int = Query(50, ge=1, le=200),
) -> List[HoldingRow]:
    """Who among the tracked smart-money list holds this ticker right now."""
    rows = storage.list_holdings_by_ticker(ticker, limit=limit)
    return [_holding_row(r) for r in rows]


class TickerSummary(BaseModel):
    ticker: str
    manager_count: int
    total_value: float
    total_shares: int
    top_managers: List[Dict[str, Any]]
    new_buys: int
    large_trims: int
    net_share_change_pct: Optional[float] = None


@router.get("/ticker/{ticker}/summary", response_model=TickerSummary)
def get_ticker_summary(ticker: str) -> TickerSummary:
    """One-card smart-money view of a ticker — for the dashboard widget."""
    s = storage.smart_money_summary_for_ticker(ticker)
    return TickerSummary(**s)


@router.post("/refresh")
async def refresh() -> Dict[str, Any]:
    """Force one 13F poll cycle right now.

    Offloaded to a worker thread because the cycle hits SEC EDGAR
    serially for every enabled manager (~12 HTTP requests × ~500ms
    each = ~6s of blocked I/O). Doing it inline would freeze every
    other request handler for that window.
    """
    import asyncio
    result = await asyncio.to_thread(holdings_13f_poller._tick)
    return result
