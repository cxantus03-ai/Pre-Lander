"""Validated request bodies for the API."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Amount = Decimal


class CapitalInput(BaseModel):
    total_capital: Amount = Field(ge=0, le=1_000_000_000)
    exchange_balances: dict[str, Amount] = Field(default_factory=dict)
    reserved_capital: Amount = Field(default=Decimal(0), ge=0)
    max_allocation_per_trade: Amount | None = Field(default=None, ge=0)
    max_pct_per_trade: Decimal | None = Field(default=None, ge=0, le=100)
    max_total_open_allocation: Amount | None = Field(default=None, ge=0)
    min_profit: Amount = Field(default=Decimal(0), ge=0)
    min_roi_pct: Decimal = Field(default=Decimal(0), ge=-100, le=1000)
    max_slippage_pct: Decimal | None = Field(default=None, ge=0, le=100)
    max_days_to_settlement: int | None = Field(default=None, ge=0, le=3650)

    @field_validator("exchange_balances")
    @classmethod
    def _non_negative(cls, v: dict[str, Decimal]) -> dict[str, Decimal]:
        for k, amt in v.items():
            if amt < 0:
                raise ValueError(f"balance for {k} must be >= 0")
            if len(k) > 32:
                raise ValueError("exchange code too long")
        return v


class CalculatorInput(BaseModel):
    total_capital: Amount | None = Field(default=None, ge=0)
    exchange_balances: dict[str, Amount] = Field(default_factory=dict)
    max_allocation: Amount | None = Field(default=None, ge=0)
    max_pct_bankroll: Decimal | None = Field(default=None, ge=0, le=100)
    min_profit: Amount | None = Field(default=None, ge=0)
    min_roi_pct: Decimal | None = Field(default=None, ge=-100, le=1000)
    max_slippage_pct: Decimal | None = Field(default=None, ge=0, le=100)
    max_qty: Decimal | None = Field(default=None, gt=0)


class ManualLeg(BaseModel):
    exchange: str = Field(min_length=1, max_length=32)
    side: Literal["yes", "no"]
    label: str = Field(default="", max_length=120)
    levels: list[tuple[Decimal, Decimal]] = Field(min_length=1, max_length=200)
    fee_kind: Literal["zero", "kalshi_quadratic", "polymarket_schedule", "percent_notional"] = "zero"
    fee_rate: Decimal = Field(default=Decimal(0), ge=0, le=1)
    fee_exponent: Decimal = Field(default=Decimal(1), ge=0, le=5)
    fee_multiplier: Decimal = Field(default=Decimal(1), ge=0, le=10)
    fee_rounding: Decimal = Field(default=Decimal("0.01"), ge=0, le=1)
    qty_increment: Decimal = Field(default=Decimal(1), gt=0)
    min_order_size: Decimal = Field(default=Decimal(1), ge=0)
    payout: Decimal = Field(default=Decimal(1), gt=0, le=1000)

    @field_validator("levels")
    @classmethod
    def _valid_levels(cls, v):
        for p, q in v:
            if not (Decimal(0) < p < Decimal(1000)) or q <= 0:
                raise ValueError("each level needs price > 0 and size > 0")
        return v


class ManualCalcInput(BaseModel):
    structure: Literal["binary", "complete_set", "all_no"] = "binary"
    exhaustive: bool = False
    legs: list[ManualLeg] = Field(min_length=2, max_length=60)
    constraints: CalculatorInput = Field(default_factory=CalculatorInput)


class MatchReviewInput(BaseModel):
    action: Literal["approve", "reject", "reset", "correct"]
    orientation: Literal["same", "inverted"] | None = None
    note: str | None = Field(default=None, max_length=2000)


class ManualMatchInput(BaseModel):
    market_a: str = Field(min_length=3, max_length=200)
    market_b: str = Field(min_length=3, max_length=200)
    orientation: Literal["same", "inverted"] = "same"
    approve: bool = False
    note: str | None = Field(default=None, max_length=2000)


class AlertInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    event_types: list[str] = Field(default_factory=list)
    min_net_roi: float | None = Field(default=None, ge=-100, le=1000)
    min_profit: float | None = Field(default=None, ge=0)
    min_qty: float | None = Field(default=None, ge=0)
    min_liquidity: float | None = Field(default=None, ge=0)
    price_change_threshold: float = Field(default=0.03, gt=0, le=1)
    categories: list[str] = Field(default_factory=list)
    exchanges: list[str] = Field(default_factory=list)
    cross_exchange_only: bool = False
    verified_only: bool = False
    notify_in_app: bool = True
    notify_browser: bool = True
    notify_email: bool = False
    email_to: str | None = Field(default=None, max_length=256, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    cooldown_s: int = Field(default=300, ge=10, le=86400)


class TradeLeg(BaseModel):
    exchange: str = Field(min_length=1, max_length=32)
    side: str = Field(min_length=1, max_length=64)
    market: str | None = Field(default=None, max_length=200)
    price: Decimal = Field(gt=0, le=1000)
    qty: Decimal = Field(gt=0)
    fees: Decimal = Field(default=Decimal(0), ge=0)


class TradeInput(BaseModel):
    opportunity_id: str | None = Field(default=None, max_length=32)
    title: str = Field(min_length=1, max_length=500)
    category: str = Field(default="other", max_length=32)
    strategy: str = Field(default="cross_exchange_binary", max_length=32)
    legs: list[TradeLeg] = Field(min_length=1, max_length=60)
    theoretical_profit: Decimal | None = None
    notes: str | None = Field(default=None, max_length=4000)


class SettlementInput(BaseModel):
    result: str = Field(min_length=1, max_length=64)
    payout_received: Decimal = Field(ge=0)
    additional_fees: Decimal = Field(default=Decimal(0), ge=0)
    notes: str | None = Field(default=None, max_length=4000)


class RuntimeSettingsInput(BaseModel):
    book_poll_interval_s: int | None = Field(default=None, ge=5, le=600)
    discovery_interval_s: int | None = Field(default=None, ge=60, le=3600)
    stale_after_s: int | None = Field(default=None, ge=5, le=900)
    max_watched_books: int | None = Field(default=None, ge=10, le=2000)
