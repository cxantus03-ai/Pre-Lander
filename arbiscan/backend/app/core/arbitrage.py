"""Depth-aware, fee-aware arbitrage mathematics.

Every strategy is expressed as a *covered structure*: a list of legs, each
bought in the same quantity Q, plus the list of mutually exclusive and
collectively exhaustive real-world scenarios with the payout each leg returns
in each scenario. Examples:

* Binary complement (Polymarket YES + Kalshi NO):
      scenarios  YES-happens -> {A:1, B:0}    NO-happens -> {A:0, B:1}
* Complete multi-outcome set (buy YES on every outcome):
      scenario i wins -> {i:1, others:0}
* All-NO on a mutually exclusive set of N outcomes:
      scenario i wins -> N-1 ;  none wins -> N
* Implication pair (A implies B; buy YES on B and NO on A):
      A&B -> 1 ; B only -> 2 ; neither -> 1

For quantity Q the engine walks each leg's ask ladder, charges fees per fill
(rounded up), and reports profit in every scenario. The *worst-case* scenario
profit is the number that matters: an opportunity is only arbitrage if the
worst case is positive after fees.

The sizing optimiser exploits the fact that, for ascending ask ladders, the
marginal cost of one more complete set is non-decreasing in Q. It therefore
stops at the last order-book segment whose marginal cost is still below the
guaranteed payout, instead of applying the best displayed price to the whole
bankroll.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

from .domain import ONE, ZERO, FeeSpec, PriceLevel, Side
from .fees import fee_for_fills, marginal_fee_per_contract

CENT = Decimal("0.01")


def q2(x: Decimal) -> Decimal:
    """Round a dollar amount to cents for display (calculations keep full precision)."""
    return x.quantize(CENT)


def floor_to(x: Decimal, inc: Decimal) -> Decimal:
    if inc <= 0:
        return x
    return (x / inc).to_integral_value(rounding=ROUND_FLOOR) * inc


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


@dataclass
class Leg:
    leg_id: str
    exchange: str
    market_key: str
    side: Side
    asks: list[PriceLevel]  # ascending executable prices for buying `side`
    fee: FeeSpec
    payout: Decimal = ONE
    qty_increment: Decimal = ONE
    min_order_size: Decimal = ONE
    label: str = ""

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def depth(self) -> Decimal:
        return sum((lv.size for lv in self.asks), ZERO)

    @property
    def top_size(self) -> Decimal:
        return self.asks[0].size if self.asks else ZERO


@dataclass
class Scenario:
    name: str
    payouts: dict[str, Decimal]  # leg_id -> dollars paid per contract held


@dataclass
class CoveredStructure:
    legs: list[Leg]
    scenarios: list[Scenario]

    def set_payout(self, scenario: Scenario) -> Decimal:
        return sum((scenario.payouts.get(leg.leg_id, ZERO) for leg in self.legs), ZERO)

    @property
    def guaranteed_payout(self) -> Decimal:
        """Minimum payout per complete set across every modelled scenario."""
        if not self.scenarios:
            return ZERO
        return min(self.set_payout(s) for s in self.scenarios)

    @property
    def qty_increment(self) -> Decimal:
        return max((leg.qty_increment for leg in self.legs), default=ONE)

    @property
    def min_qty(self) -> Decimal:
        m = max((leg.min_order_size for leg in self.legs), default=ONE)
        inc = self.qty_increment
        return (m / inc).to_integral_value(rounding="ROUND_CEILING") * inc


# ---------------------------------------------------------------------------
# Evaluation at a fixed quantity
# ---------------------------------------------------------------------------


def walk_asks(levels: list[PriceLevel], qty: Decimal) -> list[tuple[Decimal, Decimal]] | None:
    """Fills needed to buy ``qty`` contracts; ``None`` if the book is too thin."""
    fills: list[tuple[Decimal, Decimal]] = []
    remaining = qty
    for lv in levels:
        if remaining <= 0:
            break
        take = min(lv.size, remaining)
        if take > 0:
            fills.append((lv.price, take))
            remaining -= take
    if remaining > 0:
        return None
    return fills


@dataclass
class LegExecution:
    leg_id: str
    exchange: str
    market_key: str
    side: Side
    label: str
    qty: Decimal
    fills: list[tuple[Decimal, Decimal]]
    cost: Decimal  # contract premium, dollars
    fees: Decimal
    fee_verified: bool

    @property
    def vwap(self) -> Decimal:
        return self.cost / self.qty if self.qty > 0 else ZERO

    @property
    def worst_price(self) -> Decimal:
        return max((p for p, _ in self.fills), default=ZERO)

    @property
    def total(self) -> Decimal:
        return self.cost + self.fees

    def to_dict(self) -> dict:
        return {
            "leg_id": self.leg_id,
            "exchange": self.exchange,
            "market_key": self.market_key,
            "side": self.side.value,
            "label": self.label,
            "qty": str(self.qty),
            "fills": [[str(p), str(q)] for p, q in self.fills],
            "levels_consumed": len(self.fills),
            "vwap": str(self.vwap.quantize(Decimal("0.0001"))) if self.qty > 0 else None,
            "worst_price": str(self.worst_price),
            "cost": str(q2(self.cost)),
            "fees": str(q2(self.fees)),
            "total": str(q2(self.total)),
            "fee_verified": self.fee_verified,
        }


@dataclass
class Evaluation:
    qty: Decimal
    legs: list[LegExecution]
    scenario_payouts: dict[str, Decimal]
    total_cost: Decimal
    total_fees: Decimal

    @property
    def total_outlay(self) -> Decimal:
        return self.total_cost + self.total_fees

    @property
    def scenario_profits(self) -> dict[str, Decimal]:
        return {k: v - self.total_outlay for k, v in self.scenario_payouts.items()}

    @property
    def guaranteed_payout(self) -> Decimal:
        return min(self.scenario_payouts.values()) if self.scenario_payouts else ZERO

    @property
    def worst_case_profit(self) -> Decimal:
        return self.guaranteed_payout - self.total_outlay

    @property
    def gross_profit(self) -> Decimal:
        return self.guaranteed_payout - self.total_cost

    @property
    def net_roi_pct(self) -> Decimal:
        if self.total_outlay <= 0:
            return ZERO
        return self.worst_case_profit / self.total_outlay * 100

    def outlay_by_exchange(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for leg in self.legs:
            out[leg.exchange] = out.get(leg.exchange, ZERO) + leg.total
        return out

    def to_dict(self) -> dict:
        return {
            "qty": str(self.qty),
            "legs": [leg.to_dict() for leg in self.legs],
            "total_cost": str(q2(self.total_cost)),
            "total_fees": str(q2(self.total_fees)),
            "total_outlay": str(q2(self.total_outlay)),
            "guaranteed_payout": str(q2(self.guaranteed_payout)),
            "scenario_payouts": {k: str(q2(v)) for k, v in self.scenario_payouts.items()},
            "scenario_profits": {k: str(q2(v)) for k, v in self.scenario_profits.items()},
            "gross_profit": str(q2(self.gross_profit)),
            "worst_case_profit": str(q2(self.worst_case_profit)),
            "net_roi_pct": str(self.net_roi_pct.quantize(Decimal("0.001"))),
            "outlay_by_exchange": {k: str(q2(v)) for k, v in self.outlay_by_exchange().items()},
        }


def evaluate(structure: CoveredStructure, qty: Decimal) -> Evaluation | None:
    """Exact cost/fees/payouts for buying ``qty`` complete sets. None if depth is insufficient."""
    legs: list[LegExecution] = []
    for leg in structure.legs:
        fills = walk_asks(leg.asks, qty) if qty > 0 else []
        if fills is None:
            return None
        cost = sum((p * q for p, q in fills), ZERO)
        fees = fee_for_fills(leg.fee, fills, leg.payout)
        legs.append(
            LegExecution(leg.leg_id, leg.exchange, leg.market_key, leg.side, leg.label, qty, fills, cost, fees, leg.fee.verified)
        )
    payouts = {s.name: structure.set_payout(s) * qty for s in structure.scenarios}
    return Evaluation(
        qty=qty,
        legs=legs,
        scenario_payouts=payouts,
        total_cost=sum((l.cost for l in legs), ZERO),
        total_fees=sum((l.fees for l in legs), ZERO),
    )


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


@dataclass
class Constraints:
    exchange_budgets: dict[str, Decimal] | None = None  # max outlay (cost + fees) per exchange
    max_total_outlay: Decimal | None = None
    max_qty: Decimal | None = None
    max_slippage_pct: Decimal | None = None  # worst fill vs best ask, per leg
    min_profit: Decimal | None = None
    min_roi_pct: Decimal | None = None


@dataclass
class SizingResult:
    combined_top_price: Decimal | None
    guaranteed_payout_per_set: Decimal
    gross_profit_per_set_top: Decimal | None
    gross_pct_top: Decimal | None
    fee_adjusted_cost_per_set_top: Decimal | None
    net_profit_per_set_top: Decimal | None
    top_of_book_qty: Decimal
    liquidity_qty: Decimal
    optimal_qty: Decimal  # maximises worst-case profit, ignoring user capital
    max_profitable_qty: Decimal  # largest qty with worst-case profit > 0
    constrained_qty: Decimal  # largest qty satisfying user constraints
    recommended_qty: Decimal
    limiting_factor: str
    top_eval: Evaluation | None
    optimal_eval: Evaluation | None
    recommended_eval: Evaluation | None
    curve: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def is_profitable(self) -> bool:
        return self.optimal_eval is not None and self.optimal_eval.worst_case_profit > 0

    def to_dict(self) -> dict:
        def s(x: Decimal | None, places: str | None = None) -> str | None:
            if x is None:
                return None
            return str(x.quantize(Decimal(places))) if places else str(x)

        return {
            "combined_top_price": s(self.combined_top_price, "0.0001"),
            "guaranteed_payout_per_set": s(self.guaranteed_payout_per_set),
            "gross_profit_per_set_top": s(self.gross_profit_per_set_top, "0.0001"),
            "gross_pct_top": s(self.gross_pct_top, "0.001"),
            "fee_adjusted_cost_per_set_top": s(self.fee_adjusted_cost_per_set_top, "0.0001"),
            "net_profit_per_set_top": s(self.net_profit_per_set_top, "0.0001"),
            "top_of_book_qty": s(self.top_of_book_qty),
            "liquidity_qty": s(self.liquidity_qty),
            "optimal_qty": s(self.optimal_qty),
            "max_profitable_qty": s(self.max_profitable_qty),
            "constrained_qty": s(self.constrained_qty),
            "recommended_qty": s(self.recommended_qty),
            "limiting_factor": self.limiting_factor,
            "is_profitable": self.is_profitable,
            "top_eval": self.top_eval.to_dict() if self.top_eval else None,
            "optimal_eval": self.optimal_eval.to_dict() if self.optimal_eval else None,
            "recommended_eval": self.recommended_eval.to_dict() if self.recommended_eval else None,
            "curve": self.curve,
            "reasons": self.reasons,
        }


def _breakpoints(structure: CoveredStructure, cap: Decimal) -> list[Decimal]:
    pts: set[Decimal] = set()
    for leg in structure.legs:
        cum = ZERO
        for lv in leg.asks:
            cum += lv.size
            if cum >= cap:
                break
            pts.add(cum)
    pts.add(cap)
    return sorted(p for p in pts if p > 0)


def _price_at(levels: list[PriceLevel], q: Decimal) -> Decimal | None:
    """Price of the level that supplies the contract just after cumulative qty ``q``."""
    cum = ZERO
    for lv in levels:
        cum += lv.size
        if q < cum:
            return lv.price
    return None


def _slippage_cap(leg: Leg, max_slippage_pct: Decimal) -> Decimal:
    if not leg.asks:
        return ZERO
    limit = leg.asks[0].price * (ONE + max_slippage_pct / 100)
    return sum((lv.size for lv in leg.asks if lv.price <= limit), ZERO)


def _largest_satisfying(lo: Decimal, hi: Decimal, inc: Decimal, pred) -> Decimal:
    """Largest multiple of inc in [lo, hi] with pred true, assuming pred is monotone (true then false)."""
    lo_n = int((lo / inc).to_integral_value(rounding=ROUND_FLOOR))
    hi_n = int((hi / inc).to_integral_value(rounding=ROUND_FLOOR))
    if hi_n < lo_n or not pred(inc * lo_n):
        return ZERO
    while lo_n < hi_n:
        mid = (lo_n + hi_n + 1) // 2
        if pred(inc * mid):
            lo_n = mid
        else:
            hi_n = mid - 1
    return inc * lo_n


def size_structure(structure: CoveredStructure, constraints: Constraints | None = None) -> SizingResult:
    c = constraints or Constraints()
    reasons: list[str] = []
    inc = structure.qty_increment
    S = structure.guaranteed_payout
    legs = structure.legs

    if not legs or any(not leg.asks for leg in legs):
        missing = [leg.label or leg.leg_id for leg in legs if not leg.asks]
        reasons.append("No executable ask on: " + ", ".join(missing) if missing else "No legs")
        return SizingResult(None, S, None, None, None, None, ZERO, ZERO, ZERO, ZERO, ZERO, ZERO, "no_liquidity", None, None, None, [], reasons)

    combined = sum((leg.asks[0].price for leg in legs), ZERO)
    marginal_fee_top = sum((marginal_fee_per_contract(leg.fee, leg.asks[0].price, leg.payout) for leg in legs), ZERO)
    gross_top = S - combined
    gross_pct = (gross_top / combined * 100) if combined > 0 else None
    top_qty = floor_to(min(leg.top_size for leg in legs), inc)
    liquidity = floor_to(min(leg.depth for leg in legs), inc)

    # --- optimal quantity: walk segments while marginal set cost < payout ----
    optimal_raw = ZERO
    prev = ZERO
    for bp in _breakpoints(structure, liquidity):
        marginal = ZERO
        for leg in legs:
            p = _price_at(leg.asks, prev)
            if p is None:
                marginal = None  # type: ignore[assignment]
                break
            marginal += p + marginal_fee_per_contract(leg.fee, p, leg.payout)
        if marginal is None or marginal >= S:
            break
        optimal_raw = bp
        prev = bp
    optimal_qty = floor_to(optimal_raw, inc)
    min_qty = structure.min_qty

    def profit_at(q: Decimal) -> Decimal | None:
        ev = evaluate(structure, q)
        return ev.worst_case_profit if ev else None

    optimal_eval = evaluate(structure, optimal_qty) if optimal_qty > 0 else None
    if optimal_eval and optimal_eval.worst_case_profit <= 0:
        # per-fill fee rounding can make the theoretical optimum unprofitable
        reasons.append("Fee rounding eliminates profit at the depth-optimal size.")
    if optimal_qty <= 0:
        if gross_top <= 0:
            reasons.append(f"Combined best ask {combined:.4f} >= guaranteed payout {S}: no gross arbitrage.")
        else:
            reasons.append("Fees eliminate the gross edge at the best ask.")

    # --- largest still-profitable quantity (beyond the optimum profit falls) --
    max_profitable = ZERO
    if optimal_eval and optimal_eval.worst_case_profit > 0:
        max_profitable = _largest_satisfying(
            optimal_qty, liquidity, inc, lambda q: (profit_at(q) or ZERO) > 0
        )

    # --- user/exchange constraints -------------------------------------------
    caps: list[tuple[Decimal, str]] = [(liquidity, "liquidity")]
    if c.max_qty is not None:
        caps.append((floor_to(c.max_qty, inc), "max_quantity"))
    if c.max_slippage_pct is not None:
        for leg in legs:
            caps.append((floor_to(_slippage_cap(leg, c.max_slippage_pct), inc), f"slippage:{leg.exchange}"))
    hard_cap = min(v for v, _ in caps)

    def within_budget(q: Decimal) -> bool:
        if q <= 0:
            return True
        ev = evaluate(structure, q)
        if ev is None:
            return False
        if c.max_total_outlay is not None and ev.total_outlay > c.max_total_outlay:
            return False
        if c.exchange_budgets:
            for ex, spent in ev.outlay_by_exchange().items():
                budget = c.exchange_budgets.get(ex)
                if budget is not None and spent > budget:
                    return False
        return True

    constrained = _largest_satisfying(ZERO, hard_cap, inc, within_budget)
    if constrained < hard_cap:
        ev = evaluate(structure, constrained + inc)
        binding = "capital"
        if ev is not None:
            if c.max_total_outlay is not None and ev.total_outlay > c.max_total_outlay:
                binding = "capital:total"
            for ex, spent in ev.outlay_by_exchange().items():
                b = (c.exchange_budgets or {}).get(ex)
                if b is not None and spent > b:
                    binding = f"capital:{ex}"
                    break
        caps.append((constrained, binding))

    recommended = min(optimal_qty, constrained)
    limiting = "profitability (order-book depth)" if optimal_qty <= constrained else min(caps, key=lambda t: t[0])[1]
    if optimal_qty <= 0:
        limiting = "not_profitable"
    if 0 < recommended < min_qty:
        reasons.append(f"Feasible size {recommended} is below the minimum order size {min_qty}.")
        recommended = ZERO
        limiting = "minimum_order_size"

    rec_eval = evaluate(structure, recommended) if recommended > 0 else None
    if rec_eval and rec_eval.worst_case_profit <= 0:
        reasons.append("Not profitable at the size your constraints allow (fee rounding / fixed costs).")
        recommended, rec_eval, limiting = ZERO, None, "fees_at_feasible_size"
    if rec_eval and c.min_profit is not None and rec_eval.worst_case_profit < c.min_profit:
        reasons.append(f"Worst-case profit {q2(rec_eval.worst_case_profit)} below your minimum {c.min_profit}.")
        limiting = "below_min_profit"
    if rec_eval and c.min_roi_pct is not None and rec_eval.net_roi_pct < c.min_roi_pct:
        reasons.append(f"Net ROI {rec_eval.net_roi_pct:.3f}% below your minimum {c.min_roi_pct}%.")
        limiting = "below_min_roi"

    top_eval = evaluate(structure, top_qty) if top_qty > 0 else None
    net_top = None
    fee_adj_top = None
    if top_eval and top_qty > 0:
        fee_adj_top = top_eval.total_outlay / top_qty
        net_top = S - fee_adj_top
    else:
        fee_adj_top = combined + marginal_fee_top
        net_top = S - fee_adj_top

    # --- profit curve for charts ----------------------------------------------
    curve: list[dict] = []
    curve_cap = min(liquidity, max(max_profitable, optimal_qty) * 2 if optimal_qty > 0 else liquidity)
    pts = [p for p in _breakpoints(structure, curve_cap) if p >= min_qty][:60]
    for p in pts:
        p = floor_to(p, inc)
        ev = evaluate(structure, p)
        if ev:
            curve.append({"qty": str(p), "outlay": str(q2(ev.total_outlay)), "worst_profit": str(q2(ev.worst_case_profit))})

    return SizingResult(
        combined_top_price=combined,
        guaranteed_payout_per_set=S,
        gross_profit_per_set_top=gross_top,
        gross_pct_top=gross_pct,
        fee_adjusted_cost_per_set_top=fee_adj_top,
        net_profit_per_set_top=net_top,
        top_of_book_qty=top_qty,
        liquidity_qty=liquidity,
        optimal_qty=optimal_qty,
        max_profitable_qty=max_profitable,
        constrained_qty=constrained,
        recommended_qty=recommended,
        limiting_factor=limiting,
        top_eval=top_eval,
        optimal_eval=optimal_eval,
        recommended_eval=rec_eval,
        curve=curve,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Structure builders
# ---------------------------------------------------------------------------


def binary_complement(yes_leg: Leg, no_leg: Leg) -> CoveredStructure:
    """YES on one contract + NO on an equivalent contract (same or other exchange)."""
    yes_leg.leg_id, no_leg.leg_id = "YES", "NO"
    return CoveredStructure(
        legs=[yes_leg, no_leg],
        scenarios=[
            Scenario("Event resolves YES", {"YES": yes_leg.payout, "NO": ZERO}),
            Scenario("Event resolves NO", {"YES": ZERO, "NO": no_leg.payout}),
        ],
    )


def complete_outcome_set(yes_legs: list[Leg], exhaustive: bool) -> CoveredStructure:
    """Buy YES on every outcome. If the set is not proven exhaustive a
    'none of the listed outcomes' scenario pays zero and kills the guarantee."""
    for i, leg in enumerate(yes_legs):
        leg.leg_id = f"O{i + 1}"
    scenarios = [
        Scenario(f"{leg.label or leg.leg_id} wins", {l.leg_id: (l.payout if l is leg else ZERO) for l in yes_legs})
        for leg in yes_legs
    ]
    if not exhaustive:
        scenarios.append(Scenario("No listed outcome wins", {l.leg_id: ZERO for l in yes_legs}))
    return CoveredStructure(yes_legs, scenarios)


def all_no_set(no_legs: list[Leg], exhaustive: bool) -> CoveredStructure:
    """Buy NO on every outcome of a mutually exclusive set: at least N-1 pay out."""
    for i, leg in enumerate(no_legs):
        leg.leg_id = f"N{i + 1}"
    scenarios = [
        Scenario(f"{leg.label or leg.leg_id} wins", {l.leg_id: (ZERO if l is leg else l.payout) for l in no_legs})
        for leg in no_legs
    ]
    if not exhaustive:
        scenarios.append(Scenario("No listed outcome wins", {l.leg_id: l.payout for l in no_legs}))
    return CoveredStructure(no_legs, scenarios)


def implication_pair(yes_on_implied: Leg, no_on_implying: Leg) -> CoveredStructure:
    """A implies B. Buy YES(B) and NO(A). Minimum payout is 1 in every world."""
    yes_on_implied.leg_id, no_on_implying.leg_id = "YES_B", "NO_A"
    return CoveredStructure(
        legs=[yes_on_implied, no_on_implying],
        scenarios=[
            Scenario("A and B occur", {"YES_B": yes_on_implied.payout, "NO_A": ZERO}),
            Scenario("B occurs, A does not", {"YES_B": yes_on_implied.payout, "NO_A": no_on_implying.payout}),
            Scenario("Neither occurs", {"YES_B": ZERO, "NO_A": no_on_implying.payout}),
        ],
    )
