"""build_archive.py — assemble the archive envelope JSON.

Takes the per-phase reports Claude produced in this session, plus the
run metadata, and writes a single archive envelope JSON in the schema_version 1
shape (matches gui/log_browser.py:106-111 in the TradingAgents repo).

Two invocation modes:

**Mode A — single-JSON input (preferred):**
    python build_archive.py --state state.json --metadata metadata.json
    [--tool-trace tool_trace.json] [--output out.json]

`state.json` is a JSON object with at minimum these keys (see
`schemas/archive.schema.json`):
    market_report, sentiment_report, news_report, fundamentals_report,
    investment_debate_state, investment_plan, trader_investment_plan,
    risk_debate_state, final_trade_decision

`metadata.json` is a JSON object with at minimum:
    run_id, ticker, trade_date, started_at, completed_at,
    provider, deep_model, quick_model, debate_rounds, risk_rounds

**Mode B — per-field args (fallback for inline use):**
    python build_archive.py --run-id … --ticker NVDA --trade-date 2026-05-15
        --started-at … --completed-at … --market-report-file mr.md
        --sentiment-report-file sr.md  …  --output out.json

Either way, the script:
1. Composes the archive envelope.
2. Validates against `schemas/archive.schema.json`.
3. Writes to the output path (temp file if not specified).
4. Prints the output path to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SCHEMA_PATH = SKILL_DIR / "schemas" / "archive.schema.json"


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _read_text_arg(value: str | None) -> str:
    """If value looks like a path that exists, read it; otherwise return as-is."""
    if value is None:
        return ""
    p = Path(value)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return value


def _ensure_state_shape(state: dict, metadata: dict) -> dict:
    """Backfill any missing state keys with empty strings / default dicts so
    the archive validates and the existing webapp can read it without
    crashing on missing keys."""
    state = dict(state)
    for k in ("market_report", "sentiment_report", "news_report",
              "fundamentals_report", "investment_plan",
              "trader_investment_plan", "final_trade_decision"):
        state.setdefault(k, "")

    debate_defaults = {
        "bull_history": "", "bear_history": "", "history": "",
        "current_response": "", "judge_decision": "", "count": 0,
    }
    state["investment_debate_state"] = {
        **debate_defaults,
        **(state.get("investment_debate_state") or {}),
    }

    risk_defaults = {
        "aggressive_history": "", "conservative_history": "", "neutral_history": "",
        "history": "", "latest_speaker": "",
        "current_aggressive_response": "", "current_conservative_response": "",
        "current_neutral_response": "", "judge_decision": "", "count": 0,
    }
    state["risk_debate_state"] = {
        **risk_defaults,
        **(state.get("risk_debate_state") or {}),
    }
    return state


def _validate(envelope: dict) -> None:
    try:
        import jsonschema
    except ImportError:
        _eprint("WARN: jsonschema not installed — skipping validation")
        return
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(envelope, schema)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--state", help="Path to JSON file with the state object")
    p.add_argument("--metadata", help="Path to JSON file with the metadata object")
    p.add_argument("--tool-trace", help="Optional path to JSON file with tool_trace array")

    # Fallback mode (per-field args)
    p.add_argument("--run-id")
    p.add_argument("--ticker")
    p.add_argument("--trade-date")
    p.add_argument("--started-at")
    p.add_argument("--completed-at")
    p.add_argument("--provider", default="claude-desktop-skill")
    p.add_argument("--deep-model", default="claude-opus-4-7")
    p.add_argument("--quick-model", default="claude-opus-4-7")
    p.add_argument("--debate-rounds", type=int, default=2)
    p.add_argument("--risk-rounds", type=int, default=1)
    p.add_argument("--parent-run-id", default=None,
                   help="If this is an update of a prior run, the parent's run_id. "
                        "Stored as metadata.parent_run_id so the chain can be "
                        "reconstructed.")
    p.add_argument("--ensemble-index", type=int, default=None,
                   help="When part of an ensemble, 0..N-1. Stored as "
                        "metadata.ensemble_index.")
    p.add_argument("--ensemble-size", type=int, default=None,
                   help="Total runs in the ensemble (default 1).")
    p.add_argument("--batch-id", default=None,
                   help="Batch grouping ID (multi-ticker / ensemble runs share this).")
    for key in ("market-report", "sentiment-report", "news-report",
                "fundamentals-report", "investment-plan",
                "trader-investment-plan", "final-trade-decision",
                "bull-history", "bear-history", "debate-history",
                "aggressive-history", "conservative-history",
                "neutral-history", "risk-history"):
        p.add_argument(f"--{key}-file", help=f"Path or inline text for {key}")

    p.add_argument("--output", "-o", help="Output JSON path (default: temp file)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # Resolve metadata
    if args.metadata:
        metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
    else:
        metadata = {
            "run_id": args.run_id,
            "ticker": args.ticker,
            "trade_date": args.trade_date,
            "started_at": args.started_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "completed_at": args.completed_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": args.provider,
            "deep_model": args.deep_model,
            "quick_model": args.quick_model,
            "debate_rounds": args.debate_rounds,
            "risk_rounds": args.risk_rounds,
        }

    # Optional augmentations (apply whether metadata came from --metadata or CLI flags)
    if args.parent_run_id and not metadata.get("parent_run_id"):
        metadata["parent_run_id"] = args.parent_run_id
    if args.ensemble_index is not None and "ensemble_index" not in metadata:
        metadata["ensemble_index"] = args.ensemble_index
    if args.ensemble_size is not None and "ensemble_size" not in metadata:
        metadata["ensemble_size"] = args.ensemble_size
    if args.batch_id and not metadata.get("batch_id"):
        metadata["batch_id"] = args.batch_id

    required_meta = ["run_id", "ticker", "trade_date", "started_at", "completed_at"]
    missing = [k for k in required_meta if not metadata.get(k)]
    if missing:
        _eprint(f"ERROR: metadata missing required keys: {missing}")
        return 2

    # Resolve state
    if args.state:
        state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    else:
        state = {
            "market_report": _read_text_arg(args.market_report_file),
            "sentiment_report": _read_text_arg(args.sentiment_report_file),
            "news_report": _read_text_arg(args.news_report_file),
            "fundamentals_report": _read_text_arg(args.fundamentals_report_file),
            "investment_debate_state": {
                "bull_history": _read_text_arg(args.bull_history_file),
                "bear_history": _read_text_arg(args.bear_history_file),
                "history": _read_text_arg(args.debate_history_file),
                "current_response": "",
                "judge_decision": _read_text_arg(args.investment_plan_file),
                "count": (args.debate_rounds or 0) * 2,
            },
            "investment_plan": _read_text_arg(args.investment_plan_file),
            "trader_investment_plan": _read_text_arg(args.trader_investment_plan_file),
            "risk_debate_state": {
                "aggressive_history": _read_text_arg(args.aggressive_history_file),
                "conservative_history": _read_text_arg(args.conservative_history_file),
                "neutral_history": _read_text_arg(args.neutral_history_file),
                "history": _read_text_arg(args.risk_history_file),
                "latest_speaker": "Judge",
                "current_aggressive_response": "",
                "current_conservative_response": "",
                "current_neutral_response": "",
                "judge_decision": _read_text_arg(args.final_trade_decision_file),
                "count": (args.risk_rounds or 0) * 3,
            },
            "final_trade_decision": _read_text_arg(args.final_trade_decision_file),
        }

    state = _ensure_state_shape(state, metadata)

    # Resolve tool_trace
    tool_trace: list[dict[str, Any]] = []
    if args.tool_trace:
        tool_trace = json.loads(Path(args.tool_trace).read_text(encoding="utf-8"))

    envelope = {
        "schema_version": 1,
        "kind": "archive",
        "metadata": metadata,
        "state": state,
        "tool_trace": tool_trace,
    }

    try:
        _validate(envelope)
    except Exception as e:
        _eprint(f"ERROR: archive failed schema validation: {e}")
        return 3

    # Write output
    if args.output:
        out_path = Path(args.output)
    else:
        # Filename mirrors the framework convention so a hand-copy works too:
        #   <run_id>__<date>__<UTC_ts>.json
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"{metadata['run_id']}__{metadata['trade_date']}__{ts}.json"
        out_path = Path(tempfile.gettempdir()) / name

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    _eprint(f"OK: wrote archive {out_path}")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
