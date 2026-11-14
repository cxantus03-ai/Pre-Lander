"""Opportunity detection.

Builds candidate covered structures from the store (matched cross-exchange
pairs, same-market YES+NO, multi-outcome event sets, implication ladders),
sizes them against real order-book depth, classifies them, and tracks their
lifecycle (first seen / disappeared / invalidated).

Classification is strictly separated:
  verified   mathematically covered + settlement verified + fees verified +
             fresh, open, full-depth books + positive worst-case net profit
  potential  positive modelled net profit but at least one assumption is not
             verified (settlement review, estimated fees, inferred structure)
  invalid    gross edge exists but fees / depth / settlement remove it
Opportunities whose quotes are stale or whose markets are suspended are never
'active'; they are shown with state ``stale`` / ``unavailable``.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

from ..core.arbitrage import (
    Constraints,
    CoveredStructure,
    Leg,
    SizingResult,
    all_no_set,
    binary_complement,
    complete_outcome_set,
    implication_pair,
    size_structure,
)
from ..core.domain import EventGroup, Market, MarketStatus, Side, utcnow
from ..matching.matcher import MatchResult, MatchStatus
from ..matching.normalize import thresholds as parse_thresholds
from .store import MarketStore

log = logging.getLogger(__name__)

STRATEGY_LABELS = {
    "cross_exchange_binary": "Cross-exchange YES/NO",
    "same_exchange_binary": "Same-market YES+NO",
    "multi_outcome_yes": "Multi-outcome: buy every YES",
    "multi_outcome_no": "Multi-outcome: buy every NO",
    "implication": "Cross-market implication",
}


@dataclass
class LegSpec:
    market_key: str
    side: Side
    label: str


@dataclass
class Candidate:
    key: str
    strategy: str
    title: str
    category: str
    legs: list[LegSpec]
    build: Callable[[list[Leg]], CoveredStructure]
    structure_verified: bool
    structure_notes: list[str] = field(default_factory=list)
    match_id: str | None = None
    group_key: str | None = None


@dataclass
class Flag:
    level: str  # error | warn | info
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message}


@dataclass
class Opportunity:
    id: str
    key: str
    strategy: str
    title: str
    category: str
    classification: str
    state: str
    legs: list[dict]
    sizing: dict
    user_sizing: dict
    flags: list[Flag]
    match_id: str | None
    match_status: str | None
    group_key: str | None
    close_time: datetime | None
    first_seen: datetime
    last_seen: datetime
    updated_at: datetime
    max_quote_age_s: float
    is_simulated: bool
    exchanges: list[str]
    invalid_reason: str | None = None
    ended_at: datetime | None = None
    end_reason: str | None = None
    peak_net_roi: float = 0.0
    peak_profit: float = 0.0

    @property
    def cross_exchange(self) -> bool:
        return len(set(self.exchanges)) > 1

    @property
    def market_type(self) -> str:
        return "multi" if self.strategy.startswith("multi") else "binary"

    def metric(self, name: str) -> float:
        ev = self.sizing.get("optimal_eval") or {}
        if name == "net_roi":
            return float(ev.get("net_roi_pct") or 0)
        if name == "max_profit":
            return float(ev.get("worst_case_profit") or 0)
        if name == "capital":
            return float(ev.get("total_outlay") or 0)
        if name == "liquidity":
            return float(self.sizing.get("liquidity_qty") or 0)
        if name == "gross_pct":
            return float(self.sizing.get("gross_pct_top") or 0)
        return 0.0

    def summary(self) -> dict:
        ev = self.sizing.get("optimal_eval") or {}
        now = utcnow()
        return {
            "id": self.id,
            "strategy": self.strategy,
            "strategy_label": STRATEGY_LABELS.get(self.strategy, self.strategy),
            "title": self.title,
            "category": self.category,
            "classification": self.classification,
            "state": self.state,
            "legs": self.legs,
            "combined_price": self.sizing.get("combined_top_price"),
            "guaranteed_payout_per_set": self.sizing.get("guaranteed_payout_per_set"),
            "gross_pct": self.sizing.get("gross_pct_top"),
            "net_roi_pct": ev.get("net_roi_pct"),
            "max_profit": ev.get("worst_case_profit"),
            "gross_profit": ev.get("gross_profit"),
            "fees": ev.get("total_fees"),
            "capital_required": ev.get("total_outlay"),
            "optimal_qty": self.sizing.get("optimal_qty"),
            "max_profitable_qty": self.sizing.get("max_profitable_qty"),
            "liquidity_qty": self.sizing.get("liquidity_qty"),
            "top_of_book_qty": self.sizing.get("top_of_book_qty"),
            "user_qty": self.user_sizing.get("recommended_qty"),
            "user_profit": (self.user_sizing.get("recommended_eval") or {}).get("worst_case_profit"),
            "user_limiting_factor": self.user_sizing.get("limiting_factor"),
            "flags": [f.to_dict() for f in self.flags],
            "match_id": self.match_id,
            "match_status": self.match_status,
            "close_time": self.close_time.isoformat() if self.close_time else None,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "age_s": (now - self.first_seen).total_seconds(),
            "max_quote_age_s": round(self.max_quote_age_s, 1),
            "is_simulated": self.is_simulated,
            "exchanges": self.exchanges,
            "cross_exchange": self.cross_exchange,
            "market_type": self.market_type,
            "invalid_reason": self.invalid_reason,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "end_reason": self.end_reason,
        }


@dataclass
class ScanEvent:
    kind: str  # new | updated | disappeared | invalidated | state_changed | settlement_changed
    opp: Opportunity
    previous: dict | None = None


def _oid(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:16]


GT_WORDS = re.compile(r"(above|over|greater|more than|at least|or (?:more|higher|above)|\+|↑|>)", re.I)


def _ladder_strike(m: Market) -> tuple[Decimal, str, bool] | None:
    """(strike, direction, from_exchange_metadata) for threshold-ladder markets."""
    if m.strike is not None and m.strike_type in ("greater", "greater_or_equal", "less", "less_or_equal"):
        return m.strike, "gt" if m.strike_type.startswith("greater") else "lt", True
    label = m.outcome_label or m.title
    th = [t for t in parse_thresholds(label) if t.direction in ("gt", "lt")]
    if len(th) == 1:
        return th[0].value, th[0].direction, False
    return None


class Scanner:
    def __init__(self, store: MarketStore, stale_after_s: float, constraints_provider: Callable[[Opportunity | None], Constraints] | None = None,
                 max_days_provider: Callable[[], int | None] | None = None):
        self.store = store
        self.stale_after_s = stale_after_s
        self.constraints_provider = constraints_provider or (lambda _o: Constraints())
        self.max_days_provider = max_days_provider or (lambda: None)
        self.candidates: dict[str, Candidate] = {}
        self.by_market: dict[str, set[str]] = {}
        self.opps: dict[str, Opportunity] = {}
        self._version = -1
        self.last_scan: datetime | None = None
        self.counters = {"invalid_fees": 0, "invalid_settlement": 0, "disappeared": 0}

    # ------------------------------------------------------------ candidates
    def rebuild_candidates(self) -> None:
        s = self.store
        cands: dict[str, Candidate] = {}

        # A: cross-exchange binary via matches
        for r in s.matches.values():
            a, b = s.markets.get(r.market_a), s.markets.get(r.market_b)
            if not a or not b:
                continue
            if r.status != MatchStatus.INCOMPATIBLE:
                combos = [(Side.YES, Side.NO), (Side.NO, Side.YES)] if r.orientation == "same" else [(Side.YES, Side.YES), (Side.NO, Side.NO)]
                for sa, sb in combos:
                    key = f"x|{a.key}|{sa.value}|{b.key}|{sb.value}"
                    yes_first = sa is Side.YES
                    legs = [LegSpec(a.key, sa, f"{a.exchange} {sa.value.upper()}"), LegSpec(b.key, sb, f"{b.exchange} {sb.value.upper()}")]
                    if not yes_first:
                        legs = legs[::-1]
                    cands[key] = Candidate(
                        key, "cross_exchange_binary", a.title, a.category, legs,
                        build=lambda L: binary_complement(L[0], L[1]),
                        structure_verified=r.status == MatchStatus.VERIFIED,
                        structure_notes=list(r.reasons), match_id=r.id,
                    )
            # D: cross-exchange implication from threshold-only mismatch
            if r.implication:
                other_fails = [c for c in r.checks if c.result == "fail" and c.code != "thresholds"]
                if not other_fails:
                    implying, implied = s.markets.get(r.implication["implying"]), s.markets.get(r.implication["implied"])
                    if implying and implied:
                        key = f"imp|{implied.key}|{implying.key}"
                        cands[key] = Candidate(
                            key, "implication", f"{implied.title}  ⇐  {implying.title}", implied.category,
                            [LegSpec(implied.key, Side.YES, f"{implied.exchange} YES (implied)"), LegSpec(implying.key, Side.NO, f"{implying.exchange} NO (implying)")],
                            build=lambda L: implication_pair(L[0], L[1]), structure_verified=False,
                            structure_notes=["Implication inferred from differing thresholds across exchanges; settlement sources/rules may differ."],
                            match_id=r.id,
                        )

        # B: same-market YES + NO
        for m in s.markets.values():
            key = f"s|{m.key}"
            cands[key] = Candidate(
                key, "same_exchange_binary", m.title, m.category,
                [LegSpec(m.key, Side.YES, f"{m.exchange} YES"), LegSpec(m.key, Side.NO, f"{m.exchange} NO")],
                build=lambda L: binary_complement(L[0], L[1]), structure_verified=True,
                structure_notes=["YES and NO of the same contract are complementary by construction."],
            )

        # C: multi-outcome sets & D: ladders inside one event
        for g in s.groups.values():
            members = [s.markets[k] for k in g.market_keys if k in s.markets]
            if len(members) < 2:
                continue
            if g.mutually_exclusive:
                exhaustive = bool(g.exhaustive)
                cands[f"my|{g.key}"] = Candidate(
                    f"my|{g.key}", "multi_outcome_yes", g.title, g.category,
                    [LegSpec(m.key, Side.YES, m.outcome_label or m.title) for m in members],
                    build=(lambda L, ex=exhaustive: complete_outcome_set(L, ex)), structure_verified=exhaustive,
                    structure_notes=[g.exhaustive_reason], group_key=g.key,
                )
                cands[f"mn|{g.key}"] = Candidate(
                    f"mn|{g.key}", "multi_outcome_no", g.title, g.category,
                    [LegSpec(m.key, Side.NO, m.outcome_label or m.title) for m in members],
                    build=(lambda L, ex=exhaustive: all_no_set(L, ex)), structure_verified=True,
                    structure_notes=["Outcomes declared mutually exclusive by the exchange: at most one NO loses."], group_key=g.key,
                )
            else:
                ladder = [(m, _ladder_strike(m)) for m in members]
                ladder = [(m, st) for m, st in ladder if st is not None]
                for direction in ("gt", "lt"):
                    rungs = sorted([(st[0], m, st[2]) for m, st in ladder if st[1] == direction], key=lambda t: t[0])
                    for (lo_v, lo_m, lo_meta), (hi_v, hi_m, hi_meta) in zip(rungs, rungs[1:]):
                        implying, implied = (hi_m, lo_m) if direction == "gt" else (lo_m, hi_m)
                        meta = lo_meta and hi_meta
                        key = f"imp|{implied.key}|{implying.key}"
                        cands[key] = Candidate(
                            key, "implication", f"{g.title}: {implied.outcome_label or implied.title} ⇐ {implying.outcome_label or implying.title}",
                            g.category,
                            [LegSpec(implied.key, Side.YES, f"YES {implied.outcome_label or implied.title}"),
                             LegSpec(implying.key, Side.NO, f"NO {implying.outcome_label or implying.title}")],
                            build=lambda L: implication_pair(L[0], L[1]), structure_verified=meta,
                            structure_notes=["Strike ladder within one event (exchange strike metadata)." if meta
                                             else "Ladder inferred from outcome wording; verify the strikes and settlement."],
                            group_key=g.key,
                        )
        self.candidates = cands
        self.by_market = {}
        for c in cands.values():
            for leg in c.legs:
                self.by_market.setdefault(leg.market_key, set()).add(c.key)
        self._version = s.structure_version

    # ----------------------------------------------------------------- scan
    def scan(self, dirty: set[str] | None = None, full: bool = False) -> list[ScanEvent]:
        if self._version != self.store.structure_version:
            self.rebuild_candidates()
            full = True
        if full or dirty is None:
            keys = set(self.candidates)
        else:
            keys = set()
            for k in dirty:
                keys |= self.by_market.get(k, set())
        events: list[ScanEvent] = []
        for ck in keys:
            c = self.candidates.get(ck)
            if c is None:
                continue
            try:
                ev = self._evaluate(c)
            except Exception:  # noqa: BLE001 - one bad market must not stop the scan
                log.exception("scan failed for %s", ck)
                continue
            if ev:
                events.append(ev)
        # candidates that vanished (market delisted / match rejected)
        for oid, opp in list(self.opps.items()):
            if opp.key not in self.candidates and opp.state not in ("expired",):
                ended = self._end(opp, "Market or match no longer available")
                if ended:
                    events.append(ended)
        self._prune()
        self.last_scan = utcnow()
        return events

    def refresh_staleness(self) -> list[ScanEvent]:
        """Re-evaluate opportunities whose quotes may have aged past the threshold."""
        keys = {o.key for o in self.opps.values() if o.state in ("active", "stale")}
        dirty = set()
        for k in keys:
            c = self.candidates.get(k)
            if c:
                dirty |= {l.market_key for l in c.legs}
        return self.scan(dirty)

    def _prune(self) -> None:
        cutoff = utcnow() - timedelta(minutes=30)
        for oid in [oid for oid, o in self.opps.items() if o.state == "expired" and o.ended_at and o.ended_at < cutoff]:
            del self.opps[oid]
        # invalid entries are kept only briefly in memory (counted in history)
        cutoff = utcnow() - timedelta(minutes=5)
        for oid in [oid for oid, o in self.opps.items() if o.state == "invalid" and o.last_seen < cutoff]:
            del self.opps[oid]

    def _end(self, opp: Opportunity, reason: str) -> ScanEvent | None:
        if opp.state == "invalid":
            # never was a profitable opportunity: drop quietly (already counted as invalidated)
            self.opps.pop(opp.id, None)
            return None
        prev = opp.summary()
        opp.state = "expired"
        opp.ended_at = utcnow()
        opp.end_reason = reason
        self.counters["disappeared"] += 1
        return ScanEvent("disappeared", opp, prev)

    def _evaluate(self, c: Candidate) -> ScanEvent | None:
        s = self.store
        oid = _oid(c.key)
        existing = self.opps.get(oid)
        markets: list[Market] = []
        legs: list[Leg] = []
        now = utcnow()
        max_age = 0.0
        flags: list[Flag] = []
        for spec in c.legs:
            m = s.markets.get(spec.market_key)
            book = s.books.get(spec.market_key)
            if m is None or book is None:
                if existing and existing.state != "expired":
                    return self._end(existing, "Order book unavailable")
                return None
            markets.append(m)
            max_age = max(max_age, book.age_seconds(now))
            if book.is_stale(self.stale_after_s, now):
                flags.append(Flag("error", "STALE_QUOTE", f"{m.exchange} quote is {book.age_seconds(now):.0f}s old" + (" (feed disconnected)" if book.force_stale else "")))
            if not book.depth_complete:
                flags.append(Flag("warn", "INCOMPLETE_BOOK", f"{m.exchange} order book depth incomplete"))
            legs.append(Leg("", m.exchange, m.key, spec.side, list(book.asks(spec.side)), m.fee, m.payout, m.qty_increment, m.min_order_size, spec.label))

        # quick gross screen before any heavy work
        if any(not l.asks for l in legs):
            if existing and existing.state != "expired":
                return self._end(existing, "No executable ask on at least one leg")
            return None
        structure = c.build(legs)
        combined = sum((l.asks[0].price for l in legs), Decimal(0))
        if combined >= structure.guaranteed_payout:
            if existing and existing.state not in ("expired",):
                return self._end(existing, "Combined price moved above payout")
            return None

        # flags -------------------------------------------------------------
        for m in markets:
            if m.status is MarketStatus.SUSPENDED:
                flags.append(Flag("error", "SUSPENDED", f"{m.exchange} market is suspended / not accepting orders"))
            elif m.status in (MarketStatus.CLOSED, MarketStatus.SETTLED):
                flags.append(Flag("error", "CLOSED", f"{m.exchange} market is {m.status.value}"))
            elif m.status is MarketStatus.UNKNOWN:
                flags.append(Flag("warn", "STATUS_UNKNOWN", f"{m.exchange} market status unknown"))
            if m.close_time and m.close_time < now:
                flags.append(Flag("error", "PAST_CLOSE", f"{m.exchange} market passed its scheduled close"))
            if not m.fee.verified:
                flags.append(Flag("warn", "FEES_ESTIMATED", f"{m.exchange} fees not verified: {m.fee.notes or m.fee.source}"))
            if m.is_simulated:
                flags.append(Flag("info", "SIMULATED", "Simulated DEMO data, not a real quote"))
        match = s.matches.get(c.match_id) if c.match_id else None
        match_status = match.status.value if match else None
        if c.strategy == "cross_exchange_binary" and match:
            if match.status == MatchStatus.LIKELY:
                flags.append(Flag("warn", "SETTLEMENT_REVIEW", "Settlement equivalence likely but not verified — review the rules"))
            elif match.status == MatchStatus.INSUFFICIENT:
                flags.append(Flag("warn", "SETTLEMENT_UNKNOWN", "Insufficient information to verify settlement equivalence"))
            elif match.status == MatchStatus.INCOMPATIBLE:
                flags.append(Flag("error", "SETTLEMENT_INCOMPATIBLE", "Markets have incompatible settlement rules"))
        if not c.structure_verified and c.strategy != "cross_exchange_binary":
            code = "INCOMPLETE_OUTCOME_SET" if c.strategy == "multi_outcome_yes" else "STRUCTURE_UNVERIFIED"
            flags.append(Flag("warn" if c.strategy == "implication" else "error", code, "; ".join(n for n in c.structure_notes if n)))
        max_days = self.max_days_provider()
        close = min((m.close_time for m in markets if m.close_time), default=None)
        if max_days and close and close > now + timedelta(days=max_days):
            flags.append(Flag("info", "BEYOND_HORIZON", f"Settles after your {max_days}-day horizon"))
        if c.strategy == "cross_exchange_binary" and len(markets) == 2:
            # Equivalent contracts should imply similar probabilities. A huge gap is far more
            # likely to be a matching error than a real arbitrage.
            mids = []
            for m, spec in zip(markets, c.legs):
                b = s.books[m.key]
                yb, ya = b.best_bid(Side.YES), b.best_ask(Side.YES)
                if yb and ya:
                    mid = (yb.price + ya.price) / 2
                    # convert to "probability of the event the YES leg pays on"
                    mids.append(mid if spec.side is Side.YES else 1 - mid)
            if len(mids) == 2 and abs(mids[0] - (1 - mids[1])) > Decimal("0.25"):
                flags.append(Flag("warn", "PRICE_DIVERGENCE",
                                  "The two contracts imply probabilities more than 25 points apart — verify they describe the same event."))
        ex_ts = [s.books[l.market_key].exchange_ts for l in legs if s.books[l.market_key].exchange_ts]
        if len(ex_ts) > 1 and (max(ex_ts) - min(ex_ts)).total_seconds() > self.stale_after_s:
            flags.append(Flag("warn", "QUOTE_SKEW", "Quotes were captured at materially different times"))

        seen_flags: set[tuple[str, str]] = set()
        flags = [f for f in flags if not ((f.code, f.message) in seen_flags or seen_flags.add((f.code, f.message)))]

        # sizing ------------------------------------------------------------
        sizing: SizingResult = size_structure(structure)
        user: SizingResult = size_structure(c.build([Leg(l.leg_id, l.exchange, l.market_key, l.side, l.asks, l.fee, l.payout, l.qty_increment, l.min_order_size, l.label) for l in legs]),
                                            self.constraints_provider(existing))
        if sizing.is_profitable and sizing.recommended_qty < structure.min_qty:
            flags.append(Flag("warn", "INSUFFICIENT_LIQUIDITY", "Profitable size below minimum order size"))

        errors = {f.code for f in flags if f.level == "error"}
        warns = {f.code for f in flags if f.level == "warn"}
        invalid_reason = None
        if not sizing.is_profitable:
            classification, state = "invalid", "invalid"
            invalid_reason = "fees" if (sizing.gross_profit_per_set_top or 0) > 0 else "price"
            if sizing.reasons and "depth" in " ".join(sizing.reasons).lower():
                invalid_reason = "liquidity"
        elif "SETTLEMENT_INCOMPATIBLE" in errors or "INCOMPLETE_OUTCOME_SET" in errors:
            classification, state = "invalid", "invalid"
            invalid_reason = "settlement"
        else:
            unverified = warns & {"SETTLEMENT_REVIEW", "SETTLEMENT_UNKNOWN", "FEES_ESTIMATED", "STRUCTURE_UNVERIFIED", "INCOMPLETE_BOOK", "STATUS_UNKNOWN", "PRICE_DIVERGENCE"}
            classification = "potential" if unverified or not c.structure_verified else "verified"
            if errors & {"STALE_QUOTE"}:
                state = "stale"
            elif errors & {"SUSPENDED", "CLOSED", "PAST_CLOSE"}:
                state = "unavailable"
            else:
                state = "active"
            if state != "active" and classification == "verified":
                classification = "potential"  # never verified while not currently executable

        leg_info = []
        for spec, m, l in zip(c.legs, markets, legs):
            b = s.books[m.key]
            leg_info.append({
                "market_key": m.key, "exchange": m.exchange, "side": spec.side.value, "label": spec.label,
                "title": m.title, "outcome_label": m.outcome_label, "url": m.url,
                "best_ask": str(l.asks[0].price) if l.asks else None,
                "best_ask_size": str(l.asks[0].size) if l.asks else None,
                "depth": str(l.depth), "fee_kind": m.fee.kind.value, "fee_verified": m.fee.verified,
                "quote_age_s": round(b.age_seconds(now), 1), "status": m.status.value,
                "received_at": b.received_at.isoformat(), "exchange_ts": b.exchange_ts.isoformat() if b.exchange_ts else None,
            })

        opp_prev = existing.summary() if existing else None
        if existing is None or existing.state == "expired":
            opp = Opportunity(
                id=oid, key=c.key, strategy=c.strategy, title=c.title, category=c.category, classification=classification,
                state=state, legs=leg_info, sizing=sizing.to_dict(), user_sizing=user.to_dict(), flags=flags,
                match_id=c.match_id, match_status=match_status, group_key=c.group_key, close_time=close,
                first_seen=now, last_seen=now, updated_at=now, max_quote_age_s=max_age,
                is_simulated=any(m.is_simulated for m in markets), exchanges=[m.exchange for m in markets],
                invalid_reason=invalid_reason,
            )
            self.opps[oid] = opp
            if state == "invalid":
                self.counters["invalid_fees" if invalid_reason in ("fees", "liquidity") else "invalid_settlement"] += 1
                return ScanEvent("invalidated", opp, None)
            return ScanEvent("new", opp, None)

        opp = existing
        prev_state, prev_class, prev_match = opp.state, opp.classification, opp.match_status
        opp.classification, opp.state, opp.legs, opp.flags = classification, state, leg_info, flags
        opp.sizing, opp.user_sizing = sizing.to_dict(), user.to_dict()
        opp.match_status, opp.close_time, opp.max_quote_age_s = match_status, close, max_age
        opp.last_seen = opp.updated_at = now
        opp.invalid_reason = invalid_reason
        opp.peak_net_roi = max(opp.peak_net_roi, opp.metric("net_roi"))
        opp.peak_profit = max(opp.peak_profit, opp.metric("max_profit"))
        if prev_match != match_status:
            return ScanEvent("settlement_changed", opp, opp_prev)
        if state == "invalid" and prev_state != "invalid":
            self.counters["invalid_fees" if invalid_reason in ("fees", "liquidity") else "invalid_settlement"] += 1
            return ScanEvent("invalidated", opp, opp_prev)
        if prev_state != state or prev_class != classification:
            return ScanEvent("state_changed", opp, opp_prev)
        return ScanEvent("updated", opp, opp_prev)

    # --------------------------------------------------------------- query
    def list(self, include_inactive: bool = False) -> list[Opportunity]:
        out = [o for o in self.opps.values() if include_inactive or o.state in ("active", "stale", "unavailable")]
        return out
