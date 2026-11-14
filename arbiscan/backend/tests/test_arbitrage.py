"""Unit tests for the arbitrage mathematics (required scenarios 1-4, 6, 8, 9, 11)."""
from decimal import Decimal

from app.core.arbitrage import (
    Constraints,
    all_no_set,
    binary_complement,
    complete_outcome_set,
    evaluate,
    implication_pair,
    size_structure,
)
from app.core.domain import FeeKind, FeeSpec
from app.core.fees import ceil_to, fee_for_fill, polymarket_fee_spec

from .helpers import KALSHI_FEE, ZERO_FEE, leg

D = Decimal


# --- Test 1: valid cross-exchange arbitrage -----------------------------------
def test_valid_cross_exchange_gross_math():
    s = binary_complement(leg("polymarket", "yes", [(0.43, 100)]), leg("kalshi", "no", [(0.54, 100)]))
    r = size_structure(s)
    assert r.combined_top_price == D("0.97")
    assert r.gross_profit_per_set_top == D("0.03")
    assert r.gross_pct_top.quantize(D("0.01")) == D("3.09")
    ev = evaluate(s, D(100))
    assert ev.total_cost == D("97.00")
    assert ev.worst_case_profit == D("3.00")
    # both scenarios pay exactly 100
    assert set(ev.scenario_payouts.values()) == {D(100)}
    assert r.optimal_qty == D(100) and r.is_profitable


# --- Test 2: no arbitrage --------------------------------------------------------
def test_no_arbitrage_rejected():
    s = binary_complement(leg("polymarket", "yes", [(0.52, 100)]), leg("kalshi", "no", [(0.51, 100)]))
    r = size_structure(s)
    assert r.combined_top_price == D("1.03")
    assert r.optimal_qty == 0
    assert not r.is_profitable
    assert r.recommended_qty == 0
    assert any("no gross arbitrage" in x for x in r.reasons)


# --- Test 3: fees eliminate arbitrage ----------------------------------------------
def test_fees_eliminate_arbitrage():
    # 0.49 + 0.49 = 0.98 gross; Kalshi fee ~0.0175/contract on each leg -> 0.035 > 0.02
    s = binary_complement(
        leg("kalshi", "yes", [(0.49, 1000)], fee=KALSHI_FEE), leg("kalshi2", "no", [(0.49, 1000)], fee=KALSHI_FEE)
    )
    r = size_structure(s)
    assert r.gross_profit_per_set_top == D("0.02")
    assert r.net_profit_per_set_top < 0
    assert not r.is_profitable
    assert r.recommended_qty == 0
    ev = evaluate(s, D(1000))
    assert ev.worst_case_profit < 0


def test_kalshi_fee_rounds_up_per_fill():
    # 0.07 * 10 * 0.5 * 0.5 = 0.175 -> 0.18
    assert fee_for_fill(KALSHI_FEE, D("0.50"), D(10)) == D("0.18")
    # 1 contract at 0.01: 0.07*0.01*0.99 = 0.000693 -> 0.01
    assert fee_for_fill(KALSHI_FEE, D("0.01"), D(1)) == D("0.01")
    assert ceil_to(D("0.1701"), D("0.01")) == D("0.18")


# --- Test 4: insufficient liquidity ----------------------------------------------
def test_price_not_applied_beyond_available_depth():
    s = binary_complement(leg("polymarket", "yes", [(0.40, 10)]), leg("kalshi", "no", [(0.50, 1000)]))
    r = size_structure(s, Constraints(exchange_budgets={"polymarket": D(10000), "kalshi": D(10000)}))
    assert r.liquidity_qty == D(10)
    assert r.recommended_qty == D(10)
    assert evaluate(s, D(1000)) is None  # cannot fill 1,000 at any price
    assert r.recommended_eval.worst_case_profit == D("1.00")


# --- Test 6: independent exchange balances ----------------------------------------
def test_independent_exchange_balances():
    s = binary_complement(leg("polymarket", "yes", [(0.45, 5000)]), leg("kalshi", "no", [(0.50, 5000)]))
    r = size_structure(s, Constraints(exchange_budgets={"polymarket": D(500), "kalshi": D(50)}))
    # Kalshi leg costs 0.50/contract -> at most 100 contracts on $50
    assert r.recommended_qty == D(100)
    by_ex = r.recommended_eval.outlay_by_exchange()
    assert by_ex["kalshi"] <= D(50)
    assert by_ex["polymarket"] <= D(500)
    assert r.limiting_factor == "capital:kalshi"


def test_total_bankroll_is_not_interchangeable():
    # $600 total but only $100 on Kalshi: must not recommend $300 on Kalshi
    s = binary_complement(leg("polymarket", "yes", [(0.40, 10000)]), leg("kalshi", "no", [(0.50, 10000)]))
    r = size_structure(
        s, Constraints(exchange_budgets={"polymarket": D(500), "kalshi": D(100)}, max_total_outlay=D(600))
    )
    assert r.recommended_eval.outlay_by_exchange()["kalshi"] <= D(100)
    assert r.recommended_qty == D(200)


# --- Test 8: multi-outcome coverage --------------------------------------------------
def test_incomplete_multi_outcome_set_is_not_arbitrage():
    legs = [leg("kalshi", "yes", [(0.30, 100)], label=n) for n in ("A", "B", "C")]
    s = complete_outcome_set(legs, exhaustive=False)
    assert s.guaranteed_payout == 0  # "none of the above" pays nothing
    r = size_structure(s)
    assert not r.is_profitable


