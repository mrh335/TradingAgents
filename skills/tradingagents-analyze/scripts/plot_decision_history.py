"""plot_decision_history.py — thin client for the framework's decision-history chart.

The chart is rendered server-side by the framework at
    GET /charts/decisions/{ticker}.png
and the raw data is available at
    GET /charts/decisions/{ticker}
(see service/routers/charts.py).

This script's only job is to give the user a clickable URL to the
rendered chart and (optionally) attach a `chart.md` sidecar on the most
recent run that embeds the chart inline via an <img> link, so the chart
shows up on the run's webapp page.

No matplotlib dep on the client side — stdlib only.

Usage:
    python plot_decision_history.py <TICKER> [--lookback-days N]
                                              [--config <path>]
                                              [--no-publish]
                                              [--save-png <path>]

By default prints the chart URL to stdout. With --save-png, downloads
the PNG locally as well. With --no-publish, skips the sidecar attach.
"""

from __future__ import annotations

import argparse
import json
import sys
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


def _http(method: str, url: str, payload: dict | None = None, timeout: int = 30):
    body = json.dumps(payload).encode("utf-8") if payload else None
    headers = {"Accept": "application/json"}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return 0, str(e.reason)


def _download_png(url: str, dest: Path, timeout: int = 30) -> tuple[bool, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            dest.write_bytes(resp.read())
            return True, "ok"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        return False, f"HTTP {e.code}: {body}"
    except urllib.error.URLError as e:
        return False, str(e.reason)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("ticker")
    p.add_argument("--lookback-days", type=int, default=180)
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--save-png", default=None,
                   help="Also download the PNG to this path.")
    p.add_argument("--no-publish", action="store_true",
                   help="Don't attach a chart.md sidecar; just print the URL.")
    args = p.parse_args()

    cfg = _load_config(Path(args.config))
    api = (cfg.get("api_base_url") or "http://192.168.2.34:8001").rstrip("/")
    web_base = (cfg.get("webapp_base_url") or "http://192.168.2.34:3001").rstrip("/")

    ticker = args.ticker.upper()
    qs = urllib.parse.urlencode({"lookback_days": args.lookback_days})
    png_url = f"{api}/charts/decisions/{ticker}.png?{qs}"
    data_url = f"{api}/charts/decisions/{ticker}?{qs}"

    # Sanity-check the data endpoint first — fast, tells us if there are
    # actually any decisions to chart.
    _eprint(f"GET {data_url}")
    status, data = _http("GET", data_url)
    if status == 0:
        _eprint(f"  CONNECTION FAILED: {data}")
        _eprint(f"  Is the FastAPI service running at {api}?")
        return 5
    if status == 404:
        _eprint(f"  no decisions found for {ticker} in the last {args.lookback_days}d.")
        return 1
    if status != 200 or not isinstance(data, dict):
        _eprint(f"  HTTP {status}: {data}")
        return 2

    n_decisions = len(data.get("decisions") or [])
    _eprint(f"  {n_decisions} decisions in window")

    # Optionally download the PNG locally
    if args.save_png:
        dest = Path(args.save_png)
        _eprint(f"downloading PNG → {dest}")
        ok, msg = _download_png(png_url, dest)
        if ok:
            _eprint(f"  OK: {dest.stat().st_size} bytes")
        else:
            _eprint(f"  WARN: download failed: {msg}")

    # Pick the most recent run to attach the chart sidecar to
    decisions = data.get("decisions") or []
    if args.no_publish or not decisions:
        if not decisions:
            _eprint("  no runs to attach a sidecar to; skipping publish.")
        print(png_url)
        return 0

    latest = decisions[-1]
    md = (
        f"# {ticker} — decision-history chart\n\n"
        f"Last {args.lookback_days} days, {n_decisions} runs. "
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC.\n\n"
        f"![Decision history]({png_url})\n\n"
        f"Open the live chart: <{png_url}>\n\n"
        f"Raw data (JSON): <{data_url}>\n"
    )

    sidecar_url = f"{api}/sidecars/run/{latest['run_id']}/sidecar/markdown"
    _eprint(f"POST chart.md sidecar → {latest['run_id']}")
    status, resp = _http("POST", sidecar_url, {"kind": "chart.md", "content": md})

    if status == 200:
        _eprint(f"  OK: chart attached to {latest['run_id']}")
        print(f"{web_base}/history/{latest['run_id']}")
        return 0
    if status == 404:
        _eprint(f"  the generic sidecar endpoint isn't deployed on the server yet.")
        _eprint(f"  chart is still viewable directly: {png_url}")
        print(png_url)
        return 0
    _eprint(f"  HTTP {status}: {resp}")
    _eprint(f"  chart URL still works: {png_url}")
    print(png_url)
    return 1


if __name__ == "__main__":
    sys.exit(main())
