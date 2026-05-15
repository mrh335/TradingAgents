"""Re-extract decisions for runs that hit the old parse_rating bug.

Older parse_rating only recognised "Rating: X" labels and the 5-tier
vocab words verbatim. Ollama runs that produced essay-style PM output
without an explicit "Rating:" header fell through to the default
``Hold`` regardless of what the analysis actually concluded.

This script walks every archive under ``~/.tradingagents/logs/<TICKER>/
TradingAgentsStrategy_logs/runs/`` and re-runs the (now smarter) parser
against the combined PM + trader text, comparing the result to the
``decision`` column in the SQLite ``runs`` table. By default it prints
what would change; pass ``--apply`` to write the new decisions back.

    python scripts/reextract_decisions.py             # dry run
    python scripts/reextract_decisions.py --apply     # write to DB

The on-disk archive files are NOT modified — only the SQLite row.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from gui import storage
from gui.log_browser import discover_logs
from tradingagents.agents.utils.rating import parse_rating


def _load_archive(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and data.get("kind") == "tradingagents-gui-archive":
        return data.get("state") or {}
    return data if isinstance(data, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write new decisions back to SQLite (default: dry run)")
    parser.add_argument("--only-hold", action="store_true",
                        help="Only consider rows currently labelled 'Hold'")
    args = parser.parse_args()

    # Build lookup: archive run_id -> file path (use the most recent archive
    # per run_id if there are multiple).
    entries = discover_logs()
    by_run_id: dict[str, str] = {}
    for e in entries:
        rid = e.get("run_id")
        if rid:
            by_run_id[rid] = e["log_path"]

    rows = storage.list_runs(limit=100_000)
    if args.only_hold:
        rows = [r for r in rows if (r.get("decision") or "").lower() == "hold"]

    print(f"Considering {len(rows)} run(s)…")
    updates: list[tuple[str, str, str, str]] = []

    for r in rows:
        rid = r["run_id"]
        archive_path = r.get("log_path") or by_run_id.get(rid)
        if not archive_path or not Path(archive_path).exists():
            continue
        state = _load_archive(archive_path)
        if not state:
            continue
        pm = state.get("final_trade_decision") or ""
        trader = (state.get("trader_investment_decision")
                  or state.get("trader_investment_plan")
                  or "")
        combined = (pm + "\n\n" + trader).strip() if trader else pm
        new = parse_rating(combined)
        old = r.get("decision") or ""
        if new and new != old:
            updates.append((rid, r["ticker"], old, new))

    if not updates:
        print("Nothing would change.")
        return 0

    print(f"\n{len(updates)} run(s) would change:")
    print(f"  {'run_id (8)':<10}  {'ticker':<8}  {'old':<14}  -> new")
    for rid, ticker, old, new in updates:
        print(f"  {rid[:8]}  {ticker:<8}  {old:<14}  -> {new}")

    if not args.apply:
        print("\nDry run — pass --apply to write these back to SQLite.")
        return 0

    with sqlite3.connect(storage.DB_PATH) as c:
        for rid, _, _, new in updates:
            c.execute("UPDATE runs SET decision=? WHERE run_id=?", (new, rid))
        c.commit()
    print(f"\nApplied {len(updates)} update(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
