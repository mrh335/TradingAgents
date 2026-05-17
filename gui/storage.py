"""SQLite layer for notes and run metadata.

One file at ~/.tradingagents/gui.db with two tables:
- ``runs``: one row per analysis the GUI has launched. The actual debate
  transcript still lives on disk in ~/.tradingagents/logs/<TICKER>/...,
  this table just indexes them with status, costs, and the path back.
- ``notes``: free-text markdown notes. Optional fk to a run_id and/or
  ticker so notes attach to whatever the user is looking at.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

DB_PATH = Path.home() / ".tradingagents" / "gui.db"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def init_db() -> None:
    """Create the database file and tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                provider TEXT,
                deep_model TEXT,
                quick_model TEXT,
                debate_rounds INTEGER,
                risk_rounds INTEGER,
                vendors_json TEXT,
                status TEXT NOT NULL,
                decision TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                llm_calls INTEGER DEFAULT 0,
                tool_calls INTEGER DEFAULT 0,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                log_path TEXT,
                error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS runs_ticker_date ON runs(ticker, trade_date);
            CREATE INDEX IF NOT EXISTS runs_started ON runs(started_at DESC);

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                run_id TEXT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                tags TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS notes_ticker ON notes(ticker);
            CREATE INDEX IF NOT EXISTS notes_run ON notes(run_id);

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                model TEXT
            );
            CREATE INDEX IF NOT EXISTS chat_messages_run ON chat_messages(run_id, id);

            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT UNIQUE NOT NULL,
                added_at TEXT NOT NULL,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                shares REAL NOT NULL,
                cost_basis_per_share REAL NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                closing_price REAL,
                account TEXT,
                notes TEXT
            );
            CREATE INDEX IF NOT EXISTS positions_ticker ON positions(ticker, closed_at);

            CREATE TABLE IF NOT EXISTS simulations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                base_run_id TEXT,
                ticker TEXT,
                scenario_json TEXT,
                result_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY,
                name TEXT,
                trade_date TEXT NOT NULL,
                total INTEGER NOT NULL,
                provider TEXT,
                deep_model TEXT,
                quick_model TEXT,
                debate_rounds INTEGER,
                risk_rounds INTEGER,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                error_message TEXT
            );

            -- Trading restrictions: per-ticker blackout windows the user is
            -- obligated to honor (employee restricted lists, 10b5-1 closure
            -- windows around earnings, regulatory restrictions, etc). The
            -- trader + PM agents read these and refuse to recommend trades
            -- inside the window. Hard constraint, not a soft suggestion.
            CREATE TABLE IF NOT EXISTS trading_restrictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                start_date TEXT NOT NULL,       -- YYYY-MM-DD inclusive
                end_date TEXT,                  -- YYYY-MM-DD inclusive; NULL = open-ended
                kind TEXT NOT NULL DEFAULT 'blackout',  -- blackout | restricted_list | other
                reason TEXT,                    -- free-form explanation surfaced to the agent
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS trading_restrictions_ticker ON trading_restrictions(ticker, start_date);

            -- Run queue: work items deposited by the webapp, consumed by an
            -- external poller (e.g. Claude Desktop / Claude Code running the
            -- tradingagents-analyze skill). Decouples "user wants analysis"
            -- from "LLM client runs it" — the webapp just records the ask,
            -- the poller picks up unclaimed rows.
            CREATE TABLE IF NOT EXISTS run_queue (
                id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'analyze',  -- analyze | brief | refresh
                options_json TEXT,                     -- free-form per-mode options
                requested_by TEXT,                     -- 'web-ui' | 'api' | user label
                priority INTEGER NOT NULL DEFAULT 0,   -- higher = picked first
                status TEXT NOT NULL DEFAULT 'pending',-- pending | claimed | done | error | cancelled
                claimed_by TEXT,                       -- worker identifier
                claimed_at TEXT,
                completed_at TEXT,
                result_run_id TEXT,                    -- foreign key to runs.run_id once finished
                error_message TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS run_queue_status ON run_queue(status, priority DESC, created_at);
            CREATE INDEX IF NOT EXISTS run_queue_ticker ON run_queue(ticker);
            """
        )
        # Lazy column add — older DBs created before batch support.
        try:
            c.execute("ALTER TABLE runs ADD COLUMN batch_id TEXT")
        except Exception:
            pass
        c.execute("CREATE INDEX IF NOT EXISTS runs_batch ON runs(batch_id)")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with concurrency settings tuned for the
    webapp's workload.

    Default SQLite journal mode is rollback-journal (DELETE), which serializes
    writers and blocks them behind readers — concurrent POSTs from the
    /batch queue button instantly produce ``database is locked`` errors.
    We enable WAL mode (one writer + many readers without blocking) and
    set a 10-second busy-timeout so the rare contention waits instead of
    erroring. Both are idempotent — running them on every connection is
    cheap and the persistent mode change carries across the whole DB.

    Also bumps the timeout= constructor arg from the default 5s to 10s so
    a slow disk (or a backup task on the NAS) doesn't break the API.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # WAL = concurrent readers don't block writers; one writer at a time
    # but other connections see the last committed snapshot meanwhile.
    # busy_timeout = if SQLite hits an internal lock, wait up to N ms
    # before erroring instead of raising immediately.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")  # WAL-safe; faster than FULL
    except sqlite3.OperationalError:
        # Older SQLite versions or read-only filesystem — fall through,
        # the original blocking behaviour is still correct.
        pass
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def new_run_id() -> str:
    return uuid.uuid4().hex


def create_run(
    *,
    run_id: str,
    ticker: str,
    trade_date: str,
    provider: str,
    deep_model: str,
    quick_model: str,
    debate_rounds: int,
    risk_rounds: int,
    vendors: Dict[str, str],
) -> None:
    with _conn() as c:
        c.execute(
            """
            INSERT INTO runs(run_id, ticker, trade_date, provider, deep_model,
                             quick_model, debate_rounds, risk_rounds, vendors_json,
                             status, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                run_id, ticker, trade_date, provider, deep_model, quick_model,
                debate_rounds, risk_rounds, json.dumps(vendors), _now(),
            ),
        )


