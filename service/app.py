"""FastAPI application factory + entrypoint.

Run from the repo root with:
    uvicorn service.app:app --host 0.0.0.0 --port 8000 --reload   # dev
    uvicorn service.app:app --host 0.0.0.0 --port 8000            # prod

Or via the console script (after pip install '.[service]'):
    tradingagents-api
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gui import storage
from service.runner_pool import pool
from service.streaming import broadcaster
from service.routers import (
    backtest as backtest_router,
    batches,
    briefs,
    calendar as calendar_router,
    charts as charts_router,
    chat,
    dashboard as dashboard_router,
    discover as discover_router,
    earnings as earnings_router,
    exports,
    health,
    holders as holders_router,
    macro as macro_router,
    memory,
    news_alerts as news_alerts_router,
    news_feed,
    notes,
    planner,
    portfolio,
    portfolio_metrics as portfolio_metrics_router,
    restrictions as restrictions_router,
    risk as risk_router,
    run_queue as run_queue_router,
    runs,
    schedules as schedules_router,
    settings,
    sidecars as sidecars_router,
    simulation,
    streaming,
    tokens as tokens_router,
    trades as trades_router,
    watchlist,
)

logger = logging.getLogger(__name__)


def _allowed_origins() -> list[str]:
    """Origins allowed for CORS.

    Defaults to common LAN dev origins. Override with CORS_ORIGINS env var
    (comma-separated), e.g. for the deployed Next.js host.
    """
    raw = os.environ.get("CORS_ORIGINS")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        # NAS LAN — adjust via CORS_ORIGINS env var if your NAS IP differs.
        "http://192.168.2.34:3000",
        "http://192.168.2.34",
    ]


app = FastAPI(
    title="TradingAgents API",
    version="0.3.0",
    description=(
        "REST + WebSocket API for the TradingAgents framework. Powers the "
        "Next.js frontend; also usable directly as the integration surface "
        "for any custom client. OpenAPI docs at /docs."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    storage.init_db()
    loop = asyncio.get_running_loop()
    pool.attach_loop(loop)
    broadcaster.start(loop)
    # Pre-warm the broadcaster with watchlist + position tickers so the
    # header strip and dashboard widgets see live prices without waiting
    # for a user to land on a page that subscribes.
    prewarm: set = set()
    try:
        for entry in storage.list_watchlist():
            prewarm.add(entry["ticker"])
    except Exception:
        pass
    try:
        for p in storage.list_positions(include_closed=False):
            if p.get("ticker"):
                prewarm.add(p["ticker"])
    except Exception:
        pass
    for ticker in prewarm:
        try:
            await broadcaster.subscribe("price", ticker)
        except Exception:
            pass
    # Per-ticker auto-run scheduler — ticks every 60s, evaluates each
    # enabled row in ticker_schedules and queues runs when due. See
    # service.scheduler for the loop.
    from service import scheduler as scheduler_service
    loop.create_task(scheduler_service.run(interval_seconds=60))
    # News-alerts poller — ticks every 15 min, fetches yfinance news
    # for watchlist + positions, scores impact, persists new items.
    from service import news_alerts_poller
    loop.create_task(news_alerts_poller.run(interval_seconds=900))
    # 13F holdings poller — refreshes the smart-money-manager filings
    # from SEC EDGAR once a week. Initial delay 60 min so app boot is
    # snappy; 13F data is days-stale anyway.
    from service import holdings_13f_poller
    loop.create_task(holdings_13f_poller.run(interval_seconds=7 * 24 * 3600))
    logger.info("TradingAgents API ready. CORS origins: %s", _allowed_origins())


@app.on_event("shutdown")
async def _shutdown() -> None:
    await broadcaster.stop()


# Routers
app.include_router(health.router)
# Note: batches router registers BEFORE runs.router so its prefix
# /runs/batch wins over runs.router's /runs/{run_id} catch-all.
app.include_router(batches.router)
app.include_router(runs.router)
app.include_router(briefs.router)
app.include_router(chat.router)
app.include_router(notes.router)
app.include_router(settings.router)
app.include_router(memory.router)
app.include_router(charts_router.router)
app.include_router(exports.router)
app.include_router(streaming.router)
app.include_router(watchlist.router)
app.include_router(portfolio.router)
app.include_router(calendar_router.router)
app.include_router(news_feed.router)
app.include_router(simulation.router)
app.include_router(planner.router)
app.include_router(sidecars_router.router)
app.include_router(run_queue_router.router)
app.include_router(restrictions_router.router)
app.include_router(tokens_router.router)
app.include_router(dashboard_router.router)
app.include_router(discover_router.router)
app.include_router(schedules_router.router)
app.include_router(backtest_router.router)
app.include_router(risk_router.router)
app.include_router(trades_router.router)
app.include_router(news_alerts_router.router)
app.include_router(holders_router.router)
app.include_router(portfolio_metrics_router.router)
app.include_router(macro_router.router)
app.include_router(earnings_router.router)


def main() -> int:
    """Console-script entrypoint — ``tradingagents-api``."""
    import uvicorn

    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run("service.app:app", host=host, port=port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
