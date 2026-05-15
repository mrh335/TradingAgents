"""Sidecar files — Claude Code's lane.

A "sidecar" is a file that lives **next to** a run's archive JSON and
carries human-or-Claude-Code-authored content that the web app surfaces
without burning API tokens. The pattern:

    runs/<run_id>__<date>__<ts>.json            ← the archive (machine-written)
    runs/<run_id>__<date>__<ts>.brief.json      ← structured Brief sidecar (optional)
    runs/<run_id>__<date>__<ts>.brief.md        ← markdown brief sidecar (optional)
    runs/<run_id>__<date>__<ts>.brief.request.md ← marker the web app drops to ask
                                                   Claude Code for a brief
    runs/<run_id>__<date>__<ts>.analysis.md     ← free-form deep dive
    runs/<run_id>__<date>__<ts>.chat.md         ← chat-session transcript

The web app prefers sidecar files over LLM-generated content. So if you
drop a ``*.brief.json`` next to the archive, the Brief tab displays
that instead of calling the quick-think model. The web app never
modifies sidecar files — only the request marker.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from gui.brief import Brief


def archive_basename(archive_path: str | Path) -> Path:
    """Return the basename Path used to derive sidecar filenames.

    For ``foo/bar/<runid>__<date>__<ts>.json`` returns ``foo/bar/<runid>__<date>__<ts>``.
    """
    p = Path(archive_path)
    # Strip the ``.json`` extension.
    return p.with_suffix("")


def sidecar_path(archive_path: str | Path, suffix: str) -> Path:
    """Build a sidecar path next to ``archive_path``.

    ``suffix`` should NOT include a leading dot. Examples:
        sidecar_path("…/abc.json", "brief.json")  -> …/abc.brief.json
        sidecar_path("…/abc.json", "brief.md")    -> …/abc.brief.md
    """
    base = archive_basename(archive_path)
    return base.parent / f"{base.name}.{suffix}"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_brief_sidecar(archive_path: str | Path) -> Optional[Brief]:
    """Look for a structured ``*.brief.json`` next to the archive. Returns
    a parsed ``Brief`` or ``None`` if not present / unparseable."""
    p = sidecar_path(archive_path, "brief.json")
    if not p.exists():
        return None
    try:
        return Brief.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_brief_markdown(archive_path: str | Path) -> Optional[str]:
    """Look for a free-form ``*.brief.md`` next to the archive. Returns the
    markdown text or ``None``. Use this when Claude Code has written a
    less-structured brief — the UI will render the markdown directly."""
    p = sidecar_path(archive_path, "brief.md")
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def list_sidecars(archive_path: str | Path) -> List[Dict[str, Any]]:
    """Enumerate every sidecar that exists for an archive. Useful for the
    UI to show "what content has Claude Code produced for this run"."""
    base = archive_basename(archive_path)
    parent = base.parent
    if not parent.exists():
        return []
    prefix = base.name + "."
    out: List[Dict[str, Any]] = []
    for p in sorted(parent.iterdir()):
        if not p.is_file() or not p.name.startswith(prefix):
            continue
        if p.suffix == ".json" and p.name == base.name + ".json":
            # That's the archive itself, not a sidecar.
            continue
        try:
            stat = p.stat()
            out.append({
                "name": p.name,
                "kind": p.name[len(prefix):],  # e.g. "brief.json", "brief.md"
                "path": str(p),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
        except OSError:
            continue
    return out


# ---------------------------------------------------------------------------
# Write — the only mutation the web app does is dropping a request marker.
# Claude Code itself writes the actual sidecar content (brief.json / md).
# ---------------------------------------------------------------------------

def write_request(archive_path: str | Path, *, kind: str, prompt: str) -> Path:
    """Drop a ``*.{kind}.request.md`` marker that asks Claude Code to
    produce a sidecar of the given kind. Idempotent: rewrites if present."""
    p = sidecar_path(archive_path, f"{kind}.request.md")
    p.write_text(prompt, encoding="utf-8")
    return p


def request_exists(archive_path: str | Path, kind: str) -> bool:
    return sidecar_path(archive_path, f"{kind}.request.md").exists()


def clear_request(archive_path: str | Path, kind: str) -> bool:
    p = sidecar_path(archive_path, f"{kind}.request.md")
    if p.exists():
        try:
            p.unlink()
            return True
        except OSError:
            pass
    return False
