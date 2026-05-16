"""lookup_parent_run.py — find the most recent completed run for a ticker.

Queries the framework API to find the most recent `status=done` run for a
given ticker, then GETs its full archive. Used by the skill's update-mode
flow (see prompts/00-update-mode.md).

Usage:
    python lookup_parent_run.py <TICKER> [--lookback-days N] [--config <path>]
                                          [--output <path>]

Output JSON shape (printed path goes to stdout; status to stderr):
    {
      "found": true,
      "parent_run": {
        "metadata": { run_id, ticker, trade_date, completed_at, ... },
        "state":    { market_report, ..., final_trade_decision, ... },
        "brief":    { ... } | null,
        "delta_window": { "from": "<parent.completed_at>", "to": "<now>" }
      }
    }

If no qualifying parent run exists, `found` is false and `parent_run` is
null — the caller should run a fresh analysis.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import tempfile

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG = SKILL_DIR / "config" / "defaults.yaml"


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _http_get(url: str, timeout: int = 20) -> tuple[int, Any]:
    req = urllib.request.Request(url, method="GET",
                                 headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        return 0, str(e.reason)


def _http_get_brief_sidecar(api_base: str, run_id: str) -> dict | None:
    """Fetch the brief sidecar via /sidecars/run/{id}. Returns None if absent."""
    status, body = _http_get(f"{api_base}/sidecars/run/{run_id}")
    if status != 200 or not isinstance(body, dict):
        return None
    for s in body.get("existing_sidecars") or []:
        if s.get("kind") == "brief.json":
            # Need the actual content — fetch the briefs endpoint
            bstatus, bbody = _http_get(f"{api_base}/briefs/{run_id}")
            if bstatus == 200 and isinstance(bbody, dict):
                return bbody.get("brief")
    return None


def find_parent(ticker: str, lookback_days: int, api_base: str) -> dict | None:
    """Find the most recent `status=done` run for this ticker."""
    status, runs = _http_get(
        f"{api_base}/runs?ticker={urllib.parse.quote(ticker)}&limit=20"
    )
    if status != 200 or not isinstance(runs, list):
        _eprint(f"  list runs failed: HTTP {status}: {runs}")
        return None

    now = datetime.now(timezone.utc)
    horizon = now - timedelta(days=lookback_days)

    for r in runs:
        if (r.get("status") or "").lower() != "done":
            continue
        completed_at = r.get("completed_at")
        if not completed_at:
            continue
        try:
            # Tolerate "...Z" or "+00:00"
            completed_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            if completed_dt.tzinfo is None:
                completed_dt = completed_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if completed_dt < horizon:
            _eprint(f"  most recent run is older than {lookback_days}d (cutoff {horizon.isoformat()})")
            return None
        return r

    _eprint(f"  no completed runs found for {ticker}")
    return None


def fetch_archive(api_base: str, run_id: str) -> dict | None:
    status, body = _http_get(f"{api_base}/runs/{run_id}")
    if status != 200 or not isinstance(body, dict):
        _eprint(f"  GET /runs/{run_id} failed: HTTP {status}")
        return None
    return body  # RunDetail = RunSummary + state + tool_trace


def main() -> int:
    # Lazy import (urllib.parse is stdlib but pyright likes explicit)
    import urllib.parse  # noqa: F401  (used in find_parent)

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("ticker")
    p.add_argument("--lookback-days", type=int, default=None,
                   help="How far back to look for a parent run (default from config: 7)")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--output", "-o", default=None,
                   help="Output JSON path (default: temp file)")
    args = p.parse_args()

    cfg = _load_config(Path(args.config))
    api_base = (cfg.get("api_base_url") or "http://192.168.2.34:8001").rstrip("/")
    lookback_days = args.lookback_days
    if lookback_days is None:
        lookback_days = int(cfg.get("update_lookback_days") or 7)

    _eprint(f"looking up parent run for {args.ticker} (lookback={lookback_days}d)")

    summary = find_parent(args.ticker, lookback_days, api_base)
    if summary is None:
        out_obj = {"found": False, "parent_run": None}
    else:
        detail = fetch_archive(api_base, summary["run_id"])
        if detail is None:
            out_obj = {"found": False, "parent_run": None}
        else:
            brief = _http_get_brief_sidecar(api_base, summary["run_id"])
            out_obj = {
                "found": True,
                "parent_run": {
                    "metadata": {
                        "run_id": summary["run_id"],
                        "ticker": summary["ticker"],
                        "trade_date": summary["trade_date"],
                        "completed_at": summary["completed_at"],
                        "decision": summary.get("decision"),
                        "provider": summary.get("provider"),
                    },
                    "state": detail.get("state") or {},
                    "brief": brief,
                    "delta_window": {
                        "from": summary["completed_at"],
                        "to": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    },
                },
            }
            _eprint(f"  found parent: {summary['run_id']} "
                    f"(completed {summary['completed_at']}, decision={summary.get('decision')})")

    if args.output:
        out_path = Path(args.output)
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"parent_run_{args.ticker}_", suffix=".json",
            delete=False, mode="w", encoding="utf-8",
        )
        out_path = Path(tmp.name)
        tmp.close()

    out_path.write_text(json.dumps(out_obj, indent=2), encoding="utf-8")
    _eprint(f"OK: wrote {out_path}")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
