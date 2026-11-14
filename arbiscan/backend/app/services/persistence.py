"""Batched database writes for markets, history, opportunities and matches.

All functions are synchronous and are called from worker threads
(``asyncio.to_thread``) so the event loop never blocks on I/O.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from ..core.domain import EventGroup, Market, OrderBook, Side
from ..db import session_scope
from ..matching.matcher import Check, MatchResult, MatchStatus
from ..models import (
    BalanceSettings,
    Event,
    Exchange,
    FeeSchedule,
    MarketMatchRow,
    MarketOutcome,
    MarketRow,
    OpportunityRow,
    OpportunitySnapshot,
    OrderBookLevel,
    OrderBookSnapshot,
    PriceHistory,
    ResolutionRule,
)
from .scanner import Opportunity

log = logging.getLogger(__name__)

EXCHANGE_NAMES = {
    "polymarket": ("Polymarket", False),
    "kalshi": ("Kalshi", False),
    "demo_poly": ("SIM Poly-style venue", True),
    "demo_kalshi": ("SIM Kalshi-style venue", True),
}


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Persistence:
    def __init__(self) -> None:
        self._exchange_ids: dict[str, int] = {}
        self._market_cache: dict[str, tuple[int, str]] = {}  # key -> (id, content hash)
        self._event_ids: dict[str, int] = {}

    # ----------------------------------------------------------- exchanges
    def _exchange_id(self, s, code: str) -> int:
        if code in self._exchange_ids:
            return self._exchange_ids[code]
        row = s.scalar(select(Exchange).where(Exchange.code == code))
        if row is None:
            name, sim = EXCHANGE_NAMES.get(code, (code, code.startswith("demo")))
            row = Exchange(code=code, display_name=name, is_simulated=sim)
            s.add(row)
            s.flush()
        self._exchange_ids[code] = row.id
        return row.id

    # -------------------------------------------------------------- markets
    @staticmethod
    def _hash(m: Market) -> str:
        parts = [m.title, m.status.value, str(m.close_time), m.rules_primary, m.rules_secondary, m.description,
                 m.fee.kind.value, str(m.fee.rate), str(m.fee.multiplier), str(m.fee.verified), m.outcome_label or ""]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()

    def upsert_markets(self, markets: list[Market], groups: list[EventGroup], chunk: int = 400) -> int:
        """Upsert in short transactions so other writers are never blocked for long."""
        written = self._upsert_chunk([], groups)
        changed = [m for m in markets if self._market_cache.get(m.key, (0, ""))[1] != self._hash(m)]
        for i in range(0, len(changed), chunk):
            written += self._upsert_chunk(changed[i:i + chunk], [])
        return written

    def _upsert_chunk(self, markets: list[Market], groups: list[EventGroup]) -> int:
        written = 0
        with session_scope() as s:
            for g in groups:
                ex_id = self._exchange_id(s, g.exchange)
                ev = s.scalar(select(Event).where(Event.exchange_id == ex_id, Event.external_id == g.event_id))
                if ev is None:
                    ev = Event(exchange_id=ex_id, external_id=g.event_id, title=g.title)
                    s.add(ev)
                ev.title, ev.category, ev.url = g.title, g.category, g.url
                ev.mutually_exclusive, ev.exhaustive, ev.exhaustive_reason = g.mutually_exclusive, g.exhaustive, g.exhaustive_reason
                ev.extra = g.extra
                s.flush()
                self._event_ids[g.key] = ev.id
            if not self._market_cache:
                for mid, key in s.execute(select(MarketRow.id, MarketRow.key)):
                    self._market_cache[key] = (mid, "")
            for m in markets:
                h = self._hash(m)
                cached = self._market_cache.get(m.key)
                if cached and cached[1] == h:
                    continue
                ex_id = self._exchange_id(s, m.exchange)
                row = s.get(MarketRow, cached[0]) if cached else None
                if row is None:
                    row = MarketRow(key=m.key, exchange_id=ex_id, external_id=m.external_id, title=m.title, status=m.status.value,
                                    tick_size=m.tick_size, min_order_size=m.min_order_size, qty_increment=m.qty_increment)
                    s.add(row)
                    s.flush()
                    s.add_all([
                        MarketOutcome(market_id=row.id, side=Side.YES.value, label=m.yes_label[:256], token_id=m.yes_token_id, payout=m.payout),
                        MarketOutcome(market_id=row.id, side=Side.NO.value, label=m.no_label[:256], token_id=m.no_token_id, payout=m.payout),
                    ])
                row.title, row.outcome_label = m.title, (m.outcome_label or None)
                row.yes_label, row.no_label = m.yes_label[:256], m.no_label[:256]
                row.category, row.raw_category, row.status = m.category, m.raw_category, m.status.value
                row.close_time, row.expiration_time, row.url = m.close_time, m.expiration_time, m.url
                row.payout, row.yes_token_id, row.no_token_id = m.payout, m.yes_token_id, m.no_token_id
                row.tick_size, row.min_order_size, row.qty_increment = m.tick_size, m.min_order_size, m.qty_increment
                row.volume, row.open_interest, row.is_simulated = m.volume, m.open_interest, m.is_simulated
                row.exchange_updated_at, row.last_seen_at = m.exchange_updated_at, m.received_at
                row.normalized_event_key = f"{m.exchange}:{m.event_id}" if m.event_id else None
                row.event_id = self._event_ids.get(f"{m.exchange}:{m.event_id}") if m.event_id else None
                row.extra = {k: v for k, v in m.extra.items() if k != "top"}
                rule = s.scalar(select(ResolutionRule).where(ResolutionRule.market_id == row.id).order_by(ResolutionRule.id.desc()))
                if rule is None or (rule.rules_primary, rule.rules_secondary, rule.description) != (m.rules_primary, m.rules_secondary, m.description):
                    s.add(ResolutionRule(market_id=row.id, rules_primary=m.rules_primary, rules_secondary=m.rules_secondary,
                                         description=m.description, resolution_sources=m.resolution_sources,
                                         can_close_early=m.can_close_early, early_close_condition=m.early_close_condition))
                fee = s.scalar(select(FeeSchedule).where(FeeSchedule.market_id == row.id))
                if fee is None:
                    fee = FeeSchedule(market_id=row.id)
                    s.add(fee)
                fee.kind, fee.rate, fee.exponent = m.fee.kind.value, m.fee.rate, m.fee.exponent
                fee.multiplier, fee.rounding_increment = m.fee.multiplier, m.fee.rounding_increment
                fee.verified, fee.source = m.fee.verified, m.fee.source[:2000]
                self._market_cache[m.key] = (row.id, h)
                written += 1
        return written

    # -------------------------------------------------------------- history
    def write_price_history(self, books: list[OrderBook], simulated: dict[str, bool]) -> None:
        with session_scope() as s:
            rows = []
            for b in books:
                def top(levels):
                    return levels[0].price if levels else None
                rows.append(PriceHistory(
                    market_key=b.market_key, ts=b.received_at,
                    yes_bid=top(b.yes_bids), yes_ask=top(b.yes_asks), no_bid=top(b.no_bids), no_ask=top(b.no_asks),
                    yes_ask_depth=sum((l.size for l in b.yes_asks), Decimal(0)), no_ask_depth=sum((l.size for l in b.no_asks), Decimal(0)),
                    is_simulated=simulated.get(b.market_key, False),
                ))
            s.add_all(rows)

    def write_book_snapshots(self, books: list[OrderBook], max_levels: int = 20) -> None:
        with session_scope() as s:
            for b in books:
                snap = OrderBookSnapshot(market_key=b.market_key, captured_at=b.received_at, exchange_ts=b.exchange_ts, source=b.source)
                for side, book_side, levels in (("yes", "bid", b.yes_bids), ("yes", "ask", b.yes_asks), ("no", "bid", b.no_bids), ("no", "ask", b.no_asks)):
                    for i, lv in enumerate(levels[:max_levels]):
                        snap.levels.append(OrderBookLevel(side=side, book_side=book_side, level=i, price=lv.price, size=lv.size))
                s.add(snap)

    # -------------------------------------------------------- opportunities
    def upsert_opportunities(self, opps: list[Opportunity], snapshot: bool) -> None:
        if not opps:
            return
        with session_scope() as s:
            for o in opps:
                row = s.get(OpportunityRow, o.id)
                if row is None:
                    row = OpportunityRow(id=o.id, key=o.key, strategy=o.strategy, title=o.title, category=o.category,
                                         classification=o.classification, state=o.state, first_seen=o.first_seen, last_seen=o.last_seen)
                    s.add(row)
                elif row.state == "expired" and o.state != "expired":
                    # re-appeared after disappearing: a new lifecycle in history
                    row.first_seen, row.ended_at, row.end_reason = o.first_seen, None, None
                row.classification, row.state, row.title = o.classification, o.state, o.title
                row.exchanges = o.exchanges
                row.exchange_pair = " / ".join(sorted(set(o.exchanges)))
                row.legs = o.legs
                row.match_id, row.match_status = o.match_id, o.match_status
                row.last_seen, row.ended_at, row.end_reason = o.last_seen, o.ended_at, o.end_reason
                row.invalid_reason = o.invalid_reason
                row.peak_net_roi = max(row.peak_net_roi or 0, o.metric("net_roi"))
                row.peak_profit = max(row.peak_profit or 0, o.metric("max_profit"))
                row.last_net_roi, row.last_profit = o.metric("net_roi"), o.metric("max_profit")
                row.last_capital, row.last_liquidity = o.metric("capital"), o.metric("liquidity")
                row.is_simulated = o.is_simulated
                if snapshot or row.state in ("expired",):
                    s.add(OpportunitySnapshot(
                        opportunity_id=o.id, ts=o.updated_at, state=o.state, classification=o.classification,
                        combined_price=float(o.sizing.get("combined_top_price") or 0) or None,
                        net_roi=o.metric("net_roi"), max_profit=o.metric("max_profit"), capital=o.metric("capital"),
                        liquidity=o.metric("liquidity"),
                        detail={"legs": [{k: l.get(k) for k in ("exchange", "side", "best_ask", "best_ask_size")} for l in o.legs]},
                    ))

    # --------------------------------------------------------------- matches
    def save_matches(self, results: list[MatchResult]) -> None:
        with session_scope() as s:
            existing = {r.id: r for r in s.scalars(select(MarketMatchRow))}
            for r in results:
                row = existing.get(r.id)
                if row is not None and row.method == "manual":
                    continue  # never overwrite a human decision
                if row is None:
                    row = MarketMatchRow(id=r.id, market_a=r.market_a, market_b=r.market_b, candidate_score=r.candidate_score,
                                         semantic_score=r.semantic_score, orientation=r.orientation, status=r.status.value)
                    s.add(row)
                row.candidate_score, row.semantic_score = r.candidate_score, r.semantic_score
                row.orientation, row.status, row.method = r.orientation, r.status.value, r.method
                row.checks = [c.to_dict() for c in r.checks]
                row.reasons, row.implication, row.llm = r.reasons, r.implication, r.llm

    def load_manual_matches(self) -> list[MatchResult]:
        out = []
        with session_scope() as s:
            for row in s.scalars(select(MarketMatchRow).where(MarketMatchRow.method == "manual")):
                out.append(row_to_match(row))
        return out


def row_to_match(row: MarketMatchRow) -> MatchResult:
    return MatchResult(
        market_a=row.market_a, market_b=row.market_b, candidate_score=row.candidate_score, semantic_score=row.semantic_score,
        orientation=row.orientation, checks=[Check(**c) for c in row.checks or []], status=MatchStatus(row.status),
        reasons=list(row.reasons or []), method=row.method, llm=row.llm, implication=row.implication,
    )


def load_balance_settings() -> BalanceSettings:
    with session_scope() as s:
        row = s.get(BalanceSettings, 1)
        if row is None:
            row = BalanceSettings(id=1)
            s.add(row)
            s.flush()
        s.expunge(row)
        return row
