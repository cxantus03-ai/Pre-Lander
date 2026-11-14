"""Polymarket connector (public, unauthenticated market data).

Endpoints used (response formats checked against live responses):
  Gamma  GET  /events/keyset?limit&active=true&closed=false&after_cursor=
         -> {"events": [...nested "markets"...], "next_cursor": "..."}
         Market fields: id, question, conditionId, slug, description, endDate,
         outcomes (JSON string), clobTokenIds (JSON string), bestBid, bestAsk,
         active, closed, acceptingOrders, enableOrderBook,
         orderPriceMinTickSize, orderMinSize, resolutionSource, feesEnabled,
         feeSchedule {rate, exponent, takerOnly, rebateRate}, volumeNum,
         volume24hr, liquidityNum, updatedAt, groupItemTitle, negRiskOther.
  CLOB   POST /books  body [{"token_id": ...}]
         -> [{"asset_id", "timestamp" (ms), "bids": [{"price","size"}], "asks": [...],
              "tick_size", "min_order_size", "neg_risk"}]
  WS     wss://ws-subscriptions-clob.polymarket.com/ws/market
         subscribe {"assets_ids": [...], "type": "market"}; receives "book"
         snapshots then "price_change" deltas {asset_id, price, size, side}.

Polymarket YES and NO are separate ERC-1155 tokens with their own books;
both are fetched so each side uses its own executable asks.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

from ..config import Settings
from ..core.categories import normalize_category
from ..core.domain import D, EventGroup, Market, MarketStatus, OrderBook, PriceLevel, utcnow
from ..core.fees import polymarket_fee_spec
from .base import BookCallback, ExchangeConnector, StaleCallback
from .http import HttpClient

log = logging.getLogger(__name__)

CATCH_ALL = ("other", "none", "no one", "nobody", "field", "any other", "someone else", "another")


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _json_list(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v:
        try:
            out = json.loads(v)
            return out if isinstance(out, list) else []
        except ValueError:
            return []
    return []


def parse_book_levels(raw: list[dict]) -> list[PriceLevel]:
    return [PriceLevel(D(x["price"]), D(x["size"])) for x in raw if x.get("price") is not None]


def _ms_to_dt(ms) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


class PolymarketConnector(ExchangeConnector):
    name = "polymarket"
    display_name = "Polymarket"
    supports_streaming = True

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.gamma = HttpClient(settings.polymarket_gamma_url, settings.polymarket_rps, "polymarket-gamma")
        self.clob = HttpClient(settings.polymarket_clob_url, settings.polymarket_rps, "polymarket-clob")
        # token_id -> (market_key, "yes"|"no")
        self._token_index: dict[str, tuple[str, str]] = {}
        # streaming state: token_id -> {"bids": {price: size}, "asks": {...}, "ts": dt}
        self._ws_books: dict[str, dict] = {}

    async def close(self) -> None:
        await self.gamma.close()
        await self.clob.close()

    # ------------------------------------------------------------------ parse
    def parse_event(self, ev: dict) -> tuple[list[Market], EventGroup | None]:
        tags = [t.get("label", "") for t in (ev.get("tags") or []) if isinstance(t, dict)]
        category = normalize_category(ev.get("category"), *tags, ev.get("title"))
        event_id = str(ev.get("id"))
        url = f"https://polymarket.com/event/{ev.get('slug')}" if ev.get("slug") else None
        markets: list[Market] = []
        for m in ev.get("markets") or []:
            mk = self.parse_market(m, ev, category, url)
            if mk:
                markets.append(mk)
        group = None
        if len(markets) > 1:
            neg_risk = bool(ev.get("enableNegRisk") or ev.get("negRisk"))
            augmented = bool(ev.get("negRiskAugmented"))
            exhaustive = None
            reason = ""
            if neg_risk:
                labels = [(mk.outcome_label or "").lower() for mk in markets]
                has_catch_all = any(any(lbl.strip() == c or lbl.startswith(c + " ") for c in CATCH_ALL) for lbl in labels)
                if augmented:
                    reason = "negRiskAugmented event: outcome list may be extended; completeness not guaranteed."
                elif has_catch_all:
                    exhaustive = True
                    reason = "negRisk event with explicit catch-all outcome."
                else:
                    reason = "negRisk (mutually exclusive) but no catch-all outcome: a 'none of these' result is possible."
            else:
                reason = "Event is not negRisk: outcomes are not declared mutually exclusive."
            group = EventGroup(
                exchange=self.name,
                event_id=event_id,
                title=ev.get("title") or "",
                category=category,
                market_keys=[mk.key for mk in markets],
                mutually_exclusive=True if neg_risk else None,
                exhaustive=exhaustive,
                exhaustive_reason=reason,
                url=url,
                extra={"neg_risk": neg_risk, "neg_risk_augmented": augmented},
            )
        return markets, group

    def parse_market(self, m: dict, ev: dict, category: str, url: str | None) -> Market | None:
        tokens = _json_list(m.get("clobTokenIds"))
        outcomes = _json_list(m.get("outcomes"))
        if len(tokens) != 2 or len(outcomes) != 2:
            return None  # only binary CLOB markets
        closed = bool(m.get("closed"))
        close_time = _parse_dt(m.get("endDate"))
        if closed:
            status = MarketStatus.CLOSED
        elif m.get("active") and m.get("acceptingOrders") and m.get("enableOrderBook", True):
            status = MarketStatus.OPEN
        else:
            status = MarketStatus.SUSPENDED
        fee = polymarket_fee_spec(m.get("feesEnabled"), m.get("feeSchedule"), self.settings.polymarket_fee_formula_verified)
        sources = [s for s in [m.get("resolutionSource"), ev.get("resolutionSource")] if s]
        best_bid, best_ask = m.get("bestBid"), m.get("bestAsk")
        mk = Market(
            exchange=self.name,
            external_id=str(m.get("id")),
            title=m.get("question") or "",
            event_id=str(ev.get("id")),
            event_title=ev.get("title"),
            outcome_label=m.get("groupItemTitle") or None,
            yes_label=str(outcomes[0]),
            no_label=str(outcomes[1]),
            description=m.get("description") or "",
            rules_primary=m.get("description") or "",
            category=category,
            raw_category=ev.get("category"),
            close_time=close_time,
            expiration_time=close_time,
            status=status,
            url=url,
            yes_token_id=str(tokens[0]),
            no_token_id=str(tokens[1]),
            tick_size=D(m.get("orderPriceMinTickSize") or "0.01"),
            min_order_size=D(m.get("orderMinSize") or 5),
            qty_increment=Decimal("0.01"),
            fee=fee,
            volume=D(m.get("volumeNum") or 0),
            volume_24h=D(m.get("volume24hr") or 0),
            liquidity=D(m.get("liquidityNum")) if m.get("liquidityNum") is not None else None,
            resolution_sources=sources,
            exchange_updated_at=_parse_dt(m.get("updatedAt")),
            extra={
                "condition_id": m.get("conditionId"),
                "slug": m.get("slug"),
                "neg_risk": m.get("negRisk"),
                "neg_risk_other": m.get("negRiskOther"),
                "group_item_threshold": m.get("groupItemThreshold"),
                "fee_type": m.get("feeType"),
                "fee_schedule": m.get("feeSchedule"),
                "restricted": m.get("restricted"),
                "uma_resolution_statuses": m.get("umaResolutionStatuses"),
                # listing top-of-book (YES token). NO token is the mirrored book.
                "top": {
                    "yes_bid": str(best_bid) if best_bid is not None else None,
                    "yes_ask": str(best_ask) if best_ask is not None else None,
                    "no_bid": str(1 - D(best_ask)) if best_ask not in (None, 0) else None,
                    "no_ask": str(1 - D(best_bid)) if best_bid not in (None, 0) else None,
                },
            },
        )
        self._token_index[mk.yes_token_id] = (mk.key, "yes")
        self._token_index[mk.no_token_id] = (mk.key, "no")
        return mk

    # ---------------------------------------------------------------- fetch
    async def fetch_markets(self) -> tuple[list[Market], list[EventGroup]]:
        markets: list[Market] = []
        groups: list[EventGroup] = []
        cursor: str | None = None
        n_events = 0
        while n_events < self.settings.polymarket_max_events:
            params = {"limit": 100, "active": "true", "closed": "false"}
            if cursor:
                params["after_cursor"] = cursor
            data = await self.gamma.get("/events/keyset", params=params)
            events = data.get("events", []) if isinstance(data, dict) else data
            for ev in events:
                ms, g = self.parse_event(ev)
                markets.extend(ms)
                if g:
                    groups.append(g)
            n_events += len(events)
            cursor = data.get("next_cursor") if isinstance(data, dict) else None
            if not cursor or not events:
                break
        self.status.markets = len(markets)
        self.status.connected = True
        self.status.last_success = utcnow()
        return markets, groups

    async def fetch_markets_sample(self):
        data = await self.gamma.get("/events/keyset", params={"limit": 5, "active": "true", "closed": "false"})
        events = data.get("events", []) if isinstance(data, dict) else []
        out = []
        for ev in events:
            out.extend(self.parse_event(ev)[0])
        return out, []

    def book_from_token_books(self, market_key: str, yes: dict | None, no: dict | None, source: str) -> OrderBook:
        book = OrderBook(market_key=market_key, source=source)
        ts = []
        if yes:
            book.yes_bids = parse_book_levels(yes.get("bids", []))
            book.yes_asks = parse_book_levels(yes.get("asks", []))
            ts.append(_ms_to_dt(yes.get("timestamp")))
        if no:
            book.no_bids = parse_book_levels(no.get("bids", []))
            book.no_asks = parse_book_levels(no.get("asks", []))
            ts.append(_ms_to_dt(no.get("timestamp")))
        ts = [t for t in ts if t]
        book.exchange_ts = min(ts) if ts else None
        book.depth_complete = bool(yes and no)
        return book.normalise()

    async def fetch_order_books(self, markets: list[Market]) -> list[OrderBook]:
        token_ids: list[str] = []
        for m in markets:
            if m.yes_token_id and m.no_token_id:
                token_ids += [m.yes_token_id, m.no_token_id]
        raw: dict[str, dict] = {}
        for i in range(0, len(token_ids), 40):
            chunk = token_ids[i : i + 40]
            data = await self.clob.post("/books", [{"token_id": t} for t in chunk])
            for b in data or []:
                raw[str(b.get("asset_id"))] = b
        books = []
        for m in markets:
            y, n = raw.get(m.yes_token_id or ""), raw.get(m.no_token_id or "")
            if y is None and n is None:
                continue
            books.append(self.book_from_token_books(m.key, y, n, "rest"))
        self.status.books = len(books)
        self.status.last_success = utcnow()
        return books

    # -------------------------------------------------------------- stream
    def apply_ws_message(self, msg: dict) -> set[str]:
        """Update local token books from one WS event; returns affected market keys."""
        affected: set[str] = set()
        etype = msg.get("event_type")
        if etype == "book" or ("bids" in msg and "asks" in msg and "asset_id" in msg):
            tok = str(msg.get("asset_id"))
            self._ws_books[tok] = {
                "bids": {D(x["price"]): D(x["size"]) for x in msg.get("bids", [])},
                "asks": {D(x["price"]): D(x["size"]) for x in msg.get("asks", [])},
                "ts": msg.get("timestamp"),
            }
            if tok in self._token_index:
                affected.add(self._token_index[tok][0])
        elif etype == "price_change":
            for ch in msg.get("price_changes", []) or [msg]:
                tok = str(ch.get("asset_id"))
                state = self._ws_books.get(tok)
                if state is None:
                    continue  # no snapshot yet; wait for a "book" event / REST reconcile
                side = "bids" if str(ch.get("side", "")).upper() == "BUY" else "asks"
                price, size = D(ch.get("price")), D(ch.get("size"))
                if size <= 0:
                    state[side].pop(price, None)
                else:
                    state[side][price] = size
                state["ts"] = msg.get("timestamp", state.get("ts"))
                if tok in self._token_index:
                    affected.add(self._token_index[tok][0])
        return affected

    def ws_book_for(self, market: Market) -> OrderBook | None:
        def as_raw(tok):
            st = self._ws_books.get(tok or "")
            if st is None:
                return None
            return {
                "bids": [{"price": p, "size": s} for p, s in st["bids"].items()],
                "asks": [{"price": p, "size": s} for p, s in st["asks"].items()],
                "timestamp": st.get("ts"),
            }

        y, n = as_raw(market.yes_token_id), as_raw(market.no_token_id)
        if y is None or n is None:
            return None
        return self.book_from_token_books(market.key, y, n, "ws")

    async def subscribe(self, markets: list[Market], on_book: BookCallback, on_stale: StaleCallback, connect=None) -> None:
        """Maintain the market WebSocket with automatic reconnection.

        On disconnect every subscribed market is marked stale; on reconnect the
        server re-sends full "book" snapshots, which replace local state
        (reconciliation). ``connect`` is injectable for tests.
        """
        import websockets

        connect = connect or (lambda: websockets.connect(self.settings.polymarket_ws_url, ping_interval=20, ping_timeout=20, max_size=2**24))
        by_key = {m.key: m for m in markets}
        assets = [t for m in markets for t in (m.yes_token_id, m.no_token_id) if t]
        self.status.streaming = True
        attempt = 0
        while True:
            try:
                async with connect() as ws:
                    await ws.send(json.dumps({"assets_ids": assets, "type": "market"}))
                    self.status.stream_connected = True
                    attempt = 0
                    self._ws_books.clear()  # fresh snapshots will follow
                    ping_task = asyncio.create_task(self._pinger(ws))
                    try:
                        async for raw in ws:
                            if raw in ("PONG", "pong"):
                                continue
                            try:
                                payload = json.loads(raw)
                            except ValueError:
                                continue
                            msgs = payload if isinstance(payload, list) else [payload]
                            affected: set[str] = set()
                            for msg in msgs:
                                if isinstance(msg, dict):
                                    affected |= self.apply_ws_message(msg)
                            for key in affected:
                                mk = by_key.get(key)
                                book = self.ws_book_for(mk) if mk else None
                                if book:
                                    await on_book(book)
                    finally:
                        ping_task.cancel()
            except asyncio.CancelledError:
                self.status.stream_connected = False
                raise
            except Exception as exc:  # noqa: BLE001 - any failure triggers reconnect
                log.warning("Polymarket WS disconnected: %s", exc)
                self.status.last_error = f"websocket: {exc}"
            self.status.stream_connected = False
            await on_stale(list(by_key))
            attempt += 1
            await asyncio.sleep(min(60, 2 ** min(attempt, 6)))

    @staticmethod
    async def _pinger(ws) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                await ws.send("PING")
        except Exception:  # noqa: BLE001
            return
