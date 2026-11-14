"""Exchange-agnostic domain objects.

Every price is a ``Decimal`` in dollars per contract (0 < p < payout). Every
quantity is a ``Decimal`` number of contracts. Nothing in the calculation path
uses binary floating point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

ZERO = Decimal("0")
ONE = Decimal("1")


def D(x: Any) -> Decimal:
    """Convert API values (str/int/float/Decimal/None) to Decimal safely."""
    if x is None or x == "":
        return ZERO
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(str(x))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Side(str, Enum):
    YES = "yes"
    NO = "no"

    @property
    def other(self) -> "Side":
        return Side.NO if self is Side.YES else Side.YES


class MarketStatus(str, Enum):
    OPEN = "open"
    SUSPENDED = "suspended"  # exists but not currently accepting orders
    CLOSED = "closed"  # trading finished, awaiting settlement
    SETTLED = "settled"
    UNKNOWN = "unknown"


class FeeKind(str, Enum):
    ZERO = "zero"
    KALSHI_QUADRATIC = "kalshi_quadratic"
    POLYMARKET_SCHEDULE = "polymarket_schedule"
    PERCENT_NOTIONAL = "percent_notional"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FeeSpec:
    kind: FeeKind
    rate: Decimal = ZERO
    exponent: Decimal = ONE
    multiplier: Decimal = ONE
    # dollars; fees are rounded *up* to this increment (per fill, conservative)
    rounding_increment: Decimal = Decimal("0.01")
    verified: bool = False
    source: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "rate": str(self.rate),
            "exponent": str(self.exponent),
            "multiplier": str(self.multiplier),
            "rounding_increment": str(self.rounding_increment),
            "verified": self.verified,
            "source": self.source,
            "notes": self.notes,
        }


@dataclass
class PriceLevel:
    price: Decimal
    size: Decimal

    def to_list(self) -> list[str]:
        return [str(self.price), str(self.size)]


@dataclass
class OrderBook:
    """Executable book for one binary market, expressed for both sides.

    ``yes_asks`` are prices at which YES can be *bought* (ascending), etc.
    For Kalshi, asks are implied from the opposite side's bids (YES ask =
    payout - NO bid), which is how the exchange itself defines them.
    """

    market_key: str
    yes_bids: list[PriceLevel] = field(default_factory=list)  # descending
    yes_asks: list[PriceLevel] = field(default_factory=list)  # ascending
    no_bids: list[PriceLevel] = field(default_factory=list)
    no_asks: list[PriceLevel] = field(default_factory=list)
    exchange_ts: datetime | None = None
    received_at: datetime = field(default_factory=utcnow)
    source: str = "rest"  # rest | ws | listing | demo | manual
    depth_complete: bool = True  # False when only top-of-book is known
    force_stale: bool = False  # set when the feed carrying it disconnected

    def asks(self, side: Side) -> list[PriceLevel]:
        return self.yes_asks if side is Side.YES else self.no_asks

    def bids(self, side: Side) -> list[PriceLevel]:
        return self.yes_bids if side is Side.YES else self.no_bids

    def best_ask(self, side: Side) -> PriceLevel | None:
        a = self.asks(side)
        return a[0] if a else None

    def best_bid(self, side: Side) -> PriceLevel | None:
        b = self.bids(side)
        return b[0] if b else None

    def age_seconds(self, now: datetime | None = None) -> float:
        now = now or utcnow()
        return (now - self.received_at).total_seconds()

    def is_stale(self, stale_after_s: float, now: datetime | None = None) -> bool:
        return self.force_stale or self.age_seconds(now) > stale_after_s

    def normalise(self) -> "OrderBook":
        """Sort, drop empty levels, merge duplicate prices."""

        def clean(levels: list[PriceLevel], desc: bool) -> list[PriceLevel]:
            agg: dict[Decimal, Decimal] = {}
            for lv in levels:
                if lv.size > 0 and lv.price > 0:
                    agg[lv.price] = agg.get(lv.price, ZERO) + lv.size
            return [PriceLevel(p, s) for p, s in sorted(agg.items(), key=lambda kv: kv[0], reverse=desc)]

        self.yes_bids = clean(self.yes_bids, True)
        self.no_bids = clean(self.no_bids, True)
        self.yes_asks = clean(self.yes_asks, False)
        self.no_asks = clean(self.no_asks, False)
        return self

    def to_dict(self, max_levels: int = 25) -> dict:
        return {
            "market_key": self.market_key,
            "yes_bids": [lv.to_list() for lv in self.yes_bids[:max_levels]],
            "yes_asks": [lv.to_list() for lv in self.yes_asks[:max_levels]],
            "no_bids": [lv.to_list() for lv in self.no_bids[:max_levels]],
            "no_asks": [lv.to_list() for lv in self.no_asks[:max_levels]],
            "exchange_ts": self.exchange_ts.isoformat() if self.exchange_ts else None,
            "received_at": self.received_at.isoformat(),
            "source": self.source,
            "depth_complete": self.depth_complete,
            "force_stale": self.force_stale,
        }


@dataclass
class Market:
    exchange: str
    external_id: str
    title: str
    event_id: str | None = None
    event_title: str | None = None
    outcome_label: str | None = None  # e.g. candidate name inside a multi-outcome event
    yes_label: str = "Yes"
    no_label: str = "No"
    description: str = ""
    rules_primary: str = ""
    rules_secondary: str = ""
    category: str = "other"
    raw_category: str | None = None
    close_time: datetime | None = None
    expiration_time: datetime | None = None
    status: MarketStatus = MarketStatus.UNKNOWN
    url: str | None = None
    payout: Decimal = ONE  # dollars paid per winning contract
    yes_token_id: str | None = None
    no_token_id: str | None = None
    tick_size: Decimal = Decimal("0.01")
    min_order_size: Decimal = ONE
    qty_increment: Decimal = ONE
    fee: FeeSpec = field(default_factory=lambda: FeeSpec(FeeKind.UNKNOWN))
    volume: Decimal = ZERO
    volume_24h: Decimal = ZERO
    open_interest: Decimal | None = None
    liquidity: Decimal | None = None
    strike: Decimal | None = None
    strike_type: str | None = None  # greater | less | between | ...
    resolution_sources: list[str] = field(default_factory=list)
    can_close_early: bool | None = None
    early_close_condition: str | None = None
    is_simulated: bool = False
    exchange_updated_at: datetime | None = None
    received_at: datetime = field(default_factory=utcnow)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.external_id}"

    @property
    def full_rules(self) -> str:
        return "\n\n".join(t for t in (self.rules_primary, self.rules_secondary, self.description) if t)


@dataclass
class EventGroup:
    """A set of markets an exchange groups under one event (multi-outcome)."""

    exchange: str
    event_id: str
    title: str
    category: str = "other"
    market_keys: list[str] = field(default_factory=list)
    # At most one market in the group can resolve YES (exchange-declared).
    mutually_exclusive: bool | None = None
    # Exactly one market resolves YES (no uncovered "none of the above").
    # Only True when established from exchange metadata, never guessed.
    exhaustive: bool | None = None
    exhaustive_reason: str = ""
    url: str | None = None
    is_simulated: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.event_id}"
