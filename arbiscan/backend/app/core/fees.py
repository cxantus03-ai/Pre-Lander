"""Exchange fee models.

Fees are computed per *fill* (one price level consumed by one order) and
rounded **up** to the exchange's currency increment. Rounding per fill is
never cheaper than rounding per order, so modelled fees are conservative.

Kalshi (published fee schedule, series ``fee_type == "quadratic"``):
    taker fee = ceil_to_cent( 0.07 * fee_multiplier * C * P * (1 - P) )

Polymarket (market ``feeSchedule`` from the Gamma API, taker-only):
    fee = C * P * rate * (P * (1 - P)) ** exponent
    The rate/exponent come from the exchange. The formula is a model and is
    marked unverified unless ARBISCAN_POLYMARKET_FEE_FORMULA_VERIFIED=true.

Unknown fee structures fall back to a conservative percentage of notional and
are always ``verified=False`` so no opportunity relying on them can be
classified as verified arbitrage.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from typing import Iterable

from .domain import ONE, ZERO, FeeKind, FeeSpec

KALSHI_TAKER_RATE = Decimal("0.07")
UNKNOWN_FEE_FALLBACK = Decimal("0.02")  # 2% of notional, deliberately pessimistic


def ceil_to(x: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        return x
    return (x / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def kalshi_fee_spec(fee_type: str | None, multiplier: Decimal | None) -> FeeSpec:
    mult = multiplier if multiplier is not None else ONE
    if fee_type in ("quadratic", "quadratic_with_maker_fees"):
        return FeeSpec(
            kind=FeeKind.KALSHI_QUADRATIC,
            rate=KALSHI_TAKER_RATE,
            multiplier=mult,
            rounding_increment=Decimal("0.01"),
            verified=True,
            source=f"kalshi series fee_type={fee_type} fee_multiplier={mult}",
        )
    if fee_type == "free" or (fee_type is not None and mult == 0):
        return FeeSpec(kind=FeeKind.ZERO, verified=True, source=f"kalshi series fee_type={fee_type}")
    return FeeSpec(
        kind=FeeKind.PERCENT_NOTIONAL,
        rate=UNKNOWN_FEE_FALLBACK,
        verified=False,
        source=f"kalshi series fee_type={fee_type!r} not modelled",
        notes="Unrecognised Kalshi fee type; using pessimistic 2% of notional.",
    )


def polymarket_fee_spec(fees_enabled: bool | None, schedule: dict | None, formula_verified: bool) -> FeeSpec:
    if fees_enabled is False:
        return FeeSpec(kind=FeeKind.ZERO, verified=True, source="polymarket feesEnabled=false")
    if fees_enabled and schedule and schedule.get("rate") is not None:
        return FeeSpec(
            kind=FeeKind.POLYMARKET_SCHEDULE,
            rate=Decimal(str(schedule["rate"])),
            exponent=Decimal(str(schedule.get("exponent", 1))),
            rounding_increment=Decimal("0.0001"),
            verified=formula_verified,
            source=f"polymarket feeSchedule={schedule}",
            notes="" if formula_verified else "Polymarket fee formula not verified against current docs; profit is ESTIMATED.",
        )
    return FeeSpec(
        kind=FeeKind.PERCENT_NOTIONAL,
        rate=UNKNOWN_FEE_FALLBACK,
        verified=False,
        source="polymarket fee information missing",
        notes="No fee schedule available; using pessimistic 2% of notional.",
    )


def fee_for_fill(spec: FeeSpec, price: Decimal, qty: Decimal, payout: Decimal = ONE) -> Decimal:
    """Fee in dollars for buying ``qty`` contracts at ``price``."""
    if qty <= 0:
        return ZERO
    p = price / payout if payout != ONE else price  # normalise to probability units
    if spec.kind is FeeKind.ZERO:
        return ZERO
    if spec.kind is FeeKind.KALSHI_QUADRATIC:
        raw = spec.rate * spec.multiplier * qty * p * (ONE - p) * payout
    elif spec.kind is FeeKind.POLYMARKET_SCHEDULE:
        base = p * (ONE - p)
        exp = spec.exponent
        factor = base ** int(exp) if exp == exp.to_integral_value() else Decimal(str(float(base) ** float(exp)))
        raw = qty * price * spec.rate * factor
    else:  # PERCENT_NOTIONAL / UNKNOWN
        rate = spec.rate if spec.rate > 0 else UNKNOWN_FEE_FALLBACK
        raw = qty * price * rate
    return ceil_to(raw, spec.rounding_increment)


def fee_for_fills(spec: FeeSpec, fills: Iterable[tuple[Decimal, Decimal]], payout: Decimal = ONE) -> Decimal:
    return sum((fee_for_fill(spec, p, q, payout) for p, q in fills), ZERO)


def marginal_fee_per_contract(spec: FeeSpec, price: Decimal, payout: Decimal = ONE) -> Decimal:
    """Un-rounded fee per contract at ``price`` (used to locate the optimum)."""
    p = price / payout if payout != ONE else price
    if spec.kind is FeeKind.ZERO:
        return ZERO
    if spec.kind is FeeKind.KALSHI_QUADRATIC:
        return spec.rate * spec.multiplier * p * (ONE - p) * payout
    if spec.kind is FeeKind.POLYMARKET_SCHEDULE:
        base = p * (ONE - p)
        exp = spec.exponent
        factor = base ** int(exp) if exp == exp.to_integral_value() else Decimal(str(float(base) ** float(exp)))
        return price * spec.rate * factor
    rate = spec.rate if spec.rate > 0 else UNKNOWN_FEE_FALLBACK
    return price * rate
