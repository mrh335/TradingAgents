"""Regression tests for the SQLite storage layer (gui/storage.py).

These guard the highest-risk part of the webapp:

* the **idempotent migrations** in ``init_db()`` — a bad ``ALTER`` would
  corrupt the production DB on the next deploy, and the lazy column-adds
  must survive being run on every startup;
* the **core run lifecycle CRUD** the whole app indexes against.

``storage.py`` imports only the stdlib, so these run anywhere pytest does
— no API keys, no LLM deps, no network.
"""

import pytest

from gui import storage


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Point storage at a throwaway DB file and initialise it."""
    db = tmp_path / "gui.db"
    monkeypatch.setattr(storage, "DB_PATH", db)
    storage.init_db()
    return db


def _columns(table: str) -> set[str]:
    with storage._conn() as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}


def test_init_db_is_idempotent(tmp_path, monkeypatch):
    """Running init repeatedly (every startup does) must never raise."""
    db = tmp_path / "gui.db"
    monkeypatch.setattr(storage, "DB_PATH", db)
    storage.init_db()
    storage.init_db()
    storage.init_db()
    assert db.exists()


def test_core_tables_exist(fresh_db):
    with storage._conn() as c:
        names = {
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    for t in (
        "runs", "notes", "chat_messages", "watchlist",
        "positions", "paper_positions",
    ):
        assert t in names, f"missing table {t}"


def test_lazy_migration_columns_present(fresh_db):
    """Guard the guarded ALTER TABLE column-adds (batch + comparison)."""
    cols = _columns("runs")
    assert "batch_id" in cols
    assert "comparison_id" in cols


def test_wal_mode_enabled(fresh_db):
    """_conn() must put the DB in WAL mode so concurrent POSTs don't 'lock'."""
    with storage._conn() as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_run_lifecycle_roundtrip(fresh_db):
    run_id = storage.new_run_id()
    storage.create_run(
        run_id=run_id, ticker="NVDA", trade_date="2026-05-28",
        provider="anthropic", deep_model="claude", quick_model="claude",
        debate_rounds=1, risk_rounds=1, vendors={"news": "yfinance"},
    )
    row = storage.get_run(run_id)
    assert row is not None
    assert row["ticker"] == "NVDA"
    assert row["status"] == "running"
    assert row["decision"] is None

    storage.update_run_stats(
        run_id, llm_calls=3, tool_calls=5, tokens_in=100, tokens_out=200
    )
    storage.finalize_run(run_id, decision="Buy", log_path="/x/y.json")

    row = storage.get_run(run_id)
    assert row["status"] == "done"
    assert row["decision"] == "Buy"
    assert row["llm_calls"] == 3
    assert row["tokens_out"] == 200
    assert row["completed_at"] is not None


def test_list_runs_filters_by_ticker_and_limit(fresh_db):
    for tk in ("NVDA", "AAPL", "NVDA"):
        storage.create_run(
            run_id=storage.new_run_id(), ticker=tk, trade_date="2026-05-28",
            provider="p", deep_model="d", quick_model="q",
            debate_rounds=1, risk_rounds=1, vendors={},
        )
    nvda = storage.list_runs(ticker="NVDA")
    assert len(nvda) == 2
    assert all(r["ticker"] == "NVDA" for r in nvda)
    assert len(storage.list_runs(limit=1)) == 1
    assert len(storage.list_runs()) == 3


def test_get_run_missing_returns_none(fresh_db):
    assert storage.get_run("does-not-exist") is None


def test_finalize_run_with_error_sets_error_status(fresh_db):
    run_id = storage.new_run_id()
    storage.create_run(
        run_id=run_id, ticker="TSLA", trade_date="2026-05-28",
        provider="p", deep_model="d", quick_model="q",
        debate_rounds=1, risk_rounds=1, vendors={},
    )
    storage.finalize_run(run_id, decision=None, log_path=None, error="boom")
    row = storage.get_run(run_id)
    assert row["status"] == "error"
    assert row["error_message"] == "boom"
