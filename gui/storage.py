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

            -- Per-ticker analysis schedules. Each row is one (ticker, cron)
            -- pair that the background scheduler in service/scheduler.py
            -- evaluates every minute. When due, it POSTs a queue item to
            -- /run-queue with the row's mode + options. Composes with the
            -- existing Claude-Desktop drain cron — schedules push work in,
            -- the drain cron pulls it out.
            CREATE TABLE IF NOT EXISTS ticker_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                cron_expression TEXT NOT NULL,    -- 5-field cron, local TZ
                mode TEXT NOT NULL DEFAULT 'analyze',
                options_json TEXT,                 -- worker options (provider, models, etc.)
                enabled INTEGER NOT NULL DEFAULT 1,
                notes TEXT,                        -- user-facing label e.g. "weekday morning refresh"
                last_fired_at TEXT,                -- ISO timestamp of last successful fire
                last_queue_id TEXT,                -- run_queue.id of the last-created item
                last_error TEXT,                   -- if last fire failed, the message
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ticker_schedules_enabled ON ticker_schedules(enabled, ticker);

            -- Trade journal: actual executed trades the user logs (or
            -- imports from the broker). Separate from `positions` which
            -- tracks current holdings; this table is the history of
            -- buy/sell/dividend/split/transfer activity over time. Feeds
            -- the realized-P&L view + future backtest "actual vs notional"
            -- comparison.
            CREATE TABLE IF NOT EXISTS trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,           -- buy | sell | dividend | split | transfer | short | cover
                shares REAL NOT NULL,           -- always positive; action implies sign
                price REAL,                     -- per-share execution price (NULL for splits)
                executed_at TEXT NOT NULL,      -- YYYY-MM-DD trade date
                account TEXT,
                notes TEXT,
                linked_run_id TEXT,             -- optional FK to runs.run_id ("traded on this recommendation")
                fees REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS trade_journal_ticker ON trade_journal(ticker, executed_at);

            -- News alerts: scored news items per ticker, surfaced when
            -- impact is meaningful. Populated by a background poller
            -- (service/news_alerts_poller.py) that calls yfinance every
            -- 15 min for watchlist + position tickers.
            CREATE TABLE IF NOT EXISTS news_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                headline TEXT NOT NULL,
                url TEXT,
                published_at TEXT,              -- ISO timestamp from upstream
                source TEXT,
                impact TEXT NOT NULL DEFAULT 'low',   -- high | medium | low
                impact_score INTEGER NOT NULL DEFAULT 0,
                keywords TEXT,                   -- comma-separated triggering keywords
                status TEXT NOT NULL DEFAULT 'unread',  -- unread | read | dismissed
                fetched_at TEXT NOT NULL,
                hash TEXT UNIQUE                 -- dedupe key (url or headline+ticker)
            );
            CREATE INDEX IF NOT EXISTS news_alerts_ticker ON news_alerts(ticker, published_at DESC);
            CREATE INDEX IF NOT EXISTS news_alerts_status ON news_alerts(status, impact_score DESC);

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

            -- Smart-money managers: institutional managers whose 13F-HR
            -- filings we track for the /holders view. Seeded on first
            -- init with the curated list from edgar_client.SMART_MONEY_SEEDS
            -- (Berkshire, Burry's Scion, Klarman/Baupost, Ackman/Pershing,
            -- etc.). User can disable any row to skip it on the next poll.
            CREATE TABLE IF NOT EXISTS smart_money_managers (
                cik TEXT PRIMARY KEY,         -- 10-digit zero-padded CIK
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_refreshed_at TEXT,
                last_filing_date TEXT,        -- date of latest 13F successfully fetched
                last_report_date TEXT,        -- the report period the filing covers
                last_accession_no TEXT,
                total_value REAL,             -- sum of position values, USD
                position_count INTEGER,
                last_error TEXT,
                created_at TEXT NOT NULL
            );

            -- 13F holdings: one row per (manager, filing, position). The
            -- (manager_cik, accession_no, cusip) tuple is unique. When
            -- a fresh filing lands, the poller inserts the new
            -- snapshot AND looks up the prior filing to compute
            -- qoq_change_pct so the UI can highlight new buys / dumps.
            CREATE TABLE IF NOT EXISTS holdings_13f (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manager_cik TEXT NOT NULL,
                manager_name TEXT NOT NULL,
                accession_no TEXT NOT NULL,
                filing_date TEXT NOT NULL,    -- YYYY-MM-DD when filing was submitted
                report_date TEXT NOT NULL,    -- YYYY-MM-DD end-of-quarter the filing reports
                cusip TEXT NOT NULL,
                ticker TEXT,                  -- mapped via edgar_client.CUSIP_TO_TICKER (NULL = unmapped)
                name_of_issuer TEXT,
                title_of_class TEXT,
                shares INTEGER NOT NULL,
                value REAL NOT NULL,          -- USD position value at report_date
                put_call TEXT,                -- 'Put' | 'Call' | NULL (long stock)
                prev_shares INTEGER,          -- shares in the prior quarter's filing
                qoq_change_pct REAL,          -- (shares - prev_shares) / prev_shares * 100; NULL = new position
                pct_of_manager_aum REAL,      -- this position's value / manager's total AUM, percent
                created_at TEXT NOT NULL,
                UNIQUE(manager_cik, accession_no, cusip)
            );
            CREATE INDEX IF NOT EXISTS holdings_13f_ticker ON holdings_13f(ticker, report_date DESC);
            CREATE INDEX IF NOT EXISTS holdings_13f_manager ON holdings_13f(manager_cik, report_date DESC);

            -- Portfolio Q&A: free-form questions asked of the user's
            -- portfolio + analysis data. Each row is one question with
            -- its captured context snapshot, the resulting answer, and
            -- (for queue-mode questions) the run_queue id tracking the
            -- pending Claude Desktop work. Grouped into multi-turn
            -- conversations via conversation_id.
            CREATE TABLE IF NOT EXISTS portfolio_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,    -- groups a thread of questions
                question TEXT NOT NULL,
                answer_md TEXT,                    -- the answer (markdown)
                context_snapshot_json TEXT,        -- positions/runs/trades captured at ask-time
                mode TEXT NOT NULL DEFAULT 'queue', -- 'queue' (Claude Desktop) | 'sync' (Anthropic API)
                status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'complete' | 'error'
                source TEXT,                       -- 'claude-desktop' | 'anthropic-api' | 'manual'
                queue_id TEXT,                     -- run_queue row id when mode='queue'
                error_message TEXT,
                tokens_in INTEGER,                 -- when mode='sync', captures cost
                tokens_out INTEGER,
                requested_at TEXT NOT NULL,
                answered_at TEXT
            );
            CREATE INDEX IF NOT EXISTS portfolio_questions_conv ON portfolio_questions(conversation_id, id);
            CREATE INDEX IF NOT EXISTS portfolio_questions_status ON portfolio_questions(status, requested_at DESC);

            -- Earnings summaries: one row per (ticker, report_date) pair,
            -- holding the AI-generated plain-English digest of that quarter's
            -- press release. Sidecar-style content but stored in SQL so we
            -- can query by ticker + sort by report_date easily. Populated
            -- by Claude Desktop via the run_queue with mode='earnings_summary';
            -- the webapp drops the request, the skill fetches the press
            -- release and structured data, generates the digest, POSTs back.
            CREATE TABLE IF NOT EXISTS earnings_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                report_date TEXT NOT NULL,    -- YYYY-MM-DD of the earnings call
                bullets_md TEXT,              -- plain-english bullet summary (markdown)
                structured_json TEXT,         -- side-by-side comparison table as JSON
                source TEXT NOT NULL DEFAULT 'claude-desktop',  -- 'claude-desktop' | 'manual'
                status TEXT NOT NULL DEFAULT 'pending',         -- 'pending' | 'complete' | 'error'
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(ticker, report_date)
            );
            CREATE INDEX IF NOT EXISTS earnings_summaries_ticker ON earnings_summaries(ticker, report_date DESC);
            """
        )
        # Lazy column add — older DBs created before batch support.
        try:
            c.execute("ALTER TABLE runs ADD COLUMN batch_id TEXT")
        except Exception:
            pass
        c.execute("CREATE INDEX IF NOT EXISTS runs_batch ON runs(batch_id)")

        # Lazy column adds for earnings-window restrictions (Batch 3).
        # Empty (NULL) means "use start_date/end_date the old way";
        # populated means "this is an earnings-relative restriction —
        # compute the window dynamically from the ticker's next earnings".
        for stmt in (
            "ALTER TABLE trading_restrictions ADD COLUMN earnings_days_before INTEGER",
            "ALTER TABLE trading_restrictions ADD COLUMN earnings_days_after INTEGER",
            # New columns for the earnings-RELATIVE-WINDOW (open) variant.
            # earnings_window semantics:
            #   open_offset_days: when the trading window OPENS, expressed
            #     as days after the earnings date (typically 1-3 for a
            #     post-earnings cooldown).
            #   duration_days: how long the window stays open before closing
            #     again until the next earnings call.
            # Outside [open, open+duration], the ticker is RESTRICTED.
            "ALTER TABLE trading_restrictions ADD COLUMN earnings_window_open_offset_days INTEGER",
            "ALTER TABLE trading_restrictions ADD COLUMN earnings_window_duration_days INTEGER",
        ):
            try:
                c.execute(stmt)
            except Exception:
                pass

        # Seed the smart-money manager list on first init. If the table
        # is empty, insert the curated names from edgar_client.
        # SMART_MONEY_SEEDS. Subsequent inits are no-ops — the user can
        # disable rows or add new managers via the API without re-seeding.
        try:
            existing = c.execute("SELECT COUNT(*) FROM smart_money_managers").fetchone()[0]
        except Exception:
            existing = 0
        if not existing:
            try:
                from service.edgar_client import SMART_MONEY_SEEDS  # local import to avoid circular
                now = _now()
                for cik, name in SMART_MONEY_SEEDS:
                    try:
                        c.execute(
                            """INSERT OR IGNORE INTO smart_money_managers
                               (cik, name, enabled, created_at) VALUES (?, ?, 1, ?)""",
                            (cik, name, now),
                        )
                    except Exception:
                        continue
            except Exception:
                # service module may not be importable in some contexts (e.g.
                # a tooling script run before the API package is on the path).
                # Seeding will happen on the first API boot, which is fine.
                pass


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
# Trade journal — actual executed trades
# ---------------------------------------------------------------------------

ALLOWED_TRADE_ACTIONS = (
    "buy", "sell", "dividend", "split", "transfer", "short", "cover",
)


def list_trades(*, ticker: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM trade_journal WHERE 1=1"
    args: List[Any] = []
    if ticker:
        sql += " AND ticker = ?"
        args.append(ticker.strip().upper())
    sql += " ORDER BY executed_at DESC, id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def get_trade(trade_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute("SELECT * FROM trade_journal WHERE id=?", (trade_id,)).fetchone()
        return dict(row) if row else None


def add_trade(*, ticker: str, action: str, shares: float,
              executed_at: str, price: Optional[float] = None,
              account: Optional[str] = None, notes: Optional[str] = None,
              linked_run_id: Optional[str] = None,
              fees: float = 0.0) -> Dict[str, Any]:
    if action not in ALLOWED_TRADE_ACTIONS:
        raise ValueError(f"invalid action {action!r}; allowed: {ALLOWED_TRADE_ACTIONS}")
    now = _now()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO trade_journal(ticker, action, shares, price,
                                          executed_at, account, notes,
                                          linked_run_id, fees,
                                          created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker.strip().upper(), action, float(shares),
             float(price) if price is not None else None,
             executed_at, account, notes, linked_run_id, float(fees), now, now),
        )
        row = c.execute(
            "SELECT * FROM trade_journal WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def update_trade(trade_id: int, *,
                 action: Optional[str] = None,
                 shares: Optional[float] = None,
                 price: Optional[float] = None,
                 executed_at: Optional[str] = None,
                 account: Optional[str] = None,
                 notes: Optional[str] = None,
                 linked_run_id: Optional[str] = None,
                 fees: Optional[float] = None) -> Optional[Dict[str, Any]]:
    fields = []
    args: List[Any] = []
    if action is not None:
        if action not in ALLOWED_TRADE_ACTIONS:
            raise ValueError(f"invalid action {action!r}")
        fields.append("action=?"); args.append(action)
    if shares is not None:
        fields.append("shares=?"); args.append(float(shares))
    if price is not None:
        fields.append("price=?"); args.append(float(price))
    if executed_at is not None:
        fields.append("executed_at=?"); args.append(executed_at)
    if account is not None:
        fields.append("account=?"); args.append(account)
    if notes is not None:
        fields.append("notes=?"); args.append(notes)
    if linked_run_id is not None:
        fields.append("linked_run_id=?"); args.append(linked_run_id)
    if fees is not None:
        fields.append("fees=?"); args.append(float(fees))
    if not fields:
        return get_trade(trade_id)
    fields.append("updated_at=?"); args.append(_now())
    args.append(trade_id)
    with _conn() as c:
        c.execute(
            f"UPDATE trade_journal SET {', '.join(fields)} WHERE id=?",
            args,
        )
    return get_trade(trade_id)


def delete_trade(trade_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM trade_journal WHERE id=?", (trade_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# News alerts — scored news items, populated by the background poller
# ---------------------------------------------------------------------------

def list_news_alerts(*, ticker: Optional[str] = None,
                     status: Optional[str] = None,
                     impact: Optional[str] = None,
                     limit: int = 200) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM news_alerts WHERE 1=1"
    args: List[Any] = []
    if ticker:
        sql += " AND ticker = ?"
        args.append(ticker.strip().upper())
    if status:
        sql += " AND status = ?"
        args.append(status)
    if impact:
        sql += " AND impact = ?"
        args.append(impact)
    sql += " ORDER BY impact_score DESC, published_at DESC, id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def add_news_alert(*, ticker: str, headline: str,
                    url: Optional[str] = None,
                    published_at: Optional[str] = None,
                    source: Optional[str] = None,
                    impact: str = "low",
                    impact_score: int = 0,
                    keywords: Optional[str] = None,
                    hash_key: str) -> Optional[Dict[str, Any]]:
    """Insert one news alert. On hash collision, returns None (dedupe)."""
    with _conn() as c:
        try:
            cur = c.execute(
                """INSERT INTO news_alerts(ticker, headline, url, published_at,
                                            source, impact, impact_score,
                                            keywords, status, fetched_at, hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unread', ?, ?)""",
                (ticker.strip().upper(), headline, url, published_at, source,
                 impact, impact_score, keywords, _now(), hash_key),
            )
        except sqlite3.IntegrityError:
            return None  # duplicate hash
        row = c.execute(
            "SELECT * FROM news_alerts WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def update_news_alert_status(alert_id: int, status: str) -> bool:
    if status not in ("unread", "read", "dismissed"):
        raise ValueError(f"invalid status {status!r}")
    with _conn() as c:
        cur = c.execute(
            "UPDATE news_alerts SET status=? WHERE id=?", (status, alert_id),
        )
        return cur.rowcount > 0


def mark_all_news_alerts_read(*, ticker: Optional[str] = None) -> int:
    sql = "UPDATE news_alerts SET status='read' WHERE status='unread'"
    args: List[Any] = []
    if ticker:
        sql += " AND ticker = ?"
        args.append(ticker.strip().upper())
    with _conn() as c:
        cur = c.execute(sql, args)
        return cur.rowcount


def news_alerts_unread_count() -> Dict[str, int]:
    """Returns {ticker: count_of_unread_high_or_medium} for the dashboard
    notification badge."""
    with _conn() as c:
        rows = c.execute(
            """SELECT ticker, COUNT(*) as n FROM news_alerts
               WHERE status='unread' AND impact IN ('high', 'medium')
               GROUP BY ticker"""
        ).fetchall()
        return {r["ticker"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# Per-ticker schedules — the auto-run scheduler. Each row drives the
# background loop in service/scheduler.py; when due, the loop POSTs a
# queue item via storage.queue_request(). Composes with the existing
# Claude-Desktop drain cron — schedules push work in, drain cron pulls
# it out.
# ---------------------------------------------------------------------------

def list_schedules(*, enabled_only: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM ticker_schedules"
    if enabled_only:
        sql += " WHERE enabled=1"
    sql += " ORDER BY ticker, cron_expression"
    with _conn() as c:
        return [dict(r) for r in c.execute(sql).fetchall()]


def get_schedule(schedule_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM ticker_schedules WHERE id=?", (schedule_id,)
        ).fetchone()
        return dict(row) if row else None


def add_schedule(*, ticker: str, cron_expression: str, mode: str = "analyze",
                  options: Optional[Dict[str, Any]] = None,
                  enabled: bool = True, notes: Optional[str] = None) -> Dict[str, Any]:
    now = _now()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO ticker_schedules(ticker, cron_expression, mode,
                                            options_json, enabled, notes,
                                            created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker.strip().upper(), cron_expression.strip(), mode,
             json.dumps(options or {}), 1 if enabled else 0, notes, now, now),
        )
        row = c.execute(
            "SELECT * FROM ticker_schedules WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def update_schedule(schedule_id: int, *,
                     cron_expression: Optional[str] = None,
                     mode: Optional[str] = None,
                     options: Optional[Dict[str, Any]] = None,
                     enabled: Optional[bool] = None,
                     notes: Optional[str] = None) -> Optional[Dict[str, Any]]:
    fields = []
    args: List[Any] = []
    if cron_expression is not None:
        fields.append("cron_expression=?"); args.append(cron_expression.strip())
    if mode is not None:
        fields.append("mode=?"); args.append(mode)
    if options is not None:
        fields.append("options_json=?"); args.append(json.dumps(options))
    if enabled is not None:
        fields.append("enabled=?"); args.append(1 if enabled else 0)
    if notes is not None:
        fields.append("notes=?"); args.append(notes)
    if fields:
        fields.append("updated_at=?"); args.append(_now())
        args.append(schedule_id)
        with _conn() as c:
            c.execute(
                f"UPDATE ticker_schedules SET {', '.join(fields)} WHERE id=?",
                args,
            )
    return get_schedule(schedule_id)


def delete_schedule(schedule_id: int) -> bool:
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM ticker_schedules WHERE id=?", (schedule_id,)
        )
        return cur.rowcount > 0


def has_pending_for_schedule(schedule_id: int) -> bool:
    """True if there's an unconsumed run_queue row created by this schedule
    (status='pending'). Used by the scheduler to dedup repeated fires —
    if Claude Desktop has been offline and the same daily schedule fired
    yesterday with no drain since, we don't want to queue a second copy
    today."""
    with _conn() as c:
        row = c.execute(
            """SELECT id FROM run_queue
               WHERE requested_by = ? AND status = 'pending'
               LIMIT 1""",
            (f"scheduler:{schedule_id}",),
        ).fetchone()
        return row is not None


def record_schedule_fire(schedule_id: int, *,
                          queue_id: Optional[str] = None,
                          error: Optional[str] = None) -> None:
    """Mark a schedule as fired (success or failure)."""
    with _conn() as c:
        c.execute(
            """UPDATE ticker_schedules
               SET last_fired_at=?, last_queue_id=?, last_error=?, updated_at=?
               WHERE id=?""",
            (_now(), queue_id, error, _now(), schedule_id),
        )


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
    ``active_on``: YYYY-MM-DD — only return restrictions active on this
    date. Two restriction shapes are supported:
    - Fixed-window (legacy): active if start_date <= active_on <= end_date
      (open-ended end_date treated as indefinitely active).
    - Earnings-window: ``earnings_days_before`` + ``earnings_days_after``
      define a blackout window relative to the ticker's NEXT earnings
      date. Active if next_earnings_date − days_before <= active_on <=
      next_earnings_date + days_after.

    The earnings-window resolution happens in Python (not SQL) because it
    requires a per-ticker yfinance lookup. Cached for 15 minutes per
    ticker to avoid hammering the upstream.
    """
    sql = "SELECT * FROM trading_restrictions WHERE 1=1"
    args: List[Any] = []
    if ticker:
        sql += " AND ticker = ?"
        args.append(ticker.strip().upper())
    sql += " ORDER BY ticker, start_date DESC"
    with _conn() as c:
        rows = [dict(r) for r in c.execute(sql, args).fetchall()]

    if not active_on:
        return rows

    # Filter to active-on-date. Apply the two evaluation rules.
    from datetime import date as _date, datetime as _dt, timedelta as _td
    try:
        ao = _dt.fromisoformat(active_on).date()
    except ValueError:
        return rows

    out: List[Dict[str, Any]] = []
    for r in rows:
        kind = (r.get("kind") or "").lower()
        edb = r.get("earnings_days_before")
        eda = r.get("earnings_days_after")
        wo = r.get("earnings_window_open_offset_days")
        wd = r.get("earnings_window_duration_days")

        # ── earnings_window: the user defines when trading is OPEN.
        #    Restricted when active_on is OUTSIDE the open window.
        if kind == "earnings_window" or wo is not None or wd is not None:
            try:
                ne = _next_earnings_date(r["ticker"])
            except Exception:
                ne = None
            if ne is None:
                # Without an earnings date we conservatively report
                # "currently restricted" so the UI doesn't show a stale
                # OPEN badge — the user can investigate.
                annotated = {**r, "_resolved_start": None,
                             "_resolved_end": None,
                             "_resolved_earnings_date": None,
                             "_currently_open": False}
                out.append(annotated)
                continue
            offset = int(wo or 0)
            duration = int(wd or 0)
            open_start = ne + _td(days=offset)
            open_end = open_start + _td(days=duration)
            # Active (restricted) when active_on is OUTSIDE the open window.
            currently_open = open_start <= ao <= open_end
            if not currently_open:
                annotated = {**r, "_resolved_start": open_start.isoformat(),
                             "_resolved_end": open_end.isoformat(),
                             "_resolved_earnings_date": ne.isoformat(),
                             "_currently_open": False}
                out.append(annotated)
            # When currently_open, we INTENTIONALLY don't include the row
            # in the "active restrictions" output — trading IS allowed.

        # ── earnings_blackout: the user defines when trading is CLOSED.
        #    Restricted when active_on is INSIDE the blackout window.
        elif edb is not None or eda is not None:
            try:
                ne = _next_earnings_date(r["ticker"])
            except Exception:
                ne = None
            if ne is None:
                continue
            days_before = int(edb or 0)
            days_after = int(eda or 0)
            window_start = ne - _td(days=days_before)
            window_end = ne + _td(days=days_after)
            if window_start <= ao <= window_end:
                annotated = {**r, "_resolved_start": window_start.isoformat(),
                             "_resolved_end": window_end.isoformat(),
                             "_resolved_earnings_date": ne.isoformat(),
                             "_currently_open": False}
                out.append(annotated)

        # ── Fixed-window restriction (legacy / hand-set dates).
        else:
            start = r.get("start_date")
            end = r.get("end_date")
            if not start:
                continue
            try:
                sd = _dt.fromisoformat(start).date()
            except ValueError:
                continue
            if sd > ao:
                continue
            if end:
                try:
                    ed = _dt.fromisoformat(end).date()
                    if ed < ao:
                        continue
                except ValueError:
                    pass
            out.append(r)
    return out


# Earnings date cache: ticker → (date, fetched_at). 15-minute TTL.
_EARNINGS_DATE_CACHE: Dict[str, tuple] = {}
_EARNINGS_CACHE_TTL_SEC = 900


def _next_earnings_date(ticker: str):
    """Resolve the next earnings date for a ticker via yfinance, with a
    short in-memory cache.

    Returns a ``date`` or ``None`` if not available.

    Cache behaviour: hits live for 15 minutes; MISSES (None results)
    live for only 60 seconds. Without that distinction, one transient
    yfinance error stamps None into the cache for 15 min and locks out
    later calls — exactly the symptom that hid the next-earnings date
    on the AAPL row.
    """
    from datetime import date as _date, datetime as _dt
    import time
    t = (ticker or "").upper()
    now_ts = time.time()
    cached = _EARNINGS_DATE_CACHE.get(t)
    if cached:
        cached_value, cached_ts = cached
        ttl = _EARNINGS_CACHE_TTL_SEC if cached_value is not None else 60
        if now_ts - cached_ts < ttl:
            return cached_value
    try:
        import yfinance as yf
        cal = yf.Ticker(t).calendar
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date") or cal.get("earningsDate")
            if isinstance(ed, list) and ed:
                ed = ed[0]
            # yfinance returns bare `date` objects from .calendar, but
            # `datetime` from other endpoints. date objects do NOT have a
            # .date() method — checking isinstance(_date) first prevents
            # the silent-None bug.
            if isinstance(ed, _date) and not hasattr(ed, "time"):
                # `date` (no time component) — pass through
                resolved = ed
            elif hasattr(ed, "date") and callable(getattr(ed, "date")):
                # datetime — strip to date
                resolved = ed.date()
            elif isinstance(ed, str):
                resolved = _dt.fromisoformat(ed[:10]).date()
            else:
                resolved = None
        else:
            resolved = None
        _EARNINGS_DATE_CACHE[t] = (resolved, now_ts)
        return resolved
    except Exception:
        _EARNINGS_DATE_CACHE[t] = (None, now_ts)
        return None


def add_restriction(*, ticker: str, start_date: str,
                     end_date: Optional[str] = None,
                     kind: str = "blackout",
                     reason: Optional[str] = None,
                     earnings_days_before: Optional[int] = None,
                     earnings_days_after: Optional[int] = None,
                     earnings_window_open_offset_days: Optional[int] = None,
                     earnings_window_duration_days: Optional[int] = None,
                     ) -> Dict[str, Any]:
    now = _now()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO trading_restrictions(ticker, start_date, end_date,
                                                kind, reason,
                                                earnings_days_before,
                                                earnings_days_after,
                                                earnings_window_open_offset_days,
                                                earnings_window_duration_days,
                                                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker.strip().upper(), start_date, end_date, kind, reason,
             earnings_days_before, earnings_days_after,
             earnings_window_open_offset_days, earnings_window_duration_days,
             now, now),
        )
        row = c.execute(
            "SELECT * FROM trading_restrictions WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def update_restriction(restriction_id: int, *,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None,
                        kind: Optional[str] = None,
                        reason: Optional[str] = None,
                        earnings_days_before: Optional[int] = None,
                        earnings_days_after: Optional[int] = None,
                        earnings_window_open_offset_days: Optional[int] = None,
                        earnings_window_duration_days: Optional[int] = None,
                        ) -> Optional[Dict[str, Any]]:
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
    if earnings_days_before is not None:
        fields.append("earnings_days_before=?"); args.append(earnings_days_before)
    if earnings_days_after is not None:
        fields.append("earnings_days_after=?"); args.append(earnings_days_after)
    if earnings_window_open_offset_days is not None:
        fields.append("earnings_window_open_offset_days=?")
        args.append(earnings_window_open_offset_days)
    if earnings_window_duration_days is not None:
        fields.append("earnings_window_duration_days=?")
        args.append(earnings_window_duration_days)
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


# ---------------------------------------------------------------------------
# 13F institutional holdings — smart-money managers + their positions
# ---------------------------------------------------------------------------

def list_smart_money_managers(*, enabled_only: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM smart_money_managers"
    if enabled_only:
        sql += " WHERE enabled=1"
    sql += " ORDER BY name"
    with _conn() as c:
        return [dict(r) for r in c.execute(sql).fetchall()]


def upsert_smart_money_manager(*, cik: str, name: str,
                                enabled: bool = True) -> Dict[str, Any]:
    """Insert a new manager or update the name/enabled flag on an existing
    row. The CIK is the primary key — passing an existing one
    overwrites name + enabled without touching the latest filing pointers."""
    now = _now()
    cik_p = str(cik).lstrip("0").zfill(10)
    with _conn() as c:
        c.execute(
            """INSERT INTO smart_money_managers(cik, name, enabled, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(cik) DO UPDATE SET
                 name=excluded.name, enabled=excluded.enabled""",
            (cik_p, name, 1 if enabled else 0, now),
        )
        row = c.execute(
            "SELECT * FROM smart_money_managers WHERE cik=?", (cik_p,)
        ).fetchone()
        return dict(row)


def set_smart_money_manager_enabled(cik: str, enabled: bool) -> bool:
    cik_p = str(cik).lstrip("0").zfill(10)
    with _conn() as c:
        cur = c.execute(
            "UPDATE smart_money_managers SET enabled=? WHERE cik=?",
            (1 if enabled else 0, cik_p),
        )
        return cur.rowcount > 0


def update_manager_after_fetch(*, cik: str, filing_date: Optional[str],
                                report_date: Optional[str],
                                accession_no: Optional[str],
                                total_value: Optional[float],
                                position_count: Optional[int],
                                error: Optional[str] = None) -> None:
    """Record the result of a poll cycle. Called by the 13F poller after
    each manager's filing is fetched (or after a failure). ``error`` is
    set when fetch/parse fails; the other fields are cleared in that
    case so the next poll retries cleanly."""
    cik_p = str(cik).lstrip("0").zfill(10)
    now = _now()
    with _conn() as c:
        c.execute(
            """UPDATE smart_money_managers
               SET last_refreshed_at=?, last_filing_date=COALESCE(?, last_filing_date),
                   last_report_date=COALESCE(?, last_report_date),
                   last_accession_no=COALESCE(?, last_accession_no),
                   total_value=COALESCE(?, total_value),
                   position_count=COALESCE(?, position_count),
                   last_error=?
               WHERE cik=?""",
            (now, filing_date, report_date, accession_no,
             total_value, position_count, error, cik_p),
        )


def list_holdings_by_ticker(ticker: str, *, limit: int = 100) -> List[Dict[str, Any]]:
    """All smart-money positions in a single ticker.

    Joins to smart_money_managers so the UI gets the manager name +
    AUM context without a second query. Only returns the LATEST
    filing per manager — historical snapshots are still in the table
    but the per-ticker view is intentionally "who holds this NOW".
    """
    sql = """
        SELECT h.*, m.name as manager_name_current,
               m.total_value as manager_total_value
        FROM holdings_13f h
        JOIN smart_money_managers m ON m.cik = h.manager_cik
        WHERE h.ticker = ?
          AND h.accession_no = m.last_accession_no
          AND m.enabled = 1
        ORDER BY h.value DESC
        LIMIT ?
    """
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, (ticker.strip().upper(), limit)).fetchall()]


def list_holdings_by_manager(cik: str, *, accession_no: Optional[str] = None,
                              limit: int = 200) -> List[Dict[str, Any]]:
    """All positions in a manager's latest filing (or a specific one)."""
    cik_p = str(cik).lstrip("0").zfill(10)
    if accession_no:
        sql = """SELECT * FROM holdings_13f
                 WHERE manager_cik=? AND accession_no=?
                 ORDER BY value DESC LIMIT ?"""
        args: tuple = (cik_p, accession_no, limit)
    else:
        # Latest accession for this manager.
        sql = """SELECT h.* FROM holdings_13f h
                 JOIN smart_money_managers m ON m.cik = h.manager_cik
                 WHERE h.manager_cik=? AND h.accession_no = m.last_accession_no
                 ORDER BY h.value DESC LIMIT ?"""
        args = (cik_p, limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def prior_filing_for_manager(cik: str,
                              before_accession: str) -> Optional[Dict[str, Any]]:
    """Return any one row from the manager's filing BEFORE ``before_accession``
    so the poller can pull prev_shares for QoQ comparison. Picks the row
    with the most recent report_date strictly older than the current one."""
    cik_p = str(cik).lstrip("0").zfill(10)
    with _conn() as c:
        # Look up the report_date of the current filing first.
        cur_row = c.execute(
            """SELECT report_date FROM holdings_13f
               WHERE manager_cik=? AND accession_no=? LIMIT 1""",
            (cik_p, before_accession),
        ).fetchone()
        if not cur_row:
            return None
        cur_report = cur_row["report_date"]
        prior = c.execute(
            """SELECT accession_no, report_date FROM holdings_13f
               WHERE manager_cik=? AND report_date < ?
               ORDER BY report_date DESC LIMIT 1""",
            (cik_p, cur_report),
        ).fetchone()
        return dict(prior) if prior else None


def get_prev_position_shares(cik: str, accession_no: str,
                              cusip: str) -> Optional[int]:
    """How many shares of CUSIP did this manager hold in the named filing?"""
    cik_p = str(cik).lstrip("0").zfill(10)
    with _conn() as c:
        row = c.execute(
            """SELECT shares FROM holdings_13f
               WHERE manager_cik=? AND accession_no=? AND cusip=? LIMIT 1""",
            (cik_p, accession_no, cusip.upper()),
        ).fetchone()
        return int(row["shares"]) if row else None


def insert_holdings_snapshot(*, manager_cik: str, manager_name: str,
                              accession_no: str, filing_date: str,
                              report_date: str,
                              holdings: List[Dict[str, Any]]) -> int:
    """Bulk insert one filing's worth of holdings. Computes prev_shares
    and qoq_change_pct against the prior filing (if any) per row.
    Returns the count of rows inserted (rows that conflict on
    (manager_cik, accession_no, cusip) are skipped silently)."""
    cik_p = str(manager_cik).lstrip("0").zfill(10)
    now = _now()
    inserted = 0
    prior = prior_filing_for_manager(cik_p, accession_no)
    prior_acc = prior["accession_no"] if prior else None

    # Compute total AUM first so we can store pct_of_manager_aum per row.
    total_aum = float(sum(h.get("value") or 0 for h in holdings)) or 1.0

    with _conn() as c:
        for h in holdings:
            cusip = (h.get("cusip") or "").upper()
            if not cusip:
                continue
            value = float(h.get("value") or 0)
            shares = int(h.get("shares") or 0)
            pct = (value / total_aum * 100.0) if total_aum else None

            prev_shares: Optional[int] = None
            qoq: Optional[float] = None
            if prior_acc:
                prev = c.execute(
                    """SELECT shares FROM holdings_13f
                       WHERE manager_cik=? AND accession_no=? AND cusip=? LIMIT 1""",
                    (cik_p, prior_acc, cusip),
                ).fetchone()
                if prev:
                    prev_shares = int(prev["shares"])
                    if prev_shares > 0:
                        qoq = (shares - prev_shares) / prev_shares * 100.0

            try:
                c.execute(
                    """INSERT OR IGNORE INTO holdings_13f
                       (manager_cik, manager_name, accession_no, filing_date,
                        report_date, cusip, ticker, name_of_issuer,
                        title_of_class, shares, value, put_call,
                        prev_shares, qoq_change_pct, pct_of_manager_aum,
                        created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (cik_p, manager_name, accession_no, filing_date, report_date,
                     cusip, h.get("ticker"), h.get("name_of_issuer"),
                     h.get("title_of_class"), shares, value, h.get("put_call"),
                     prev_shares,
                     round(qoq, 2) if qoq is not None else None,
                     round(pct, 4) if pct is not None else None,
                     now),
                )
                inserted += c.total_changes  # not exact but reasonable signal
            except Exception:
                continue

    # Tighten "inserted" — re-count by querying back.
    with _conn() as c:
        cnt = c.execute(
            """SELECT COUNT(*) FROM holdings_13f
               WHERE manager_cik=? AND accession_no=?""",
            (cik_p, accession_no),
        ).fetchone()[0]
    return int(cnt)


def smart_money_summary_for_ticker(ticker: str) -> Dict[str, Any]:
    """One-row summary card for "smart money owning $TICKER".

    Returns: total managers holding, combined value, top 3 by value,
    newest-quarter QoQ aggregate change (new buys + adds vs trims/exits).
    Useful for a dashboard widget."""
    rows = list_holdings_by_ticker(ticker, limit=200)
    if not rows:
        return {
            "ticker": ticker.upper(), "manager_count": 0,
            "total_value": 0, "total_shares": 0,
            "top_managers": [], "new_buys": 0, "large_trims": 0,
            "net_share_change_pct": None,
        }
    total_value = sum(r.get("value") or 0 for r in rows)
    total_shares = sum(r.get("shares") or 0 for r in rows)
    new_buys = sum(1 for r in rows if r.get("prev_shares") is None)
    # An "exit" is when prev_shares > 0 but shares == 0 — but our list
    # already filters to LATEST filings which contain the current
    # position. True exits aren't in this table. Approximate with
    # "shares dropped 50%+" since this snapshot.
    big_trims = sum(
        1 for r in rows
        if (r.get("qoq_change_pct") is not None and r["qoq_change_pct"] <= -50)
    )
    # Weighted share-change: sum of (current - prev) weighted by value.
    deltas = [
        ((r.get("shares") or 0) - (r.get("prev_shares") or 0)) for r in rows
        if r.get("prev_shares") is not None and r.get("prev_shares", 0) > 0
    ]
    base = [r.get("prev_shares") or 0 for r in rows if r.get("prev_shares") is not None and r.get("prev_shares", 0) > 0]
    net_pct = (sum(deltas) / sum(base) * 100.0) if base else None

    top = sorted(rows, key=lambda r: -(r.get("value") or 0))[:3]
    return {
        "ticker": ticker.upper(),
        "manager_count": len(rows),
        "total_value": round(total_value, 2),
        "total_shares": total_shares,
        "top_managers": [
            {"name": r.get("manager_name"), "value": round(r.get("value") or 0, 2),
             "shares": r.get("shares"), "qoq_change_pct": r.get("qoq_change_pct")}
            for r in top
        ],
        "new_buys": new_buys,
        "large_trims": big_trims,
        "net_share_change_pct": round(net_pct, 2) if net_pct is not None else None,
    }


# ---------------------------------------------------------------------------
# Trade-journal lookups for backtest "actual vs notional" comparison
# ---------------------------------------------------------------------------

def trades_for_run(run_id: str) -> List[Dict[str, Any]]:
    """Every trade the user has linked to this recommendation run via
    ``linked_run_id``. Ordered chronologically so the backtest can walk
    them as a FIFO cost-basis layer."""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM trade_journal
               WHERE linked_run_id=?
               ORDER BY executed_at ASC, id ASC""",
            (run_id,),
        ).fetchall()]


def trades_for_ticker_in_window(ticker: str, *, start_date: str,
                                 end_date: str) -> List[Dict[str, Any]]:
    """All trades for ``ticker`` with executed_at in [start, end].
    Used when a run isn't directly linked but you want to attribute
    trades that happened in the run's window."""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM trade_journal
               WHERE ticker=? AND executed_at >= ? AND executed_at <= ?
               ORDER BY executed_at ASC, id ASC""",
            (ticker.strip().upper(), start_date, end_date),
        ).fetchall()]


# ---------------------------------------------------------------------------
# Earnings summaries — plain-English digests of each quarter's press release.
# Populated by Claude Desktop via the run_queue (mode='earnings_summary').
# ---------------------------------------------------------------------------

def get_earnings_summary(ticker: str, report_date: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute(
            """SELECT * FROM earnings_summaries
               WHERE ticker=? AND report_date=? LIMIT 1""",
            (ticker.strip().upper(), report_date),
        ).fetchone()
        return dict(row) if row else None


def latest_earnings_summary(ticker: str) -> Optional[Dict[str, Any]]:
    """Most recent summary on file for this ticker, regardless of status."""
    with _conn() as c:
        row = c.execute(
            """SELECT * FROM earnings_summaries
               WHERE ticker=? ORDER BY report_date DESC LIMIT 1""",
            (ticker.strip().upper(),),
        ).fetchone()
        return dict(row) if row else None


def upsert_earnings_summary(*, ticker: str, report_date: str,
                              bullets_md: Optional[str] = None,
                              structured_json: Optional[str] = None,
                              source: str = "claude-desktop",
                              status: str = "complete") -> Dict[str, Any]:
    """Insert or replace a summary row keyed by (ticker, report_date).
    Called by the /summary POST endpoint when Claude Desktop pushes
    back a digest, or by /queue-summary to seed a 'pending' row."""
    now = _now()
    t = ticker.strip().upper()
    with _conn() as c:
        existing = c.execute(
            """SELECT id FROM earnings_summaries
               WHERE ticker=? AND report_date=? LIMIT 1""",
            (t, report_date),
        ).fetchone()
        if existing:
            c.execute(
                """UPDATE earnings_summaries SET
                       bullets_md=COALESCE(?, bullets_md),
                       structured_json=COALESCE(?, structured_json),
                       source=?, status=?, updated_at=?
                   WHERE id=?""",
                (bullets_md, structured_json, source, status, now, existing["id"]),
            )
            rid = existing["id"]
        else:
            cur = c.execute(
                """INSERT INTO earnings_summaries(ticker, report_date,
                                                   bullets_md, structured_json,
                                                   source, status,
                                                   created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (t, report_date, bullets_md, structured_json, source, status,
                 now, now),
            )
            rid = cur.lastrowid
        row = c.execute(
            "SELECT * FROM earnings_summaries WHERE id=?", (rid,)
        ).fetchone()
        return dict(row)


def list_earnings_summaries(ticker: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM earnings_summaries
               WHERE ticker=?
               ORDER BY report_date DESC
               LIMIT ?""",
            (ticker.strip().upper(), limit),
        ).fetchall()]


# ---------------------------------------------------------------------------
# Portfolio Q&A — free-form questions about positions / runs / trades.
# Two execution modes: queue (Claude Desktop, free, async) or
# sync (Anthropic API, instant, costs tokens).
# ---------------------------------------------------------------------------

def new_conversation_id() -> str:
    return uuid.uuid4().hex[:16]


def insert_question(*, conversation_id: str, question: str,
                     mode: str = "queue",
                     context_snapshot_json: Optional[str] = None) -> Dict[str, Any]:
    """Insert a pending question row. Status='pending' until answered.
    Caller is expected to follow up with attach_queue_id() (for queue
    mode) or complete_question() (for sync mode)."""
    now = _now()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO portfolio_questions(conversation_id, question,
                                                context_snapshot_json, mode,
                                                status, requested_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (conversation_id, question, context_snapshot_json, mode, now),
        )
        row = c.execute(
            "SELECT * FROM portfolio_questions WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def attach_queue_id(question_id: int, queue_id: str) -> None:
    """Link a queue-mode question to its run_queue row."""
    with _conn() as c:
        c.execute(
            "UPDATE portfolio_questions SET queue_id=? WHERE id=?",
            (queue_id, question_id),
        )


def complete_question(question_id: int, *, answer_md: str,
                       source: str = "claude-desktop",
                       tokens_in: Optional[int] = None,
                       tokens_out: Optional[int] = None) -> Dict[str, Any]:
    """Mark a question complete with its answer."""
    now = _now()
    with _conn() as c:
        c.execute(
            """UPDATE portfolio_questions
               SET answer_md=?, status='complete', source=?, answered_at=?,
                   tokens_in=COALESCE(?, tokens_in),
                   tokens_out=COALESCE(?, tokens_out)
               WHERE id=?""",
            (answer_md, source, now, tokens_in, tokens_out, question_id),
        )
        row = c.execute(
            "SELECT * FROM portfolio_questions WHERE id=?", (question_id,)
        ).fetchone()
        return dict(row) if row else None


def fail_question(question_id: int, error: str) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE portfolio_questions
               SET status='error', error_message=?, answered_at=?
               WHERE id=?""",
            (error[:1000], _now(), question_id),
        )


def get_question(question_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM portfolio_questions WHERE id=?", (question_id,)
        ).fetchone()
        return dict(row) if row else None


def get_question_by_queue_id(queue_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM portfolio_questions WHERE queue_id=? LIMIT 1",
            (queue_id,),
        ).fetchone()
        return dict(row) if row else None


def list_conversation(conversation_id: str) -> List[Dict[str, Any]]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM portfolio_questions
               WHERE conversation_id=? ORDER BY id ASC""",
            (conversation_id,),
        ).fetchall()]


def list_conversations(*, limit: int = 50) -> List[Dict[str, Any]]:
    """Aggregate by conversation_id, returning the latest question + count
    per thread. Used for the /ask sidebar history view."""
    with _conn() as c:
        rows = c.execute(
            """SELECT conversation_id,
                      MIN(requested_at) AS started_at,
                      MAX(requested_at) AS last_question_at,
                      COUNT(*) AS turn_count,
                      MAX(id) AS last_question_id
               FROM portfolio_questions
               GROUP BY conversation_id
               ORDER BY MAX(id) DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # Attach the first-question text as a "title preview"
            title_row = c.execute(
                """SELECT question FROM portfolio_questions
                   WHERE conversation_id=? ORDER BY id ASC LIMIT 1""",
                (d["conversation_id"],),
            ).fetchone()
            d["preview"] = (title_row["question"][:120] if title_row else "") + (
                "…" if title_row and len(title_row["question"]) > 120 else ""
            )
            out.append(d)
        return out
