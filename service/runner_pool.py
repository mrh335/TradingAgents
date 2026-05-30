"""In-process registry of running analyses.

When a client POSTs to /runs, we spawn a worker subprocess (reusing the
existing ``gui.runner`` machinery — its `RunnerHandle` is exactly the
shape we need) and put its output queue into a fan-out.

When clients connect to the WebSocket /runs/{id}/stream, they each get
an asyncio.Queue subscribed to the run's event stream. The fan-out
reader thread reads NDJSON events from the worker stdout, dispatches
to all subscribers, and persists them to SQLite via ``gui.storage``
when terminal events arrive.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gui import runner as legacy_runner
from gui import storage
from gui.config import export_env, load as load_config


@dataclass
class ManagedRun:
    run_id: str
    handle: legacy_runner.RunnerHandle
    subscribers: List["asyncio.Queue[Dict[str, Any]]"] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    finished: bool = False
    decision: Optional[str] = None
    archive_path: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None
    stats: Dict[str, int] = field(default_factory=lambda: {
        "llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0,
    })
    _lock: threading.Lock = field(default_factory=threading.Lock)


class RunnerPool:
    """Singleton-ish registry. Module-level instance below."""

    def __init__(self) -> None:
        self._runs: Dict[str, ManagedRun] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ---- Lifecycle ----------------------------------------------------

    def start(self, *, run_id: str, job: Dict[str, Any],
              batch_id: Optional[str] = None) -> ManagedRun:
        cfg = load_config()
        env = export_env(cfg)
        # Inject run_id so the worker writes the right archive path.
        full_job = dict(job)
        full_job["run_id"] = run_id
        handle = legacy_runner.launch(full_job, env=env)

        managed = ManagedRun(run_id=run_id, handle=handle)
        managed.batch_id = batch_id  # type: ignore[attr-defined]
        with self._lock:
            self._runs[run_id] = managed

        # Mark the DB row as running (it was 'queued' if part of a batch).
        try:
            storage.mark_run_running(run_id)
        except Exception:
            pass

        # Background reader thread: drains the legacy queue and fans out.
        threading.Thread(
            target=self._reader_loop,
            args=(managed,),
            daemon=True,
        ).start()
        return managed

    def start_batch(self, *, batch_id: str, tickers: List[str],
                    base_job: Dict[str, Any]) -> List[str]:
        """Create N runs in 'queued' status for a batch and kick off the first one.

        ``base_job`` is the shared run config (provider, models, depth, vendors,
        date). For each ticker we generate a unique run_id and insert a 'queued'
        row in the runs table. Returns the ordered list of run_ids.

        When a batch run completes, ``_ingest`` sees the ``done``/``error`` event
        and calls ``_advance_batch`` to start the next queued run.
        """
        if not tickers:
            return []

        # Insert queued rows for every ticker.
        run_ids: List[str] = []
        for ticker in tickers:
            run_id = storage.new_run_id()
            run_ids.append(run_id)
            storage.create_run_in_batch(
                run_id=run_id, batch_id=batch_id, ticker=ticker,
                trade_date=base_job["trade_date"],
                provider=base_job["llm_provider"],
                deep_model=base_job["deep_think_llm"],
                quick_model=base_job["quick_think_llm"],
                debate_rounds=base_job["max_debate_rounds"],
                risk_rounds=base_job["max_risk_discuss_rounds"],
                vendors=base_job.get("data_vendors") or {},
            )

        # Kick off the first one. The rest fire on done-events.
        self._start_next_in_batch(batch_id)
        return run_ids

    def _start_next_in_batch(self, batch_id: str) -> bool:
        """Start the next queued run in a batch. Returns False if none left
        (in which case the batch is finalised as 'done')."""
        next_run = storage.next_queued_in_batch(batch_id)
        if not next_run:
            storage.finalize_batch(batch_id)
            return False
        # Build the job from the stored row (vendors live in vendors_json).
        vendors: Dict[str, str] = {}
        try:
            vendors = json.loads(next_run.get("vendors_json") or "{}")
        except Exception:
            pass
        job = {
            "ticker": next_run["ticker"],
            "trade_date": next_run["trade_date"],
            "llm_provider": next_run["provider"],
            "deep_think_llm": next_run["deep_model"],
            "quick_think_llm": next_run["quick_model"],
            "max_debate_rounds": next_run["debate_rounds"] or 1,
            "max_risk_discuss_rounds": next_run["risk_rounds"] or 1,
            "data_vendors": vendors or {
                "core_stock_apis": "yfinance",
                "technical_indicators": "yfinance",
                "fundamental_data": "yfinance",
                "news_data": "yfinance",
            },
        }
        self.start(run_id=next_run["run_id"], job=job, batch_id=batch_id)
        return True

    def cancel_batch(self, batch_id: str) -> int:
        """Cancel any running run for this batch + mark remaining queued runs
        as cancelled. Returns the count of cancelled queued runs."""
        # Cancel the currently-active run if any.
        with self._lock:
            active = [m for m in self._runs.values()
                      if getattr(m, "batch_id", None) == batch_id and m.handle.is_running()]
        for m in active:
            m.handle.cancel()
        # Mark queued rows as error to skip them, finalize the batch.
        # Use the tuned connection (WAL + busy_timeout) — a raw
        # sqlite3.connect here could hit "database is locked" against a
        # batch actively writing runs.
        try:
            with storage._conn() as conn:
                cur = conn.execute(
                    "UPDATE runs SET status='error', error_message='cancelled with batch' "
                    "WHERE batch_id=? AND status='queued'",
                    (batch_id,),
                )
                count = cur.rowcount
        except Exception:
            count = 0
        storage.cancel_batch(batch_id)
        return count

    def get(self, run_id: str) -> Optional[ManagedRun]:
        with self._lock:
            return self._runs.get(run_id)

    def cancel(self, run_id: str) -> bool:
        managed = self.get(run_id)
        if not managed:
            return False
        managed.handle.cancel()
        managed.error = "Cancelled by user."
        self._mark_finished(managed)
        return True

    # ---- Subscriptions ------------------------------------------------

    async def subscribe(self, run_id: str) -> "asyncio.Queue[Dict[str, Any]]":
        """Return a queue that will receive every event for this run.

        Pre-existing events (sent before subscription) are replayed first
        so a late-arriving client still sees a full transcript.
        """
        managed = self.get(run_id)
        q: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        if not managed:
            await q.put({"type": "error", "data": {"message": f"unknown run {run_id}"}})
            await q.put({"type": "_eof", "data": {}})
            return q
        with managed._lock:
            for ev in managed.history:
                await q.put(ev)
            if managed.finished:
                await q.put({"type": "_eof", "data": {}})
            else:
                managed.subscribers.append(q)
        return q

    def unsubscribe(self, run_id: str, q: "asyncio.Queue[Dict[str, Any]]") -> None:
        managed = self.get(run_id)
        if not managed:
            return
        with managed._lock:
            try:
                managed.subscribers.remove(q)
            except ValueError:
                pass

    # ---- Internal -----------------------------------------------------

    def _reader_loop(self, managed: ManagedRun) -> None:
        """Drain the legacy RunnerHandle and dispatch to subscribers + DB."""
        while True:
            events = managed.handle.poll_events()
            if events:
                for ev in events:
                    self._ingest(managed, ev)
            if not managed.handle.is_running() and not events:
                break
            # Short sleep — this is a thread, not asyncio.
            threading.Event().wait(0.25)

        # Final drain
        for ev in managed.handle.poll_events():
            self._ingest(managed, ev)
        self._mark_finished(managed)

    def _ingest(self, managed: ManagedRun, raw: Dict[str, Any]) -> None:
        # Normalise to {type, data} envelope expected by the schema.
        kind = raw.get("type", "log")
        data = {k: v for k, v in raw.items() if k != "type"}
        envelope = {"type": kind, "data": data}

        with managed._lock:
            managed.history.append(envelope)
            if kind == "stats":
                for k in ("llm_calls", "tool_calls", "tokens_in", "tokens_out"):
                    if k in data:
                        managed.stats[k] = data[k]
            elif kind == "warning":
                managed.warning = data.get("message")
            elif kind == "done":
                managed.decision = data.get("decision")
                managed.archive_path = data.get("archive_path") or data.get("report_path")
                # Persist to DB.
                try:
                    storage.update_run_stats(
                        managed.run_id,
                        llm_calls=managed.stats["llm_calls"],
                        tool_calls=managed.stats["tool_calls"],
                        tokens_in=managed.stats["tokens_in"],
                        tokens_out=managed.stats["tokens_out"],
                    )
                    storage.finalize_run(
                        managed.run_id,
                        decision=managed.decision,
                        log_path=managed.archive_path,
                    )
                except Exception:
                    pass
            elif kind == "error":
                managed.error = data.get("message", "unknown error")
                try:
                    storage.finalize_run(
                        managed.run_id,
                        decision=None,
                        log_path=None,
                        error=managed.error,
                    )
                except Exception:
                    pass
            subs = list(managed.subscribers)

        for q in subs:
            self._loop_call(q.put_nowait, envelope)

    def _mark_finished(self, managed: ManagedRun) -> None:
        with managed._lock:
            if managed.finished:
                return
            managed.finished = True
            subs = list(managed.subscribers)
            managed.subscribers.clear()
        for q in subs:
            self._loop_call(q.put_nowait, {"type": "_eof", "data": {}})

        # If this run was part of a batch, kick off the next queued one.
        # Run on a worker thread to avoid blocking the reader.
        batch_id = getattr(managed, "batch_id", None)
        if batch_id:
            threading.Thread(
                target=self._start_next_in_batch,
                args=(batch_id,),
                daemon=True,
            ).start()

    def _loop_call(self, fn, *args, **kwargs) -> None:
        """Schedule a thread-safe call on the asyncio loop, falling back to
        direct call if no loop has been attached (shouldn't happen in
        normal operation but keeps unit tests simple)."""
        if self._loop is None or not self._loop.is_running():
            try:
                fn(*args, **kwargs)
            except Exception:
                pass
            return
        self._loop.call_soon_threadsafe(fn, *args, **kwargs)


pool = RunnerPool()
