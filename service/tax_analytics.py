"""Tax-aware de-risking engine.

Pure Python (stdlib only) so every calculation is unit-tested locally with
no network, no numpy, no FastAPI. The router fetches real tax lots from the
financial planner (GET /api/investment-ledger/lots) and hands plain dicts in;
this module does the math.

Concepts (audience = an engineer, not a tax pro):
* Tax lot — one purchase: N shares, a per-share cost basis, an acquired date,
  and a term (long if held >1 year, else short).
* Realized gain — (price - basis) x shares, only when you SELL. Long-term
  gains are taxed lower than short-term.
* Lot selection — when you sell part of a position you choose which shares.
  HIFO (highest-cost first) realizes the least gain -> least tax.
* Loss harvesting — selling an underwater lot realizes a loss that offsets
  gains 1:1, lowering the bill.
* Charitable donation of appreciated shares — give long-term shares and nobody
  pays the cap-gains tax; you also deduct fair-market value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class TaxRates:
    """Combined marginal rates (federal + state + NIIT already summed).

    Defaults = California top bracket:
      long-term  = 20% fed + 3.8% NIIT + 13.3% CA = 37.1%
      short-term = 37% fed + 3.8% NIIT + 13.3% CA = 54.1%
    Ordinary is used to value a charitable deduction.
    """
    long_term: float = 0.371
    short_term: float = 0.541
    ordinary: float = 0.541


RATE_PRESETS: Dict[str, Dict[str, float]] = {
    "ca_top":               {"long_term": 0.371, "short_term": 0.541, "ordinary": 0.541},
    "ca_mid":               {"long_term": 0.282, "short_term": 0.452, "ordinary": 0.452},
    "fed_top_notax_state":  {"long_term": 0.238, "short_term": 0.408, "ordinary": 0.408},
    "fed_15_notax_state":   {"long_term": 0.188, "short_term": 0.320, "ordinary": 0.320},
}


@dataclass
class Lot:
    symbol: str
    shares: float
    cost_basis_per_share: float
    acquired_date: str          # ISO yyyy-mm-dd (display / tie-breaks)
    term: str                   # "long" | "short"
    account: str = ""
    plan_type: str = ""         # RSU | ESPP | Brokerage | ...

    def market_value(self, price: float) -> float:
        return self.shares * price

    def embedded_gain(self, price: float) -> float:
        return (price - self.cost_basis_per_share) * self.shares


def _compute_term(acquired_iso: str, today_iso: str = "") -> str:
    """Long if held >1 year, else short. The planner serializes term=None, so
    we derive it from the acquisition date. today_iso is injectable for tests.
    """
    if not acquired_iso:
        return "long"
    try:
        from datetime import date
        ay, am, ad = (int(x) for x in acquired_iso[:10].split("-"))
        acq = date(ay, am, ad)
        if today_iso:
            ty, tm, td = (int(x) for x in today_iso[:10].split("-"))
            today = date(ty, tm, td)
        else:
            today = date.today()
        return "long" if (today - acq).days > 365 else "short"
    except Exception:
        return "long"


def lot_from_planner(d: dict, today_iso: str = "") -> Optional[Lot]:
    """Build a Lot from a planner /investment-ledger/lots item. Returns None
    for closed/zero lots so callers can filter.

    The planner's _lot() serializer names the open-share field ``shares_remaining``
    (NOT remaining_shares / shares_remaining_this_lot) and always returns
    ``term: None`` — so we read the right field and DERIVE term from the
    purchase date. (Getting either wrong silently zeroes the whole feature or
    understates short-term tax.)
    """
    # The planner often leaves shares_remaining_this_lot NULL and only fills
    # shares_acquired/shares_sold, so fall back to acquired - sold (matching
    # the planner's own _shares_remaining helper). Getting this wrong rejects
    # every open lot and silently empties the whole feature.
    rem = d.get("shares_remaining_this_lot")
    if rem is None:
        rem = d.get("shares_remaining")
    if rem is None:
        rem = d.get("remaining_shares")
    if rem is None:
        acq = float(d.get("shares_acquired") or 0)
        sold = float(d.get("shares_sold") or 0)
        rem = acq - sold
    rem = float(rem or 0)
    if rem <= 0 or d.get("sale_date"):
        return None
    acquired = str(d.get("purchase_date") or d.get("grant_date") or "")[:10]
    term = (d.get("term") or "").lower() or _compute_term(acquired, today_iso)
    return Lot(
        symbol=(d.get("symbol") or "").upper(),
        shares=rem,
        cost_basis_per_share=float(d.get("cost_basis_per_share") or 0),
        acquired_date=acquired,
        term=term,
        account=d.get("account_label") or "",
        plan_type=d.get("plan_type") or "",
    )


def reconcile_lots_to_totals(
    lots: List[Lot], authoritative_shares: float, authoritative_cost: Optional[float] = None,
) -> List[Lot]:
    """Scale a symbol's lots so their shares sum to the planner's authoritative
    current share count (from the consolidated /summary view).

    WHY: the raw lot ledger mixes Purchased / Sold / Split / Transfer rows and
    often leaves shares_remaining null, so naively summing acquisition rows
    over-counts a position (e.g. 8,338 AAPL sh of historical lots vs 6,665
    actually held). The consolidated /summary nets all of that correctly, but
    only gives a blended basis. So we keep the *shape* of the lot distribution
    (relative sizes, per-lot basis, acquired dates -> drives HIFO + term) and
    rescale it to the *authoritative* total. The result reconciles to the real
    book while preserving lot-level tax optimization.

    If authoritative_cost is given and the scaled cost basis drifts from it,
    each lot's basis is nudged by a uniform factor so total cost matches too —
    keeping per-share gains honest against the real embedded gain.
    """
    held = [l for l in lots if l.shares > 0]
    raw_total = sum(l.shares for l in held)
    if raw_total <= 0 or authoritative_shares <= 0:
        return []
    share_factor = authoritative_shares / raw_total
    scaled = [
        Lot(
            symbol=l.symbol,
            shares=l.shares * share_factor,
            cost_basis_per_share=l.cost_basis_per_share,
            acquired_date=l.acquired_date,
            term=l.term,
            account=l.account,
            plan_type=l.plan_type,
        )
        for l in held
    ]
    if authoritative_cost and authoritative_cost > 0:
        scaled_cost = sum(l.shares * l.cost_basis_per_share for l in scaled)
        if scaled_cost > 0:
            cost_factor = authoritative_cost / scaled_cost
            for l in scaled:
                l.cost_basis_per_share *= cost_factor
    return scaled


def _sort_key(method: str, price: float):
    m = method.lower()
    if m in ("hifo", "mingain"):   # highest cost first -> least gain
        return lambda l: -l.cost_basis_per_share
    if m == "fifo":                # oldest first
        return lambda l: l.acquired_date
    if m == "lifo":                # newest first
        return lambda l: _neg_date(l.acquired_date)
    if m == "maxloss":             # most-negative gain-per-share first
        return lambda l: (price - l.cost_basis_per_share)
    return lambda l: -l.cost_basis_per_share


def _neg_date(iso: str) -> str:
    return "".join(chr(255 - ord(c)) if c.isdigit() else c for c in iso)


@dataclass
class SoldSlice:
    lot: Lot
    shares_sold: float

    def proceeds(self, price: float) -> float:
        return self.shares_sold * price

    def gain(self, price: float) -> float:
        return (price - self.lot.cost_basis_per_share) * self.shares_sold


def select_lots_for_value(
    lots: List[Lot], price: float, target_value: float, method: str = "hifo"
) -> List[SoldSlice]:
    """Pick lots (with partial fills) to raise ~target_value in proceeds."""
    if target_value <= 0:
        return []
    ordered = sorted(lots, key=_sort_key(method, price))
    out: List[SoldSlice] = []
    raised = 0.0
    for lot in ordered:
        if raised >= target_value - 1e-6:
            break
        lot_value = lot.market_value(price)
        if lot_value <= 0:
            continue
        need = target_value - raised
        if lot_value <= need + 1e-6:
            out.append(SoldSlice(lot, lot.shares)); raised += lot_value
        else:
            shares = need / price
            out.append(SoldSlice(lot, shares)); raised += shares * price
    return out


@dataclass
class SaleResult:
    proceeds: float = 0.0
    long_term_gain: float = 0.0
    short_term_gain: float = 0.0
    long_term_loss: float = 0.0
    short_term_loss: float = 0.0
    tax: float = 0.0
    net_cash: float = 0.0
    shares_sold: float = 0.0
    slices: List[dict] = field(default_factory=list)

    @property
    def net_gain(self) -> float:
        return (self.long_term_gain + self.long_term_loss
                + self.short_term_gain + self.short_term_loss)


def compute_sale(slices: List[SoldSlice], price: float, rates: TaxRates) -> SaleResult:
    """Tax on a set of sold slices. Losses net against same-term gains first,
    then cross-term; a remaining net loss yields NEGATIVE tax (a saving that
    offsets gains elsewhere)."""
    res = SaleResult()
    lt = st = 0.0
    for s in slices:
        g = s.gain(price)
        res.proceeds += s.proceeds(price)
        res.shares_sold += s.shares_sold
        if s.lot.term == "long":
            lt += g
        else:
            st += g
        res.slices.append({
            "symbol": s.lot.symbol,
            "shares": round(s.shares_sold, 4),
            "cost_basis_per_share": s.lot.cost_basis_per_share,
            "acquired_date": s.lot.acquired_date,
            "term": s.lot.term,
            "plan_type": s.lot.plan_type,
            "proceeds": round(s.proceeds(price), 2),
            "gain": round(g, 2),
        })
    res.long_term_gain = max(0.0, lt)
    res.long_term_loss = min(0.0, lt)
    res.short_term_gain = max(0.0, st)
    res.short_term_loss = min(0.0, st)

    net_lt, net_st = lt, st
    if net_lt < 0 and net_st > 0:
        applied = min(-net_lt, net_st); net_st -= applied; net_lt += applied
    elif net_st < 0 and net_lt > 0:
        applied = min(-net_st, net_lt); net_lt -= applied; net_st += applied

    res.tax = net_lt * rates.long_term + net_st * rates.short_term
    res.net_cash = res.proceeds - res.tax
    return res


def position_summary(lots: List[Lot], price: float) -> dict:
    shares = sum(l.shares for l in lots)
    value = shares * price
    cost = sum(l.shares * l.cost_basis_per_share for l in lots)

    def bucket(term: str, want_gain: bool) -> float:
        total = 0.0
        for l in lots:
            g = l.embedded_gain(price)
            if l.term == term and (g > 0) == want_gain:
                total += g
        return total

    return {
        "shares": round(shares, 4),
        "value": round(value, 2),
        "cost": round(cost, 2),
        "embedded_gain": round(value - cost, 2),
        "long_term_gain": round(bucket("long", True), 2),
        "short_term_gain": round(bucket("short", True), 2),
        "long_term_loss": round(bucket("long", False), 2),
        "short_term_loss": round(bucket("short", False), 2),
        "lot_count": len(lots),
    }


def derisk_position(
    lots: List[Lot], price: float, target_value: float, method: str, rates: TaxRates,
) -> dict:
    slices = select_lots_for_value(lots, price, target_value, method)
    sale = compute_sale(slices, price, rates)
    drag = (sale.tax / sale.proceeds) if sale.proceeds > 0 else 0.0
    return {
        "method": method,
        "target_value": round(target_value, 2),
        "proceeds": round(sale.proceeds, 2),
        "shares_sold": round(sale.shares_sold, 4),
        "realized_gain": round(sale.net_gain, 2),
        "long_term_gain": round(sale.long_term_gain, 2),
        "short_term_gain": round(sale.short_term_gain, 2),
        "tax": round(sale.tax, 2),
        "net_cash": round(sale.net_cash, 2),
        "tax_drag_pct": round(drag, 4),
        "slices": sale.slices,
    }


def compare_methods(
    lots: List[Lot], price: float, target_value: float, rates: TaxRates,
    methods: Optional[List[str]] = None,
) -> List[dict]:
    methods = methods or ["hifo", "fifo", "lifo"]
    return [derisk_position(lots, price, target_value, m, rates) for m in methods]


def harvest_losses(all_lots: List[Lot], prices: Dict[str, float], rates: TaxRates) -> dict:
    """Every lot currently at a loss + the tax it would offset (same-term rate)."""
    harvest = []
    total_lt = total_st = 0.0
    for l in all_lots:
        p = prices.get(l.symbol, 0.0)
        g = l.embedded_gain(p)
        if g < 0:
            harvest.append({
                "symbol": l.symbol, "shares": round(l.shares, 4),
                "acquired_date": l.acquired_date, "term": l.term,
                "loss": round(g, 2),
            })
            if l.term == "long":
                total_lt += g
            else:
                total_st += g
    tax_saving = -(total_lt * rates.long_term + total_st * rates.short_term)
    return {
        "harvestable_lots": harvest,
        "long_term_loss": round(total_lt, 2),
        "short_term_loss": round(total_st, 2),
        "total_loss": round(total_lt + total_st, 2),
        "tax_offset_value": round(tax_saving, 2),
    }


def charitable_donation(
    lots: List[Lot], price: float, donate_value: float, rates: TaxRates,
) -> dict:
    """Donate ~donate_value of appreciated long-term shares: cap-gains tax on
    them is avoided entirely AND you deduct fair-market value. Donates the
    lowest-basis (most-appreciated) long-term lots first."""
    lt_lots = [l for l in lots if l.term == "long" and l.embedded_gain(price) > 0]
    ordered = sorted(lt_lots, key=lambda l: l.cost_basis_per_share)
    slices: List[SoldSlice] = []
    raised = 0.0
    for lot in ordered:
        if raised >= donate_value - 1e-6:
            break
        lot_value = lot.market_value(price)
        need = donate_value - raised
        if lot_value <= need + 1e-6:
            slices.append(SoldSlice(lot, lot.shares)); raised += lot_value
        else:
            sh = need / price
            slices.append(SoldSlice(lot, sh)); raised += sh * price
    gain_avoided = sum(s.gain(price) for s in slices)
    return {
        "donated_value": round(raised, 2),
        "shares_donated": round(sum(s.shares_sold for s in slices), 4),
        "embedded_gain_avoided": round(gain_avoided, 2),
        "cap_gains_tax_avoided": round(gain_avoided * rates.long_term, 2),
        "income_deduction_value": round(raised * rates.ordinary, 2),
        "total_tax_benefit": round(gain_avoided * rates.long_term + raised * rates.ordinary, 2),
        "slices": [
            {"acquired_date": s.lot.acquired_date, "shares": round(s.shares_sold, 4),
             "cost_basis_per_share": s.lot.cost_basis_per_share,
             "gain_avoided": round(s.gain(price), 2)}
            for s in slices
        ],
    }
