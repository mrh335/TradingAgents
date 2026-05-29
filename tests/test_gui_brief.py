"""Regression tests for gui/brief.py — the plain-English Brief schema, the
decision normaliser, and the markdown-fallback parser used when a model
can't emit structured JSON.

The LLM structured-output path (``with_structured_output``) needs langchain
+ a live client and is out of scope here; these cover the pure-Python pieces
that decide what the Brief panel renders, especially for local/Ollama runs
that fall through to the markdown path.
"""

import pytest

from gui.brief import Brief, Trigger, _normalize_decision, _parse_markdown_to_brief


# ---- _normalize_decision -------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Buy", "Buy"), ("buy", "Buy"), ("**Buy**", "Buy"), ("BUY.", "Buy"),
    ("Overweight", "Overweight"), ("hold", "Hold"), ("Sell", "Sell"),
    ("Underweight", "Underweight"),
])
def test_normalize_decision_canonical(raw, expected):
    assert _normalize_decision(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("accumulate", "Buy"), ("bullish", "Buy"), ("long", "Buy"), ("strong buy", "Buy"),
    ("add", "Overweight"), ("increase", "Overweight"),
    ("neutral", "Hold"), ("wait", "Hold"), ("watch", "Hold"), ("maintain", "Hold"),
    ("reduce", "Underweight"), ("trim", "Underweight"),
    ("exit", "Sell"), ("short", "Sell"), ("avoid", "Sell"),
])
def test_normalize_decision_synonyms(raw, expected):
    assert _normalize_decision(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("trim the position", "Underweight"),  # falls back to first word
    ("wait and see", "Hold"),
    ("exit now", "Sell"),
])
def test_normalize_decision_multiword_uses_first_word(raw, expected):
    assert _normalize_decision(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "qwerty", "no idea"])
def test_normalize_decision_unknown_returns_none(raw):
    assert _normalize_decision(raw) is None


# ---- _parse_markdown_to_brief --------------------------------------------

SAMPLE_MD = """
## Decision
Buy

## Action (plain English)
buy a starter position

## TL;DR
Buy a small NVDA position and add on dips.

## Timeframe
4-6 weeks

## Position size
3-5% of portfolio

## Entry
Start small, add on a pullback.

## Stop loss
Below $180

## Take profit
$245

## Triggers
- IF price closes below $180 THEN sell everything
- 50-day crosses above 200-day -> add more
- Q3 revenue miss: trim half

## Key risks
- AI capex slows
- China export limits widen

## Benchmark view
Likely beats SPY by ~5%.
"""


def test_parse_markdown_full():
    b = _parse_markdown_to_brief(SAMPLE_MD, {"ticker": "NVDA"})
    assert isinstance(b, Brief)
    assert b.decision == "Buy"
    assert b.action_plain == "buy a starter position"
    assert "NVDA position" in b.tldr
    assert b.timeframe == "4-6 weeks"
    assert b.stop_loss == "Below $180"
    assert "SPY" in b.benchmark_view


def test_parse_markdown_triggers_all_formats():
    """IF/THEN, arrow, and colon-separated triggers all parse."""
    b = _parse_markdown_to_brief(SAMPLE_MD, {})
    pairs = {(t.condition, t.action) for t in b.triggers}
    assert ("price closes below $180", "sell everything") in pairs
    assert ("50-day crosses above 200-day", "add more") in pairs
    assert ("Q3 revenue miss", "trim half") in pairs


def test_parse_markdown_risks():
    b = _parse_markdown_to_brief(SAMPLE_MD, {})
    assert b.key_risks == ["AI capex slows", "China export limits widen"]


def test_parse_markdown_sparse_uses_meta_and_safe_defaults():
    """Empty model output must still yield a valid, renderable Brief:
    decision falls back to meta, and the required list fields get non-empty
    placeholders so the Pydantic model validates and the panel renders."""
    b = _parse_markdown_to_brief("", {"decision": "Sell"})
    assert b.decision == "Sell"
    assert b.tldr            # non-empty fallback sentence
    assert len(b.triggers) == 1   # placeholder trigger, never empty
    assert len(b.key_risks) == 1  # placeholder risk, never empty
    assert b.timeframe       # non-empty default


def test_parse_markdown_decision_defaults_to_hold_without_meta():
    b = _parse_markdown_to_brief("garbage with no decision header", {})
    assert b.decision == "Hold"


# ---- Brief model + serialisation -----------------------------------------

def _full_brief() -> Brief:
    return Brief(
        decision="Buy", action_plain="buy a starter position",
        tldr="Buy a little.", timeframe="4-6 weeks",
        position_size="3-5%", entry_strategy="scale in",
        stop_loss="below $180", take_profit="$245",
        triggers=[Trigger(condition="x", action="y")],
        key_risks=["risk a"], benchmark_view="beats SPY",
    )


def test_brief_json_roundtrip():
    """store_brief / get_cached_brief depend on this exact round-trip."""
    b = _full_brief()
    restored = Brief.model_validate_json(b.model_dump_json())
    assert restored.decision == "Buy"
    assert restored.triggers[0].condition == "x"


def test_brief_to_markdown_contains_core_sections():
    md = _full_brief().to_markdown()
    assert "### Buy" in md
    assert "buy a starter position" in md
    assert "vs S&P 500:" in md
