"""Trading restrictions — per-ticker blackout windows.

Stored in SQLite ``trading_restrictions``. Surfaced into the trader + PM
agent prompts as a HARD constraint — the agents refuse to recommend
trades inside an active window regardless of fundamental/technical signal.

Endpoints
---------
GET    /restrictions                       — list all (newest first)
GET    /restrictions?ticker=AAPL           — filter by ticker
GET    /restrictions?active_on=YYYY-MM-DD  — only those active on the date
POST   /restrictions                       — add a new restriction
PUT    /restrictions/{id}                  — edit
DELETE /restrictions/{id}                  — remove
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from gui import storage

router = APIRouter(prefix="/restrictions", tags=["restrictions"])

ALLOWED_KINDS = {
    "blackout", "restricted_list", "regulatory", "other",
    "earnings_blackout",  # closed N days before to M days after earnings
    "earnings_window",    # OPEN from N days post-earnings, for M days
}


def _validate_iso_date(v: Optional[str]) -> Optional[str]:
    if v is None or v == "":
        return None
    try:
        datetime.fromisoformat(v).date()
        return v[:10]
    except ValueError:
        raise ValueError(f"invalid date format: {v!r}, expected YYYY-MM-DD")


class Restriction(BaseModel):
    id: int
    ticker: str
    start_date: str
    end_date: Optional[str] = None
    kind: str
    reason: Optional[str] = None
    earnings_days_before: Optional[int] = None
    earnings_days_after: Optional[int] = None
    earnings_window_open_offset_days: Optional[int] = None
    earnings_window_duration_days: Optional[int] = None
    created_at: str
    updated_at: str
    # When the active_on filter is set and the row is an earnings-relative
    # restriction, these surface the resolved window:
    # - earnings_blackout: resolved_start/end = the closed period
    # - earnings_window:   resolved_start/end = the OPEN period (when
    #                       trading is allowed)
    resolved_start: Optional[str] = None
    resolved_end: Optional[str] = None
    resolved_earnings_date: Optional[str] = None
    # For earnings_window rows: is the window currently open? True means
    # trading is allowed RIGHT NOW; False means we're in a closure
    # between two open windows.
    currently_open: Optional[bool] = None


class RestrictionCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    start_date: str = Field(
        default="",
        description=(
            "YYYY-MM-DD inclusive. Required for fixed-window restrictions; "
            "ignored when earnings_days_before / earnings_days_after are set."
        ),
    )
    end_date: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD inclusive; omit for an open-ended (indefinite) restriction",
    )
    kind: str = Field(default="blackout")
    reason: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Free-form explanation surfaced to the agent",
    )
    earnings_days_before: Optional[int] = Field(
        default=None,
        ge=0, le=120,
        description=(
            "Days BEFORE the ticker's next earnings date to block trading. "
            "If set along with earnings_days_after, this becomes an "
            "earnings-relative restriction that auto-recomputes every "
            "earnings cycle. Typical: 14 (two-week pre-earnings blackout)."
        ),
    )
    earnings_days_after: Optional[int] = Field(
        default=None,
        ge=0, le=30,
        description=(
            "Days AFTER the ticker's next earnings date to keep blocking "
            "(typically 1-2 days to let the post-print volatility settle "
            "before re-opening the trading window)."
        ),
    )
    earnings_window_open_offset_days: Optional[int] = Field(
        default=None,
        ge=0, le=30,
        description=(
            "For kind=earnings_window: how many days AFTER the next "
            "earnings date the trading window OPENS. Typical: 1-3 days "
            "of cooldown to let post-print volatility settle."
        ),
    )
    earnings_window_duration_days: Optional[int] = Field(
        default=None,
        ge=1, le=120,
        description=(
            "For kind=earnings_window: how many days the open window "
            "stays open before closing again until the next earnings. "
            "Typical: 14-28 days (2-4 weeks of trading allowed)."
        ),
    )

    @field_validator("start_date")
    @classmethod
    def _v_start(cls, v: str) -> str:
        return _validate_iso_date(v)

    @field_validator("end_date")
    @classmethod
    def _v_end(cls, v: Optional[str]) -> Optional[str]:
        return _validate_iso_date(v)

    @field_validator("kind")
    @classmethod
    def _v_kind(cls, v: str) -> str:
        if v not in ALLOWED_KINDS:
            raise ValueError(
                f"invalid kind {v!r}; allowed: {sorted(ALLOWED_KINDS)}"
            )
        return v


class RestrictionUpdateRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    kind: Optional[str] = None
    reason: Optional[str] = None
    earnings_days_before: Optional[int] = None
    earnings_days_after: Optional[int] = None
    earnings_window_open_offset_days: Optional[int] = None
    earnings_window_duration_days: Optional[int] = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _v_iso(cls, v: Optional[str]) -> Optional[str]:
        return _validate_iso_date(v)

    @field_validator("kind")
    @classmethod
    def _v_kind(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALLOWED_KINDS:
            raise ValueError(
                f"invalid kind {v!r}; allowed: {sorted(ALLOWED_KINDS)}"
            )
        return v


def _row(d: dict) -> Restriction:
    return Restriction(
        id=d["id"],
        ticker=d["ticker"],
        start_date=d.get("start_date") or "",
        end_date=d.get("end_date"),
        kind=d.get("kind") or "blackout",
        reason=d.get("reason"),
        earnings_days_before=d.get("earnings_days_before"),
        earnings_days_after=d.get("earnings_days_after"),
        earnings_window_open_offset_days=d.get("earnings_window_open_offset_days"),
        earnings_window_duration_days=d.get("earnings_window_duration_days"),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        resolved_start=d.get("_resolved_start"),
        resolved_end=d.get("_resolved_end"),
        resolved_earnings_date=d.get("_resolved_earnings_date"),
        currently_open=d.get("_currently_open"),
    )


@router.get("", response_model=List[Restriction])
def list_restrictions_endpoint(
    ticker: Optional[str] = None,
    active_on: Optional[str] = None,
) -> List[Restriction]:
    """List restrictions, optionally filtered.

    - ``ticker``: case-insensitive exact match
    - ``active_on``: YYYY-MM-DD; only return restrictions whose
      [start_date, end_date] window contains this date (open-ended
      end_date counts as "indefinitely active")
    """
    if active_on is not None:
        try:
            datetime.fromisoformat(active_on).date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"active_on must be YYYY-MM-DD; got {active_on!r}",
            )
    rows = storage.list_restrictions(ticker=ticker, active_on=active_on)
    return [_row(r) for r in rows]


@router.post("", response_model=Restriction)
def create_restriction_endpoint(req: RestrictionCreateRequest) -> Restriction:
    # For earnings-relative restrictions, allow start_date to be empty —
    # the dynamic window doesn't need a hard start. Store today as a
    # placeholder so the NOT NULL constraint passes.
    is_earnings = (
        req.kind in ("earnings_blackout", "earnings_window")
        or req.earnings_days_before is not None
        or req.earnings_days_after is not None
        or req.earnings_window_open_offset_days is not None
        or req.earnings_window_duration_days is not None
    )
    start_date = req.start_date or (date.today().isoformat() if is_earnings else "")
    if not start_date:
        raise HTTPException(
            status_code=400,
            detail="start_date is required for fixed-window restrictions",
        )
    # For earnings_window, require both parameters or it's incoherent.
    if req.kind == "earnings_window":
        if req.earnings_window_duration_days is None:
            raise HTTPException(
                status_code=400,
                detail="earnings_window requires earnings_window_duration_days "
                       "(how long the open window stays open)",
            )
    row = storage.add_restriction(
        ticker=req.ticker,
        start_date=start_date,
        end_date=req.end_date,
        kind=req.kind,
        reason=req.reason,
        earnings_days_before=req.earnings_days_before,
        earnings_days_after=req.earnings_days_after,
        earnings_window_open_offset_days=req.earnings_window_open_offset_days,
        earnings_window_duration_days=req.earnings_window_duration_days,
    )
    return _row(row)


@router.put("/{restriction_id}", response_model=Restriction)
def update_restriction_endpoint(
    restriction_id: int, req: RestrictionUpdateRequest
) -> Restriction:
    existing = storage.get_restriction(restriction_id)
    if not existing:
        raise HTTPException(status_code=404, detail="restriction not found")
    row = storage.update_restriction(
        restriction_id,
        start_date=req.start_date,
        end_date=req.end_date,
        kind=req.kind,
        reason=req.reason,
        earnings_days_before=req.earnings_days_before,
        earnings_days_after=req.earnings_days_after,
        earnings_window_open_offset_days=req.earnings_window_open_offset_days,
        earnings_window_duration_days=req.earnings_window_duration_days,
    )
    return _row(row)


@router.delete("/{restriction_id}")
def delete_restriction_endpoint(restriction_id: int) -> dict:
    ok = storage.delete_restriction(restriction_id)
    if not ok:
        raise HTTPException(status_code=404, detail="restriction not found")
    return {"deleted": restriction_id}
