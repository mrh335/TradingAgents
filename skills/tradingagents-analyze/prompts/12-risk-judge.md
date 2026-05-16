# Persona — Risk Judge (sub-persona of Portfolio Manager)

This is a reference file documenting the **risk-judge** framing that the
Portfolio Manager prompt uses. The existing repo collapses the
risk-judge and portfolio-manager into a single LLM call. We do the same:
**use `prompts/13-portfolio-manager.md` directly** — that prompt embeds
the risk-judging step.

## Why this file exists

The plan listed a 12-risk-judge.md slot for symmetry with the existing
repo's file structure (`tradingagents/agents/managers/risk_manager.py`
which combines risk-judging + portfolio decision). Keeping this file
makes the prompt directory easier to navigate ("I see 14 numbered prompts
and the workflow has 14 phases") even though Phase 6 of the orchestrator
only loads one of `12` or `13` — currently `13`.

If a future refactor splits risk-judging from portfolio-decisioning into
two distinct phases (e.g., adding a separate risk-judge that vetoes the
PM's trade if risk metrics are out of bounds), the prompt for that
veto-judge step belongs here.

## Output of the combined step

See `prompts/13-portfolio-manager.md`. The judgement and the final
decision are produced together as a single structured output.

Stored under both `state.final_trade_decision` and
`state.risk_debate_state.judge_decision`.
