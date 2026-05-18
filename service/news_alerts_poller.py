"""News-alerts poller — fetches yfinance news for watchlist + positions,
scores impact, persists new items to ``news_alerts``.

Runs as an asyncio task spawned in app startup. 15-minute interval.

Scoring heuristic (cheap deterministic):
- HIGH impact (score 100):  earnings, guidance, fda, merger, acquisition,
  buyout, settlement, lawsuit, sec, recall, bankruptcy
- MEDIUM impact (score 50): upgrade, downgrade, target, analyst, contract,
  partnership, layoffs, restructuring, dividend
- LOW impact (score 10):    anything else, or matches only general "news"
- Recency bonus: + (24h: 30 pts, 72h: 10 pts)
- Position-size bonus: +20 if the user holds a meaningful (≥5%) position

Dedup via hash of (ticker + headline) — re-runs the same article won't
spam.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set

from gui import storage

logger = logging.getLogger(__name__)


HIGH_KEYWORDS = (
    "earnings", "guidance", "fda", "approval", "merger", "acquisition", "buyout",
    "settlement", "lawsuit", "sec ", "recall", "bankruptcy", "shutdown",
    "fired", "ceo step", "ceo departs", "investigation",
)
MEDIUM_KEYWORDS = (
    "upgrade", "downgrade", "target", "analyst", "contract", "partnership",
    "layoffs", "restructuring", "dividend", "split", "spinoff", "stake",
)


def _classify(text: str) -> tuple[str, int, str]:
    """Return (impact_label, score, matched_keywords_csv)."""
    low = text.lower()
    matched_high: list = [k for k in HIGH_KEYWORDS if k in low]
    matched_med:  list = [k for k in MEDIUM_KEYWORDS if k in low]
    if matched_high:
        return "high", 100, ",".join(matched_high)
    if matched_med:
        return "medium", 50, ",".join(matched_med)
    return "low", 10, ""


def _recency_bonus(published_at: Optional[datetime]) -> int:
    if not published_at:
        return 0
    now = datetime.now(timezone.utc) if published_at.tzinfo else datetime.utcnow()
    age = now - published_at
    if age <= timedelta(hours=24):
        return 30
    if age <= timedelta(hours=72):
        return 10
    return 0


def _position_bonus(ticker: str, position_weights: Dict[str, float]) -> int:
    return 20 if position_weights.get(ticker, 0) >= 0.05 else 0


def _hash_key(ticker: str, headline: str, url: str = "") -> str:
    raw = (ticker + "|" + (url or headline)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _safe_parse_dt(value: Any) -> Optional[datetime]:
    """yfinance returns publish times as epoch seconds or ISO strings
    depending on version."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        s = str(value)
        if s.isdigit():
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError, OSError):
        return None


def _portfolio_weights() -> Dict[str, float]:
    """Build {ticker: fraction-of-book-at-cost} for the position bonus."""
    try:
        positions = storage.list_positions(include_closed=False)
    except Exception:
        return {}
    by_ticker: Dict[str, float] = {}
    total = 0.0
    for p in positions:
        ticker = (p.get("ticker") or "").upper()
        basis = float(p.get("shares") or 0) * float(p.get("cost_basis_per_share") or 0)
        by_ticker[ticker] = by_ticker.get(ticker, 0.0) + basis
        total += basis
    return {t: (v / total) for t, v in by_ticker.items()} if total > 0 else {}


def _interesting_tickers() -> Set[str]:
    tickers: Set[str] = set()
    try:
        for p in storage.list_positions(include_closed=False):
            t = (p.get("ticker") or "").upper()
            if t:
                tickers.add(t)
    except Exception:
        pass
    try:
        for w in storage.list_watchlist():
            t = (w.get("ticker") or "").upper()
            if t:
                tickers.add(t)
    except Exception:
        pass
    return tickers


def _fetch_news_for(ticker: str) -> list[dict]:
    """yfinance Ticker.news → list of dicts. Returns [] on any failure."""
    try:
        import yfinance as yf
        items = yf.Ticker(ticker).news or []
        return list(items)
    except Exception as e:
        logger.warning(f"news fetch {ticker}: {e}")
        return []


def _tick() -> int:
    """One pass: fetch news for each interesting ticker, score, persist.

    Returns the count of NEW alerts inserted (after dedupe).
    """
    weights = _portfolio_weights()
    tickers = _interesting_tickers()
    if not tickers:
        return 0

    inserted = 0
    for ticker in sorted(tickers):
        for item in _fetch_news_for(ticker):
            # yfinance shape: {title, link, publisher, providerPublishTime, type, ...}
            # or newer shape: {content: {title, canonicalUrl, pubDate, provider:{...}}}
            title = item.get("title") or (item.get("content") or {}).get("title")
            if not title:
                continue
            url = item.get("link") or (item.get("content") or {}).get("canonicalUrl", {}).get("url")
            published = item.get("providerPublishTime") or (item.get("content") or {}).get("pubDate")
            published_dt = _safe_parse_dt(published)
            source = item.get("publisher") or (
                (item.get("content") or {}).get("provider") or {}
            ).get("displayName")

            scored_text = f"{title} {url or ''}".lower()
            impact, base_score, keywords = _classify(scored_text)
            score = base_score + _recency_bonus(published_dt) + _position_bonus(ticker, weights)

            try:
                storage.add_news_alert(
                    ticker=ticker,
                    headline=title[:300],
                    url=str(url) if url else None,
                    published_at=published_dt.isoformat() if published_dt else None,
                    source=str(source) if source else None,
                    impact=impact,
                    impact_score=score,
                    keywords=keywords or None,
                    hash_key=_hash_key(ticker, title, str(url or "")),
                )
                inserted += 1
            except Exception as e:
                logger.warning(f"news insert {ticker}: {e}")
                continue
    return inserted


async def run(interval_seconds: int = 900) -> None:
    """Run the news-alerts poller loop forever.

    Each tick calls yfinance synchronously per ticker (~3-5s per call).
    With ~10 tickers that's ~30-50s of blocked I/O, which would freeze
    the FastAPI event loop if we called _tick() directly. So we offload
    to a worker thread via asyncio.to_thread — other request handlers
    continue serving while the poll runs.

    Also defer the FIRST tick by 30s after startup so the api becomes
    responsive before the poller does any work.
    """
    logger.info("news_alerts_poller started (interval=%ds)", interval_seconds)
    # Initial delay so the api can finish handling startup traffic
    # before we block a worker thread for ~30s on yfinance fetches.
    await asyncio.sleep(30)
    while True:
        try:
            n = await asyncio.to_thread(_tick)
            if n:
                logger.info("news_alerts_poller inserted %d new alerts", n)
        except Exception as e:
            logger.exception("news_alerts_poller tick failed: %s", e)
        await asyncio.sleep(interval_seconds)
