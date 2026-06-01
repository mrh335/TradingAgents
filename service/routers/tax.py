"""Tax-aware de-risking API.

Pulls real per-lot cost basis from the financial planner, applies the pure
tax engine in service.tax_analytics, and exposes:

    GET  /tax/lots                  — open lots + per-position summary + embedded gains
    POST /tax/derisk                — sell ~$ from a position; tax/net/drag + method compare
    GET  /tax/harvest               — harvestable losses across the book
    POST /tax/charitable            — donate appreciated long-term shares

Live prices come from yfinance (cached). Rates default to California top
bracket; override via a preset id or explicit rates.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import yfinance as yf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from service import planner_client
from service import tax_analytics as tx

router = APIRouter(prefix="/tax", tags=["tax"])


# ---- helpers --------------------------------------------------------------

def _rates(preset: Optional[str], rates: Optional[Dict[str, float]]) -> tx.TaxRates:
    if rates:
        return tx.TaxRates(**{k: float(v) for k, v in rates.items() if k in
                              ("long_term", "short_term", "ordinary")})
    if preset and preset in tx.RATE_PRESETS:
        return tx.TaxRates(**tx.RATE_PRESETS[preset])
    return tx.TaxRates()  # CA top default


def _load_lots() -> List[tx.Lot]:
    """Open lots, RECONCILED to the planner's authoritative consolidated book.

    The raw lot ledger over-counts (historical Purchased/Split/Transfer rows,
    null shares_remaining) — naively it shows ~8,338 AAPL sh / $2.74M and
    phantom symbols. The consolidated /summary nets all that to the real held
    shares + total cost per symbol. We keep the lot-level basis distribution
    (for HIFO + term) but rescale each symbol to its authoritative totals, and
    DROP symbols the consolidated book no longer holds.
    """
    if not planner_client.is_configured():
        raise HTTPException(status_code=412, detail="Planner not configured (PLANNER_API_URL / PLANNER_API_KEY).")
    try:
        raw = planner_client.list_lots()
    except planner_client.PlannerClientError as e:
        raise HTTPException(status_code=502, detail=str(e))

    parsed = [l for l in (tx.lot_from_planner(d) for d in raw) if l is not None]

    # Authoritative current holdings (symbol -> shares, total_cost_basis).
    authoritative: Dict[str, Dict[str, float]] = {}
    try:
        consolidated = planner_client.list_consolidated_holdings().get("holdings") or []
        for h in consolidated:
            sym = (h.get("symbol") or "").upper()
            sh = float(h.get("shares") or 0)
            if sym and sh > 0:
                authoritative[sym] = {
                    "shares": sh,
                    "cost": float(h.get("total_cost_basis") or 0) or None,
                }
    except planner_client.PlannerClientError:
        # If the consolidated view is unavailable, fall back to raw lots
        # (better to show over-counted data than nothing) but flag via logs.
        return parsed

    grouped = _by_symbol(parsed)
    reconciled: List[tx.Lot] = []
    for sym, lots in grouped.items():
        auth = authoritative.get(sym)
        if not auth:
            # Symbol not in the current consolidated book -> not actually held
            # (e.g. fully-sold PYPL still present as historical lots). Drop it.
            continue
        reconciled.extend(
            tx.reconcile_lots_to_totals(lots, auth["shares"], auth.get("cost"))
        )
    return reconciled


def _prices(symbols: List[str]) -> Dict[str, float]:
    syms = sorted(set(s for s in symbols if s))
    if not syms:
        return {}
    out: Dict[str, float] = {}
    try:
        raw = yf.download(syms, period="5d", interval="1d", auto_adjust=True, progress=False)
        close = raw["Close"] if hasattr(raw, "columns") and "Close" in getattr(raw, "columns", []) else raw
        # MultiIndex (many tickers) vs single
        try:
            last = close.ffill().iloc[-1]
        except Exception:
            last = None
        for s in syms:
            try:
                out[s] = float(last[s]) if last is not None else 0.0
            except Exception:
                try:
                    out[s] = float(last)
                except Exception:
                    out[s] = 0.0
    except Exception:
        out = {s: 0.0 for s in syms}
    return out


def _by_symbol(lots: List[tx.Lot]) -> Dict[str, List[tx.Lot]]:
    d: Dict[str, List[tx.Lot]] = {}
    for l in lots:
        d.setdefault(l.symbol, []).append(l)
    return d


# ---- schemas --------------------------------------------------------------

class DeriskRequest(BaseModel):
    symbol: str
    target_value: float = Field(gt=0, description="Dollar amount of the position to sell")
    methods: List[str] = Field(default=["hifo", "fifo", "lifo"])
    rate_preset: Optional[str] = "ca_top"
    rates: Optional[Dict[str, float]] = None
    price: Optional[float] = None  # override live price (e.g. what-if)


class CharitableRequest(BaseModel):
    symbol: str
    donate_value: float = Field(gt=0)
    rate_preset: Optional[str] = "ca_top"
    rates: Optional[Dict[str, float]] = None
    price: Optional[float] = None


# ---- endpoints ------------------------------------------------------------

@router.get("/lots")
def get_lots() -> dict:
    """Open lots grouped by symbol, with live prices, per-position summary,
    and book-wide embedded long/short gain + loss totals."""
    lots = _load_lots()
    prices = _prices([l.symbol for l in lots])
    grouped = _by_symbol(lots)

    positions = []
    tot_lt_gain = tot_st_gain = tot_lt_loss = tot_st_loss = 0.0
    for sym in sorted(grouped):
        p = prices.get(sym, 0.0)
        summ = tx.position_summary(grouped[sym], p)
        summ["symbol"] = sym
        summ["price"] = p
        summ["lots"] = [
            {
                "shares": round(l.shares, 4),
                "cost_basis_per_share": l.cost_basis_per_share,
                "acquired_date": l.acquired_date,
                "term": l.term,
                "plan_type": l.plan_type,
                "account": l.account,
                "market_value": round(l.market_value(p), 2),
                "embedded_gain": round(l.embedded_gain(p), 2),
            }
            for l in sorted(grouped[sym], key=lambda x: -x.embedded_gain(p))
        ]
        positions.append(summ)
        tot_lt_gain += summ["long_term_gain"]
        tot_st_gain += summ["short_term_gain"]
        tot_lt_loss += summ["long_term_loss"]
        tot_st_loss += summ["short_term_loss"]

    total_value = sum(s["value"] for s in positions)
    # Concentration = largest position as % of book.
    top = max(positions, key=lambda s: s["value"], default=None)
    return {
        "positions": positions,
        "total_value": round(total_value, 2),
        "embedded": {
            "long_term_gain": round(tot_lt_gain, 2),
            "short_term_gain": round(tot_st_gain, 2),
            "long_term_loss": round(tot_lt_loss, 2),
            "short_term_loss": round(tot_st_loss, 2),
        },
        "concentration": {
            "symbol": top["symbol"] if top else None,
            "pct": round(top["value"] / total_value * 100, 2) if top and total_value else None,
        },
        "rate_presets": tx.RATE_PRESETS,
    }


@router.post("/derisk")
def derisk(req: DeriskRequest) -> dict:
    lots = [l for l in _load_lots() if l.symbol == req.symbol.upper()]
    if not lots:
        raise HTTPException(status_code=404, detail=f"no open lots for {req.symbol}")
    price = req.price if req.price else _prices([req.symbol.upper()]).get(req.symbol.upper(), 0.0)
    if price <= 0:
        raise HTTPException(status_code=502, detail=f"could not price {req.symbol}")
    rates = _rates(req.rate_preset, req.rates)
    summary = tx.position_summary(lots, price)
    comparison = tx.compare_methods(lots, price, req.target_value, rates, req.methods)
    # Concentration before/after assuming the proceeds leave this symbol.
    return {
        "symbol": req.symbol.upper(),
        "price": price,
        "position": summary,
        "rates": {"long_term": rates.long_term, "short_term": rates.short_term, "ordinary": rates.ordinary},
        "comparison": comparison,
        "best": min(comparison, key=lambda c: c["tax"]) if comparison else None,
    }


@router.get("/harvest")
def harvest(rate_preset: str = "ca_top") -> dict:
    lots = _load_lots()
    prices = _prices([l.symbol for l in lots])
    rates = _rates(rate_preset, None)
    return tx.harvest_losses(lots, prices, rates)


@router.post("/charitable")
def charitable(req: CharitableRequest) -> dict:
    lots = [l for l in _load_lots() if l.symbol == req.symbol.upper()]
    if not lots:
        raise HTTPException(status_code=404, detail=f"no open lots for {req.symbol}")
    price = req.price if req.price else _prices([req.symbol.upper()]).get(req.symbol.upper(), 0.0)
    if price <= 0:
        raise HTTPException(status_code=502, detail=f"could not price {req.symbol}")
    rates = _rates(req.rate_preset, req.rates)
    # Contrast: donating vs selling the same dollar value.
    donate = tx.charitable_donation(lots, price, req.donate_value, rates)
    sell = tx.derisk_position(lots, price, req.donate_value, "hifo", rates)
    return {
        "symbol": req.symbol.upper(),
        "price": price,
        "donate": donate,
        "sell_equivalent": sell,
        "advantage_vs_selling": round(donate["total_tax_benefit"] + sell["tax"], 2),
    }
