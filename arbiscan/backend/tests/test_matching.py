"""Settlement-matching tests, including look-alike markets with different rules (Test 5)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.domain import Market, MarketStatus
from app.matching.matcher import MatchStatus, compare, match_markets
from app.matching.normalize import dates, extract, thresholds

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def mk(ex, ext, title, rules, days=30, sources=(), **kw):
    return Market(exchange=ex, external_id=ext, title=title, rules_primary=rules, close_time=NOW + timedelta(days=days),
                  status=MarketStatus.OPEN, resolution_sources=list(sources), **kw)


def run(a, b):
    return compare(a, b, extract(a), extract(b), 0.5)


def test_identical_settlement_is_verified():
    a = mk("polymarket", "1", "Will the Fed cut interest rates at the December 9, 2026 FOMC meeting?",
           "Resolves Yes if the Federal Reserve cuts the target range at the FOMC meeting concluding December 9, 2026. Source: Federal Reserve.")
    b = mk("kalshi", "FED-1", "Fed rate cut at the December 9, 2026 FOMC meeting?",
           "If the Federal Reserve cuts the federal funds target range at the meeting concluding December 9, 2026, resolves Yes. Source: Federal Reserve.")
    r = run(a, b)
    assert r.status == MatchStatus.VERIFIED, r.reasons
    assert r.orientation == "same"


# --- Test 5: incompatible settlement rules ---------------------------------------
def test_different_cutoff_dates_are_incompatible():
    a = mk("polymarket", "2", "Government shutdown before February 1, 2027?",
           "Resolves Yes if a federal government shutdown begins before February 1, 2027 11:59 PM ET. Source: OMB.")
    b = mk("kalshi", "SHUT", "Government shutdown before January 15, 2027?",
           "If a federal government shutdown begins before January 15, 2027, resolves Yes. Source: OMB.")
    r = run(a, b)
    assert r.status == MatchStatus.INCOMPATIBLE
    assert any(c.code == "deadline" and c.result == "fail" for c in r.checks)


def test_different_thresholds_are_incompatible_but_flag_implication():
    a = mk("polymarket", "3", "Will Bitcoin be above $100,000 on December 31, 2026?", "Resolves Yes if BTC is above $100,000 on December 31, 2026.")
    b = mk("kalshi", "BTC", "Bitcoin above $110,000 on December 31, 2026?", "If BTC is above $110,000 on December 31, 2026, resolves Yes.")
    r = run(a, b)
    assert r.status == MatchStatus.INCOMPATIBLE
    # "above 110k" implies "above 100k"
    assert r.implication == {"implying": b.key, "implied": a.key, "direction": "gt"}


def test_different_resolution_sources_need_review():
    a = mk("polymarket", "4", "Will Bitcoin be above $100,000 on December 31, 2026?", "Resolves using the Coinbase BTC-USD price.", sources=["Coinbase"])
    b = mk("kalshi", "BTC2", "Bitcoin above $100,000 on December 31, 2026?", "Resolves using the CF Benchmarks index.", sources=["CF Benchmarks"])
    r = run(a, b)
    assert r.status == MatchStatus.LIKELY
    assert any(c.code == "sources" and c.result == "warn" for c in r.checks)


def test_exceptional_outcome_clauses_need_review():
    a = mk("polymarket", "5", "Will the Comets win the 2027 championship?",
           "Resolves Yes if the Comets win the 2027 championship. If the season is cancelled the market resolves 50-50.", days=200)
    b = mk("kalshi", "CMT", "Comets to win the 2027 championship?",
           "If the Comets win the 2027 championship, resolves Yes. If cancelled, resolves to a fair price.", days=200)
    r = run(a, b)
    assert r.status != MatchStatus.VERIFIED
    assert any(c.code == "exceptional" and c.result == "warn" for c in r.checks)


def test_negated_wording_is_inverted_and_not_auto_verified():
    a = mk("polymarket", "6", "Will the Senate pass the Water Act before January 1, 2027?", "Resolves Yes if the Senate passes the Water Act before January 1, 2027.")
    b = mk("kalshi", "WTR", "Will the Senate not pass the Water Act before January 1, 2027?", "Resolves Yes if the Senate does not pass the Water Act before January 1, 2027.")
    r = run(a, b)
    assert r.orientation == "inverted"
    assert r.status != MatchStatus.VERIFIED


def test_different_outcomes_in_multi_outcome_events_incompatible():
    a = mk("polymarket", "7", "Will Gavin Newsom win the 2028 Democratic nomination?", "Resolves Yes if Gavin Newsom wins.", outcome_label="Gavin Newsom")
    b = mk("kalshi", "DEM-JS", "Will Josh Shapiro win the 2028 Democratic nomination?", "Resolves Yes if Josh Shapiro wins.", outcome_label="Josh Shapiro")
    r = run(a, b)
    assert r.status == MatchStatus.INCOMPATIBLE


def test_missing_rules_is_insufficient():
    a = mk("polymarket", "8", "Will X happen?", "")
    a.close_time = None
    b = mk("kalshi", "X", "Will X happen?", "")
    b.close_time = None
    r = run(a, b)
    assert r.status == MatchStatus.INSUFFICIENT


def test_candidate_discovery_pairs_equivalent_markets_only():
    A = [mk("polymarket", "c1", "Will CPI year-over-year inflation be above 3.0% for October 2026?", "BLS CPI release."),
         mk("polymarket", "c2", "Will the Lakers win the 2027 NBA Finals?", "League official.")]
    B = [mk("kalshi", "K1", "October 2026 CPI YoY above 3.0%?", "BLS CPI release."),
         mk("kalshi", "K2", "Will it snow in Miami in December 2026?", "NWS.")]
    res = match_markets(A, B)
    pairs = {(r.market_a, r.market_b) for r in res}
    assert ("polymarket:c1", "kalshi:K1") in pairs
    assert not any(b == "kalshi:K2" and a == "polymarket:c1" for a, b in pairs)


def test_feature_extraction():
    assert {t.value for t in thresholds("above $100,000")} == {Decimal(100000)}
    assert {(t.direction, t.unit) for t in thresholds("4.5% or higher")} == {("gt", "%")}
    assert {(t.direction) for t in thresholds("below 2.5%")} == {"lt"}
    assert datetime(2026, 12, 31).date() in dates("before January 1, 2027") | dates("by December 31, 2026")
    assert datetime(2026, 12, 31).date() in dates("Before 2027?")


def test_similar_but_different_predicates_rejected():
    # real-world false positive caught in live testing
    a = mk("polymarket", "e1", "Fed emergency rate cut before 2027?", "Resolves Yes if the Fed announces an emergency (unscheduled) cut before 2027.")
    b = mk("kalshi", "FEDHIKE", "Will the Federal Reserve hike rates by December 31, 2026?", "If the Federal Reserve hikes rates by December 31, 2026, resolves Yes.")
    assert run(a, b).status == MatchStatus.INCOMPATIBLE


def test_extra_qualifier_requires_review():
    a = mk("polymarket", "e2", "Fed emergency rate cut before 2027?", "Resolves Yes if the Fed announces an emergency cut before 2027.")
    b = mk("kalshi", "FEDCUT", "Will the Federal Reserve cut rates by December 31, 2026?", "If the Federal Reserve cuts rates by December 31, 2026, resolves Yes.")
    r = run(a, b)
    assert r.status != MatchStatus.VERIFIED
    assert any(c.code == "wording" and c.result == "warn" and "emergency" in (c.a or "") for c in r.checks)


def test_unrelated_questions_about_same_entity_rejected():
    a = mk("polymarket", "e3", "Fed abolished before 2027?", "Resolves Yes if the Federal Reserve is abolished before 2027.")
    b = mk("kalshi", "FEDCUT2", "Will the Federal Reserve cut rates by December 31, 2026?", "If the Federal Reserve cuts rates by December 31, 2026, resolves Yes.")
    assert run(a, b).status == MatchStatus.INCOMPATIBLE


def test_spelling_variants_tolerated():
    a = mk("polymarket", "e4", "Will Trump, Putin and Zelensky meet together before 2027?", "Resolves Yes if they meet before 2027.")
    b = mk("kalshi", "MEET", "Will Trump, Zelenskyy and Putin meet together before 2027?", "Resolves Yes if they meet before 2027.")
    r = run(a, b)
    assert r.status != MatchStatus.INCOMPATIBLE


def test_llm_opinion_can_only_add_caution():
    from app.matching.llm import apply_llm_opinion
    a = mk("polymarket", "l1", "Will Acme IPO by December 31, 2026?", "Resolves Yes if Acme IPOs by December 31, 2026. Source: Reuters.")
    b = mk("kalshi", "L1", "Will Acme IPO by December 31, 2026?", "Resolves Yes if Acme IPOs by December 31, 2026. Source: Reuters.")
    r = run(a, b)
    assert r.status == MatchStatus.VERIFIED
    apply_llm_opinion(r, {"equivalent": "no", "orientation": "same", "concerns": ["different source"]})
    assert r.status == MatchStatus.LIKELY and r.llm["advisory_only"]
    r2 = run(a, b)
    r2.status = MatchStatus.LIKELY
    apply_llm_opinion(r2, {"equivalent": "yes", "orientation": "same", "concerns": []})
    assert r2.status == MatchStatus.LIKELY  # never promoted by the LLM


def test_different_participants_rejected():
    a = mk("polymarket", "p1", "ECB rate cut in 2026?", "Resolves Yes if the ECB cuts rates in 2026.")
    b = mk("kalshi", "P1", "Will the Federal Reserve cut rates by December 31, 2026?", "If the Federal Reserve cuts rates by December 31, 2026.")
    assert run(a, b).status == MatchStatus.INCOMPATIBLE
    c = mk("polymarket", "p2", "Trump, Putin, and Zelensky meet together before 2027?", "Resolves Yes if they meet before 2027.")
    d = mk("kalshi", "P2", "Will Trump, Xi and Putin meet together before 2027?", "Resolves Yes if they meet before 2027.")
    assert run(c, d).status == MatchStatus.INCOMPATIBLE
