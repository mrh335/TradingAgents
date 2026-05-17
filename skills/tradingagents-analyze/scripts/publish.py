"""publish.py — POST a complete run (archive + brief) to the TradingAgents API.

Calls `POST <api_base_url>/runs/import` (added to the framework in
service/routers/runs.py). The server writes the archive to
<results_dir>/<TICKER>/TradingAgentsStrategy_logs/runs/<basename>.json,
writes the brief sidecar next to it, and INSERTs a row into gui.db.runs
with status='done'. After this returns, the run is indistinguishable
from a framework-generated run in every read path (History page,
search, sidecars API, brief API).

Usage:
    python publish.py --archive <archive.json> --brief <brief.json>
                      [--brief-md <brief.md>] [--config <path>]
                      [--dry-run]

Stdlib only — no `requests` dep.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG = SKILL_DIR / "config" / "defaults.yaml"


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _load_config(path: Path) -> dict:
    if not path.exists():
        _eprint(f"WARN: config file not found: {path} — using defaults")
        return {}
    try:
        import yaml
    except ImportError:
        _eprint("ERROR: PyYAML not installed (pip install pyyaml)")
        sys.exit(2)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _post_json(url: str, payload: dict, timeout: int = 60) -> tuple[int, dict | str]:
    """POST JSON; return (status_code, parsed_response_or_error_text)."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
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
        return 0, f"could not reach API: {e.reason}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--archive", required=True, help="Path to archive JSON")
    p.add_argument("--brief", required=True, help="Path to brief JSON")
    p.add_argument("--brief-md", help="Optional path to brief markdown")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--dry-run", action="store_true",
                   help="Print the payload that would be POSTed without sending.")
    # Token usage — passed in by the skill's run wrapper after token_logger.py
    # has estimated input/output from the prompt + response files. These end
    # up in archive.metadata so the webapp's /tokens page and per-run pages
    # show real counts instead of zeros.
    p.add_argument("--tokens-in", type=int, default=None,
                   help="Estimated input tokens for the run (from token_logger).")
    p.add_argument("--tokens-out", type=int, default=None,
                   help="Estimated output tokens for the run (from token_logger).")
    p.add_argument("--llm-calls", type=int, default=None,
                   help="Number of LLM invocations the skill made for this run.")
    p.add_argument("--tool-calls", type=int, default=None,
                   help="Number of tool calls made during the run.")
    args = p.parse_args()

    archive_path = Path(args.archive)
    brief_path = Path(args.brief)
    if not archive_path.exists():
        _eprint(f"ERROR: archive not found: {archive_path}")
        return 2
    if not brief_path.exists():
        _eprint(f"ERROR: brief not found: {brief_path}")
        return 2

    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief_md = None
    if args.brief_md:
        bm_path = Path(args.brief_md)
        if bm_path.exists():
            brief_md = bm_path.read_text(encoding="utf-8")
        else:
            _eprint(f"WARN: --brief-md not found, skipping: {bm_path}")

    meta = (archive.get("metadata") or {})
    if not all(meta.get(k) for k in ("run_id", "ticker", "trade_date")):
        _eprint("ERROR: archive metadata missing run_id / ticker / trade_date")
        return 2

    # Inject token usage into archive.metadata if the caller passed it.
    # The webapp's /runs/import reads metadata.tokens_in / tokens_out /
    # llm_calls / tool_calls and stores them on the runs row, which feeds
    # the /tokens chart and per-run header. Without these, the chart shows
    # zeros for skill-imported runs.
    metadata_updates = {}
    for arg_name, meta_key in (
        ("tokens_in", "tokens_in"),
        ("tokens_out", "tokens_out"),
        ("llm_calls", "llm_calls"),
        ("tool_calls", "tool_calls"),
    ):
        val = getattr(args, arg_name, None)
        if val is not None:
            metadata_updates[meta_key] = int(val)
    if metadata_updates:
        archive.setdefault("metadata", {}).update(metadata_updates)
        meta = archive["metadata"]
        _eprint(f"  metadata token fields populated: {metadata_updates}")

    cfg = _load_config(Path(args.config))
    api_base = (cfg.get("api_base_url") or "http://192.168.2.34:8001").rstrip("/")
    web_base = (cfg.get("webapp_base_url") or "http://192.168.2.34:3001").rstrip("/")

    payload = {"archive": archive, "brief": brief}
    if brief_md:
        payload["brief_markdown"] = brief_md

    url = f"{api_base}/runs/import"

    if args.dry_run:
        _eprint(f"DRY RUN — would POST to {url}")
        _eprint(f"  payload size: {len(json.dumps(payload))} bytes")
        _eprint(f"  archive run_id: {meta['run_id']}")
        _eprint(f"  brief decision: {brief.get('decision', '?')}")
        _eprint(f"  on success → {web_base}/history/{meta['run_id']}")
        return 0

    _eprint(f"POST {url}")
    status, body = _post_json(url, payload)

    if status == 200:
        run_id = body.get("run_id") if isinstance(body, dict) else meta["run_id"]
        _eprint(f"  OK: run_id={run_id} status={body.get('status') if isinstance(body, dict) else '?'}")
        _eprint(f"  log_path={body.get('log_path') if isinstance(body, dict) else '?'}")
        print(f"{web_base}/history/{run_id}")
        return 0

    if status == 409:
        _eprint(f"  CONFLICT: run_id already exists. Server response:")
        _eprint(f"    {body}")
        return 3

    if status == 400:
        _eprint(f"  BAD REQUEST: {body}")
        return 4

    if status == 0:
        _eprint(f"  CONNECTION FAILED: {body}")
        _eprint(f"  Is the FastAPI service running at {api_base}?")
        _eprint(f"  Is the /runs/import endpoint deployed? "
                f"(see service/routers/runs.py — added in this skill's companion repo PR)")
        return 5

    _eprint(f"  HTTP {status}: {body}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