def update_run_stats(run_id: str, *, llm_calls: int, tool_calls: int,
                    tokens_in: int, tokens_out: int) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE runs SET llm_calls=?, tool_calls=?, tokens_in=?, tokens_out=?
               WHERE run_id=?""",
            (llm_calls, tool_calls, tokens_in, tokens_out, run_id),
        )


def finalize_run(run_id: str, *, decision: Optional[str], log_path: Optional[str],
                 error: Optional[str] = None) -> None:
    status = "error" if error else "done"
    with _conn() as c:
        c.execute(
            """UPDATE runs SET status=?, decision=?, log_path=?, error_message=?,
                              completed_at=? WHERE run_id=?""",
            (status, decision, log_path, error, _now(), run_id),
        )


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(*, ticker: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    with _conn() as c:
        if ticker:
            rows = c.execute(
                "SELECT * FROM runs WHERE ticker=? ORDER BY started_at DESC LIMIT ?",
                (ticker, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def add_note(*, title: str, body: str, ticker: Optional[str] = None,
             run_id: Optional[str] = None, tags: Optional[str] = None) -> int:
    now = _now()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO notes(ticker, run_id, title, body, tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker, run_id, title, body, tags, now, now),
        )
        return int(cur.lastrowid)


def update_note(note_id: int, *, title: str, body: str, tags: Optional[str]) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE notes SET title=?, body=?, tags=?, updated_at=? WHERE id=?""",
            (title, body, tags, _now(), note_id),
        )


