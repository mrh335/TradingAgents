# Portfolio Q&A via the TradingAgents MCP server

Use Claude Code (or any MCP-capable client) as a conversational interface
to your portfolio, runs, and what-if scenarios — **without writing any
code**. The MCP server exposes ~15 tools that the AI calls directly when
you ask questions in plain English.

## What you have

The `tradingagents-mcp` container is already running on the NAS at
`http://192.168.2.34:8002/mcp` (port 8002). It exposes structured tools
that Claude Code invokes automatically based on your question.

You've been using these tools all session — every time a tool name like
`mcp__tradingagents__get_portfolio` appears in a Claude Code response,
that's the MCP server.

## One-time setup (Claude Code)

If Claude Code isn't already pointed at the MCP server, add it to your
client config. Two ways:

### Option A — via Claude Code MCP UI

Open Claude Code → Settings → MCP Servers → Add:

```
Name: tradingagents
Transport: HTTP (Streamable)
URL: http://192.168.2.34:8002/mcp
```

Restart Claude Code. The 15 `mcp__tradingagents__*` tools will appear
in any session.

### Option B — via config file

Edit `~/.claude/mcp_servers.json` (create if missing) and add:

```json
{
  "mcpServers": {
    "tradingagents": {
      "type": "http",
      "url": "http://192.168.2.34:8002/mcp"
    }
  }
}
```

Restart Claude Code.

### Verify it's connected

In any Claude Code session, ask:

> What MCP tools do I have available from the tradingagents server?

If you see a list of `get_portfolio`, `paper_open`, `compare_runs`, etc.
the connection is working. If you see no MCP tools or a connection
error, check that the `mcp` container is healthy:

```bash
ssh markh@192.168.2.34 "docker compose -f /volume1/docker/tradingagents/docker-compose.yml ps mcp"
```

Should show `Up X minutes (healthy)`.

## Available tools (full list)

| Tool | What it does |
|---|---|
| `get_portfolio` | List all open positions with cost basis + current value |
| `get_portfolio_by_account` | Same, grouped by account (taxable / IRA / 401k) |
| `get_watchlist` | List tickers on the watchlist with next-earnings dates |
| `get_quote` | Current price for one ticker |
| `get_recent_news` | High-impact news for held + watched tickers |
| `get_restrictions` | Active trading restrictions (open windows, blackouts) |
| `list_runs` | All historical analysis runs (filterable by ticker + date) |
| `get_run` | Full archive for one run (state + reports + decision) |
| `get_run_history` | Compact decision history for a ticker over time |
| `get_brief` | Plain-English brief for one run (action / triggers / risks) |
| `compare_runs` | Side-by-side comparison of N runs (e.g. across models) |
| `paper_open` | Open a paper-trading position (mirrors a real trade without affecting brokerage) |
| `paper_close` | Close a paper position at a given price |
| `paper_list` | All open paper positions |
| `paper_history` | All closed paper trades |
| `paper_summary` | P&L summary across all paper trades |

## What-if scenarios — concrete prompts to try

These prompts are written assuming you're talking to Claude Code in a
chat session with the MCP connected. Paste any of them as-is.

### "What's in my portfolio right now?"

> Pull my current portfolio. Show each position with cost basis, current
> price, unrealized P&L, and which account it's in. Sort by largest
> position value first.

The AI will call `get_portfolio_by_account`, `get_quote` per ticker, and
present the table.

### Risk + concentration questions

> What's my largest concentration risk right now? Are any 3 positions
> together more than 50% of my book?

> Which holdings are in the same sector? If tech-heavy, by how much?

> Sort my positions by current unrealized P&L. Which 3 are biggest
> winners / losers?

### Decision-tracking questions

> What were the framework's last 5 NVDA recommendations? Did the
> decisions change over time, and did the price reflect that?

> Show me every Sell call the framework made in the last 90 days and
> what happened to those stocks afterward.

> For AAPL, what was the framework's verdict on 2026-05-20? Show me the
> brief.

### What-if (paper trading)

> Open paper positions for every recent Buy recommendation at the
> current market price, sized at 1% of a notional $100k portfolio each.
> Track them so I can see how the framework's calls would have
> performed if I'd acted.

