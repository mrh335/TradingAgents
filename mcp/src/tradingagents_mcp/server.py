"""MCP server exposing the TradingAgents stack to Claude Desktop / Claude Code.

Tool surface (read-only over the webapp + paper trading writes):

  Runs / briefs
    list_runs            — find recent analyses
    get_run              — full archive (every transcript section + decision)
    get_brief            — plain-English brief for a run
    get_run_history      — recent runs for one ticker (decision evolution)
    compare_runs         — side-by-side: decision, key risks, triggers

  Portfolio / market data
    get_portfolio        — real positions with live P&L
    get_portfolio_by_account
    get_watchlist        — tickers being tracked
    get_quote            — live price (yfinance) for arbitrary tickers
    get_restrictions     — earnings windows, blackouts
    get_recent_news      — news items

  Paper trading
    paper_open           — open a paper position (uses live price if entry omitted)
    paper_close          — close a paper position (uses live price if exit omitted)
    paper_list           — list paper positions (open / closed / all)
    paper_summary        — mark-to-market: total cost, current value, P&L
    paper_history        — closed paper trades with realized P&L

Transport: stdio (standard for Claude Desktop). The MCP SDK handles framing.

Configuration (env vars):
  TRADINGAGENTS_API_BASE  default http://192.168.2.34:8001
"""

from __future__ import annotations

import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

from tradingagents_mcp.api_client import ApiError, TradingAgentsClient

logger = logging.getLogger("tradingagents-mcp")

mcp = FastMCP("tradingagents")


def _client() -> TradingAgentsClient:
    return TradingAgentsClient()


