"""build_brief.py — validate a Brief JSON against the schema and emit the canonical form.

Usage:
    # Validate from a JSON file:
    python build_brief.py --input brief.json [--output validated.json]

    # Validate from stdin (Claude pipes the JSON directly):
    echo '{...}' | python build_brief.py [--output validated.json]

On success:
- Writes the validated JSON (re-serialised, indent=2) to --output or to a
  temp file.
- Also generates the markdown rendering at <output>.md (mirrors
  gui/brief.py:Brief.to_markdown for parity with the existing webapp).
- Prints the JSON path to stdout (Mode A) so the orchestrator can chain it.

On failure: prints a precise error to stderr with the offending field path.
Exit codes: 0 success, 2 input error, 3 validation error.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SCHEMA_PATH = SKILL_DIR / "schemas" / "brief.schema.json"

ALLOWED_DECISIONS = {"Buy", "Overweight", "Hold", "Underweight", "Sell"}


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _validate(brief: dict) -> list[str]:
    """Return a list of error messages; empty list means valid."""
    errors: list[str] = []

    try:
        import jsonschema
    except ImportError:
        _eprint("WARN: jsonschema not installed — running structural checks only")
        return _validate_structural(brief)

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    for err in validator.iter_errors(brief):
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return errors


def _validate_structural(brief: dict) -> list[str]:
    """Best-effort structural validation when jsonschema isn't installed."""
    errors: list[str] = []
    required = ["decision", "tldr", "timeframe", "position_size",
                "entry_strategy", "stop_loss", "take_profit",
                "triggers", "key_risks", "benchmark_view"]
    for k in required:
        if k not in brief:
            errors.append(f"missing required field: {k}")

    d = brief.get("decision")
    if d and d not in ALLOWED_DECISIONS:
        errors.append(f"decision: {d!r} not one of {sorted(ALLOWED_DECISIONS)}")

    trigs = brief.get("triggers") or []
    if not isinstance(trigs, list):
        errors.append("triggers must be a list")
    elif not (3 <= len(trigs) <= 7):
        errors.append(f"triggers: expected 3-7 items, got {len(trigs)}")
    else:
        for i, t in enumerate(trigs):
            if not isinstance(t, dict) or "condition" not in t or "action" not in t:
                errors.append(f"triggers[{i}]: must have 'condition' and 'action'")

    risks = brief.get("key_risks") or []
    if not isinstance(risks, list):
        errors.append("key_risks must be a list")
    elif not (3 <= len(risks) <= 5):
        errors.append(f"key_risks: expected 3-5 items, got {len(risks)}")

    return errors


def _to_markdown(brief: dict) -> str:
    """Mirror gui/brief.py:Brief.to_markdown (lines 118-135) so the
    skill's markdown sidecar reads identically to the framework's."""
    triggers = brief.get("triggers") or []
    if triggers:
        trig_md = "\n".join(
            f"- **If** {t['condition'].strip()} → {t['action'].strip()}"
            for t in triggers
        )
    else:
        trig_md = "_(none extracted)_"

    risks = brief.get("key_risks") or []
    risks_md = "\n".join(f"- {r.strip()}" for r in risks) or "_(none)_"

    return (
        f"### {brief.get('decision', '')}\n\n"
        f"{(brief.get('tldr') or '').strip()}\n\n"
        f"**Timeframe:** {(brief.get('timeframe') or '').strip()}  \n"
        f"**Position size:** {(brief.get('position_size') or '').strip()}  \n"
        f"**Entry:** {(brief.get('entry_strategy') or '').strip()}  \n"
        f"**Stop loss:** {(brief.get('stop_loss') or '').strip()}  \n"
        f"**Take profit:** {(brief.get('take_profit') or '').strip()}\n\n"
        f"#### Trigger points\n\n{trig_md}\n\n"
        f"#### Key risks\n\n{risks_md}\n\n"
        f"**vs S&P 500:** {(brief.get('benchmark_view') or '').strip()}\n"
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--input", "-i", help="Path to brief JSON (else read stdin)")
    p.add_argument("--output", "-o", help="Output JSON path (default: temp file)")
    args = p.parse_args()

    # Load input
    if args.input:
        try:
            raw = Path(args.input).read_text(encoding="utf-8")
        except OSError as e:
            _eprint(f"ERROR: could not read input: {e}")
            return 2
    else:
        raw = sys.stdin.read()
        if not raw.strip():
            _eprint("ERROR: no input on stdin and no --input given")
            return 2

    try:
        brief = json.loads(raw)
    except json.JSONDecodeError as e:
        _eprint(f"ERROR: invalid JSON: {e}")
        return 2

    if not isinstance(brief, dict):
        _eprint("ERROR: top-level JSON must be an object")
        return 2

    errors = _validate(brief)
    if errors:
        _eprint("VALIDATION FAILED:")
        for e in errors:
            _eprint(f"  - {e}")
        return 3

    # Output
    if args.output:
        out_path = Path(args.output)
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix="brief_", suffix=".json", delete=False,
            mode="w", encoding="utf-8",
        )
        out_path = Path(tmp.name)
        tmp.close()

    out_path.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = out_path.with_suffix(".md")
    md_path.write_text(_to_markdown(brief), encoding="utf-8")

    _eprint(f"OK: validated brief, wrote {out_path}")
    _eprint(f"OK: markdown rendered at {md_path}")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
