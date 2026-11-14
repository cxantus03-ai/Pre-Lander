"""Common interface every exchange connector implements.

Connectors only *read* public market data. None of them can place orders,
log in to accounts, or move funds — there is deliberately no such method.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Awaitable, Callable

from ..core.domain import EventGroup, FeeSpec, Market, MarketStatus, OrderBook


@dataclass
class ConnectorStatus:
    name: str
    display_name: str
    connected: bool = False
    simulated: bool = False
    last_success: datetime | None = None
    last_error: str | None = None
    markets: int = 0
    books: int = 0
    streaming: bool = False
    stream_connected: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "connected": self.connected,
            "simulated": self.simulated,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_error": self.last_error,
            "markets": self.markets,
            "books": self.books,
            "streaming": self.streaming,
            "stream_connected": self.stream_connected,
            "notes": self.notes,
        }


BookCallback = Callable[[OrderBook], Awaitable[None]]
StaleCallback = Callable[[list[str]], Awaitable[None]]


class ExchangeConnector(abc.ABC):
    name: str = "base"
    display_name: str = "Base"
    simulated: bool = False
    supports_streaming: bool = False

    def __init__(self) -> None:
        self.status = ConnectorStatus(self.name, self.display_name, simulated=self.simulated)

    @abc.abstractmethod
    async def fetch_markets(self) -> tuple[list[Market], list[EventGroup]]:
        """Discover open markets (paginated) and their event groupings."""

    @abc.abstractmethod
    async def fetch_order_books(self, markets: list[Market]) -> list[OrderBook]:
        """Full-depth executable books for the given markets."""

    async def fetch_market_details(self, market: Market) -> Market:
        return market

    async def fetch_fee_info(self, market: Market) -> FeeSpec:
        return market.fee

    async def fetch_recent_prices(self, market: Market) -> list[tuple[datetime, float]]:
        return []

    def market_status(self, market: Market) -> MarketStatus:
        return market.status

    async def subscribe(self, markets: list[Market], on_book: BookCallback, on_stale: StaleCallback) -> None:
        """Stream book updates until cancelled. Default: unsupported."""
        raise NotImplementedError

    async def close(self) -> None:
        return None

    async def healthcheck(self) -> bool:
        try:
            markets, _ = await self.fetch_markets_sample()
            return bool(markets)
        except Exception:
            return False

    async def fetch_markets_sample(self) -> tuple[list[Market], list[EventGroup]]:
        return await self.fetch_markets()


async def aiter_pages(fetch_page: Callable[[str | None], Awaitable[tuple[list, str | None]]], max_items: int) -> AsyncIterator[list]:
    cursor: str | None = None
    seen = 0
    while True:
        items, cursor = await fetch_page(cursor)
        yield items
        seen += len(items)
        if not cursor or not items or seen >= max_items:
            return
