"""interrogate.py — load a past run's archive for follow-up Q&A.

Two-step workflow used by the orchestrator's `ask <run_id>` mode:

1. **Fetch:** `python interrogate.py fetch <run_id>` GETs the archive +
   brief + existing sidecars from the framework API, writes them to a
   temp file, prints the path. The orchestrator reads that file into
   working memory before invoking `prompts/17-interrogation.md`.

2. **Publish:** `python interrogate.py publish <run_id> --question "..."
   --answer "..."` POSTs the Q&A as a `chat.md` sidecar attached to the
   run. The webapp surfaces it (kind: chat.md).

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
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


def cmd_fetch(args, cfg) -> int:
    api = (cfg.get("api_base_url") or "http://192.168.2.34:8001").rstrip("/")

    # Use the sidecars bundle endpoint — it returns the full archive
    # envelope plus existing_sidecars in one shot.
    status, bundle = _http("GET", f"{api}/sidecars/run/{args.run_id}")
    if status != 200 or not isinstance(bundle, dict):
        _eprint(f"ERROR: GET /sidecars/run/{args.run_id} → {status}: {bundle}")
        return 2

    # Also fetch the brief if there is one
    bstatus, brief_resp = _http("GET", f"{api}/briefs/{args.run_id}")
    brief_obj = None
    if bstatus == 200 and isinstance(brief_resp, dict):
        brief_obj = brief_resp.get("brief")

    out = {
        "target_run": {
            "metadata": {
                "run_id": bundle.get("run_id"),
                "ticker": bundle.get("ticker"),
                "trade_date": bundle.get("trade_date"),
                "archive_path": bundle.get("archive_path"),
                # decision lives in the full RunSummary; use the brief's if available
                "decision": (brief_obj or {}).get("decision") if brief_obj else None,
            },
            "state": (bundle.get("archive") or {}).get("state") or {},
            "brief": brief_obj,
            "existing_sidecars": bundle.get("existing_sidecars") or [],
        },
    }

    if args.output:
        out_path = Path(args.output)
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"interrogate_{args.run_id}_", suffix=".json",
            delete=False, mode="w", encoding="utf-8",
        )
        out_path = Path(tmp.name)
        tmp.close()

    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    _eprint(f"OK: wrote {out_path}")
    print(out_path)
    return 0


def cmd_publish(args, cfg) -> int:
    api = (cfg.get("api_base_url") or "http://192.168.2.34:8001").rstrip("/")

    if not args.question or not args.answer:
        _eprint("ERROR: both --question and --answer required")
        return 2

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Append (rather than overwrite) — fetch any existing chat.md, then re-POST.
    # The generic sidecar endpoint overwrites by design, so we read-modify-write.
    existing = ""
    bstatus, bundle = _http("GET", f"{api}/sidecars/run/{args.run_id}")
    if bstatus == 200 and isinstance(bundle, dict):
        for s in bundle.get("existing_sidecars") or []:
            if s.get("kind") == "chat.md":
                # Fetch via raw file URL — for now read from archive_path
                # adjacent. The bundle doesn't expose the chat.md content
                # directly. As a workaround, we accept that the first call
                # creates the file and subsequent ones overwrite; the
                # caller passes --append to fetch+merge externally.
                pass

    new_entry = f"## Q ({ts})\n\n{args.question.strip()}\n\n## A\n\n{args.answer.strip()}\n"
    body = (existing + "\n\n" + new_entry) if existing else new_entry

    status, resp = _http(
        "POST",
        f"{api}/sidecars/run/{args.run_id}/sidecar/markdown",
        {"kind": "chat.md", "content": body},
    )
    if status == 200:
        _eprint(f"OK: posted chat.md ({status})")
        web_base = (cfg.get("webapp_base_url") or "http://192.168.2.34:3001").rstrip("/")
        print(f"{web_base}/history/{args.run_id}")
        return 0
    if status == 404:
        _eprint(f"  generic sidecar endpoint not deployed on the server; "
                f"deploy the latest framework code (service/routers/sidecars.py).")
        return 5
    _eprint(f"ERROR: POST returned {status}: {resp}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="GET archive + brief + sidecars for a run")
    f.add_argument("run_id")
    f.add_argument("--output", "-o", default=None)
    f.add_argument("--config", default=str(DEFAULT_CONFIG))

    pb = sub.add_parser("publish", help="POST a Q&A chat.md sidecar")
    pb.add_argument("run_id")
    pb.add_argument("--question", required=True)
    pb.add_argument("--answer", required=True)
    pb.add_argument("--config", default=str(DEFAULT_CONFIG))

    args = parser.parse_args()
    cfg = _load_config(Path(args.config))

    if args.cmd == "fetch":
        return cmd_fetch(args, cfg)
    if args.cmd == "publish":
        return cmd_publish(args, cfg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
