"""Relational schema (SQLAlchemy 2.0). Migrations live in alembic/versions."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .core.domain import utcnow

Money = Numeric(18, 6)
Price = Numeric(12, 6)
Qty = Numeric(20, 4)


class Base(DeclarativeBase):
    pass


class Exchange(Base):
    __tablename__ = "exchanges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("exchange_id", "external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id"))
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="other")
    mutually_exclusive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    exhaustive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    exhaustive_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MarketRow(Base):
    __tablename__ = "markets"
    __table_args__ = (
        UniqueConstraint("exchange_id", "external_id"),
        Index("ix_markets_status_close", "status", "close_time"),
        Index("ix_markets_category", "category"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True, index=True)  # "<exchange>:<external_id>"
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    external_id: Mapped[str] = mapped_column(String(160))  # original exchange id, never rewritten
    normalized_event_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(Text)  # original title
    outcome_label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    yes_label: Mapped[str] = mapped_column(String(256), default="Yes")
    no_label: Mapped[str] = mapped_column(String(256), default="No")
    category: Mapped[str] = mapped_column(String(32), default="other")
    raw_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expiration_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    payout: Mapped[Decimal] = mapped_column(Price, default=Decimal(1))
    yes_token_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    no_token_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tick_size: Mapped[Decimal] = mapped_column(Price)
    min_order_size: Mapped[Decimal] = mapped_column(Qty)
    qty_increment: Mapped[Decimal] = mapped_column(Qty)
    volume: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    open_interest: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    exchange_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    rules: Mapped[list["ResolutionRule"]] = relationship(back_populates="market", cascade="all, delete-orphan")


class MarketOutcome(Base):
    __tablename__ = "market_outcomes"
    __table_args__ = (UniqueConstraint("market_id", "side"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"))
    side: Mapped[str] = mapped_column(String(8))  # yes | no
    label: Mapped[str] = mapped_column(String(256))
    token_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payout: Mapped[Decimal] = mapped_column(Price, default=Decimal(1))


class ResolutionRule(Base):
    __tablename__ = "resolution_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    rules_primary: Mapped[str] = mapped_column(Text, default="")
    rules_secondary: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    resolution_sources: Mapped[list] = mapped_column(JSON, default=list)
    can_close_early: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    early_close_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    market: Mapped[MarketRow] = relationship(back_populates="rules")


class FeeSchedule(Base):
    __tablename__ = "fee_schedules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    exponent: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    multiplier: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    rounding_increment: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    verified: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OrderBookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"
    __table_args__ = (Index("ix_obs_market_time", "market_key", "captured_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_key: Mapped[str] = mapped_column(String(200))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    exchange_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(16))
    levels: Mapped[list["OrderBookLevel"]] = relationship(cascade="all, delete-orphan")


class OrderBookLevel(Base):
    __tablename__ = "orderbook_levels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("orderbook_snapshots.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String(8))  # yes | no
    book_side: Mapped[str] = mapped_column(String(4))  # bid | ask
    level: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Price)
    size: Mapped[Decimal] = mapped_column(Qty)


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (Index("ix_ph_market_time", "market_key", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_key: Mapped[str] = mapped_column(String(200))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    yes_bid: Mapped[Decimal | None] = mapped_column(Price, nullable=True)
    yes_ask: Mapped[Decimal | None] = mapped_column(Price, nullable=True)
    no_bid: Mapped[Decimal | None] = mapped_column(Price, nullable=True)
    no_ask: Mapped[Decimal | None] = mapped_column(Price, nullable=True)
    yes_ask_depth: Mapped[Decimal | None] = mapped_column(Qty, nullable=True)
    no_ask_depth: Mapped[Decimal | None] = mapped_column(Qty, nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class MarketMatchRow(Base):
    __tablename__ = "market_matches"
    __table_args__ = (Index("ix_match_status", "status"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    market_a: Mapped[str] = mapped_column(String(200), index=True)
    market_b: Mapped[str] = mapped_column(String(200), index=True)
    candidate_score: Mapped[float] = mapped_column(Float)
    semantic_score: Mapped[float] = mapped_column(Float)
    orientation: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(16), default="auto")
    checks: Mapped[list] = mapped_column(JSON, default=list)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    implication: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    llm: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MatchVerification(Base):
    __tablename__ = "match_verifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("market_matches.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(16))  # approve | reject | reset | correct
    previous_status: Mapped[str] = mapped_column(String(32))
    new_status: Mapped[str] = mapped_column(String(32))
    orientation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OpportunityRow(Base):
    __tablename__ = "arbitrage_opportunities"
    __table_args__ = (Index("ix_opp_first_seen", "first_seen"), Index("ix_opp_class_state", "classification", "state"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(600))
    strategy: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32))
    classification: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    exchanges: Mapped[list] = mapped_column(JSON, default=list)
    exchange_pair: Mapped[str] = mapped_column(String(80))
    legs: Mapped[list] = mapped_column(JSON, default=list)
    match_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    match_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalid_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    peak_net_roi: Mapped[float] = mapped_column(Float, default=0)
    peak_profit: Mapped[float] = mapped_column(Float, default=0)  # theoretical, never realized
    last_net_roi: Mapped[float] = mapped_column(Float, default=0)
    last_profit: Mapped[float] = mapped_column(Float, default=0)
    last_capital: Mapped[float] = mapped_column(Float, default=0)
    last_liquidity: Mapped[float] = mapped_column(Float, default=0)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    snapshots: Mapped[list["OpportunitySnapshot"]] = relationship(cascade="all, delete-orphan")


class OpportunitySnapshot(Base):
    __tablename__ = "opportunity_snapshots"
    __table_args__ = (Index("ix_os_opp_time", "opportunity_id", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("arbitrage_opportunities.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    state: Mapped[str] = mapped_column(String(16))
    classification: Mapped[str] = mapped_column(String(16))
    combined_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_roi: Mapped[float] = mapped_column(Float)
    max_profit: Mapped[float] = mapped_column(Float)
    capital: Mapped[float] = mapped_column(Float)
    liquidity: Mapped[float] = mapped_column(Float)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class BalanceSettings(Base):
    """Manually maintained capital settings (single-user app: one row, id=1)."""

    __tablename__ = "user_balance_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    total_capital: Mapped[Decimal] = mapped_column(Money, default=Decimal(1000))
    exchange_balances: Mapped[dict] = mapped_column(JSON, default=dict)  # {"polymarket": "500", "kalshi": "100"}
    reserved_capital: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    max_allocation_per_trade: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    max_pct_per_trade: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    max_total_open_allocation: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    min_profit: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    min_roi_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal(0))
    max_slippage_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    max_days_to_settlement: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AlertConfig(Base):
    __tablename__ = "alert_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    event_types: Mapped[list] = mapped_column(JSON, default=list)
    min_net_roi: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_liquidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_change_threshold: Mapped[float] = mapped_column(Float, default=0.03)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    exchanges: Mapped[list] = mapped_column(JSON, default=list)
    cross_exchange_only: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_only: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_browser: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=False)
    email_to: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cooldown_s: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationEvent(Base):
    __tablename__ = "notification_events"
    __table_args__ = (Index("ix_notif_dedupe", "dedupe_key", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alert_configs.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16), default="info")
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    opportunity_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(300))
    channels: Mapped[list] = mapped_column(JSON, default=list)
    email_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RecordedTrade(Base):
    """A position the user says they actually entered (manual record only)."""

    __tablename__ = "recorded_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="other")
    strategy: Mapped[str] = mapped_column(String(32), default="cross_exchange_binary")
    legs: Mapped[list] = mapped_column(JSON, default=list)  # [{exchange, side, price, qty, fees}]
    theoretical_profit: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    total_cost: Mapped[Decimal] = mapped_column(Money)
    total_fees: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    settlement: Mapped["RecordedSettlement | None"] = relationship(back_populates="trade", cascade="all, delete-orphan", uselist=False)


class RecordedSettlement(Base):
    __tablename__ = "recorded_settlements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("recorded_trades.id", ondelete="CASCADE"), unique=True)
    result: Mapped[str] = mapped_column(String(64))  # e.g. "YES", "NO", "void", "mixed"
    payout_received: Mapped[Decimal] = mapped_column(Money)
    additional_fees: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    realized_pnl: Mapped[Decimal] = mapped_column(Money)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    trade: Mapped[RecordedTrade] = relationship(back_populates="settlement")


class AppSetting(Base):
    """Runtime-adjustable, non-secret settings (refresh cadence etc.)."""

    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
