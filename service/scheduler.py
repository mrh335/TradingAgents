"""Per-ticker analysis scheduler — wakes up every minute, fires due schedules.

Lives as an asyncio task spawned by ``service.app._startup``. For each
enabled row in the ``ticker_schedules`` table:

1. Use ``croniter`` to find the most recent past fire time (``prev_fire``)
   given the cron expression evaluated in the API container's local
   timezone.
2. If ``prev_fire > last_fired_at`` (or ``last_fired_at`` is NULL), the
   schedule is due — post a queue item via ``storage.queue_request``
   with the schedule's mode + options, then record the fire.

Idempotency: ``last_fired_at`` advances by at most one tick per loop
iteration, so a schedule never fires twice for the same cron tick even
if the loop is delayed.

Cadence: 60s default. Increasing it doesn't help (cron is at minute
precision), decreasing past 30s wastes CPU.

Failures: caught per-schedule so one broken row doesn't poison the
others. Errors are recorded on the row via
``storage.record_schedule_fire(..., error=...)`` and surfaced in the UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from gui import storage

logger = logging.getLogger(__name__)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Tolerant ISO parser — accepts the format we write (``...Z``)."""
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1])
        return datetime.fromisoformat(value)
    except ValueError:
        return None


_DOW_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _apply_weekday_memory_override(options: dict, now: datetime) -> None:
    """If options.analysis_mode_overrides is set, swap analysis_mode when
    today's weekday matches one of the override keys.

    Override shape (set by the /schedules UI):
        {"Fri": "fresh"}  or  {"Mon": "incremental", "Wed": "incremental"}

    This lets one schedule express "incremental Mon-Thu, fresh Friday"
    without needing two separate schedule rows.
    """
    overrides = options.get("analysis_mode_overrides")
    if not isinstance(overrides, dict) or not overrides:
        return
    today_name = _DOW_NAMES[now.weekday()]
    override = overrides.get(today_name)
    if override and override != options.get("analysis_mode"):
        options["analysis_mode"] = override
        options["_overridden_today"] = True  # diagnostic flag


def _fire_schedule(row: dict, now: datetime) -> None:
    """Queue an analysis for one schedule and record the fire."""
    try:
        options_raw = row.get("options_json") or "{}"
        options = json.loads(options_raw) if isinstance(options_raw, str) else {}
    except json.JSONDecodeError:
        options = {}
    # Inject schedule metadata so the user can trace a queue item back to
    # the schedule that created it.
    options.setdefault("schedule_id", row["id"])
    options.setdefault("schedule_notes", row.get("notes") or "")

    # Apply per-weekday memory mode override if configured. Mutates options
    # in place so the resulting queue item carries the effective mode.
    _apply_weekday_memory_override(options, now)

    trade_date = now.date().isoformat()
    ticker = row["ticker"]
    mode = row.get("mode") or "analyze"
    try:
        q = storage.queue_request(
            ticker=ticker,
            trade_date=trade_date,
            mode=mode,
            options=options,
            requested_by=f"scheduler:{row['id']}",
            priority=int(options.get("priority", 0)),
        )
        storage.record_schedule_fire(row["id"], queue_id=q["id"], error=None)
        logger.info(
            "scheduler fired schedule_id=%s ticker=%s queue_id=%s",
            row["id"], ticker, q["id"],
        )
    except Exception as e:
        storage.record_schedule_fire(row["id"], queue_id=None, error=str(e)[:500])
        logger.exception(
            "scheduler failed to fire schedule_id=%s ticker=%s: %s",
            row["id"], ticker, e,
        )


def _tick(now: Optional[datetime] = None) -> int:
    """One iteration of the scheduler loop. Returns count of fired schedules.

    Exposed for unit testing — production callers use ``run()``.
    """
    # croniter import here so the module can import in environments that
    # don't have it (the storage helpers are still useful in CLI tools).
    try:
        from croniter import croniter
    except ImportError:
        logger.warning("croniter not installed — scheduler tick skipped")
        return 0

    now = now or datetime.now()
    fired = 0
    for row in storage.list_schedules(enabled_only=True):
        cron_expr = (row.get("cron_expression") or "").strip()
        if not cron_expr:
            continue
        try:
            itr = croniter(cron_expr, now)
            prev_fire = itr.get_prev(datetime)
        except (ValueError, KeyError) as e:
            storage.record_schedule_fire(
                row["id"], queue_id=None,
                error=f"invalid cron expression {cron_expr!r}: {e}",
            )
            continue

        last_fired = _parse_iso(row.get("last_fired_at"))
        if last_fired is None or prev_fire > last_fired:
            _fire_schedule(row, now)
            fired += 1
    return fired


async def run(interval_seconds: int = 60) -> None:
    """Run the scheduler loop forever — spawn this as an asyncio task on
    application startup.

    Tolerates exceptions per-iteration so a single bug doesn't kill the
    whole scheduler.
    """
    logger.info("scheduler started (interval=%ds)", interval_seconds)
    while True:
        try:
            n = _tick()
            if n:
                logger.info("scheduler fired %d schedules this tick", n)
        except Exception as e:
            logger.exception("scheduler tick failed: %s", e)
        await asyncio.sleep(interval_seconds)