def test_complete_multi_outcome_set_is_arbitrage():
    legs = [leg("kalshi", "yes", [(0.30, 100)], label=n) for n in ("A", "B", "C")]
    s = complete_outcome_set(legs, exhaustive=True)
    r = size_structure(s)
    assert r.combined_top_price == D("0.90")
    assert r.is_profitable and r.optimal_qty == 100
    assert r.optimal_eval.worst_case_profit == D("10.00")


def test_all_no_mutually_exclusive_set():
    legs = [leg("polymarket", "no", [(0.60, 50)], label=n) for n in ("A", "B", "C")]
    s = all_no_set(legs, exhaustive=False)
    assert s.guaranteed_payout == 2  # N-1 NOs always pay
    r = size_structure(s)
    assert r.combined_top_price == D("1.80")
    assert r.optimal_eval.worst_case_profit == D("10.00")


def test_implication_pair_min_payout_one():
    s = implication_pair(leg("kalshi", "yes", [(0.55, 100)]), leg("kalshi", "no", [(0.40, 100)]))
    assert s.guaranteed_payout == 1
    ev = evaluate(s, D(100))
    assert min(ev.scenario_profits.values()) == D(5)
    assert max(ev.scenario_profits.values()) == D(105)


# --- Test 9: order-book slippage ---------------------------------------------------
def test_sizing_stops_at_last_profitable_level():
    # Profitable for the first 100 (0.43+0.54), break-even next 50 (0.45+0.55=1.00),
    # unprofitable after (0.46+0.55).
    s = binary_complement(
        leg("polymarket", "yes", [(0.43, 100), (0.45, 50), (0.46, 500)]),
        leg("kalshi", "no", [(0.54, 100), (0.55, 1000)]),
    )
    r = size_structure(s)
    assert r.optimal_qty == D(100)
    assert r.optimal_eval.worst_case_profit == D("3.00")
    # Adding the break-even level does not increase profit; going further reduces it
    assert evaluate(s, D(150)).worst_case_profit == D("3.00")
    assert evaluate(s, D(400)).worst_case_profit < D("3.00")
    # largest size that is still net positive
    assert r.max_profitable_qty == D(449)  # 3.00 - 0.01*(Q-150) > 0  => Q < 450
    assert evaluate(s, D(450)).worst_case_profit == 0


def test_slippage_tolerance_limits_size():
    s = binary_complement(
        leg("polymarket", "yes", [(0.40, 100), (0.44, 500)]), leg("kalshi", "no", [(0.50, 1000)])
    )
    r = size_structure(s, Constraints(max_slippage_pct=D(5)))
    assert r.recommended_qty == D(100)


# --- Test 11: fee rounding and contract increments -------------------------------
def test_fee_rounding_and_minimum_increments():
    # Polymarket-like leg: min order 5, Kalshi integer contracts with quadratic fees
    pm = leg("polymarket", "yes", [(0.40, 7.5)], fee=ZERO_FEE, inc="1", min_size="5")
    k = leg("kalshi", "no", [(0.55, 100)], fee=KALSHI_FEE)
    s = binary_complement(pm, k)
    r = size_structure(s)
    assert r.optimal_qty == D(7)  # 7.5 available but only whole contracts
    ev = r.optimal_eval
    # Kalshi fee: 0.07*7*0.55*0.45 = 0.121275 -> 0.13
    assert ev.legs[1].fees == D("0.13")
    assert ev.total_outlay == D("2.80") + D("3.85") + D("0.13")
    assert ev.worst_case_profit == D(7) - D("6.78")

    # below minimum order size -> not executable
    s2 = binary_complement(leg("polymarket", "yes", [(0.40, 3)], min_size="5"), leg("kalshi", "no", [(0.55, 100)]))
    r2 = size_structure(s2)
    assert r2.recommended_qty == 0 and r2.limiting_factor == "minimum_order_size"


def test_small_size_fee_rounding_can_kill_profit():
    # gross edge 0.005/contract; with per-fill cent rounding 1 contract loses money
    s = binary_complement(
        leg("kalshi", "yes", [(0.495, 1)], fee=KALSHI_FEE), leg("x", "no", [(0.50, 1)], fee=ZERO_FEE)
    )
    r = size_structure(s)
    assert r.recommended_qty == 0
    assert not (r.optimal_eval and r.optimal_eval.worst_case_profit > 0)


def test_polymarket_fee_spec_flags_unverified():
    spec = polymarket_fee_spec(True, {"rate": 0.04, "exponent": 1, "takerOnly": True}, formula_verified=False)
    assert spec.kind is FeeKind.POLYMARKET_SCHEDULE and not spec.verified
    # 100 * 0.5 * 0.04 * 0.25 = 0.5
    assert fee_for_fill(spec, D("0.5"), D(100)) == D("0.5000")
    assert polymarket_fee_spec(False, None, False).verified
    unknown = polymarket_fee_spec(None, None, False)
    assert not unknown.verified and fee_for_fill(unknown, D("0.5"), D(100)) == D("1.0000")


def test_unknown_fee_spec_is_pessimistic():
    spec = FeeSpec(FeeKind.UNKNOWN)
    assert fee_for_fill(spec, D("0.5"), D(100)) == D("1.00")
