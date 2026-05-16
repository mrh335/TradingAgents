"""token_logger.py — append a token-usage entry to both io_tokens.md files.

Skill-side: <skill_dir>/io_tokens.md
Framework-side: <repo>/io_tokens.md  (from config: io_tokens.framework_log)

Usage:
    python token_logger.py --run-id <id> --ticker NVDA --trade-date 2026-05-15
                           --transcript-files <path1> [<path2> ...]
                           [--config <path>] [--llm-calls N] [--tool-calls N]

The transcripts are everything Claude actually emitted during the skill
session — analyst reports, debate turns, final decision, brief JSON, plus
the persona prompts Claude consumed as input. The script counts input vs.
output by file: anything in <skill_dir>/prompts/ or the data block is
input; anything Claude produced is output.

Estimation uses tiktoken (`cl100k_base` encoding) when available, else
falls back to word_count * 1.3. Estimate is ±10%.

Output line format (one row per run, appended):

    - 2026-05-15T14:32:01Z | NVDA | 2026-05-15 | claude-desktop-skill/claude-opus-4-7 | in=12450 out=3210 calls=12 | run_id=claude-a3f72b1d

Idempotent: if the same run_id already appears in a file, the script skips
that file (no duplicates) but still touches the other if needed.
"""

from __future__ import annotations

import argparse
import json
import sys
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
        _eprint("WARN: PyYAML not installed; using defaults")
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _count_tokens_tiktoken(text: str, encoding_name: str = "cl100k_base") -> int | None:
    try:
        import tiktoken
        enc = tiktoken.get_encoding(encoding_name)
        return len(enc.encode(text))
    except Exception:
        return None


def _count_tokens_fallback(text: str) -> int:
    """Cheap fallback: word_count * 1.3 (English text approximation)."""
    return int(len(text.split()) * 1.3)


def _count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    n = _count_tokens_tiktoken(text, encoding_name)
    return n if n is not None else _count_tokens_fallback(text)


def _line_for_run(run_id: str, ticker: str, trade_date: str,
                  provider: str, model: str,
                  tokens_in: int, tokens_out: int, calls: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (f"- {ts} | {ticker} | {trade_date} | {provider}/{model} | "
            f"in={tokens_in} out={tokens_out} calls={calls} | run_id={run_id}\n")


def _append_if_new(path: Path, line: str, run_id: str) -> bool:
    """Append `line` to `path` unless run_id already appears in it.

    Creates the file (with a header) if missing. Returns True if appended."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if run_id in existing:
            _eprint(f"  skip {path.name}: run_id {run_id} already logged")
            return False
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    else:
        header = (
            "# Token usage log\n\n"
            "Each line records one run. Estimates are approximate (tiktoken "
            "cl100k_base encoding, ±10%) and intended for cost tracking, not "
            "billing reconciliation.\n\n"
            "| timestamp (UTC) | ticker | trade_date | provider/model | tokens | run_id |\n"
            "|---|---|---|---|---|---|\n"
        )
        path.write_text(header + line, encoding="utf-8")
    _eprint(f"  appended to {path}")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--run-id", required=True)
    p.add_argument("--ticker", required=True)
    p.add_argument("--trade-date", required=True)
    p.add_argument("--input-files", nargs="*", default=[],
                   help="Files counted as input (persona prompts, data block, etc.)")
    p.add_argument("--output-files", nargs="*", default=[],
                   help="Files counted as output (Claude's reports, brief, etc.)")
    p.add_argument("--input-text", default="",
                   help="Inline input text (concatenated with --input-files)")
    p.add_argument("--output-text", default="",
                   help="Inline output text (concatenated with --output-files)")
    p.add_argument("--llm-calls", type=int, default=0)
    p.add_argument("--tool-calls", type=int, default=0)
    p.add_argument("--provider", default="claude-desktop-skill")
    p.add_argument("--model", default="claude-opus-4-7")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = p.parse_args()

    cfg = _load_config(Path(args.config))
    io_cfg = cfg.get("io_tokens") or {}
    encoding = io_cfg.get("estimator_model") or "cl100k_base"

    def _accumulate(files: list[str], inline: str) -> tuple[int, int]:
        total_tokens = 0
        n_files = 0
        for f in files:
            try:
                txt = Path(f).read_text(encoding="utf-8")
            except OSError as e:
                _eprint(f"  warn: could not read {f}: {e}")
                continue
            total_tokens += _count_tokens(txt, encoding)
            n_files += 1
        if inline:
            total_tokens += _count_tokens(inline, encoding)
        return total_tokens, n_files

    in_tokens, in_files = _accumulate(args.input_files, args.input_text)
    out_tokens, out_files = _accumulate(args.output_files, args.output_text)

    _eprint(f"counted {in_files} input files ({in_tokens} tokens) + "
            f"{out_files} output files ({out_tokens} tokens) "
            f"using {'tiktoken/'+encoding if _count_tokens_tiktoken('x', encoding) is not None else 'word-count fallback'}")

    line = _line_for_run(
        run_id=args.run_id,
        ticker=args.ticker,
        trade_date=args.trade_date,
        provider=args.provider,
        model=args.model,
        tokens_in=in_tokens,
        tokens_out=out_tokens,
        calls=args.llm_calls,
    )

    # Skill-side log
    skill_log_name = io_cfg.get("skill_log", "io_tokens.md")
    skill_log = (SKILL_DIR / skill_log_name) if not Path(skill_log_name).is_absolute() else Path(skill_log_name)
    _append_if_new(skill_log, line, args.run_id)

    # Framework-side log (cosmetic note: write a clearly-marked entry so
    # readers know this is a skill-run leaking into the framework log)
    framework_log_path = io_cfg.get("framework_log") or ""
    if framework_log_path:
        framework_path = Path(framework_log_path)
        _append_if_new(framework_path, line, args.run_id)
    else:
        _eprint("  io_tokens.framework_log not configured — skipped framework log")

    return 0


if __name__ == "__main__":
    sys.exit(main())