def _trim_run(run: dict) -> dict:
    """Strip the run dict down to fields useful in a list view.

    Full archives contain ~30KB of analyst reports per run; in list/compare
    contexts we want the summary, not every transcript byte. Callers can
    always use get_run for the full thing.
    """
    return {
        "run_id": run.get("run_id"),
        "ticker": run.get("ticker"),
        "trade_date": run.get("trade_date"),
        "decision": run.get("decision"),
        "provider": run.get("provider"),
        "deep_model": run.get("deep_model"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "status": run.get("status"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Runs / briefs
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def list_runs(
    ticker: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """List recent TradingAgents analysis runs. Use this to find run_ids before
    calling get_run / get_brief / compare_runs.

    Args:
        ticker: Optional ticker filter (e.g. "NVDA"). Case-insensitive.
        limit:  Max rows to return (default 20).

    Returns: { runs: [...], count: N }
    Each row has run_id, ticker, trade_date, decision, provider, started_at.
    """
    try:
        runs = await _client().list_runs(ticker=ticker, limit=limit)
        return {"runs": [_trim_run(r) for r in runs], "count": len(runs)}
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_run(run_id: str) -> dict:
    """Fetch one run's full archive — every analyst report, the bull/bear
    debate, the risk debate, and the final decision. Use this when the user
    wants to read or reason about the actual analysis content.

    Args:
        run_id: The run_id from list_runs (e.g. "claude-a3920083").

    Returns the full run object including state.market_report, state.sentiment_report,
    state.news_report, state.fundamentals_report, state.investment_debate_state,
    state.investment_plan, state.trader_investment_plan, state.risk_debate_state,
    state.final_trade_decision. Be aware these are large strings.
    """
    try:
        return await _client().get_run(run_id)
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_brief(run_id: str) -> dict:
    """Fetch the plain-English brief for a run — the actionable summary written
    for someone who doesn't speak Wall Street jargon. Includes decision, tldr,
    timeframe, position_size, entry_strategy, stop_loss, take_profit, triggers,
    key_risks, benchmark_view. Faster + smaller than get_run when the user just
    wants "what should I do."

    Args:
        run_id: The run_id from list_runs.
    """
    try:
        return await _client().get_brief(run_id)
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_run_history(ticker: str, n: int = 5) -> dict:
    """Recent runs for one ticker, decision-evolution view. Use this to answer
    "is the thesis on NVDA holding up over the last few analyses, or is it
    weakening?"

    Args:
        ticker: e.g. "NVDA".
        n:      How many recent runs to fetch (default 5, max ~20).

    Returns: { ticker, runs: [trimmed run rows, newest first] }.
    For each row you get decision + dates; use get_brief for the rationale
    per run if you need to compare arguments.
    """
    try:
        runs = await _client().list_runs(ticker=ticker, limit=n)
        return {"ticker": ticker.upper(),
                "runs": [_trim_run(r) for r in runs],
                "count": len(runs)}
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def compare_runs(run_ids: list[str]) -> dict:
    """Side-by-side comparison of multiple runs. Fetches each run's brief and
    returns decision + key risks + triggers per run so the caller can spot
    trend shifts or disagreement across analyses.

    Args:
        run_ids: List of run_ids (typically 2-4) to compare.

    Returns: { comparisons: [{ run_id, ticker, decision, tldr, key_risks, triggers }] }
    """
    client = _client()
    out: list[dict] = []
    for rid in run_ids:
        try:
            run = await client.get_run(rid)
            try:
                brief_resp = await client.get_brief(rid)
                brief = brief_resp.get("brief") or {}
            except ApiError:
                brief = {}
            out.append({
                "run_id": run.get("run_id"),
                "ticker": run.get("ticker"),
                "trade_date": run.get("trade_date"),
                "decision": run.get("decision"),
                "tldr": brief.get("tldr"),
                "key_risks": brief.get("key_risks"),
                "triggers": brief.get("triggers"),
            })
        except ApiError as e:
            out.append({"run_id": rid, "error": str(e)})
    return {"comparisons": out}


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio / market data
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_portfolio() -> dict:
    """Current real-money portfolio: open positions with live prices,
    unrealized P&L per position, plus totals. This is the user's actual
    brokerage book (synced from their planner), not paper trades.

    Use paper_summary for paper trading data.
    """
    try:
        return await _client().portfolio_summary()
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_portfolio_by_account() -> dict:
    """Real portfolio grouped by account label (joint brokerage, IRA, stock
    plan, etc.). Useful for "what's in each account" questions.
    """
    try:
        return await _client().portfolio_by_account()
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_watchlist() -> dict:
    """Tickers on the user's watchlist."""
    try:
        items = await _client().watchlist()
        return {"items": items, "count": len(items) if isinstance(items, list) else 0}
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_quote(tickers: list[str]) -> dict:
    """Live prices for arbitrary tickers via yfinance. Fast — single batched
    call. Use for "what's NVDA trading at right now" type questions.

    Args:
        tickers: One or more ticker symbols.

    Returns: { quotes: { TICKER: { price, previous_close, currency, day_change_pct } } }
    Missing tickers are returned with price=null + an "error" field.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed in MCP server env"}
    out: dict[str, dict] = {}
    for t in tickers:
        sym = t.upper()
        try:
            info = yf.Ticker(sym).fast_info
            price = (getattr(info, "last_price", None)
                     or getattr(info, "regular_market_price", None))
            prev = getattr(info, "previous_close", None)
            change_pct = None
            if price and prev:
                change_pct = (price - prev) / prev * 100
            out[sym] = {
                "price": float(price) if price else None,
                "previous_close": float(prev) if prev else None,
                "currency": getattr(info, "currency", None),
                "day_change_pct": round(change_pct, 2) if change_pct is not None else None,
            }
        except Exception as e:
            out[sym] = {"price": None, "error": str(e)}
    return {"quotes": out}


@mcp.tool()
async def get_restrictions() -> dict:
    """Active trading restrictions (earnings windows, blackouts, restricted
    lists). The PM agent honors these as hard constraints; helpful for the
    user too when picking entry timing.
    """
    try:
        items = await _client().restrictions()
        return {"restrictions": items,
                "count": len(items) if isinstance(items, list) else 0}
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_recent_news(ticker: Optional[str] = None, limit: int = 10) -> dict:
    """Recent news items (high-impact filtered). Optionally filter by ticker.

    Args:
        ticker: Optional ticker.
        limit:  How many items (default 10).
    """
    try:
        feed = await _client().news_feed(ticker=ticker, limit=limit)
        return {"news": feed}
    except ApiError as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Paper trading
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def paper_open(
    ticker: str,
    shares: float,
    entry_price: Optional[float] = None,
    notes: Optional[str] = None,
    related_run_id: Optional[str] = None,
) -> dict:
    """Open a paper trading position. State is persisted server-side, visible
    from every laptop that connects to the same webapp.

    Args:
        ticker:         e.g. "NVDA". Required.
        shares:         How many shares (must be > 0). Fractional ok.
        entry_price:    Optional entry price per share. If omitted, the server
                        uses the current live price from yfinance.
        notes:          Optional free-text rationale.
        related_run_id: Optional — link this paper trade to the analysis that
                        motivated it. Lets you later ask "how did the trades
                        I opened based on May 18 NVDA's Overweight rating
                        perform?"

    Returns the created paper_position row.
    """
    try:
        return await _client().paper_open(
            ticker=ticker, shares=shares, entry_price=entry_price,
            notes=notes, related_run_id=related_run_id,
        )
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def paper_close(position_id: int,
                      exit_price: Optional[float] = None) -> dict:
    """Close an open paper position. If exit_price is omitted, server uses
    current live price.

    Args:
        position_id: The paper position id from paper_list.
        exit_price:  Optional per-share exit price. Default = live price.

    Returns the now-closed paper_position row with closed_at + closing_price set.
    """
    try:
        return await _client().paper_close(position_id, exit_price=exit_price)
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def paper_list(include_closed: bool = False) -> dict:
    """List paper positions. By default returns only open ones; pass
    include_closed=true to get the full book including closed trades.

    For each open row you get cost basis, opened_at, related_run_id, notes.
    For mark-to-market values use paper_summary.
    """
    try:
        rows = await _client().paper_list(include_closed=include_closed)
        return {"positions": rows, "count": len(rows)}
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def paper_summary() -> dict:
    """Mark-to-market across all open paper positions. Returns total cost,
    current value, unrealized P&L, per-position rows with live price and
    unrealized %. Same shape as get_portfolio() but for the paper book.
    """
    try:
        return await _client().paper_summary()
    except ApiError as e:
        return {"error": str(e)}


@mcp.tool()
async def paper_history(limit: int = 50) -> dict:
    """Closed paper trades with realized P&L per trade. Newest first.

    Args:
        limit: Max rows (default 50).
    """
    try:
        rows = await _client().paper_history(limit=limit)
        return {"trades": rows, "count": len(rows)}
    except ApiError as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Run the MCP server.

    Transport is selected by env var so the same package works for both
    Claude Desktop's local stdio spawn and the docker container's HTTP
    surface — no command-line flags needed:

      MCP_TRANSPORT=stdio              (default, local stdio)
      MCP_TRANSPORT=streamable-http    (remote HTTP — used by the NAS container)

    For HTTP transport, MCP_HOST + MCP_PORT control the bind address
    (defaults: 0.0.0.0:8002). FastMCP serves the streamable-http endpoint
    at /mcp on that host:port.
    """
    import os
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http", "sse"):
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "8002"))
        mcp.settings.host = host
        mcp.settings.port = port

        # DNS-rebinding protection: by default the MCP SDK only trusts
        # Host: localhost / 127.0.0.1 / ::1. That's right for stdio servers
        # but causes a 421 "Invalid Host header" for any LAN-reachable
        # deployment. Read allowed hosts/origins from env (comma-separated).
        # Use '*' as a single entry to disable the check entirely.
        from mcp.server.transport_security import TransportSecuritySettings
        defaults_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
        defaults_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
        extra_hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
        extra_origins = [o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
        disable = os.environ.get("MCP_DISABLE_DNS_REBINDING_PROTECTION", "").lower() in ("1", "true", "yes")
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=not disable,
            allowed_hosts=defaults_hosts + extra_hosts,
            allowed_origins=defaults_origins + extra_origins,
        )

        logger.info(
            "tradingagents-mcp starting (transport=%s, %s:%d, allowed_hosts=%s)",
            transport, host, port, defaults_hosts + extra_hosts,
        )
        # 'streamable-http' is the canonical name in current MCP SDK;
        # accept 'http' as an alias.
        canonical = "streamable-http" if transport in ("http", "streamable-http") else "sse"
        mcp.run(transport=canonical)
    else:
        logger.info("tradingagents-mcp starting (transport=stdio)")
        mcp.run()


if __name__ == "__main__":
    main()
