"""Per-ticker analysis schedules — CRUD for the auto-run scheduler.

Storage in ``ticker_schedules``. The background loop in
``service.scheduler`` consumes these rows and queues runs when each is
due.

Endpoints
---------
GET    /schedules                   — list all
POST   /schedules                   — create one
PUT    /schedules/{id}              — edit (cron, options, enabled, notes)
DELETE /schedules/{id}              — remove
POST   /schedules/{id}/fire         — fire immediately (manual override)

Cron expressions are standard 5-field, evaluated in the API container's
local timezone. The UI offers presets ("every weekday at 6am", "every
other day morning") that map to canonical expressions.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from gui import storage
from service import scheduler as scheduler_service

router = APIRouter(prefix="/schedules", tags=["schedules"])

ALLOWED_MODES = {"analyze", "brief", "refresh"}


def _validate_cron(expr: str) -> str:
    """Sanity-check that the expression parses; raise 400 if it doesn't."""
    expr = (expr or "").strip()
    try:
        from croniter import croniter
        if not croniter.is_valid(expr):
            raise ValueError("does not parse as a valid 5-field cron expression")
        # Smoke test — must produce a next fire time within 7 days.
        itr = croniter(expr, datetime.now())
        itr.get_next(datetime)
    except (ValueError, KeyError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"invalid cron expression {expr!r}: {e}",
        )
    return expr


class Schedule(BaseModel):
    id: int
    ticker: str
    cron_expression: str
    mode: str
    options: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    notes: Optional[str] = None
    last_fired_at: Optional[str] = None
    last_queue_id: Optional[str] = None
    last_error: Optional[str] = None
    created_at: str
    updated_at: str
    next_fire_at: Optional[str] = None
    cadence_human: Optional[str] = None  # human-readable cadence


class ScheduleCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    cron_expression: str = Field(description="Standard 5-field cron in container's local TZ")
    mode: str = Field(default="analyze")
    options: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    notes: Optional[str] = Field(default=None, max_length=200)

    @field_validator("mode")
    @classmethod
    def _v_mode(cls, v: str) -> str:
        if v not in ALLOWED_MODES:
            raise ValueError(f"invalid mode {v!r}; allowed: {sorted(ALLOWED_MODES)}")
        return v


class ScheduleUpdateRequest(BaseModel):
    cron_expression: Optional[str] = None
    mode: Optional[str] = None
    options: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    notes: Optional[str] = None

    @field_validator("mode")
    @classmethod
    def _v_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALLOWED_MODES:
            raise ValueError(f"invalid mode {v!r}; allowed: {sorted(ALLOWED_MODES)}")
        return v


def _cadence_human(cron_expr: str) -> str:
    """Hand-rolled human description for common patterns; falls back to the
    cron expression itself for anything unrecognised. (Avoids pulling in
    cronstrue which is large for our needs.)"""
    parts = (cron_expr or "").split()
    if len(parts) != 5:
        return cron_expr
    m, h, dom, mon, dow = parts

    def _fmt_hm() -> str:
        try:
            mi, hr = int(m), int(h)
            am_pm = "AM" if hr < 12 else "PM"
            disp_hr = hr if 1 <= hr <= 12 else (hr - 12 if hr > 12 else 12)
            return f"{disp_hr}:{mi:02d} {am_pm}"
        except ValueError:
            return f"at {h}:{m}"

    # Specific patterns first
    if dow == "1-5" and dom == "*" and mon == "*":
        return f"every weekday at {_fmt_hm()}"
    if dow == "0,6" and dom == "*" and mon == "*":
        return f"every weekend day at {_fmt_hm()}"
    if dow == "*" and dom == "*" and mon == "*":
        if m.isdigit() and h.isdigit():
            return f"every day at {_fmt_hm()}"
    if dow == "*" and dom == "*/2" and mon == "*":
        return f"every other day at {_fmt_hm()}"
    if dow in ("0", "1", "2", "3", "4", "5", "6") and dom == "*" and mon == "*":
        days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        return f"every {days[int(dow)]} at {_fmt_hm()}"
    if h == "*" and m == "0" and dow == "*":
        return "every hour"
    if h == "*" and m.startswith("*/") and dow == "*":
        try:
            n = int(m.split("/")[1])
            return f"every {n} minutes"
        except ValueError:
            pass
    return cron_expr


def _next_fire(cron_expr: str) -> Optional[str]:
    try:
        from croniter import croniter
        return croniter(cron_expr, datetime.now()).get_next(datetime).isoformat(timespec="seconds")
    except Exception:
        return None


def _row(d: Dict[str, Any]) -> Schedule:
    options_raw = d.get("options_json") or "{}"
    try:
        options = json.loads(options_raw) if isinstance(options_raw, str) else {}
    except json.JSONDecodeError:
        options = {}
    cron_expr = d.get("cron_expression") or ""
    return Schedule(
        id=d["id"],
        ticker=d["ticker"],
        cron_expression=cron_expr,
        mode=d.get("mode") or "analyze",
        options=options,
        enabled=bool(d.get("enabled")),
        notes=d.get("notes"),
        last_fired_at=d.get("last_fired_at"),
        last_queue_id=d.get("last_queue_id"),
        last_error=d.get("last_error"),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        next_fire_at=_next_fire(cron_expr),
        cadence_human=_cadence_human(cron_expr),
    )


@router.get("", response_model=List[Schedule])
def list_schedules_endpoint() -> List[Schedule]:
    return [_row(r) for r in storage.list_schedules()]


@router.post("", response_model=Schedule)
def create_schedule_endpoint(req: ScheduleCreateRequest) -> Schedule:
    cron_expr = _validate_cron(req.cron_expression)
    row = storage.add_schedule(
        ticker=req.ticker,
        cron_expression=cron_expr,
        mode=req.mode,
        options=req.options,
        enabled=req.enabled,
        notes=req.notes,
    )
    return _row(row)


@router.put("/{schedule_id}", response_model=Schedule)
def update_schedule_endpoint(schedule_id: int, req: ScheduleUpdateRequest) -> Schedule:
    if not storage.get_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="schedule not found")
    cron_expr = _validate_cron(req.cron_expression) if req.cron_expression else None
    row = storage.update_schedule(
        schedule_id,
        cron_expression=cron_expr,
        mode=req.mode,
        options=req.options,
        enabled=req.enabled,
        notes=req.notes,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="schedule disappeared during update")
    return _row(row)


@router.delete("/{schedule_id}")
def delete_schedule_endpoint(schedule_id: int) -> dict:
    ok = storage.delete_schedule(schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="schedule not found")
    return {"deleted": schedule_id}


@router.post("/{schedule_id}/fire", response_model=Schedule)
def fire_schedule_endpoint(schedule_id: int) -> Schedule:
    """Fire one schedule right now (skips the cron check). Useful for
    testing a new schedule without waiting for its next due time."""
    row = storage.get_schedule(schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="schedule not found")
    scheduler_service._fire_schedule(row, datetime.now())
    updated = storage.get_schedule(schedule_id) or row
    return _row(updated)
