"""In-memory real-time state: markets, event groups, order books, matches.

Fast path for the scanner and the API. Durable history goes to the database
(see persistence.py); an optional Redis mirror publishes snapshots for other
processes (see cache.py).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ..core.domain import EventGroup, Market, OrderBook, utcnow
from ..matching.matcher import MatchResult, MatchStatus


class MarketStore:
    def __init__(self) -> None:
        self.markets: dict[str, Market] = {}
        self.groups: dict[str, EventGroup] = {}
        self.books: dict[str, OrderBook] = {}
        self.matches: dict[str, MatchResult] = {}
        self.matches_by_market: dict[str, set[str]] = defaultdict(set)
        self.dirty: set[str] = set()
        self.priority: set[str] = set()  # markets the scanner wants full depth for
        self.last_book_update: datetime | None = None
        self.last_discovery: dict[str, datetime] = {}
        self.structure_version = 0  # bumps when matches/groups change

    # ------------------------------------------------------------ markets
    def replace_exchange_markets(self, exchanges: set[str], markets: list[Market], groups: list[EventGroup]) -> None:
        for k in [k for k, m in self.markets.items() if m.exchange in exchanges]:
            if k not in {m.key for m in markets}:
                self.markets.pop(k, None)
                self.books.pop(k, None)
        for m in markets:
            old = self.markets.get(m.key)
            if old is not None and old.status != m.status:
                self.dirty.add(m.key)
            self.markets[m.key] = m
        for k in [k for k, g in self.groups.items() if g.exchange in exchanges]:
            self.groups.pop(k, None)
        for g in groups:
            self.groups[g.key] = g
        now = utcnow()
        for ex in exchanges:
            self.last_discovery[ex] = now
        self.structure_version += 1

    def exchanges(self) -> list[str]:
        return sorted({m.exchange for m in self.markets.values()})

    # -------------------------------------------------------------- books
    def set_book(self, book: OrderBook) -> None:
        self.books[book.market_key] = book
        self.dirty.add(book.market_key)
        self.last_book_update = book.received_at

    def mark_stale(self, keys: list[str]) -> None:
        for k in keys:
            b = self.books.get(k)
            if b is not None:
                b.force_stale = True
                self.dirty.add(k)

    def take_dirty(self) -> set[str]:
        d, self.dirty = self.dirty, set()
        return d

    # ------------------------------------------------------------ matches
    def set_matches(self, results: list[MatchResult], keep_manual: bool = True) -> None:
        manual = {mid: m for mid, m in self.matches.items() if m.method == "manual"} if keep_manual else {}
        self.matches = {r.id: r for r in results}
        for mid, m in manual.items():
            self.matches[mid] = m  # human decisions always win over automation
        self._reindex_matches()

    def put_match(self, r: MatchResult) -> None:
        self.matches[r.id] = r
        self._reindex_matches()

    def _reindex_matches(self) -> None:
        self.matches_by_market = defaultdict(set)
        for mid, r in self.matches.items():
            self.matches_by_market[r.market_a].add(mid)
            self.matches_by_market[r.market_b].add(mid)
        self.structure_version += 1

    def usable_matches(self) -> list[MatchResult]:
        return [m for m in self.matches.values() if m.status != MatchStatus.INCOMPATIBLE or m.implication]

    def watch_list(self, limit: int) -> list[Market]:
        """Markets whose full depth should be polled, most important first."""
        ranked: list[str] = []
        seen: set[str] = set()

        def add(k: str) -> None:
            if k in self.markets and k not in seen:
                seen.add(k)
                ranked.append(k)

        for k in sorted(self.priority):
            add(k)
        order = {MatchStatus.VERIFIED: 0, MatchStatus.LIKELY: 1, MatchStatus.INSUFFICIENT: 2, MatchStatus.INCOMPATIBLE: 3}
        for r in sorted(self.matches.values(), key=lambda r: (order[r.status], -r.semantic_score)):
            if r.status == MatchStatus.INCOMPATIBLE and not r.implication:
                continue
            add(r.market_a)
            add(r.market_b)
        for g in self.groups.values():
            if g.mutually_exclusive and len(g.market_keys) <= 30:
                for k in g.market_keys:
                    add(k)
        return [self.markets[k] for k in ranked[:limit]]
