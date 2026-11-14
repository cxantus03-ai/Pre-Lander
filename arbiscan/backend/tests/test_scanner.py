"""Opportunity detection & classification (Tests 5, 7, 8, 10 at scanner level)."""
from datetime import timedelta
from decimal import Decimal

from app.core.domain import EventGroup, FeeKind, FeeSpec, Market, MarketStatus, OrderBook, PriceLevel, utcnow
from app.matching.matcher import Check, MatchResult, MatchStatus
from app.services.scanner import Scanner
from app.services.store import MarketStore

ZERO_FEE = FeeSpec(FeeKind.ZERO, verified=True)


def market(ex, ext, title="Event X?", fee=ZERO_FEE, **kw):
    return Market(exchange=ex, external_id=ext, title=title, rules_primary="rules", status=MarketStatus.OPEN,
                  close_time=utcnow() + timedelta(days=10), fee=fee, **kw)


def book(key, yes_asks=(), no_asks=(), age_s=0, **kw):
    b = OrderBook(market_key=key,
                  yes_asks=[PriceLevel(Decimal(str(p)), Decimal(str(s))) for p, s in yes_asks],
                  no_asks=[PriceLevel(Decimal(str(p)), Decimal(str(s))) for p, s in no_asks], **kw)
    b.received_at = utcnow() - timedelta(seconds=age_s)
    return b.normalise()


def setup(status=MatchStatus.VERIFIED, pm_fee=ZERO_FEE, pm_age=0, k_age=0):
    st = MarketStore()
    a, b = market("polymarket", "A", fee=pm_fee), market("kalshi", "B")
    st.replace_exchange_markets({"polymarket", "kalshi"}, [a, b], [])
    st.put_match(MatchResult(a.key, b.key, 0.9, 0.9, "same", [Check("x", "x", "pass", "")], status, []))
    st.set_book(book(a.key, yes_asks=[(0.43, 100)], no_asks=[(0.60, 100)], age_s=pm_age))
    st.set_book(book(b.key, yes_asks=[(0.47, 100)], no_asks=[(0.54, 100)], age_s=k_age))
    return st, a, b


def cross(sc):
    return [o for o in sc.list(include_inactive=True) if o.strategy == "cross_exchange_binary"]


def test_verified_cross_exchange_opportunity():
    st, a, b = setup()
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    opps = cross(sc)
    assert len(opps) == 1
    o = opps[0]
    assert o.classification == "verified" and o.state == "active"
    assert o.sizing["combined_top_price"] == "0.9700"
    assert o.sizing["optimal_eval"]["worst_case_profit"] == "3.00"


def test_likely_match_is_only_potential():
    st, *_ = setup(status=MatchStatus.LIKELY)
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    o = cross(sc)[0]
    assert o.classification == "potential"
    assert any(f.code == "SETTLEMENT_REVIEW" for f in o.flags)


def test_incompatible_match_never_creates_opportunity():
    # Test 5 at scanner level: incompatible matches produce no cross-exchange candidate
    st, *_ = setup(status=MatchStatus.INCOMPATIBLE)
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    assert cross(sc) == []


def test_estimated_fees_downgrade_to_potential():
    st, *_ = setup(pm_fee=FeeSpec(FeeKind.POLYMARKET_SCHEDULE, rate=Decimal("0.0"), verified=False, notes="unverified"))
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    o = cross(sc)[0]
    assert o.classification == "potential"
    assert any(f.code == "FEES_ESTIMATED" for f in o.flags)


# --- Test 7: stale market prices -----------------------------------------------------
def test_stale_quote_excluded_from_verified():
    st, *_ = setup(k_age=120)
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    o = cross(sc)[0]
    assert o.state == "stale"
    assert o.classification != "verified"
    assert any(f.code == "STALE_QUOTE" for f in o.flags)


