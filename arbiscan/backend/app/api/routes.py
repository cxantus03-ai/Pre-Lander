"""REST + WebSocket API. Read-only with respect to exchanges: there is no
endpoint that places orders, moves funds or logs into an exchange account."""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select, update

from ..core.arbitrage import Constraints, Leg, all_no_set, binary_complement, complete_outcome_set, size_structure
from ..core.domain import D, FeeKind, FeeSpec, PriceLevel, Side, utcnow
from ..db import session_scope
from ..matching.matcher import MatchStatus, compare
from ..matching.normalize import extract
from ..models import AlertConfig, BalanceSettings, MatchVerification, NotificationEvent, OpportunityRow, OpportunitySnapshot, RecordedSettlement, RecordedTrade
from ..schemas import (
    AlertInput,
    CalculatorInput,
    CapitalInput,
    ManualCalcInput,
    ManualMatchInput,
    MatchReviewInput,
    RuntimeSettingsInput,
    SettlementInput,
    TradeInput,
)
from ..services import analytics
from ..services.alerts import EVENT_TYPES, notification_dict
from ..services.persistence import as_utc
from ..services.runtime import Runtime

router = APIRouter(prefix="/api")
ws_router = APIRouter()

DISCLAIMER = ("Theoretical analysis only. Even a verified mathematical arbitrage can fail to deliver the modelled "
              "result because of execution (prices move, partial fills), resolution disputes, account/geographic "
              "access, exchange or counterparty risk. ArbiScan never places trades.")


def rt(request: Request) -> Runtime:
    return request.app.state.runtime


def _market_dict(m, book=None) -> dict:
    return {
        "key": m.key, "exchange": m.exchange, "external_id": m.external_id, "title": m.title, "event_id": m.event_id,
        "event_title": m.event_title, "outcome_label": m.outcome_label, "yes_label": m.yes_label, "no_label": m.no_label,
        "category": m.category, "raw_category": m.raw_category, "status": m.status.value, "url": m.url,
        "close_time": m.close_time.isoformat() if m.close_time else None,
        "expiration_time": m.expiration_time.isoformat() if m.expiration_time else None,
        "payout": str(m.payout), "tick_size": str(m.tick_size), "min_order_size": str(m.min_order_size),
        "qty_increment": str(m.qty_increment), "fee": m.fee.to_dict(), "volume": str(m.volume), "volume_24h": str(m.volume_24h),
        "open_interest": str(m.open_interest) if m.open_interest is not None else None,
        "liquidity": str(m.liquidity) if m.liquidity is not None else None,
        "is_simulated": m.is_simulated, "top": m.extra.get("top"), "has_book": book is not None,
        "book_age_s": round(book.age_seconds(), 1) if book else None,
        "yes_token_id": m.yes_token_id, "no_token_id": m.no_token_id,
    }


def _market_detail(r: Runtime, key: str) -> dict:
    m = r.store.markets.get(key)
    if m is None:
        raise HTTPException(404, "market not found")
    b = r.store.books.get(key)
    d = _market_dict(m, b)
    d.update({
        "description": m.description, "rules_primary": m.rules_primary, "rules_secondary": m.rules_secondary,
        "resolution_sources": m.resolution_sources, "can_close_early": m.can_close_early,
        "early_close_condition": m.early_close_condition, "extra": {k: v for k, v in m.extra.items() if k != "top"},
        "book": b.to_dict(30) if b else None,
        "book_stale": b.is_stale(r.cfg["stale_after_s"]) if b else None,
        "exchange_updated_at": m.exchange_updated_at.isoformat() if m.exchange_updated_at else None,
        "received_at": m.received_at.isoformat(),
    })
    return d


# --------------------------------------------------------------------- status
@router.get("/health")
def health(r: Runtime = Depends(rt)):
    return {"ok": True, "mode": r.mode, "time": utcnow().isoformat()}


