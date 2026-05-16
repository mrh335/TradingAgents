"""fetch_holdings.py — pull the user's current portfolio positions from the framework.

Uses the framework's existing /portfolio/positions endpoint. Filters to a
specific ticker if requested. The output JSON is consumed by the
holdings-aware analyst persona (prompts/18-holdings-context.md).

Usage:
    python fetch_holdings.py [--ticker TICKER] [--include-closed]
                              [--horizon long|short|auto] [--config <path>]
                              [--output <path>]

Output JSON:
    {
      "fetched_at": "<UTC ISO>",
      "horizon": "long|short|auto",
      "ticker_filter": "NVDA" | null,
      "positions": [
        {
          "id": 12,
          "ticker": "NVDA",
          "shares": 50,
          "cost_basis_per_share": 175.20,
          "current_price": 198.42,
          "unrealized_gain_pct": 13.25,
          "opened_at": "2025-09-01",
          "closed_at": null,
          "account": "taxable",
          "notes": "..."
        }
      ],
      "summary": {
        "total_positions": 4,
        "total_invested": 12500.00,
        "total_unrealized_pct": 8.4
      }
    }

If the user has no positions registered in the framework, returns an empty
list — the orchestrator handles this gracefully (asks user inline whether
to proceed in "no holdings" mode).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

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


def _http_get(url: str, timeout: int = 20):
    req = urllib.request.Request(url, method="GET",
                                 headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return 0, str(e.reason)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--ticker", default=None,
                   help="Filter to a specific ticker (default: all open positions).")
    p.add_argument("--include-closed", action="store_true",
                   help="Include closed positions in the output (history).")
    p.add_argument("--horizon", choices=("long", "short", "auto"), default="auto",
                   help="User's planning horizon. Passed to the analyst persona "
                        "for tone-matching. 'auto' lets the analyst infer from "
                        "the position ages.")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--output", "-o", default=None)
    args = p.parse_args()

    cfg = _load_config(Path(args.config))
    api = (cfg.get("api_base_url") or "http://192.168.2.34:8001").rstrip("/")

    qs = []
    if args.include_closed:
        qs.append("include_closed=true")
    url = f"{api}/portfolio/positions"
    if qs:
        url += "?" + "&".join(qs)

    _eprint(f"GET {url}")
    status, body = _http_get(url)
    if status != 200:
        _eprint(f"ERROR: framework returned {status}: {body}")
        return 2
    if not isinstance(body, list):
        # Some frameworks wrap in {positions: [...]}. Handle both shapes.
        if isinstance(body, dict) and isinstance(body.get("positions"), list):
            positions = body["positions"]
        else:
            _eprint(f"ERROR: unexpected response shape: {type(body)}")
            return 2
    else:
        positions = body

    # Apply ticker filter
    if args.ticker:
        upper = args.ticker.upper()
        positions = [p for p in positions if (p.get("ticker") or "").upper() == upper]

    # Compute summary statistics
    total_invested = 0.0
    total_value = 0.0
    open_count = 0
    for pos in positions:
        if pos.get("closed_at"):
            continue
        try:
            shares = float(pos.get("shares") or 0)
            cb = float(pos.get("cost_basis_per_share") or 0)
            cur = float(pos.get("current_price") or pos.get("last_price") or 0)
            total_invested += shares * cb
            total_value += shares * cur
            open_count += 1
        except (TypeError, ValueError):
            continue

    total_unrealized_pct = (
        ((total_value - total_invested) / total_invested) * 100
        if total_invested > 0 else 0.0
    )

    out = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "horizon": args.horizon,
        "ticker_filter": args.ticker.upper() if args.ticker else None,
        "positions": positions,
        "summary": {
            "total_positions": open_count,
            "total_invested": round(total_invested, 2),
            "total_current_value": round(total_value, 2),
            "total_unrealized_pct": round(total_unrealized_pct, 2),
        },
    }

    if args.output:
        out_path = Path(args.output)
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"holdings_{args.ticker or 'all'}_", suffix=".json",
            delete=False, mode="w", encoding="utf-8",
        )
        out_path = Path(tmp.name)
        tmp.close()
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    _eprint(f"OK: {open_count} open positions, total value ${total_value:,.2f} "
            f"({total_unrealized_pct:+.1f}%); wrote {out_path}")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