The AI uses `paper_open` per ticker. Later:

> Show me my paper trading summary — total P&L, win rate, biggest
> winner, biggest loser.

> Close all paper positions that have gained more than 10% and report
> the realized P&L.

### Cross-model what-if

> The last NVDA run was done by Sonnet. Spin up a comparison with Opus,
> Sonnet, and qwen2.5:14b on the same date. When it's done, tell me
> where they disagree and which model's brief I should trust given the
> current regime.

(Uses the `/compare` API — see `web/app/compare/page.tsx` for the
direct UI alternative.)

### Regime + calibration questions

> What market regime are we in right now? Given that regime, has the
> framework historically added value or underperformed? Should I
> up-weight or down-weight today's recommendations?

> Compare today's regime to the regime that was active on the last
> NVDA Buy recommendation. Has anything changed that would make me
> re-evaluate that call?

### News + catalyst questions

> Pull high-impact news from the last 7 days for any ticker I own.
> Anything I should be aware of before market open tomorrow?

> Which of my holdings has earnings this week? Pull their last earnings
> brief so I can see what the framework expects.

### Sanity-check questions

> Pull the brief for run abc12345. Walk me through the entry plan —
> when should I buy what, and at what prices?

> Compare the cost basis on my AAPL position to the framework's
> latest entry plan. Am I above or below their recommended entry
> price?

> Looking at my restrictions, which of my held positions am I currently
> in an "open" window for, and which are blocked? When does each
> blocked one re-open?

## Tips for getting good answers

1. **Be specific about timeframes** — "last 30 days" or "since
   2026-05-01" beats "recently". The AI translates to the right API
   parameters.

2. **Ask for tables when comparing things** — the AI will use them
   when prompted. "Show as a table" or "compare side-by-side" works.

3. **Chain questions** — "First, show me my portfolio. Then, for the
   3 largest positions, pull the latest brief and tell me if I should
   trim, hold, or add based on what they say."

4. **Use paper trades for safety** — "open a paper position" never
   touches your real brokerage. Great for "what if I'd taken every
   Buy call this year" exploration.

5. **Ask for the regime context** — "what regime are we in" or "what's
   the framework's hit rate in this regime" — these come from the
   /regime endpoints we just added.

## Skills vs MCP — which to use

You have three different Claude integration points:

| Use | Tool | Cost |
|---|---|---|
| **Generate new analyses** (heavy multi-agent runs) | Claude Code with the `tradingagents-analyze` skill | Your Max subscription |
| **Process the queue** (drain pending requests) | The silent Windows Scheduled Task | Your Max subscription |
| **Conversational Q&A about existing data** | Claude Code with the MCP tools | Your Max subscription |

MCP is for the third row — it gives the AI **direct read/write access** to
your portfolio state without invoking the heavy analysis pipeline. Much
faster (each MCP call is sub-second) and great for the kind of
exploratory questions you'd otherwise have to click through 4 pages
of the webapp to answer.

## Troubleshooting

**"I don't see any tradingagents tools in Claude Code"** → MCP server
isn't connected. Re-do the setup above and restart Claude Code.

**"The MCP tools error out with 'connection refused'"** → The `mcp`
container is down. SSH to NAS and `docker compose restart mcp`.

**"The AI gives me data that doesn't match the webapp"** → MCP reads
from the same SQLite database as the webapp, so any divergence means
either (a) you're looking at cached webapp data — refresh the page, or
(b) the MCP read is stale — ask the AI to re-fetch.

**"Can the AI write to my real portfolio?"** → No. The only mutation
tools are `paper_open` / `paper_close` (paper-trading only) and basic
restriction CRUD. Real positions (`get_portfolio`) are read-only via
MCP. To change real positions, use the webapp's `/portfolio` or
`/trades` page.

## Related docs

- `docs/queue-automation.md` — how scheduled queue drains work
- `mcp/README.md` — MCP server implementation details
- `CLAUDE.md` — the canonical guide for using Claude Code with this app
- `~/.claude/skills/tradingagents-analyze/SKILL.md` — the analysis skill (separate from MCP)
