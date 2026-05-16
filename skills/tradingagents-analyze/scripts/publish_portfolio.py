"""publish_portfolio.py — write the batch-level synthesis to a markdown file
   and (optionally) attach it as a sidecar to each run in the batch.

Used by Phase 11 of the orchestrator when running multi-ticker batches or
ensembles. The cross-ticker portfolio synthesis (from
`prompts/15-portfolio-cross-ticker.md`) or the ensemble consensus
(`prompts/16-ensemble-consensus.md`) is just a markdown blob — this script
finds all runs in the batch and attaches the synthesis to each.

The attachment uses the existing `*.brief.md` sidecar mechanism, which the
webapp already surfaces with a "Claude Code (markdown)" badge. Future
work can add a dedicated `*.portfolio.md` / `*.ensemble.md` sidecar kind
to the framework; for now the brief.md carrier is the path of least
resistance.

Usage:
    python publish_portfolio.py --batch-id <id> --synthesis-file <md>
                                [--kind portfolio|ensemble] [--config <path>]
                                [--dry-run]

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
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


def _http_post_json(url: str, payload: dict, timeout: int = 30):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
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


def find_batch_runs(api_base: str, batch_id: str) -> list[dict]:
    """List runs whose archive metadata.batch_id matches. We have to scan
    the runs index since the API doesn't yet filter by batch_id directly.
    Future framework work: add `GET /runs?batch_id=...` for efficiency."""
    status, runs = _http_get(f"{api_base}/runs?limit=200")
    if status != 200 or not isinstance(runs, list):
        _eprint(f"  list runs failed: {status}: {runs}")
        return []

    matches: list[dict] = []
    for r in runs:
        # We need to read each archive's metadata.batch_id — that's in
        # the per-run detail, not the list summary. Be efficient by
        # filtering candidates first.
        rid = r.get("run_id")
        if not rid:
            continue
        # Quick filter: skill runs only (those have provider=claude-desktop-skill).
        # Avoids hitting the disk for every framework run.
        if r.get("provider") != "claude-desktop-skill":
            continue
        s, detail = _http_get(f"{api_base}/runs/{rid}")
        if s != 200 or not isinstance(detail, dict):
            continue
        # The framework's RunDetail doesn't return metadata.batch_id directly;
        # we have to look it up via the archive on disk. The existing
        # /runs/{id} endpoint returns RunDetail = RunSummary + state +
        # tool_trace, but NOT the raw metadata.batch_id. Fall back to
        # reading log_path locally if reachable, or use the sidecars API
        # which returns the full archive envelope.
        s2, bundle = _http_get(f"{api_base}/sidecars/run/{rid}")
        if s2 != 200 or not isinstance(bundle, dict):
            continue
        archive_doc = bundle.get("archive") or {}
        meta = (archive_doc.get("metadata") or {}) if isinstance(archive_doc, dict) else {}
        if meta.get("batch_id") == batch_id:
            matches.append({"run_id": rid, "ticker": r.get("ticker"),
                            "decision": r.get("decision")})
    return matches


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--batch-id", required=True)
    p.add_argument("--synthesis-file", required=True,
                   help="Path to markdown file with the synthesis content")
    p.add_argument("--kind", choices=("portfolio", "ensemble"),
                   default="portfolio",
                   help="Used as a header tag in the attached markdown.")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    syn_path = Path(args.synthesis_file)
    if not syn_path.exists():
        _eprint(f"ERROR: synthesis file not found: {syn_path}")
        return 2
    synthesis_md = syn_path.read_text(encoding="utf-8")

    cfg = _load_config(Path(args.config))
    api_base = (cfg.get("api_base_url") or "http://192.168.2.34:8001").rstrip("/")

    _eprint(f"finding runs in batch {args.batch_id}…")
    runs = find_batch_runs(api_base, args.batch_id)
    if not runs:
        _eprint("  no runs found for this batch_id — nothing to attach to")
        _eprint(f"  synthesis remains at: {syn_path}")
        return 0

    _eprint(f"  found {len(runs)} runs: {', '.join(r['ticker'] for r in runs)}")

    # Wrap the synthesis with a clear header so readers know what it is.
    header = (
        f"# {args.kind.title()} synthesis (batch {args.batch_id})\n\n"
        f"This {args.kind} synthesis was produced by the Claude skill after "
        f"all runs in the batch completed. Each ticker run also has its own "
        f"individual brief — this view sits on top of them.\n\n"
        f"---\n\n"
    )
    body = header + synthesis_md

    if args.dry_run:
        _eprint("DRY RUN — would POST as brief.md sidecar to each run:")
        for r in runs:
            _eprint(f"  {r['run_id']} ({r['ticker']})")
        return 0

    # Use the generic /sidecars/run/{id}/sidecar/markdown endpoint with the
    # appropriate kind (portfolio.md or ensemble.md). This doesn't clobber
    # the per-run structured brief, which lives at *.brief.json.
    sidecar_kind = f"{args.kind}.md"
    failures = []
    for r in runs:
        url = f"{api_base}/sidecars/run/{r['run_id']}/sidecar/markdown"
        status, resp = _http_post_json(url, {"kind": sidecar_kind, "content": body})
        if status == 200:
            _eprint(f"  OK: attached to {r['run_id']} ({r['ticker']}) as {sidecar_kind}")
        elif status == 404:
            # Possibly the framework hasn't deployed the generic endpoint yet.
            # Fall back to the brief.md path with a clear warning.
            _eprint(f"  WARN: generic sidecar endpoint missing on server; "
                    f"falling back to brief.md (will overwrite any existing brief.md)")
            url_fb = f"{api_base}/sidecars/run/{r['run_id']}/brief/markdown"
            status_fb, resp_fb = _http_post_json(url_fb, {"markdown": body})
            if status_fb == 200:
                _eprint(f"  OK (fallback): attached to {r['run_id']} ({r['ticker']})")
            else:
                failures.append((r['run_id'], status_fb, resp_fb))
                _eprint(f"  FAIL: {r['run_id']}: {status_fb}: {resp_fb}")
        else:
            failures.append((r['run_id'], status, resp))
            _eprint(f"  FAIL: {r['run_id']}: {status}: {resp}")

    if failures:
        _eprint(f"  {len(failures)} attachments failed. Synthesis kept at: {syn_path}")
        return 1

    web_base = (cfg.get("webapp_base_url") or "http://192.168.2.34:3001").rstrip("/")
    _eprint(f"  synthesis attached to all {len(runs)} runs.")
    _eprint(f"  open any in the webapp: {web_base}/history/<run_id>")
    print(f"{web_base}/history/{runs[0]['run_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