@router.get("/status")
def status(r: Runtime = Depends(rt)):
    return {"mode": r.mode, "mode_reason": r.mode_reason, "connectors": r.connector_statuses(), "runtime": r.cfg,
            "settings": r.settings.public_settings(), "started_at": r.started_at.isoformat(), "matching": r.matching,
            "disclaimer": DISCLAIMER}


@router.get("/settings")
def get_settings_view(r: Runtime = Depends(rt)):
    return {"public": r.settings.public_settings(), "runtime": r.cfg, "event_types": EVENT_TYPES}


@router.put("/settings/runtime")
def put_runtime_settings(body: RuntimeSettingsInput, r: Runtime = Depends(rt)):
    return r.update_runtime_settings(body.model_dump(exclude_none=True))


@router.get("/dashboard/summary")
def dashboard(r: Runtime = Depends(rt)):
    return r.dashboard_summary() | {"disclaimer": DISCLAIMER}


# -------------------------------------------------------------- opportunities
SORTS = {
    "net_roi": lambda o: o.metric("net_roi"),
    "profit": lambda o: o.metric("max_profit"),
    "liquidity": lambda o: o.metric("liquidity"),
    "capital": lambda o: o.metric("capital"),
    "recent": lambda o: o.first_seen.timestamp(),
    "closing": lambda o: o.close_time.timestamp() if o.close_time else float("inf"),
    "exchange": lambda o: ",".join(sorted(o.exchanges)),
    "category": lambda o: o.category,
}


