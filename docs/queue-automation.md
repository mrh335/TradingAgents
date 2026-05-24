# Queue automation — the four drain paths

Reference for how the run-queue actually gets drained, written so future
sessions don't lose context. The push side (schedules → queue) is
already wired up; this document is about the **pull side** (consuming
queue items into runs).

## TL;DR — what's running right now

Open `/queue` in the webapp. The **server-side auto-drain card** at the
top shows the current state of the only always-on consumer.

For everything else, you need to either:

1. Manually invoke Claude Code/Desktop with **"process the queue"**
   (drains everything, free under Max), OR
2. Install the Windows Scheduled Task (drains every 30 min when laptop
   is on, free under Max), OR
3. Set up an Anthropic Cloud Code Routine via `/schedule`
   (drains every 60 min minimum, runs on Anthropic infra even when
   laptop is off, requires public API access via the Cloudflare tunnel).

## The four paths in detail

### Path 1: Manual Claude Code invocation

What it is: open Claude Desktop or `claude` CLI, say "process the queue".

What it covers: all modes including heavy `analyze` runs.

Cost: free under a Max subscription. Counts against your CD usage limits.

Speed: only happens when you remember to do it.

Setup: nothing — already works. The skill at
`~/.claude/skills/tradingagents-analyze/SKILL.md` has a "process the
queue" / "drain the queue" trigger that walks `/run-queue/pending` and
routes each item by mode.

### Path 2: Windows Scheduled Task → `claude -p` (silent)

What it is: a local Windows task that runs `claude -p "process the queue"`
every 30 minutes while the laptop is on. **Silent** — the installer
wraps the CLI in a VBScript launcher (`%LOCALAPPDATA%\TradingAgents\drain.vbs`)
so no console window appears. Output goes to `drain.log` next to the
wrapper for post-hoc debugging.

What it covers: all modes including heavy `analyze` runs.

Cost: free under Max. Same as path 1 — Claude Code CLI use against
your subscription.

Speed: 30-minute polling cadence.

Limitation: requires the laptop to be on and you to be logged in.
When the lid is closed, the task pauses.

Setup: from PowerShell as admin:
```powershell
.\scripts\install-windows-drain-task.ps1
```

Uninstall:
```powershell
Unregister-ScheduledTask -TaskName "TradingAgents Drain Queue" -Confirm:$false
```

To verify the install:
```powershell
Get-ScheduledTaskInfo -TaskName "TradingAgents Drain Queue"
Start-ScheduledTask -TaskName "TradingAgents Drain Queue"   # fire once now
```

### Path 3: Anthropic Cloud Code Routine

What it is: a remote routine registered via `/schedule` that runs on
Anthropic's cloud infrastructure on a cron.

What it covers: all modes — heavy and light. Runs the same skill.

Cost: free under Max (counts against subscription usage).

Speed: **1-hour minimum interval** (Anthropic constraint).

Limitation: needs PUBLIC API access. The NAS at `192.168.2.34:8001` is
LAN-only — Anthropic's cloud can't reach it. Either:

- Bring up the Cloudflare tunnel (`docker compose --profile tunnel up
  -d cloudflared`) and point the routine at the public URL, OR
- Skip this path and use 1 or 2.

Setup: ask "create a routine that runs every hour at the top of the
hour and drains the queue at https://YOUR-TUNNEL-URL/run-queue/pending".
The `/schedule` skill walks you through it.

### Path 4: Server-side drainer (in this repo)

What it is: an asyncio task in the api container (`service/queue_drainer.py`)
that polls the queue every 5 minutes.

What it covers: ONLY light modes (`ask_portfolio`, `earnings_summary`).
Heavy modes like `analyze` are rejected — too expensive to auto-process.

Cost: paid via your Anthropic API key (set `ANTHROPIC_API_KEY` in
`/volume1/docker/tradingagents/.env`). Cents/day on Haiku, more on
Sonnet/Opus.

Speed: 5-minute polling cadence.

Setup: set the env var, restart api container, go to `/queue` and flip
the auto-drain toggle on. Pick model from the dropdown.

## Recommended setup

For most users with a Max subscription:

1. **Primary: install the Windows Scheduled Task** (path 2). Free,
   30-min cadence, handles everything including analyze runs.
2. **Optional fallback: Anthropic routine** (path 3) at 1-hour cadence
   for the times the laptop is off. Requires tunnel setup.
3. **Skip server-side drainer** (path 4) unless you specifically want
   no laptop dependency and don't mind paying API tokens for light
   modes only.

## Why this exists

In an earlier session, the server-side scheduler was set up
(`/schedules` cron → queue) without a pull-side automation. The pull
side was deferred to "Claude Desktop drains it" but the actual
mechanism (manual invocation, Windows task, remote routine) was never
documented or installed. This led to confusion: schedules fired,
queue filled up, /history showed nothing, and the next session had to
re-discover the gap.

This document is the canonical record so it doesn't happen again.
