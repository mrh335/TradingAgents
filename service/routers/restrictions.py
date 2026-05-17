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

ALLOWED_KINDS = {"blackout", "restricted_list", "regulatory", "other"}


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
    created_at: str
    updated_at: str


class RestrictionCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    start_date: str = Field(description="YYYY-MM-DD inclusive")
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
        start_date=d["start_date"],
        end_date=d.get("end_date"),
        kind=d.get("kind") or "blackout",
        reason=d.get("reason"),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
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
    row = storage.add_restriction(
        ticker=req.ticker,
        start_date=req.start_date,
        end_date=req.end_date,
        kind=req.kind,
        reason=req.reason,
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
    )
    return _row(row)


@router.delete("/{restriction_id}")
def delete_restriction_endpoint(restriction_id: int) -> dict:
    ok = storage.delete_restriction(restriction_id)
    if not ok:
        raise HTTPException(status_code=404, detail="restriction not found")
    return {"deleted": restriction_id}