def delete_note(note_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM notes WHERE id=?", (note_id,))


def list_notes(*, ticker: Optional[str] = None, run_id: Optional[str] = None,
               query: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM notes WHERE 1=1"
    args: List[Any] = []
    if ticker:
        sql += " AND ticker=?"
        args.append(ticker)
    if run_id:
        sql += " AND run_id=?"
        args.append(run_id)
    if query:
        sql += " AND (title LIKE ? OR body LIKE ? OR tags LIKE ?)"
        like = f"%{query}%"
        args.extend([like, like, like])
    sql += " ORDER BY updated_at DESC"
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


def get_note(note_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Chat messages — one conversation per run_id, persisted across reloads.
# ---------------------------------------------------------------------------

def add_chat_message(*, run_id: str, role: str, content: str,
                     model: Optional[str] = None) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO chat_messages(run_id, role, content, created_at, model)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, role, content, _now(), model),
        )
        return int(cur.lastrowid)


def list_chat_messages(run_id: str) -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM chat_messages WHERE run_id=? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def clear_chat(run_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM chat_messages WHERE run_id=?", (run_id,))


# ---------------------------------------------------------------------------
# Watchlist (per-ticker subscription for live price/news streams)
# ---------------------------------------------------------------------------

def list_watchlist() -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM watchlist ORDER BY ticker"
        ).fetchall()
        return [dict(r) for r in rows]


def add_to_watchlist(ticker: str, notes: Optional[str] = None) -> Dict[str, Any]:
    ticker = ticker.strip().upper()
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO watchlist(ticker, added_at, notes) VALUES (?, ?, ?)",
            (ticker, _now(), notes),
        )
        if notes is not None:
            c.execute(
                "UPDATE watchlist SET notes=? WHERE ticker=?", (notes, ticker)
            )
        row = c.execute("SELECT * FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
        return dict(row)


def remove_from_watchlist(ticker: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM watchlist WHERE ticker=?", (ticker.upper(),))


# ---------------------------------------------------------------------------
# Positions (long-form portfolio tracking)
# ---------------------------------------------------------------------------

def list_positions(*, include_closed: bool = False) -> List[Dict[str, Any]]:
    with _conn() as c:
        if include_closed:
            rows = c.execute("SELECT * FROM positions ORDER BY ticker, opened_at").fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM positions WHERE closed_at IS NULL ORDER BY ticker, opened_at"
            ).fetchall()
        return [dict(r) for r in rows]


def get_position(position_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        return dict(row) if row else None


def add_position(*, ticker: str, shares: float, cost_basis_per_share: float,
                 opened_at: Optional[str] = None, account: Optional[str] = None,
                 notes: Optional[str] = None) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO positions(ticker, shares, cost_basis_per_share,
                                     opened_at, account, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ticker.upper(), float(shares), float(cost_basis_per_share),
             opened_at or _now(), account, notes),
        )
        return int(cur.lastrowid)


def close_position(position_id: int, *, closing_price: float,
                   closed_at: Optional[str] = None) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE positions SET closing_price=?, closed_at=? WHERE id=?""",
            (float(closing_price), closed_at or _now(), position_id),
        )


def update_position(position_id: int, *, shares: Optional[float] = None,
                    cost_basis_per_share: Optional[float] = None,
                    account: Optional[str] = None, notes: Optional[str] = None
                    ) -> None:
    fields = []
    args: List[Any] = []
    if shares is not None:
        fields.append("shares=?"); args.append(float(shares))
    if cost_basis_per_share is not None:
        fields.append("cost_basis_per_share=?"); args.append(float(cost_basis_per_share))
    if account is not None:
        fields.append("account=?"); args.append(account)
    if notes is not None:
        fields.append("notes=?"); args.append(notes)
    if not fields:
        return
    args.append(position_id)
    with _conn() as c:
        c.execute(f"UPDATE positions SET {', '.join(fields)} WHERE id=?", args)


def delete_position(position_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM positions WHERE id=?", (position_id,))


# ---------------------------------------------------------------------------
# Simulations
# ---------------------------------------------------------------------------

def list_simulations() -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM simulations ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def add_simulation(*, name: str, base_run_id: Optional[str], ticker: Optional[str],
                  scenario_json: str, result_json: str) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO simulations(name, base_run_id, ticker,
                                       scenario_json, result_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, base_run_id, (ticker.upper() if ticker else None),
             scenario_json, result_json, _now()),
        )
        return int(cur.lastrowid)


def get_simulation(sim_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute("SELECT * FROM simulations WHERE id=?", (sim_id,)).fetchone()
        return dict(row) if row else None


def delete_simulation(sim_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM simulations WHERE id=?", (sim_id,))


# ---------------------------------------------------------------------------
# Batches — group of runs over a ticker list with one shared config.
# ---------------------------------------------------------------------------

def new_batch_id() -> str:
    return uuid.uuid4().hex


def create_batch(*, batch_id: str, name: Optional[str], trade_date: str,
                 total: int, provider: str, deep_model: str, quick_model: str,
                 debate_rounds: int, risk_rounds: int) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO batches(id, name, trade_date, total, provider,
                                   deep_model, quick_model, debate_rounds,
                                   risk_rounds, status, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
            (batch_id, name, trade_date, total, provider, deep_model,
             quick_model, debate_rounds, risk_rounds, _now()),
        )


def finalize_batch(batch_id: str, *, error: Optional[str] = None) -> None:
    status = "error" if error else "done"
    with _conn() as c:
        c.execute(
            """UPDATE batches SET status=?, completed_at=?, error_message=?
               WHERE id=?""",
            (status, _now(), error, batch_id),
        )


def cancel_batch(batch_id: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE batches SET status='cancelled', completed_at=? WHERE id=?",
            (_now(), batch_id),
        )


def get_batch(batch_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        return dict(row) if row else None


def list_batches(limit: int = 50) -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM batches ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def runs_in_batch(batch_id: str) -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            """SELECT * FROM runs WHERE batch_id=?
               ORDER BY started_at ASC""",
            (batch_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Run queue — external-worker handoff (used by tradingagents-analyze skill
# running in Claude Desktop / Claude Code, or any other poller).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Trading restrictions — per-ticker blackout windows.
# ---------------------------------------------------------------------------

def list_restrictions(*, ticker: Optional[str] = None,
                       active_on: Optional[str] = None,
                       ) -> List[Dict[str, Any]]:
    """List restrictions, optionally filtered.

    ``ticker``: case-insensitive match.
    ``active_on``: YYYY-MM-DD — only return restrictions whose [start_date,
    end_date] window contains this date (open-ended end_date counts as
    "indefinitely active").
    """
    sql = "SELECT * FROM trading_restrictions WHERE 1=1"
    args: List[Any] = []
    if ticker:
        sql += " AND ticker = ?"
        args.append(ticker.strip().upper())
    if active_on:
        sql += " AND start_date <= ? AND (end_date IS NULL OR end_date >= ?)"
        args.extend([active_on, active_on])
    sql += " ORDER BY ticker, start_date DESC"
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


def add_restriction(*, ticker: str, start_date: str,
                     end_date: Optional[str] = None,
                     kind: str = "blackout",
                     reason: Optional[str] = None) -> Dict[str, Any]:
    now = _now()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO trading_restrictions(ticker, start_date, end_date,
                                                kind, reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker.strip().upper(), start_date, end_date, kind, reason, now, now),
        )
        row = c.execute(
            "SELECT * FROM trading_restrictions WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def update_restriction(restriction_id: int, *,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None,
                        kind: Optional[str] = None,
                        reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    fields = []
    args: List[Any] = []
    if start_date is not None:
        fields.append("start_date=?"); args.append(start_date)
    if end_date is not None:
        fields.append("end_date=?"); args.append(end_date)
    if kind is not None:
        fields.append("kind=?"); args.append(kind)
    if reason is not None:
        fields.append("reason=?"); args.append(reason)
    if not fields:
        return get_restriction(restriction_id)
    fields.append("updated_at=?"); args.append(_now())
    args.append(restriction_id)
    with _conn() as c:
        c.execute(
            f"UPDATE trading_restrictions SET {', '.join(fields)} WHERE id=?",
            args,
        )
    return get_restriction(restriction_id)


def get_restriction(restriction_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM trading_restrictions WHERE id=?", (restriction_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_restriction(restriction_id: int) -> bool:
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM trading_restrictions WHERE id=?", (restriction_id,)
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Token-usage events — aggregated cost/usage tracking across every run.
# ---------------------------------------------------------------------------

def list_token_events(*, since_iso: Optional[str] = None,
                       ticker: Optional[str] = None,
                       limit: int = 5000,
                       ) -> List[Dict[str, Any]]:
    """Pull tokens_in / tokens_out per run for cost + usage charts.

    Sources data from the existing ``runs`` table (status='done', tokens
    populated by the runner or by the /runs/import path from Claude Desktop
    skill submissions). The /tokens page slices this by day / provider /
    ticker without needing a separate event table.
    """
    sql = (
        "SELECT run_id, ticker, trade_date, provider, deep_model, quick_model, "
        "       started_at, completed_at, tokens_in, tokens_out, llm_calls, tool_calls "
        "FROM runs WHERE status='done' "
    )
    args: List[Any] = []
    if since_iso:
        sql += "AND completed_at >= ? "
        args.append(since_iso)
    if ticker:
        sql += "AND ticker = ? "
        args.append(ticker.strip().upper())
    sql += "ORDER BY completed_at DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


def new_queue_id() -> str:
    return uuid.uuid4().hex


def queue_request(
    *,
    ticker: str,
    trade_date: str,
    mode: str = "analyze",
    options: Optional[Dict[str, Any]] = None,
    requested_by: Optional[str] = None,
    priority: int = 0,
) -> Dict[str, Any]:
    """Insert a new pending queue row. Returns the inserted record."""
    qid = new_queue_id()
    options_json = json.dumps(options or {})
    with _conn() as c:
        c.execute(
            """INSERT INTO run_queue(id, ticker, trade_date, mode, options_json,
                                     requested_by, priority, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (qid, ticker.strip().upper(), trade_date, mode, options_json,
             requested_by, priority, _now()),
        )
        row = c.execute("SELECT * FROM run_queue WHERE id=?", (qid,)).fetchone()
        return dict(row)


def list_queue(*, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """List queue rows. Default returns everything ordered by created_at DESC.
    Pass status='pending' to scope to unclaimed work."""
    with _conn() as c:
        if status:
            rows = c.execute(
                """SELECT * FROM run_queue WHERE status=?
                   ORDER BY priority DESC, created_at ASC LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM run_queue ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_queue_item(queue_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute("SELECT * FROM run_queue WHERE id=?", (queue_id,)).fetchone()
        return dict(row) if row else None


def claim_queue_items(*, claimed_by: str, max_items: int = 5,
                      mode: Optional[str] = None) -> List[Dict[str, Any]]:
    """Atomically claim up to ``max_items`` pending rows for a worker.

    Sets status='claimed' so a second poller doesn't double-process the
    same work. The worker should then run the analysis and POST results
    back, calling ``complete_queue_item`` or ``fail_queue_item`` when done.
    Stale claims older than 30 minutes can be reverted via ``reclaim_stale``.
    """
    with _conn() as c:
        if mode:
            rows = c.execute(
                """SELECT * FROM run_queue WHERE status='pending' AND mode=?
                   ORDER BY priority DESC, created_at ASC LIMIT ?""",
                (mode, max_items),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT * FROM run_queue WHERE status='pending'
                   ORDER BY priority DESC, created_at ASC LIMIT ?""",
                (max_items,),
            ).fetchall()
        claimed = []
        for r in rows:
            c.execute(
                """UPDATE run_queue SET status='claimed', claimed_by=?, claimed_at=?
                   WHERE id=? AND status='pending'""",
                (claimed_by, _now(), r["id"]),
            )
            # Re-read to reflect the claim.
            updated = c.execute(
                "SELECT * FROM run_queue WHERE id=?", (r["id"],)
            ).fetchone()
            if updated and updated["status"] == "claimed":
                claimed.append(dict(updated))
        return claimed


def complete_queue_item(queue_id: str, *, result_run_id: Optional[str] = None) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE run_queue SET status='done', completed_at=?, result_run_id=?
               WHERE id=?""",
            (_now(), result_run_id, queue_id),
        )


def fail_queue_item(queue_id: str, *, error: str) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE run_queue SET status='error', completed_at=?, error_message=?
               WHERE id=?""",
            (_now(), error, queue_id),
        )


def cancel_queue_item(queue_id: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE run_queue SET status='cancelled', completed_at=? "
            "WHERE id=? AND status IN ('pending', 'claimed')",
            (_now(), queue_id),
        )
        return cur.rowcount > 0


def delete_queue_item(queue_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM run_queue WHERE id=?", (queue_id,))
        return cur.rowcount > 0


def reclaim_stale_queue_items(*, older_than_seconds: int = 1800) -> int:
    """Revert claims older than ``older_than_seconds`` back to pending.

    Use to recover from worker crashes — if a poller claimed a job and
    never reported back, it's safe to retry after enough time.
    Returns the number of rows reverted.
    """
    cutoff = (datetime.utcnow() - timedelta(seconds=older_than_seconds)
              ).isoformat(timespec="seconds") + "Z"
    with _conn() as c:
        cur = c.execute(
            """UPDATE run_queue SET status='pending', claimed_by=NULL, claimed_at=NULL
               WHERE status='claimed' AND claimed_at < ?""",
            (cutoff,),
        )
        return cur.rowcount


def create_run_in_batch(*, run_id: str, batch_id: str, ticker: str,
                        trade_date: str, provider: str, deep_model: str,
                        quick_model: str, debate_rounds: int, risk_rounds: int,
                        vendors: Dict[str, str]) -> None:
    """Same as create_run but tagged with batch_id and starts in ``queued``."""
    with _conn() as c:
        c.execute(
            """INSERT INTO runs(run_id, ticker, trade_date, provider, deep_model,
                                quick_model, debate_rounds, risk_rounds, vendors_json,
                                status, started_at, batch_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
            (run_id, ticker, trade_date, provider, deep_model, quick_model,
             debate_rounds, risk_rounds, json.dumps(vendors), _now(), batch_id),
        )


def next_queued_in_batch(batch_id: str) -> Optional[Dict[str, Any]]:
    """Find the next not-yet-started run in a batch (status='queued')."""
    with _conn() as c:
        row = c.execute(
            """SELECT * FROM runs WHERE batch_id=? AND status='queued'
               ORDER BY started_at ASC LIMIT 1""",
            (batch_id,),
        ).fetchone()
        return dict(row) if row else None


def mark_run_running(run_id: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE runs SET status='running', started_at=? WHERE run_id=?",
            (_now(), run_id),
        )
