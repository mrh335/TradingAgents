"""tradingagents-mcp — MCP server for the TradingAgents stack.

Exposes ~15 tools to Claude Desktop / Claude Code:
- Read-only views over the webapp (runs, briefs, portfolio, news, …)
- Paper trading (open/close/list/summary/history) backed by the same
  webapp's new /paper/* endpoints, so paper state is centralized and
  every laptop sees the same book.
"""
__version__ = "0.1.0"
