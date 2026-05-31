"""Unit tests for the tax-aware de-risking engine (service/tax_analytics.py).

Stdlib-only module, so every number is hand-verifiable. The AAPL fixture
mirrors the user's real lot shape (long-term ESPP/RSU lots + two recent
short-term RSU lots) so the tests double as a sanity check on the strategy.
"""

import pytest

from service import tax_analytics as tx
from service.tax_analytics import Lot, TaxRates


def aapl_lots():
    # (shares, basis, date, term, plan)
    raw = [
        (1860, 182.40, "2024-02-01", "short", "RSU"),
        (400, 168.50, "2024-08-15", "short", "RSU"),
        (520, 155.30, "2023-05-20", "long", "RSU"),
        (800, 95.40, "2021-11-10", "long", "ESPP"),
        (310, 84.20, "2021-03-05", "long", "RSU"),
        (290, 72.10, "2020-09-01", "long", "ESPP"),
        (145.589, 62.30, "2020-02-14", "long", "RSU"),
    ]
    return [Lot("AAPL", s, c, d, t, "acct", p) for s, c, d, t, p in raw]


PRICE = 312.06
CA = TaxRates()  # 37.1% LT / 54.1% ST


def test_rate_presets_valid():
    for k in ("ca_top", "ca_mid", "fed_top_notax_state", "fed_15_notax_state"):
        r = TaxRates(**tx.RATE_PRESETS[k])
        assert 0 < r.long_term < r.short_term <= 1


def test_lot_from_planner_filters_closed_and_zero():
    assert tx.lot_from_planner({"symbol": "X", "remaining_shares": 0}) is None
    assert tx.lot_from_planner({"symbol": "X", "remaining_shares": 10, "sale_date": "2025-01-01"}) is None
    lot = tx.lot_from_planner({
        "symbol": "aapl", "remaining_shares": 100, "cost_basis_per_share": 50,
        "purchase_date": "2020-01-01", "term": "long", "plan_type": "RSU",
        "account_label": "TOD",
    })
    assert lot.symbol == "AAPL" and lot.shares == 100 and lot.term == "long"


def test_position_summary_splits_lt_st():
    s = tx.position_summary(aapl_lots(), PRICE)
    assert s["shares"] == pytest.approx(4325.589, abs=0.01)
    assert s["short_term_gain"] > 250_000
    assert s["long_term_gain"] > 400_000
    assert s["long_term_loss"] == 0


def test_hifo_realizes_less_gain_than_fifo():
    target = 400_000.0
    hifo = tx.derisk_position(aapl_lots(), PRICE, target, "hifo", CA)
    fifo = tx.derisk_position(aapl_lots(), PRICE, target, "fifo", CA)
    assert hifo["proceeds"] == pytest.approx(fifo["proceeds"], rel=0.01)
    assert hifo["realized_gain"] < fifo["realized_gain"]
    assert hifo["tax"] < fifo["tax"]


def test_hifo_picks_highest_basis_first():
    plan = tx.derisk_position(aapl_lots(), PRICE, 100_000.0, "hifo", CA)
    assert plan["slices"][0]["cost_basis_per_share"] == pytest.approx(182.40)


def test_fifo_picks_oldest_first():
    plan = tx.derisk_position(aapl_lots(), PRICE, 100_000.0, "fifo", CA)
    assert plan["slices"][0]["acquired_date"] == "2020-02-14"


def test_short_term_costs_more_tax_at_equal_basis():
    """Isolating the TERM effect: two lots with the SAME basis and size, one
    long-term one short-term. The short-term sale must cost more tax because
    of the higher rate. (When bases differ, basis can dominate term — see
    test_basis_can_outweigh_term — which is exactly why HIFO matters.)"""
    target = 200.0 * 100
    lt = tx.derisk_position([Lot("X", 100, 100.0, "2020-01-01", "long")], 200.0, target, "hifo", CA)
    st = tx.derisk_position([Lot("X", 100, 100.0, "2025-06-01", "short")], 200.0, target, "hifo", CA)
    assert lt["realized_gain"] == pytest.approx(st["realized_gain"])
    assert st["tax"] > lt["tax"]


