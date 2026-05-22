"""Thin async HTTP client for the TradingAgents webapp.

All paths are relative to ``TRADINGAGENTS_API_BASE`` (env var; defaults to
http://192.168.2.34:8001). Errors surface as ApiError with the upstream
status code + body so Claude can show the user a meaningful message
rather than a stack trace.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

DEFAULT_BASE = "http://192.168.2.34:8001"
DEFAULT_TIMEOUT = 15.0


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str, *, url: str = "") -> None:
        super().__init__(f"[{status}] {url}: {message}")
        self.status = status
        self.message = message
        self.url = url


class TradingAgentsClient:
    """Per-request async client over the FastAPI webapp.

    Designed to be cheap to instantiate (each MCP tool call makes one).
    No connection pooling because MCP tool calls are infrequent — the
    overhead is dominated by the request RTT, not connect time.
    """

    def __init__(self, base_url: Optional[str] = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = (base_url
                         or os.environ.get("TRADINGAGENTS_API_BASE")
                         or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.request(method, url, **kwargs)
            except httpx.RequestError as e:
                raise ApiError(0, f"could not reach webapp: {e}", url=url) from e
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise ApiError(resp.status_code, str(body), url=url)
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text

    # ─────────────────────────────────────────────────────────────────────
    # Runs / briefs
    # ─────────────────────────────────────────────────────────────────────
    async def list_runs(self, ticker: Optional[str] = None,
                        limit: int = 20) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker.upper()
        return await self._request("GET", "/runs", params=params)

    async def get_run(self, run_id: str) -> dict:
        return await self._request("GET", f"/runs/{run_id}")

    async def get_brief(self, run_id: str) -> dict:
        # Returns {source, brief, brief_markdown?} per service/routers/briefs.py
        return await self._request("GET", f"/runs/{run_id}/brief")

    # ─────────────────────────────────────────────────────────────────────
    # Portfolio / watchlist
    # ─────────────────────────────────────────────────────────────────────
    async def portfolio_summary(self) -> dict:
        return await self._request("GET", "/portfolio/summary")

    async def portfolio_by_account(self) -> dict:
        return await self._request("GET", "/portfolio/by-account")

    async def watchlist(self) -> list[dict]:
        return await self._request("GET", "/watchlist")

    async def restrictions(self) -> list[dict]:
        return await self._request("GET", "/restrictions")

    async def news_feed(self, ticker: Optional[str] = None,
                        limit: int = 10) -> Any:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker.upper()
        return await self._request("GET", "/news/feed", params=params)

    # ─────────────────────────────────────────────────────────────────────
    # Paper trading
    # ─────────────────────────────────────────────────────────────────────
    async def paper_list(self, include_closed: bool = False) -> list[dict]:
        return await self._request(
            "GET", "/paper/positions",
            params={"include_closed": str(include_closed).lower()},
        )

    async def paper_open(self, ticker: str, shares: float,
                         entry_price: Optional[float] = None,
                         notes: Optional[str] = None,
                         related_run_id: Optional[str] = None,
                         created_by: str = "claude-mcp") -> dict:
        body: dict[str, Any] = {
            "ticker": ticker.upper(),
            "shares": shares,
            "created_by": created_by,
        }
        if entry_price is not None:
            body["entry_price"] = entry_price
        if notes:
            body["notes"] = notes
        if related_run_id:
            body["related_run_id"] = related_run_id
        return await self._request("POST", "/paper/positions", json=body)

    async def paper_close(self, position_id: int,
                          exit_price: Optional[float] = None) -> dict:
        body: dict[str, Any] = {}
        if exit_price is not None:
            body["exit_price"] = exit_price
        return await self._request(
            "POST", f"/paper/positions/{position_id}/close", json=body,
        )

    async def paper_delete(self, position_id: int) -> dict:
        return await self._request("DELETE", f"/paper/positions/{position_id}")

    async def paper_summary(self) -> dict:
        return await self._request("GET", "/paper/summary")

    async def paper_history(self, limit: int = 50) -> list[dict]:
        return await self._request("GET", "/paper/history",
                                   params={"limit": limit})
