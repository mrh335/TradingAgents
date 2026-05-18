"""Stock discovery — sector gaps + peer suggestions + (placeholder) screener.

Three sub-views the frontend renders as tabs on /discover:

1. **Sector gaps** — compare the user's portfolio sector mix to SPY's
   approximate sector weights and surface under/overweights, plus
   suggested tickers in the underweight sectors.
2. **Peer suggestions** — for each ticker the user owns, return a
   curated list of comparable names (same sector, similar size /
   business model).
3. **Screener** (placeholder) — filter the universe by P/E, dividend
   yield, market cap, beta. Returns a static "coming soon" payload for
   now; backend is wired so the frontend can light up the UI without a
   second backend change.

All data sources here are static / hardcoded so the page loads instantly
without yfinance roundtrips on every render. Real-time enrichment can
be layered in later as a separate endpoint.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from gui import storage
from service.routers.dashboard import _sector_for as sector_for

router = APIRouter(prefix="/discover", tags=["discover"])


# SPY's approximate sector weights (rounded). These shift over time but
# the deltas don't matter much for "where am I gapped" analysis.
SPY_SECTOR_WEIGHTS: Dict[str, float] = {
    "Technology": 30.0,
    "Financials": 13.0,
    "Healthcare": 12.0,
    "Consumer Discretionary": 11.0,
    "Communication Services": 9.0,
    "Industrials": 8.0,
    "Consumer Staples": 6.0,
    "Energy": 4.0,
    "Utilities": 3.0,
    "Real Estate": 2.0,
    "Materials": 2.0,
}


# Hand-curated peer map. Keys are tickers; values are 3-5 peer dicts.
PEER_MAP: Dict[str, List[Dict[str, str]]] = {
    "NVDA": [
        {"ticker": "AMD", "rationale": "GPU + CPU competitor, AI accelerator angle"},
        {"ticker": "AVGO", "rationale": "Custom AI silicon + networking chips"},
        {"ticker": "ASML", "rationale": "EUV lithography — picks-and-shovels"},
        {"ticker": "ARM", "rationale": "CPU architecture licensing, edge-AI"},
        {"ticker": "TSM", "rationale": "Contract foundry — actually makes NVDA chips"},
    ],
    "AMD": [
        {"ticker": "NVDA", "rationale": "AI accelerator leader"},
        {"ticker": "INTC", "rationale": "x86 competitor + foundry comeback"},
        {"ticker": "AVGO", "rationale": "Custom silicon + networking"},
        {"ticker": "QCOM", "rationale": "Mobile + edge AI chips"},
    ],
    "INTC": [
        {"ticker": "AMD", "rationale": "Direct x86 server + client competitor"},
        {"ticker": "NVDA", "rationale": "Data-center share leader"},
        {"ticker": "TSM", "rationale": "Foundry competitor"},
        {"ticker": "QCOM", "rationale": "Edge / mobile compute"},
    ],
    "AAPL": [
        {"ticker": "MSFT", "rationale": "Trillion-cap club, less hardware-cyclical"},
        {"ticker": "GOOGL", "rationale": "Mega-cap, more advertising-tied"},
        {"ticker": "META", "rationale": "Mega-cap with VR/AR overlap"},
    ],
    "MSFT": [
        {"ticker": "AMZN", "rationale": "Cloud (Azure vs AWS) head-to-head"},
        {"ticker": "GOOGL", "rationale": "AI + cloud (GCP) competitor"},
        {"ticker": "ORCL", "rationale": "Enterprise software incumbent"},
    ],
    "GOOGL": [
        {"ticker": "MSFT", "rationale": "Cloud + AI (Copilot vs Gemini)"},
        {"ticker": "META", "rationale": "Advertising duopoly"},
        {"ticker": "AMZN", "rationale": "Cloud competitor (GCP vs AWS)"},
    ],
    "META": [
        {"ticker": "GOOGL", "rationale": "Advertising duopoly partner"},
        {"ticker": "SNAP", "rationale": "Social media smaller-cap"},
        {"ticker": "PINS", "rationale": "Visual social discovery"},
    ],
    "AMZN": [
        {"ticker": "MSFT", "rationale": "AWS vs Azure cloud rivalry"},
        {"ticker": "GOOGL", "rationale": "Cloud (GCP) + advertising overlap"},
        {"ticker": "WMT", "rationale": "Retail competitor with grocery + e-commerce"},
        {"ticker": "COST", "rationale": "Membership-warehouse retail comp"},
    ],
    "TSLA": [
        {"ticker": "RIVN", "rationale": "EV pure-play, pickup/SUV focus"},
        {"ticker": "LCID", "rationale": "Luxury EV competitor"},
        {"ticker": "BYDDY", "rationale": "China EV leader, global expansion"},
        {"ticker": "F", "rationale": "Legacy auto with growing EV mix"},
    ],
    "RIVN": [
        {"ticker": "TSLA", "rationale": "EV market leader, pickup overlap"},
        {"ticker": "LCID", "rationale": "Pure-play EV peer, similar capital-burn"},
        {"ticker": "F", "rationale": "Lightning + Mustang Mach-E direct competitor"},
    ],
    "LCID": [
        {"ticker": "TSLA", "rationale": "EV leader, luxury overlap with Model S/X"},
        {"ticker": "RIVN", "rationale": "Pure-play EV peer"},
        {"ticker": "NIO", "rationale": "China luxury EV"},
    ],
    "PG": [
        {"ticker": "KO", "rationale": "Mega-cap consumer staple, defensive"},
        {"ticker": "PEP", "rationale": "Consumer staple + snacks"},
        {"ticker": "CL", "rationale": "Personal care direct competitor"},
        {"ticker": "WMT", "rationale": "Retail channel that sells PG products"},
    ],
    "PYPL": [
        {"ticker": "V", "rationale": "Payment rails (much larger)"},
        {"ticker": "MA", "rationale": "Payment network"},
        {"ticker": "SQ", "rationale": "Block (Square + Cash App), direct competitor"},
    ],
    "MOD": [
        {"ticker": "WCC", "rationale": "Industrial distribution"},
        {"ticker": "CARR", "rationale": "Larger HVAC peer (Carrier Global)"},
        {"ticker": "JCI", "rationale": "Building systems / HVAC (Johnson Controls)"},
        {"ticker": "LII", "rationale": "Lennox — HVAC manufacturer"},
    ],
}


# "Fill the gap" entrant picks per sector. Curated toward large-cap,
# liquid, lower-volatility names.
SECTOR_ENTRY_PICKS: Dict[str, List[Dict[str, str]]] = {
    "Healthcare": [
        {"ticker": "UNH", "rationale": "Largest health insurer; defensive growth"},
        {"ticker": "LLY", "rationale": "GLP-1 mega-trend leader"},
        {"ticker": "JNJ", "rationale": "Diversified pharma + medtech; dividend aristocrat"},
    ],
    "Financials": [
        {"ticker": "JPM", "rationale": "Largest US bank, fortress balance sheet"},
        {"ticker": "BRK.B", "rationale": "Berkshire — broad financials + industrials"},
        {"ticker": "V", "rationale": "Payment-network duopoly, high margins"},
    ],
    "Utilities": [
        {"ticker": "NEE", "rationale": "Largest US utility + renewable energy leader"},
        {"ticker": "DUK", "rationale": "Regulated electric utility, dividend"},
        {"ticker": "SO", "rationale": "Southeast US regulated utility"},
    ],
    "Energy": [
        {"ticker": "XOM", "rationale": "Integrated oil major; strong balance sheet"},
        {"ticker": "CVX", "rationale": "Integrated oil major + dividend"},
        {"ticker": "EOG", "rationale": "Premier shale operator with capital discipline"},
    ],
    "Consumer Staples": [
        {"ticker": "COST", "rationale": "Membership warehouse — defensive growth"},
        {"ticker": "KO", "rationale": "Beverage mega-brand, dividend aristocrat"},
        {"ticker": "WMT", "rationale": "Retail giant + grocery; defensive"},
    ],
    "Industrials": [
        {"ticker": "CAT", "rationale": "Heavy machinery; construction + mining cycle"},
        {"ticker": "GE", "rationale": "Aerospace + healthcare spinoff complete"},
        {"ticker": "RTX", "rationale": "Defense + aerospace"},
    ],
    "Real Estate": [
        {"ticker": "PLD", "rationale": "Industrial logistics REIT (e-commerce warehouses)"},
        {"ticker": "AMT", "rationale": "Cell-tower REIT, 5G demand"},
        {"ticker": "EQIX", "rationale": "Data-center REIT, AI capex tailwind"},
    ],
    "Materials": [
        {"ticker": "LIN", "rationale": "Industrial gases — picks-and-shovels"},
        {"ticker": "APD", "rationale": "Industrial gases peer"},
        {"ticker": "FCX", "rationale": "Copper miner; electrification beneficiary"},
    ],
    "Communication Services": [
        {"ticker": "GOOGL", "rationale": "Search advertising + cloud"},
        {"ticker": "META", "rationale": "Social advertising"},
        {"ticker": "NFLX", "rationale": "Streaming + ad-tier"},
    ],
    "Consumer Discretionary": [
        {"ticker": "AMZN", "rationale": "E-commerce + cloud"},
        {"ticker": "HD", "rationale": "Home Depot — housing-cycle exposure"},
        {"ticker": "MCD", "rationale": "Defensive consumer growth + dividends"},
    ],
    "Technology": [
        {"ticker": "MSFT", "rationale": "Mega-cap tech anchor"},
        {"ticker": "AVGO", "rationale": "Diversified semis + software"},
        {"ticker": "ORCL", "rationale": "Enterprise software + cloud"},
    ],
}


class SectorGapRow(BaseModel):
    sector: str
    portfolio_pct: float
    benchmark_pct: float
    gap_pct: float
    underweight: bool
    suggested_tickers: List[Dict[str, str]] = []


class SectorGapsResponse(BaseModel):
    portfolio_total_basis: float
    sector_rows: List[SectorGapRow]
    biggest_underweights: List[str]


@router.get("/sector-gaps", response_model=SectorGapsResponse)
def sector_gaps() -> SectorGapsResponse:
    positions = storage.list_positions(include_closed=False)
    by_sector: Dict[str, float] = {}
    total = 0.0
    for p in positions:
        sector = sector_for(p["ticker"])
        basis = float(p["shares"]) * float(p["cost_basis_per_share"])
        by_sector[sector] = by_sector.get(sector, 0.0) + basis
        total += basis

    all_sectors = sorted(set(SPY_SECTOR_WEIGHTS) | set(by_sector))
    rows: List[SectorGapRow] = []
    for sector in all_sectors:
        port_pct = (by_sector.get(sector, 0.0) / total * 100) if total > 0 else 0.0
        bench_pct = SPY_SECTOR_WEIGHTS.get(sector, 0.0)
        gap = port_pct - bench_pct
        underweight = gap < -3.0
        suggested = SECTOR_ENTRY_PICKS.get(sector, []) if underweight else []
        rows.append(SectorGapRow(
            sector=sector,
            portfolio_pct=round(port_pct, 1),
            benchmark_pct=bench_pct,
            gap_pct=round(gap, 1),
            underweight=underweight,
            suggested_tickers=suggested,
        ))
    rows.sort(key=lambda r: (0 if r.underweight else 1, r.gap_pct, r.sector))

    biggest_underweights: List[str] = []
    for row in rows:
        if row.underweight and row.suggested_tickers:
            biggest_underweights.append(row.suggested_tickers[0]["ticker"])

    return SectorGapsResponse(
        portfolio_total_basis=round(total, 2),
        sector_rows=rows,
        biggest_underweights=biggest_underweights[:5],
    )


class PeerSuggestion(BaseModel):
    base_ticker: str
    base_sector: str
    peers: List[Dict[str, str]]


class PeersResponse(BaseModel):
    suggestions: List[PeerSuggestion]


@router.get("/peers", response_model=PeersResponse)
def peers(ticker: Optional[str] = None) -> PeersResponse:
    if ticker:
        t = ticker.upper()
        return PeersResponse(suggestions=[
            PeerSuggestion(base_ticker=t, base_sector=sector_for(t),
                           peers=PEER_MAP.get(t, []))
        ])
    seen: set = set()
    suggestions: List[PeerSuggestion] = []
    for p in storage.list_positions(include_closed=False):
        t = (p["ticker"] or "").upper()
        if not t or t in seen:
            continue
        seen.add(t)
        peers_list = PEER_MAP.get(t, [])
        if peers_list:
            suggestions.append(PeerSuggestion(
                base_ticker=t, base_sector=sector_for(t), peers=peers_list,
            ))
    return PeersResponse(suggestions=suggestions)


class ScreenerResponse(BaseModel):
    status: str
    message: str
    available_filters: List[str]


@router.get("/screener", response_model=ScreenerResponse)
def screener() -> ScreenerResponse:
    return ScreenerResponse(
        status="not_implemented",
        message=(
            "Full screener (filter universe by P/E, dividend yield, market "
            "cap, beta) is a future build. For now, use the sector-gaps tab "
            "to find suggested tickers in underweight sectors, or the peers "
            "tab to expand around what you own."
        ),
        available_filters=["market_cap", "pe_ratio", "dividend_yield",
                           "beta", "52_week_high_pct", "sector"],
    )