def test_basis_can_outweigh_term():
    """Real-world subtlety: a high-basis SHORT-term lot can be cheaper to sell
    than a low-basis LONG-term lot, because the realized gain is so much
    smaller. Guards the engine against a naive 'always sell long-term' rule."""
    # ST lot bought near today's price (tiny gain) vs LT lot with a huge gain.
    st_highbasis = tx.derisk_position(
        [Lot("X", 100, 300.0, "2025-06-01", "short")], 320.0, 100 * 320.0, "hifo", CA)
    lt_lowbasis = tx.derisk_position(
        [Lot("X", 100, 60.0, "2018-01-01", "long")], 320.0, 100 * 320.0, "hifo", CA)
    # Same cash raised, but the high-basis short-term lot costs LESS tax.
    assert st_highbasis["tax"] < lt_lowbasis["tax"]


def test_exact_lt_tax():
    lots = [Lot("X", 100, 100.0, "2020-01-01", "long")]
    plan = tx.derisk_position(lots, 200.0, 100 * 200.0, "hifo", CA)
    assert plan["realized_gain"] == pytest.approx(10_000, abs=1)
    assert plan["tax"] == pytest.approx(10_000 * 0.371, abs=1)


def test_exact_st_tax_higher():
    lt = tx.derisk_position([Lot("X", 100, 100.0, "2020-01-01", "long")], 200.0, 20_000, "hifo", CA)
    st = tx.derisk_position([Lot("X", 100, 100.0, "2025-06-01", "short")], 200.0, 20_000, "hifo", CA)
    assert st["tax"] == pytest.approx(10_000 * 0.541, abs=1)
    assert lt["tax"] == pytest.approx(10_000 * 0.371, abs=1)
    assert st["tax"] > lt["tax"]


def test_loss_offsets_gain_same_sale():
    lots = [
        Lot("X", 100, 100.0, "2020-01-01", "long"),  # +$10k @ $200
        Lot("X", 100, 300.0, "2020-01-01", "long"),  # -$10k @ $200
    ]
    slices = [tx.SoldSlice(l, l.shares) for l in lots]
    res = tx.compute_sale(slices, 200.0, CA)
    assert res.net_gain == pytest.approx(0.0, abs=1)
    assert res.tax == pytest.approx(0.0, abs=1)


def test_harvest_losses_rivn():
    lots = [
        Lot("RIVN", 1953, 28.30, "2024-06-05", "long", "", "ESPP"),
        Lot("AAPL", 100, 18.94, "2014-03-15", "long"),  # a gain, ignored
    ]
    prices = {"RIVN": 16.30, "AAPL": 312.06}
    h = tx.harvest_losses(lots, prices, CA)
    assert len(h["harvestable_lots"]) == 1
    expected = (16.30 - 28.30) * 1953
    assert h["total_loss"] == pytest.approx(expected, abs=1)
    assert h["tax_offset_value"] == pytest.approx(-expected * 0.371, abs=1)
    assert h["tax_offset_value"] > 8_000


def test_charitable_avoids_gain_and_deducts():
    don = tx.charitable_donation(aapl_lots(), PRICE, 100_000.0, CA)
    assert don["donated_value"] == pytest.approx(100_000.0, rel=0.02)
    assert don["slices"][0]["cost_basis_per_share"] == pytest.approx(62.30)
    assert don["cap_gains_tax_avoided"] == pytest.approx(don["embedded_gain_avoided"] * 0.371, abs=1)
    assert don["total_tax_benefit"] > don["cap_gains_tax_avoided"]


def test_compare_methods_returns_all():
    res = tx.compare_methods(aapl_lots(), PRICE, 200_000.0, CA)
    assert {r["method"] for r in res} == {"hifo", "fifo", "lifo"}
    for r in res:
        assert r["proceeds"] == pytest.approx(200_000.0, rel=0.02)
