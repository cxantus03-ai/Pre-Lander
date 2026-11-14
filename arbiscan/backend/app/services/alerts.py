"""Alert rules evaluation with de-duplication and cooldowns."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from sqlalchemy import select

from ..db import session_scope
from ..models import AlertConfig, NotificationEvent
from .notify import EmailSender, WebSocketHub
from .scanner import Opportunity, ScanEvent

log = logging.getLogger(__name__)

EVENT_TYPES = {
    "new_verified": "New verified arbitrage opportunity",
    "new_potential": "New potential opportunity (needs verification)",
    "roi_threshold": "Net ROI exceeds threshold",
    "profit_threshold": "Estimated profit exceeds threshold",
    "liquidity_executable": "Opportunity gained enough liquidity",
    "disappeared": "Opportunity disappeared",
    "unprofitable_after_fees": "Opportunity became unprofitable after fees",
    "price_change": "Significant price change",
    "exchange_disconnected": "Exchange disconnected",
    "stale_data": "Market data became stale",
    "settlement_changed": "Settlement compatibility changed",
}


@dataclass
class Rule:
    id: int
    name: str
    event_types: set[str]
    min_net_roi: float | None
    min_profit: float | None
    min_qty: float | None
    min_liquidity: float | None
    price_change_threshold: float
    categories: set[str]
    exchanges: set[str]
    cross_exchange_only: bool
    verified_only: bool
    notify_in_app: bool
    notify_browser: bool
    notify_email: bool
    email_to: str | None
    cooldown_s: int

    @classmethod
    def from_row(cls, r: AlertConfig) -> "Rule":
        return cls(r.id, r.name, set(r.event_types or []), r.min_net_roi, r.min_profit, r.min_qty, r.min_liquidity,
                   r.price_change_threshold or 0.03, set(r.categories or []), set(r.exchanges or []), r.cross_exchange_only,
                   r.verified_only, r.notify_in_app, r.notify_browser, r.notify_email, r.email_to, r.cooldown_s or 300)

    def matches_opp(self, o: Opportunity) -> bool:
        if self.categories and o.category not in self.categories:
            return False
        if self.exchanges and not (set(o.exchanges) & self.exchanges):
            return False
        if self.cross_exchange_only and not o.cross_exchange:
            return False
        if self.verified_only and o.classification != "verified":
            return False
        if self.min_liquidity is not None and o.metric("liquidity") < self.min_liquidity:
            return False
        return True


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


class AlertEngine:
    def __init__(self, hub: WebSocketHub, email: EmailSender, default_cooldown_s: int = 300, max_per_minute: int = 30):
        self.hub = hub
        self.email = email
        self.default_cooldown_s = default_cooldown_s
        self.max_per_minute = max_per_minute
        self.rules: list[Rule] = []
        self._last_sent: dict[str, float] = {}
        self._window: list[float] = []

    def reload(self) -> None:
        with session_scope() as s:
            rows = list(s.scalars(select(AlertConfig).where(AlertConfig.enabled.is_(True))))
            self.rules = [Rule.from_row(r) for r in rows]

    @staticmethod
    def ensure_default_rules() -> None:
        with session_scope() as s:
            if s.scalar(select(AlertConfig.id).limit(1)) is None:
                s.add(AlertConfig(name="Verified opportunities", event_types=["new_verified", "disappeared", "unprofitable_after_fees", "settlement_changed"],
                                  verified_only=True, notify_in_app=True, notify_browser=True))
                s.add(AlertConfig(name="System health", event_types=["exchange_disconnected", "stale_data"], notify_in_app=True, notify_browser=False))

    # ------------------------------------------------------------------
    def classify(self, ev: ScanEvent, rule: Rule) -> list[tuple[str, str, str]]:
        """(event_type, title, body) tuples this scan event triggers for ``rule``."""
        o = ev.opp
        out: list[tuple[str, str, str]] = []
        s = o.summary()
        roi, profit, qty = _f(s["net_roi_pct"]), _f(s["max_profit"]), _f(s["optimal_qty"])
        prev = ev.previous or {}
        p_roi, p_profit, p_qty = _f(prev.get("net_roi_pct")), _f(prev.get("max_profit")), _f(prev.get("optimal_qty"))
        desc = f"{o.title} — net ROI {roi:.2f}%, max modelled profit ${profit:,.2f} ({', '.join(sorted(set(o.exchanges)))})"
        if ev.kind == "new" and o.state == "active":
            if o.classification == "verified":
                out.append(("new_verified", "New verified arbitrage", desc))
            elif o.classification == "potential":
                out.append(("new_potential", "New potential opportunity — needs verification", desc))
        if o.state == "active" and o.classification in ("verified", "potential"):
            if rule.min_net_roi is not None and roi >= rule.min_net_roi and (ev.kind == "new" or p_roi < rule.min_net_roi):
                out.append(("roi_threshold", f"Net ROI ≥ {rule.min_net_roi}%", desc))
            if rule.min_profit is not None and profit >= rule.min_profit and (ev.kind == "new" or p_profit < rule.min_profit):
                out.append(("profit_threshold", f"Modelled profit ≥ ${rule.min_profit}", desc))
            min_q = rule.min_qty or 1
            if ev.kind != "new" and p_qty < min_q <= qty:
                out.append(("liquidity_executable", "Opportunity now has executable depth", desc))
        if ev.kind == "disappeared":
            out.append(("disappeared", "Opportunity disappeared", f"{o.title} — {o.end_reason or 'no longer available'}"))
        if ev.kind == "invalidated" and o.invalid_reason in ("fees", "liquidity") and ev.previous:
            out.append(("unprofitable_after_fees", "No longer profitable after fees", f"{o.title}"))
        if ev.kind == "settlement_changed":
            out.append(("settlement_changed", "Settlement compatibility changed", f"{o.title}: {prev.get('match_status')} → {o.match_status}"))
        if ev.kind == "state_changed" and o.state == "stale" and prev.get("state") != "stale":
            out.append(("stale_data", "Opportunity quotes are stale", o.title))
        if ev.previous and ev.kind in ("updated", "state_changed"):
            prev_legs = {(l["market_key"], l["side"]): _f(l.get("best_ask")) for l in prev.get("legs", [])}
            for l in o.legs:
                before = prev_legs.get((l["market_key"], l["side"]))
                now = _f(l.get("best_ask"))
                if before and abs(now - before) >= rule.price_change_threshold:
                    out.append(("price_change", "Significant price change", f"{l['label']}: {before:.3f} → {now:.3f} on {o.title}"))
                    break
        return out

    async def process(self, events: list[ScanEvent], system: list[dict] | None = None) -> list[dict]:
        if not self.rules:
            return []
        created: list[dict] = []
        for rule in self.rules:
            for ev in events:
                if not rule.matches_opp(ev.opp):
                    continue
                for etype, title, body in self.classify(ev, rule):
                    if etype in rule.event_types:
                        n = await self._emit(rule, etype, title, body, ev.opp.id, "success" if etype.startswith("new_verified") else "info")
                        if n:
                            created.append(n)
            for sev in system or []:
                if sev["type"] in rule.event_types:
                    n = await self._emit(rule, sev["type"], sev["title"], sev["body"], None, "warning", dedupe_extra=sev.get("key", ""))
                    if n:
                        created.append(n)
        return created

    async def _emit(self, rule: Rule, etype: str, title: str, body: str, opp_id: str | None, severity: str, dedupe_extra: str = "") -> dict | None:
        now = time.time()
        dedupe = f"{rule.id}:{etype}:{opp_id or dedupe_extra}"
        if now - self._last_sent.get(dedupe, 0) < (rule.cooldown_s or self.default_cooldown_s):
            return None
        self._window = [t for t in self._window if now - t < 60]
        if len(self._window) >= self.max_per_minute:
            return None
        self._last_sent[dedupe] = now
        self._window.append(now)
        channels = [c for c, on in (("in_app", rule.notify_in_app), ("browser", rule.notify_browser), ("email", rule.notify_email)) if on]
        email_status = None
        if rule.notify_email and rule.email_to:
            email_status = await asyncio.to_thread(self.email.send, rule.email_to, f"[ArbiScan] {title}", body)
        payload = await asyncio.to_thread(self._persist, rule.id, etype, severity, title, body, opp_id, dedupe, channels, email_status)
        await self.hub.broadcast({"type": "notification", "data": payload})
        return payload

    @staticmethod
    def _persist(rule_id, etype, severity, title, body, opp_id, dedupe, channels, email_status) -> dict:
        with session_scope() as s:
            n = NotificationEvent(alert_id=rule_id, event_type=etype, severity=severity, title=title, body=body,
                                  opportunity_id=opp_id, dedupe_key=dedupe, channels=channels, email_status=email_status)
            s.add(n)
            s.flush()
            return notification_dict(n)


def notification_dict(n: NotificationEvent) -> dict:
    return {
        "id": n.id, "alert_id": n.alert_id, "event_type": n.event_type, "severity": n.severity, "title": n.title,
        "body": n.body, "opportunity_id": n.opportunity_id, "channels": n.channels, "email_status": n.email_status,
        "read": n.read, "created_at": n.created_at.isoformat() if n.created_at else None,
    }