@router.get("/opportunities")
def list_opportunities(
    r: Runtime = Depends(rt),
    sort: str = Query("net_roi"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    classification: list[str] = Query(default=[]),
    state: list[str] = Query(default=[]),
    exchange: list[str] = Query(default=[]),
    category: list[str] = Query(default=[]),
    strategy: list[str] = Query(default=[]),
    scope: str | None = Query(default=None, pattern="^(cross|same)$"),
    market_type: str | None = Query(default=None, pattern="^(binary|multi)$"),
    min_roi: float | None = None,
    min_profit: float | None = None,
    min_liquidity: float | None = None,
    match_status: list[str] = Query(default=[]),
    close_before: datetime | None = None,
    close_after: datetime | None = None,
    q: str | None = Query(default=None, max_length=200),
    include_invalid: bool = False,
    limit: int = Query(500, ge=1, le=5000),
):
    opps = r.scanner.list(include_inactive=include_invalid)
    if not include_invalid:
        opps = [o for o in opps if o.state != "invalid"]
    def keep(o) -> bool:
        if classification and o.classification not in classification:
            return False
        if state and o.state not in state:
            return False
        if exchange and not (set(exchange) & set(o.exchanges)):
            return False
        if category and o.category not in category:
            return False
        if strategy and o.strategy not in strategy:
            return False
        if scope == "cross" and not o.cross_exchange:
            return False
        if scope == "same" and o.cross_exchange:
            return False
        if market_type and o.market_type != market_type:
            return False
        if min_roi is not None and o.metric("net_roi") < min_roi:
            return False
        if min_profit is not None and o.metric("max_profit") < min_profit:
            return False
        if min_liquidity is not None and o.metric("liquidity") < min_liquidity:
            return False
        if match_status and (o.match_status or "n/a") not in match_status:
            return False
        if close_before and o.close_time and o.close_time > as_utc(close_before):
            return False
        if close_after and o.close_time and o.close_time < as_utc(close_after):
            return False
        if q:
            hay = (o.title + " " + " ".join(l.get("title", "") for l in o.legs)).lower()
            if q.lower() not in hay:
                return False
        return True
    opps = [o for o in opps if keep(o)]
    opps.sort(key=SORTS.get(sort, SORTS["net_roi"]), reverse=(order == "desc"))
    return {"items": [o.summary() for o in opps[:limit]], "total": len(opps), "disclaimer": DISCLAIMER}


@router.get("/opportunities/recent-ended")
def recently_ended(r: Runtime = Depends(rt), limit: int = Query(50, le=500)):
    with session_scope() as s:
        rows = list(s.scalars(select(OpportunityRow).where(OpportunityRow.state == "expired").order_by(OpportunityRow.ended_at.desc()).limit(limit)))
        return [{"id": x.id, "title": x.title, "strategy": x.strategy, "classification": x.classification, "exchanges": x.exchanges,
                 "first_seen": as_utc(x.first_seen).isoformat(), "ended_at": as_utc(x.ended_at).isoformat() if x.ended_at else None,
                 "end_reason": x.end_reason, "peak_net_roi": x.peak_net_roi, "peak_profit": x.peak_profit, "is_simulated": x.is_simulated} for x in rows]


def _find_opp(r: Runtime, oid: str):
    o = r.scanner.opps.get(oid)
    if o is None:
        raise HTTPException(404, "opportunity not found or no longer tracked in memory")
    return o


@router.get("/opportunities/{oid}")
def opportunity_detail(oid: str, r: Runtime = Depends(rt)):
    o = _find_opp(r, oid)
    legs_markets = []
    for l in o.legs:
        try:
            legs_markets.append(_market_detail(r, l["market_key"]))
        except HTTPException:
            legs_markets.append(None)
    match = r.store.matches.get(o.match_id).to_dict() if o.match_id and o.match_id in r.store.matches else None
    group = r.store.groups.get(o.group_key) if o.group_key else None
    with session_scope() as s:
        snaps = list(s.scalars(select(OpportunitySnapshot).where(OpportunitySnapshot.opportunity_id == oid).order_by(OpportunitySnapshot.ts.desc()).limit(300)))
        history = [{"ts": as_utc(x.ts).isoformat(), "net_roi": x.net_roi, "max_profit": x.max_profit, "combined_price": x.combined_price,
                    "liquidity": x.liquidity, "state": x.state, "classification": x.classification} for x in reversed(snaps)]
    keys = list(dict.fromkeys(l["market_key"] for l in o.legs))
    prices = analytics.price_history(keys[:2]) if keys else {}
    return {
        "opportunity": o.summary(),
        "sizing": o.sizing,
        "user_sizing": o.user_sizing,
        "markets": legs_markets,
        "match": match,
        "group": {"title": group.title, "mutually_exclusive": group.mutually_exclusive, "exhaustive": group.exhaustive,
                  "exhaustive_reason": group.exhaustive_reason, "market_keys": group.market_keys} if group else None,
        "history": history,
        "prices": prices,
        "disclaimer": DISCLAIMER,
    }


def _constraints_from(body: CalculatorInput, r: Runtime) -> Constraints:
    base = r.constraints()
    budgets = dict(base.exchange_budgets or {})
    if body.exchange_balances:
        budgets.update({k: D(v) for k, v in body.exchange_balances.items()})
    caps = []
    if body.total_capital is not None:
        caps.append(body.total_capital)
        if body.max_pct_bankroll is not None:
            caps.append(body.total_capital * body.max_pct_bankroll / 100)
    if body.max_allocation is not None:
        caps.append(body.max_allocation)
    max_total = min(caps) if caps else base.max_total_outlay
    return Constraints(
        exchange_budgets=budgets,
        max_total_outlay=max_total,
        max_qty=body.max_qty,
        max_slippage_pct=body.max_slippage_pct if body.max_slippage_pct is not None else base.max_slippage_pct,
        min_profit=body.min_profit if body.min_profit is not None else base.min_profit,
        min_roi_pct=body.min_roi_pct if body.min_roi_pct is not None else base.min_roi_pct,
    )


def _calc_response(result, constraints: Constraints, legs: list[Leg]) -> dict:
    out = result.to_dict()
    ev = result.recommended_eval
    spent = ev.outlay_by_exchange() if ev else {}
    out["remaining_balances"] = {ex: str((b - spent.get(ex, Decimal(0))).quantize(Decimal("0.01")))
                                 for ex, b in (constraints.exchange_budgets or {}).items() if ex in {l.exchange for l in legs}}
    out["constraints"] = {
        "exchange_budgets": {k: str(v) for k, v in (constraints.exchange_budgets or {}).items()},
        "max_total_outlay": str(constraints.max_total_outlay) if constraints.max_total_outlay is not None else None,
        "max_slippage_pct": str(constraints.max_slippage_pct) if constraints.max_slippage_pct is not None else None,
        "min_profit": str(constraints.min_profit) if constraints.min_profit is not None else None,
        "min_roi_pct": str(constraints.min_roi_pct) if constraints.min_roi_pct is not None else None,
    }
    out["disclaimer"] = DISCLAIMER
    return out


@router.post("/opportunities/{oid}/calculate")
def calculate(oid: str, body: CalculatorInput, r: Runtime = Depends(rt)):
    o = _find_opp(r, oid)
    cand = r.scanner.candidates.get(o.key)
    if cand is None:
        raise HTTPException(409, "opportunity structure no longer available")
    legs = []
    for spec in cand.legs:
        m, b = r.store.markets.get(spec.market_key), r.store.books.get(spec.market_key)
        if m is None or b is None:
            raise HTTPException(409, "order book unavailable for a leg")
        legs.append(Leg("", m.exchange, m.key, spec.side, list(b.asks(spec.side)), m.fee, m.payout, m.qty_increment, m.min_order_size, spec.label))
    c = _constraints_from(body, r)
    res = size_structure(cand.build(legs), c)
    out = _calc_response(res, c, legs)
    out["opportunity"] = o.summary()
    return out


@router.post("/calculator/manual")
def manual_calculator(body: ManualCalcInput, r: Runtime = Depends(rt)):
    legs = []
    for i, l in enumerate(body.legs):
        fee = FeeSpec(FeeKind(l.fee_kind), rate=l.fee_rate, exponent=l.fee_exponent, multiplier=l.fee_multiplier,
                      rounding_increment=l.fee_rounding, verified=True, source="user-entered")
        if l.fee_kind == "kalshi_quadratic" and l.fee_rate == 0:
            fee = FeeSpec(FeeKind.KALSHI_QUADRATIC, rate=Decimal("0.07"), multiplier=l.fee_multiplier, verified=True, source="user-entered")
        levels = sorted([PriceLevel(p, q) for p, q in l.levels], key=lambda x: x.price)
        legs.append(Leg("", l.exchange, f"manual:{i}", Side(l.side), levels, fee, l.payout, l.qty_increment, l.min_order_size, l.label or f"{l.exchange} {l.side.upper()}"))
    if body.structure == "binary":
        if len(legs) != 2:
            raise HTTPException(422, "binary structure needs exactly two legs")
        structure = binary_complement(legs[0], legs[1])
    elif body.structure == "complete_set":
        structure = complete_outcome_set(legs, body.exhaustive)
    else:
        structure = all_no_set(legs, body.exhaustive)
    c = _constraints_from(body.constraints, r)
    if not body.constraints.exchange_balances:
        c.exchange_budgets = None  # manual mode: only constrain balances the user typed
    res = size_structure(structure, c)
    out = _calc_response(res, c, legs)
    out["scenarios"] = [s.name for s in structure.scenarios]
    return out


# -------------------------------------------------------------------- markets
@router.get("/markets")
def list_markets(
    r: Runtime = Depends(rt),
    q: str | None = Query(default=None, max_length=200),
    exchange: list[str] = Query(default=[]),
    category: list[str] = Query(default=[]),
    status: list[str] = Query(default=[]),
    matched: bool | None = None,
    has_book: bool | None = None,
    sort: str = Query("volume", pattern="^(volume|close|title)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    ms = list(r.store.markets.values())
    if q:
        ql = q.lower()
        ms = [m for m in ms if ql in m.title.lower() or ql in (m.event_title or "").lower() or ql in m.external_id.lower()]
    if exchange:
        ms = [m for m in ms if m.exchange in exchange]
    if category:
        ms = [m for m in ms if m.category in category]
    if status:
        ms = [m for m in ms if m.status.value in status]
    if matched is not None:
        ms = [m for m in ms if bool(r.store.matches_by_market.get(m.key)) == matched]
    if has_book is not None:
        ms = [m for m in ms if (m.key in r.store.books) == has_book]
    if sort == "volume":
        ms.sort(key=lambda m: m.volume, reverse=True)
    elif sort == "close":
        ms.sort(key=lambda m: m.close_time.timestamp() if m.close_time else float("inf"))
    else:
        ms.sort(key=lambda m: m.title)
    page = ms[offset: offset + limit]
    items = []
    for m in page:
        d = _market_dict(m, r.store.books.get(m.key))
        d["match_ids"] = sorted(r.store.matches_by_market.get(m.key, set()))
        items.append(d)
    return {"items": items, "total": len(ms), "offset": offset, "limit": limit}


@router.get("/markets/detail")
def market_detail(key: str = Query(..., max_length=200), r: Runtime = Depends(rt)):
    d = _market_detail(r, key)
    d["matches"] = [r.store.matches[mid].to_dict() for mid in r.store.matches_by_market.get(key, set())]
    d["prices"] = analytics.price_history([key])
    return d


@router.post("/markets/refresh-book")
async def refresh_book(key: str = Query(..., max_length=200), r: Runtime = Depends(rt)):
    m = r.store.markets.get(key)
    if m is None:
        raise HTTPException(404, "market not found")
    conn = r.connectors.get(r.exchange_to_connector.get(m.exchange, ""))
    if conn is None:
        raise HTTPException(409, "exchange not connected")
    books = await conn.fetch_order_books([m])
    for b in books:
        r.store.set_book(b)
    r._scan_event.set()
    return {"ok": bool(books), "book": books[0].to_dict(30) if books else None}


# -------------------------------------------------------------------- matches
@router.get("/matches")
def list_matches(r: Runtime = Depends(rt), status: list[str] = Query(default=[]), q: str | None = Query(default=None, max_length=200),
                 limit: int = Query(300, le=5000)):
    out = []
    for mt in r.store.matches.values():
        if status and mt.status.value not in status:
            continue
        a, b = r.store.markets.get(mt.market_a), r.store.markets.get(mt.market_b)
        if q and not any(q.lower() in (x.title.lower() if x else "") for x in (a, b)):
            continue
        d = mt.to_dict()
        d["a"] = _market_dict(a, r.store.books.get(a.key)) if a else None
        d["b"] = _market_dict(b, r.store.books.get(b.key)) if b else None
        out.append(d)
    order = {"verified_compatible": 0, "likely_compatible": 1, "insufficient_information": 2, "incompatible": 3}
    out.sort(key=lambda d: (order.get(d["status"], 9), -d["semantic_score"]))
    return {"items": out[:limit], "total": len(out)}


@router.get("/matches/{mid}")
def match_detail(mid: str, r: Runtime = Depends(rt)):
    mt = r.store.matches.get(mid)
    if mt is None:
        raise HTTPException(404, "match not found")
    d = mt.to_dict()
    d["a"] = _market_detail(r, mt.market_a) if mt.market_a in r.store.markets else None
    d["b"] = _market_detail(r, mt.market_b) if mt.market_b in r.store.markets else None
    with session_scope() as s:
        d["reviews"] = [{"action": v.action, "previous_status": v.previous_status, "new_status": v.new_status, "orientation": v.orientation,
                         "note": v.note, "created_at": as_utc(v.created_at).isoformat()}
                        for v in s.scalars(select(MatchVerification).where(MatchVerification.match_id == mid).order_by(MatchVerification.id.desc()))]
    return d


@router.post("/matches/{mid}/review")
async def review_match(mid: str, body: MatchReviewInput, r: Runtime = Depends(rt)):
    mt = r.store.matches.get(mid)
    if mt is None:
        raise HTTPException(404, "match not found")
    prev = mt.status
    if body.action == "approve":
        mt.status, mt.method = MatchStatus.VERIFIED, "manual"
        mt.reasons = ["Manually approved by user after reviewing settlement rules."] + ([body.note] if body.note else [])
    elif body.action == "reject":
        mt.status, mt.method = MatchStatus.INCOMPATIBLE, "manual"
        mt.reasons = ["Manually rejected by user."] + ([body.note] if body.note else [])
        mt.implication = None
    elif body.action == "correct":
        if body.orientation:
            mt.orientation = body.orientation
        mt.method = "manual"
        mt.reasons = [f"Orientation manually corrected to '{mt.orientation}'."] + mt.reasons
    else:  # reset to automatic classification
        a, b = r.store.markets.get(mt.market_a), r.store.markets.get(mt.market_b)
        if a and b:
            fresh = compare(a, b, extract(a), extract(b), mt.candidate_score)
            mt.status, mt.checks, mt.reasons, mt.orientation, mt.implication = fresh.status, fresh.checks, fresh.reasons, fresh.orientation, fresh.implication
        mt.method = "auto"
    r.store.put_match(mt)
    await asyncio.to_thread(r.persistence.save_matches, [mt])
    if mt.method == "auto":
        from ..models import MarketMatchRow
        with session_scope() as s:
            s.execute(update(MarketMatchRow).where(MarketMatchRow.id == mid).values(method="auto"))
    with session_scope() as s:
        s.add(MatchVerification(match_id=mid, action=body.action, previous_status=prev.value, new_status=mt.status.value,
                                orientation=mt.orientation, note=body.note))
    r._full_scan = True
    r._scan_event.set()
    return mt.to_dict()


@router.post("/matches")
async def create_manual_match(body: ManualMatchInput, r: Runtime = Depends(rt)):
    a, b = r.store.markets.get(body.market_a), r.store.markets.get(body.market_b)
    if not a or not b:
        raise HTTPException(404, "both markets must exist")
    if a.exchange == b.exchange:
        raise HTTPException(422, "a cross-exchange match needs markets on two different exchanges")
    if a.is_simulated != b.is_simulated:
        raise HTTPException(422, "cannot match simulated data with live data")
    res = compare(a, b, extract(a), extract(b), 0.0)
    res.orientation = body.orientation
    res.method = "manual"
    if body.approve:
        res.status = MatchStatus.VERIFIED
        res.reasons = ["Manually created and approved by user."] + res.reasons
    r.store.put_match(res)
    await asyncio.to_thread(r.persistence.save_matches, [res])
    with session_scope() as s:
        s.add(MatchVerification(match_id=res.id, action="approve" if body.approve else "correct", previous_status="none",
                                new_status=res.status.value, orientation=res.orientation, note=body.note))
    r._full_scan = True
    r._scan_event.set()
    return res.to_dict()


# -------------------------------------------------------------------- capital
def _capital_dict(b: BalanceSettings, r: Runtime) -> dict:
    return {
        "total_capital": str(b.total_capital), "exchange_balances": {k: str(v) for k, v in (b.exchange_balances or {}).items()},
        "reserved_capital": str(b.reserved_capital or 0),
        "max_allocation_per_trade": str(b.max_allocation_per_trade) if b.max_allocation_per_trade is not None else None,
        "max_pct_per_trade": str(b.max_pct_per_trade) if b.max_pct_per_trade is not None else None,
        "max_total_open_allocation": str(b.max_total_open_allocation) if b.max_total_open_allocation is not None else None,
        "min_profit": str(b.min_profit or 0), "min_roi_pct": str(b.min_roi_pct or 0),
        "max_slippage_pct": str(b.max_slippage_pct) if b.max_slippage_pct is not None else None,
        "max_days_to_settlement": b.max_days_to_settlement,
        "open_allocation": str(r.open_allocation),
        "exchanges": sorted(set(r.store.exchanges()) | set(r.exchange_to_connector)),
        "warnings": _capital_warnings(b),
    }


def _capital_warnings(b: BalanceSettings) -> list[str]:
    w = []
    total_bal = sum((D(v) for v in (b.exchange_balances or {}).values()), Decimal(0))
    if total_bal > D(b.total_capital):
        w.append("Exchange balances add up to more than total capital.")
    if D(b.reserved_capital or 0) > D(b.total_capital):
        w.append("Reserved capital exceeds total capital.")
    return w


@router.get("/capital")
def get_capital(r: Runtime = Depends(rt)):
    return _capital_dict(r.balance or BalanceSettings(), r)


@router.put("/capital")
def put_capital(body: CapitalInput, r: Runtime = Depends(rt)):
    with session_scope() as s:
        row = s.get(BalanceSettings, 1) or BalanceSettings(id=1)
        for k, v in body.model_dump().items():
            if k == "exchange_balances":
                v = {ek: str(ev) for ek, ev in v.items()}
            setattr(row, k, v)
        s.merge(row)
    r.reload_balance()
    return _capital_dict(r.balance, r)


# --------------------------------------------------------------------- alerts
def _alert_dict(a: AlertConfig) -> dict:
    return {c.name: getattr(a, c.name) for c in AlertConfig.__table__.columns} | {"created_at": as_utc(a.created_at).isoformat() if a.created_at else None}


@router.get("/alerts/rules")
def list_alert_rules():
    with session_scope() as s:
        return [_alert_dict(a) for a in s.scalars(select(AlertConfig).order_by(AlertConfig.id))]


@router.post("/alerts/rules")
def create_alert_rule(body: AlertInput, r: Runtime = Depends(rt)):
    bad = [e for e in body.event_types if e not in EVENT_TYPES]
    if bad:
        raise HTTPException(422, f"unknown event types: {bad}")
    with session_scope() as s:
        a = AlertConfig(**body.model_dump())
        s.add(a)
        s.flush()
        out = _alert_dict(a)
    r.alerts.reload()
    return out


@router.put("/alerts/rules/{aid}")
def update_alert_rule(aid: int, body: AlertInput, r: Runtime = Depends(rt)):
    bad = [e for e in body.event_types if e not in EVENT_TYPES]
    if bad:
        raise HTTPException(422, f"unknown event types: {bad}")
    with session_scope() as s:
        a = s.get(AlertConfig, aid)
        if a is None:
            raise HTTPException(404, "alert rule not found")
        for k, v in body.model_dump().items():
            setattr(a, k, v)
        out = _alert_dict(a)
    r.alerts.reload()
    return out


@router.delete("/alerts/rules/{aid}")
def delete_alert_rule(aid: int, r: Runtime = Depends(rt)):
    with session_scope() as s:
        a = s.get(AlertConfig, aid)
        if a is None:
            raise HTTPException(404, "alert rule not found")
        s.delete(a)
    r.alerts.reload()
    return {"ok": True}


@router.get("/notifications")
def list_notifications(limit: int = Query(100, le=1000), unread_only: bool = False):
    with session_scope() as s:
        q = select(NotificationEvent).order_by(NotificationEvent.id.desc()).limit(limit)
        if unread_only:
            q = q.where(NotificationEvent.read.is_(False))
        items = [notification_dict(n) for n in s.scalars(q)]
        unread = s.query(NotificationEvent).filter(NotificationEvent.read.is_(False)).count()
    return {"items": items, "unread": unread}


@router.post("/notifications/read")
def mark_read(ids: list[int] | None = None):
    with session_scope() as s:
        q = update(NotificationEvent).values(read=True)
        if ids:
            q = q.where(NotificationEvent.id.in_(ids))
        s.execute(q)
    return {"ok": True}


# ------------------------------------------------------------------ analytics
@router.get("/analytics/opportunities")
def analytics_opportunities(start: datetime | None = None, end: datetime | None = None, exchange: str | None = None,
                            category: str | None = None, strategy: str | None = None, include_simulated: bool = True):
    return analytics.opportunity_analytics(start=start, end=end, exchange=exchange, category=category, strategy=strategy,
                                           include_simulated=include_simulated) | {
        "note": "All figures are THEORETICAL (modelled from market data). They are not executed or realized profits."}


@router.get("/analytics/prices")
def analytics_prices(keys: list[str] = Query(..., max_length=4), start: datetime | None = None, end: datetime | None = None):
    return analytics.price_history(keys, start, end)


@router.get("/analytics/signals")
def analytics_signals(start: datetime | None = None, end: datetime | None = None):
    return analytics.signal_frequency(start, end)


@router.get("/analytics/performance")
def analytics_performance():
    return analytics.performance() | {"note": "REALIZED figures come only from trades and settlements you recorded manually."}


# --------------------------------------------------------------------- trades
def _trade_dict(t: RecordedTrade) -> dict:
    st = t.settlement
    return {
        "id": t.id, "opportunity_id": t.opportunity_id, "title": t.title, "category": t.category, "strategy": t.strategy,
        "legs": t.legs, "theoretical_profit": str(t.theoretical_profit) if t.theoretical_profit is not None else None,
        "total_cost": str(t.total_cost), "total_fees": str(t.total_fees), "entered_at": as_utc(t.entered_at).isoformat(), "notes": t.notes,
        "settlement": {"result": st.result, "payout_received": str(st.payout_received), "additional_fees": str(st.additional_fees),
                       "realized_pnl": str(st.realized_pnl), "settled_at": as_utc(st.settled_at).isoformat(), "notes": st.notes} if st else None,
    }


@router.get("/trades")
def list_trades():
    with session_scope() as s:
        return [_trade_dict(t) for t in s.scalars(select(RecordedTrade).order_by(RecordedTrade.id.desc()))]


@router.post("/trades")
def create_trade(body: TradeInput, r: Runtime = Depends(rt)):
    cost = sum((l.price * l.qty for l in body.legs), Decimal(0))
    fees = sum((l.fees for l in body.legs), Decimal(0))
    with session_scope() as s:
        t = RecordedTrade(opportunity_id=body.opportunity_id, title=body.title, category=body.category, strategy=body.strategy,
                          legs=[{k: str(v) if isinstance(v, Decimal) else v for k, v in l.model_dump().items()} for l in body.legs],
                          theoretical_profit=body.theoretical_profit, total_cost=cost, total_fees=fees, notes=body.notes)
        s.add(t)
        s.flush()
        out = _trade_dict(t)
    r.reload_balance()
    return out


@router.post("/trades/{tid}/settle")
def settle_trade(tid: int, body: SettlementInput, r: Runtime = Depends(rt)):
    with session_scope() as s:
        t = s.get(RecordedTrade, tid)
        if t is None:
            raise HTTPException(404, "trade not found")
        pnl = body.payout_received - t.total_cost - t.total_fees - body.additional_fees
        if t.settlement is None:
            t.settlement = RecordedSettlement(result=body.result, payout_received=body.payout_received, additional_fees=body.additional_fees,
                                              realized_pnl=pnl, notes=body.notes)
        else:
            t.settlement.result, t.settlement.payout_received = body.result, body.payout_received
            t.settlement.additional_fees, t.settlement.realized_pnl, t.settlement.notes = body.additional_fees, pnl, body.notes
        s.flush()
        out = _trade_dict(t)
    r.reload_balance()
    return out


@router.delete("/trades/{tid}")
def delete_trade(tid: int, r: Runtime = Depends(rt)):
    with session_scope() as s:
        t = s.get(RecordedTrade, tid)
        if t is None:
            raise HTTPException(404, "trade not found")
        s.delete(t)
    r.reload_balance()
    return {"ok": True}


# ------------------------------------------------------------------ websocket
@ws_router.websocket("/ws")
async def websocket(ws: WebSocket):
    r: Runtime = ws.app.state.runtime
    await r.hub.connect(ws)
    try:
        await ws.send_json({"type": "hello", "mode": r.mode, "summary": r.dashboard_summary(),
                            "data": [o.summary() for o in r.scanner.list()]})
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        r.hub.disconnect(ws)
