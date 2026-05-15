"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.
"""

from __future__ import annotations

import re
from typing import Tuple


# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: Tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# Matches "Rating: X" / "rating - X" / "Rating: **X**" — tolerates markdown
# bold wrappers and either a colon or hyphen separator.
_RATING_LABEL_RE = re.compile(r"rating.*?[:\-][\s*]*(\w+)", re.IGNORECASE)

# Verdicts other than "Rating:" that LLMs commonly emit, in priority order.
# We match label, optional whitespace/markdown noise, then the verdict word.
# Allows multi-word "Hold (Reduce Underweight)" by only capturing the first
# rating-bearing word.
_LABEL_PATTERNS = [
    re.compile(r"final\s+(?:transaction\s+)?proposal[\s*:\-]+\**\s*([A-Za-z]+)", re.IGNORECASE),
    re.compile(r"^\s*(?:[*_>]*\s*)?action\s*[:\-]\s*\**\s*([A-Za-z]+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"recommendation\s*[:\-]\s*\**\s*([A-Za-z]+)", re.IGNORECASE),
    re.compile(r"decision\s*[:\-]\s*\**\s*([A-Za-z]+)", re.IGNORECASE),
    re.compile(r"verdict\s*[:\-]\s*\**\s*([A-Za-z]+)", re.IGNORECASE),
]

# Map common synonyms to the 5-tier vocab so verdicts like "Reduce" or
# "Accumulate" don't slip through.
_SYNONYMS = {
    "accumulate": "Buy",
    "strongbuy": "Buy",
    "strong_buy": "Buy",
    "bullish": "Buy",
    "long": "Buy",
    "reduce": "Underweight",
    "trim": "Underweight",
    "avoid": "Sell",
    "short": "Sell",
    "exit": "Sell",
    "bearish": "Sell",
    "neutral": "Hold",
    "wait": "Hold",
    "watch": "Hold",
}


def _map_word(word: str) -> str | None:
    """Return a canonical 5-tier rating for a candidate word, or None."""
    if not word:
        return None
    w = word.strip("*:.,()[]{}").lower()
    if w in _RATING_SET:
        return w.capitalize()
    if w in _SYNONYMS:
        return _SYNONYMS[w]
    return None


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text.

    Four-pass strategy, ordered from most-specific to most-fuzzy:
    1. Explicit ``Rating: X`` / ``Rating - X`` label (the PM's structured-output
       template emits this for OpenAI/Anthropic).
    2. Verdict-style labels: ``FINAL TRANSACTION PROPOSAL: X``, ``Action: X``,
       ``Recommendation: X``, ``Decision: X``, ``Verdict: X``. Catches the
       common Ollama free-text shape where the model never says "Rating:".
    3. First 5-tier rating word found anywhere in the text.
    4. ``default`` (typically "Hold").

    Synonym mapping handles "Reduce" → Underweight, "Accumulate" → Buy, etc.,
    so verdicts that aren't in the strict 5-tier vocab still parse.
    """
    if not text:
        return default

    # Pass 1: Rating: X
    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m:
            mapped = _map_word(m.group(1))
            if mapped:
                return mapped

    # Pass 2: alternative verdict labels (full text, not line-by-line, so
    # multi-line bold formatting like "FINAL TRANSACTION\nPROPOSAL: BUY"
    # is also caught — \s in the patterns matches newlines).
    for pat in _LABEL_PATTERNS:
        for m in pat.finditer(text):
            mapped = _map_word(m.group(1))
            if mapped:
                return mapped

    # Pass 3: first naked rating word anywhere
    for line in text.splitlines():
        for word in line.split():
            mapped = _map_word(word)
            if mapped:
                return mapped

    return default