# --- Test 10: market suspension ---------------------------------------------------------
def test_suspended_market_not_executable():
    st, a, b = setup()
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    assert cross(sc)[0].state == "active"
    st.markets[b.key].status = MarketStatus.SUSPENDED
    st.dirty.add(b.key)
    sc.scan(st.take_dirty())
    o = cross(sc)[0]
    assert o.state == "unavailable"
    assert o.classification != "verified"
    assert any(f.code == "SUSPENDED" for f in o.flags)


def test_opportunity_disappears_when_prices_move():
    st, a, b = setup()
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    st.set_book(book(b.key, yes_asks=[(0.47, 100)], no_asks=[(0.60, 100)]))
    events = sc.scan(st.take_dirty())
    assert any(e.kind == "disappeared" for e in events)
    assert cross(sc)[0].state == "expired"


def test_feed_disconnect_marks_stale():
    st, a, b = setup()
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    st.mark_stale([a.key])
    sc.scan(st.take_dirty())
    assert cross(sc)[0].state == "stale"


# --- Test 8: incomplete multi-outcome set ------------------------------------------------
def test_incomplete_outcome_set_is_invalid():
    st = MarketStore()
    ms = [market("kalshi", f"M{i}", outcome_label=n) for i, n in enumerate(["A", "B", "C"])]
    g = EventGroup("kalshi", "E", "Who wins?", market_keys=[m.key for m in ms], mutually_exclusive=True, exhaustive=None,
                   exhaustive_reason="no catch-all")
    st.replace_exchange_markets({"kalshi"}, ms, [g])
    for m in ms:
        st.set_book(book(m.key, yes_asks=[(0.30, 100)], no_asks=[(0.75, 100)]))
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    yes_sets = [o for o in sc.list(include_inactive=True) if o.strategy == "multi_outcome_yes"]
    # guaranteed payout is 0 (none-of-the-above) so it is not even a gross arbitrage
    assert all(o.classification != "verified" for o in yes_sets)
    assert not [o for o in yes_sets if o.state == "active"]


def test_complete_outcome_set_with_catch_all_is_verified():
    st = MarketStore()
    ms = [market("kalshi", f"M{i}", outcome_label=n) for i, n in enumerate(["A", "B", "Other"])]
    g = EventGroup("kalshi", "E", "Who wins?", market_keys=[m.key for m in ms], mutually_exclusive=True, exhaustive=True)
    st.replace_exchange_markets({"kalshi"}, ms, [g])
    for m in ms:
        st.set_book(book(m.key, yes_asks=[(0.30, 100)], no_asks=[(0.75, 100)]))
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    o = [o for o in sc.list() if o.strategy == "multi_outcome_yes"][0]
    assert o.classification == "verified"
    assert o.sizing["optimal_eval"]["worst_case_profit"] == "10.00"


def test_price_divergence_blocks_verified():
    st = MarketStore()
    a, b = market("polymarket", "A"), market("kalshi", "B")
    st.replace_exchange_markets({"polymarket", "kalshi"}, [a, b], [])
    st.put_match(MatchResult(a.key, b.key, 0.9, 0.9, "same", [], MatchStatus.VERIFIED, [], method="manual"))
    # A trades ~5% YES, B trades ~90% YES: cheap "complement" but almost certainly different events
    ba = book(a.key, yes_asks=[(0.05, 100)], no_asks=[(0.96, 100)])
    ba.yes_bids = [PriceLevel(Decimal("0.04"), Decimal(100))]
    bb = book(b.key, yes_asks=[(0.91, 100)], no_asks=[(0.10, 100)])
    bb.yes_bids = [PriceLevel(Decimal("0.90"), Decimal(100))]
    st.set_book(ba)
    st.set_book(bb)
    sc = Scanner(st, stale_after_s=30)
    sc.scan(full=True)
    o = [o for o in cross(sc) if o.state == "active"][0]
    assert o.classification == "potential"
    assert any(f.code == "PRICE_DIVERGENCE" for f in o.flags)
