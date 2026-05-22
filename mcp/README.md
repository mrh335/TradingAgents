# tradingagents-mcp

MCP server that exposes the TradingAgents webapp + paper trading to Claude
Desktop and Claude Code. Lets you read analyses, view your portfolio, and
open/close paper trades from inside a Claude chat — no copy-paste, no
context switching.

## What's in the box

**Read tools** (16 total):

| Tool | Purpose |
|---|---|
| `list_runs` | Find recent analyses by ticker + filters |
| `get_run` | Full archive — every transcript section |
| `get_brief` | Plain-English brief for a run |
| `get_run_history` | Recent runs for one ticker (decision evolution) |
| `compare_runs` | Side-by-side decisions + key risks across runs |
| `get_portfolio` | Real positions with live P&L |
| `get_portfolio_by_account` | Real positions grouped by account |
| `get_watchlist` | Tickers being tracked |
| `get_quote` | Live prices via yfinance |
| `get_restrictions` | Active earnings windows / blackouts |
| `get_recent_news` | News feed (optionally per-ticker) |

**Paper trading tools** (5 total):

| Tool | Purpose |
|---|---|
| `paper_open` | Open a paper position (uses live price if entry omitted) |
| `paper_close` | Close a paper position (uses live price if exit omitted) |
| `paper_list` | List paper positions |
| `paper_summary` | Mark-to-market: total cost, value, unrealized P&L |
| `paper_history` | Closed trades with realized P&L |

Paper state lives in the webapp's database (`paper_positions` table) — every
laptop that connects to the same webapp sees the same paper book.

## Setup on a new laptop

You need: Python 3.10+, access to the TradingAgents webapp at
`http://192.168.2.34:8001`, and the cloned repo on disk.

### 1. Install dependencies

```powershell
python -m pip install --user "mcp>=1.2.0" "httpx>=0.27" "yfinance>=0.2"
```

(`--user` keeps it out of system Python; no admin needed.)

### 2. Configure Claude Code (CLI + Code Desktop)

Edit `~/.claude.json` and add a top-level `mcpServers` entry. Adjust the
PYTHONPATH to wherever you cloned the repo:

```json
{
  "mcpServers": {
    "tradingagents": {
      "command": "C:\\Users\\markh\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
      "args": ["-m", "tradingagents_mcp"],
      "env": {
        "PYTHONPATH": "Z:\\My Documents\\code repo\\active\\hedge_trader\\TradingAgents\\mcp\\src",
        "TRADINGAGENTS_API_BASE": "http://192.168.2.34:8001"
      }
    }
  },
  ... existing config ...
}
```

### 3. Configure standalone Claude Desktop (if used)

Edit `%APPDATA%\Claude\claude_desktop_config.json` and add the same
`mcpServers` block at the top level.

### 4. Restart Claude

Quit and reopen Claude Desktop / Claude Code. The MCP server is spawned
fresh on each launch. Look for "tradingagents" in the MCP servers list
(the bottom of the chat window has an indicator).

### 5. Try a tool

In a chat:

> Show me my last 3 NVDA runs and tell me whether the thesis is holding up.

Claude will call `get_run_history(ticker="NVDA", n=3)` automatically.

## Paper trading workflow

```
You: "Open a paper position of 10 NVDA shares based on the latest analysis."

Claude: (calls list_runs ticker=NVDA limit=1 → finds claude-a3920083)
        (calls get_brief run_id=claude-a3920083 → sees Overweight + entry_strategy)
        (calls paper_open ticker=NVDA shares=10 related_run_id=claude-a3920083)
        → "Opened paper position #1: 10 NVDA @ $302.25 (live). Linked to
           claude-a3920083 (Overweight)."

[time passes, weeks later]

You: "How are my paper trades doing?"

Claude: (calls paper_summary → returns all open positions with mark-to-market)
        → "5 open paper positions, total cost $X, current value $Y,
           unrealized P&L $Z (+W%)."
```

## Architecture

```
Claude Desktop / Code  ─── stdio ───  tradingagents-mcp (this package)
                                              │
                            ┌─────────────────┼─────────────────┐
                            │ HTTP            │                 │ yfinance
                            ▼                 ▼                 ▼
                  /runs, /portfolio,   /paper/*          Live quotes
                  /watchlist, etc.     (new endpoints)   (get_quote tool)
                            │                 │
                            └──── webapp at :8001 ────┘
                                       │
                                       ▼
                                 SQLite (incl. paper_positions table)
```

Paper trading lives server-side so every laptop sees the same book. Read
tools are stateless wrappers over the webapp.

## Troubleshooting

**"tradingagents" doesn't appear in Claude's MCP servers list:**
- Check the config file syntax with `python -c "import json; json.load(open(...))"`
- Quit Claude completely (System Tray → Quit) then relaunch
- Check that `python` in the config points to a Python with the deps installed

**Tools error with "could not reach webapp":**
- Confirm the webapp at `TRADINGAGENTS_API_BASE` is reachable: `curl <base>/health`
- Make sure you're on the LAN (`192.168.2.34` is internal)

**Paper trading tools return 404 Not Found:**
- The webapp needs the `/paper/*` endpoints added (see commit that adds
  `service/routers/paper.py` and the `paper_positions` table). Rebuild
  the Docker container and restart.

## Dev / changing tools

Tool definitions live in `src/tradingagents_mcp/server.py`. The
`@mcp.tool()` decorator turns each function into an MCP tool exposed
to Claude. Edit, then restart Claude to pick up changes.

Tool descriptions (the docstrings) matter — Claude reads them to decide
which tool to use for a given request. Be precise about WHEN to use each
tool, not just WHAT it does.
