# tradingagents-briefs — Claude Code skill

Companion skill to `tradingagents-analyze`. Where the analyze skill
runs the full multi-agent pipeline from scratch, **this one processes
already-completed runs** — picking up the brief-request markers the
webapp drops when the user clicks "🤖 Request via Claude Code".

## Why two skills

- **`tradingagents-analyze`** is expensive (full 18-phase pipeline).
  Use it when you want a fresh analysis.
- **`tradingagents-briefs`** is essentially free — your parametric
  knowledge plus the recorded analysis on the server is all you need.
  Use it to refresh briefs in different vocabulary, regenerate after a
  schema change, or just batch-process the queue.

## Trigger phrases

| You say | Claude does |
|---|---|
| `/tradingagents-briefs` | Process every pending request |
| "process pending briefs" | Same |
| "rewrite briefs" | Same |
| "refresh briefs from claude code" | Same |
| "process brief for NVDA" | Only the NVDA pending request(s) |
| "show me what's pending" | List, no action, ask to confirm |

## Setup

Already deployed. The skill SKILL.md is self-contained — it knows the
NAS URL (`http://192.168.2.34:8001`), the Brief schema, and the
mechanical-engineer audience guidelines. No additional config.

## How to queue work for this skill

From the webapp at `http://192.168.2.34:3001/history`:

- **🤖 Request all missing** — drops markers on every completed run
  that doesn't yet have a brief sidecar
- **🔄 Re-request all** — same, but also includes runs that already
  have a brief (use after a schema/vocabulary change)
- Per-run: open `/history/{run_id}` → Brief panel → **🤖 Request via
  Claude Code**

Then say "process pending briefs" in any Claude Code session.

## Related

- Sibling skill: `~/.claude/skills/tradingagents-analyze/` — full
  multi-agent pipeline for fresh runs
- Repo: `Z:/My Documents/code repo/active/hedge_trader/TradingAgents/`
- CLAUDE.md at the repo root has the same audience + vocabulary rules
- Webapp lives at `http://192.168.2.34:3001`, API at `:8001`
