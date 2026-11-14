"""Kalshi connector.

Public REST endpoints (no credentials required; formats checked live):
  GET /events?status=open&with_nested_markets=true&limit=200&cursor=
      -> {"events": [{event_ticker, series_ticker, title, sub_title, category,
          mutually_exclusive, settlement_sources, markets: [...]}], "cursor"}
      market fields: ticker, title, yes_sub_title, no_sub_title, status
      ("active", ...), close_time, expiration_time, latest_expiration_time,
      yes_bid_dollars / yes_ask_dollars / no_bid_dollars / no_ask_dollars
      (strings, dollars), yes_ask_size_fp, volume_fp, volume_24h_fp,
      open_interest_fp, notional_value_dollars, price_ranges, rules_primary,
      rules_secondary, can_close_early, early_close_condition.
  GET /series -> {"series": [{ticker, fee_type, fee_multiplier, settlement_sources, ...}]}
  GET /markets/{ticker}/orderbook
      -> {"orderbook_fp": {"yes_dollars": [["0.43", "100.00"], ...], "no_dollars": [...]}}
      Only BIDS are returned (ascending). A YES ask is the complement of a
      NO bid: YES ask price = 1 - NO bid price, same size. And vice versa.

WebSocket (wss://api.elections.kalshi.com/trade-api/ws/v2) requires an API
key even for market data, so streaming is only enabled when
ARBISCAN_KALSHI_API_KEY_ID and a private key are configured; otherwise the
collector polls REST order books.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

from ..config import Settings
from ..core.categories import normalize_category
from ..core.domain import D, EventGroup, FeeKind, FeeSpec, Market, MarketStatus, OrderBook, PriceLevel, utcnow
from ..core.fees import kalshi_fee_spec
from .base import BookCallback, ExchangeConnector, StaleCallback
from .http import HttpClient

log = logging.getLogger(__name__)

CATCH_ALL = ("other", "none", "no one", "nobody", "field", "any other", "someone else", "another", "tie", "no winner")

STATUS_MAP = {
    "active": MarketStatus.OPEN,
    "open": MarketStatus.OPEN,
    "initialized": MarketStatus.SUSPENDED,
    "inactive": MarketStatus.SUSPENDED,
    "paused": MarketStatus.SUSPENDED,
    "unopened": MarketStatus.SUSPENDED,
    "closed": MarketStatus.CLOSED,
    "determined": MarketStatus.CLOSED,
    "disputed": MarketStatus.CLOSED,
    "amended": MarketStatus.CLOSED,
    "settled": MarketStatus.SETTLED,
    "finalized": MarketStatus.SETTLED,
}


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _price(v) -> Decimal | None:
    if v is None or v == "":
        return None
    p = D(v)
    return p


def bids_to_levels(raw: list) -> list[PriceLevel]:
    out = []
    for row in raw or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            price, size = D(row[0]), D(row[1])
            if price > 1:  # legacy integer cents
                price = price / 100
            out.append(PriceLevel(price, size))
    return out


def parse_orderbook(market_key: str, data: dict, payout: Decimal = Decimal(1)) -> OrderBook:
    ob = data.get("orderbook_fp") or data.get("orderbook") or {}
    yes_bids = bids_to_levels(ob.get("yes_dollars") or ob.get("yes") or [])
    no_bids = bids_to_levels(ob.get("no_dollars") or ob.get("no") or [])
    book = OrderBook(
        market_key=market_key,
        yes_bids=yes_bids,
        no_bids=no_bids,
        # implied asks: buying YES at (1 - p) matches a resting NO bid at p
        yes_asks=[PriceLevel(payout - lv.price, lv.size) for lv in no_bids],
        no_asks=[PriceLevel(payout - lv.price, lv.size) for lv in yes_bids],
        source="rest",
    )
    return book.normalise()


class KalshiConnector(ExchangeConnector):
    name = "kalshi"
    display_name = "Kalshi"
    supports_streaming = True

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.http = HttpClient(settings.kalshi_api_url, settings.kalshi_rps, "kalshi")
        self.series_fees: dict[str, FeeSpec] = {}
        self.series_sources: dict[str, list[str]] = {}
        self._series_loaded_at: float = 0
        self._private_key = None

    async def close(self) -> None:
        await self.http.close()

    # ---------------------------------------------------------------- series
    async def load_series(self, force: bool = False) -> None:
        if not force and self.series_fees and time.time() - self._series_loaded_at < 3600:
            return
        data = await self.http.get("/series")
        for s in data.get("series", []) or []:
            t = s.get("ticker")
            if not t:
                continue
            mult = s.get("fee_multiplier")
            self.series_fees[t] = kalshi_fee_spec(s.get("fee_type"), D(mult) if mult is not None else None)
            self.series_sources[t] = [x.get("name") or x.get("url") for x in s.get("settlement_sources") or [] if isinstance(x, dict)]
        self._series_loaded_at = time.time()

    def fee_for_series(self, series_ticker: str | None) -> FeeSpec:
        if series_ticker and series_ticker in self.series_fees:
            return self.series_fees[series_ticker]
        return FeeSpec(
            kind=FeeKind.KALSHI_QUADRATIC,
            rate=Decimal("0.07"),
            verified=False,
            source="kalshi default quadratic schedule (series fee data unavailable)",
            notes="Series fee multiplier not retrieved; assuming standard 0.07 taker schedule.",
        )

    # ----------------------------------------------------------------- parse
    def parse_event(self, ev: dict) -> tuple[list[Market], EventGroup | None]:
        series = ev.get("series_ticker")
        category = normalize_category(ev.get("category"), ev.get("title"))
        sources = [x.get("name") or x.get("url") for x in ev.get("settlement_sources") or [] if isinstance(x, dict)]
        sources = sources or self.series_sources.get(series or "", [])
        url = f"https://kalshi.com/markets/{series.lower()}" if series else None
        markets = []
        for m in ev.get("markets") or []:
            if m.get("market_type", "binary") != "binary":
                continue
            markets.append(self.parse_market(m, ev, category, sources, url))
        group = None
        if len(markets) > 1:
            me = bool(ev.get("mutually_exclusive"))
            exhaustive = None
            reason = ""
            if me:
                labels = [(mk.outcome_label or "").lower().strip() for mk in markets]
                if any(any(l == c or l.startswith(c + " ") for c in CATCH_ALL) for l in labels):
                    exhaustive = True
                    reason = "Mutually exclusive event with explicit catch-all outcome."
                else:
                    reason = "Mutually exclusive, but no catch-all outcome: a result outside the listed markets is possible."
            else:
                reason = "Event is not mutually exclusive."
            group = EventGroup(
                exchange=self.name,
                event_id=ev.get("event_ticker"),
                title=ev.get("title") or "",
                category=category,
                market_keys=[m.key for m in markets],
                mutually_exclusive=True if me else None,
                exhaustive=exhaustive,
                exhaustive_reason=reason,
                url=url,
                extra={"series_ticker": series, "collateral_return_type": ev.get("collateral_return_type")},
            )
        return markets, group

    def parse_market(self, m: dict, ev: dict, category: str, sources: list[str], url: str | None) -> Market:
        status = STATUS_MAP.get(str(m.get("status", "")).lower(), MarketStatus.UNKNOWN)
        payout = D(m.get("notional_value_dollars") or "1") or Decimal(1)
        ranges = m.get("price_ranges") or []
        tick = D(ranges[0].get("step")) if ranges and ranges[0].get("step") else Decimal("0.01")
        yes_bid, yes_ask = _price(m.get("yes_bid_dollars")), _price(m.get("yes_ask_dollars"))
        no_bid, no_ask = _price(m.get("no_bid_dollars")), _price(m.get("no_ask_dollars"))
        title = m.get("title") or ev.get("title") or ""
        return Market(
            exchange=self.name,
            external_id=m["ticker"],
            title=title,
            event_id=m.get("event_ticker") or ev.get("event_ticker"),
            event_title=ev.get("title"),
            outcome_label=m.get("yes_sub_title") or None,
            yes_label=m.get("yes_sub_title") or "Yes",
            no_label=m.get("no_sub_title") or "No",
            description=ev.get("sub_title") or "",
            rules_primary=m.get("rules_primary") or "",
            rules_secondary=m.get("rules_secondary") or "",
            category=category,
            raw_category=ev.get("category"),
            close_time=_dt(m.get("close_time")),
            expiration_time=_dt(m.get("expected_expiration_time") or m.get("expiration_time")),
            status=status,
            url=url,
            payout=payout,
            tick_size=tick,
            min_order_size=Decimal(1),
            qty_increment=Decimal(1),
            fee=self.fee_for_series(ev.get("series_ticker")),
            volume=D(m.get("volume_fp") or 0),
            volume_24h=D(m.get("volume_24h_fp") or 0),
            open_interest=D(m.get("open_interest_fp")) if m.get("open_interest_fp") is not None else None,
            strike=D(m["floor_strike"]) if m.get("floor_strike") is not None else None,
            strike_type=m.get("strike_type"),
            resolution_sources=sources,
            can_close_early=m.get("can_close_early"),
            early_close_condition=m.get("early_close_condition"),
            exchange_updated_at=_dt(m.get("updated_time")),
            extra={
                "series_ticker": ev.get("series_ticker"),
                "latest_expiration_time": m.get("latest_expiration_time"),
                "settlement_timer_seconds": m.get("settlement_timer_seconds"),
                "price_level_structure": m.get("price_level_structure"),
                "top": {
                    "yes_bid": str(yes_bid) if yes_bid is not None else None,
                    "yes_ask": str(yes_ask) if yes_ask is not None else None,
                    "no_bid": str(no_bid) if no_bid is not None else None,
                    "no_ask": str(no_ask) if no_ask is not None else None,
                    "yes_ask_size": m.get("yes_ask_size_fp"),
                },
            },
        )

    # ----------------------------------------------------------------- fetch
    async def fetch_markets(self) -> tuple[list[Market], list[EventGroup]]:
        try:
            await self.load_series()
        except Exception as exc:  # noqa: BLE001 - fees fall back to unverified default
            log.warning("Kalshi series/fee load failed: %s", exc)
        markets: list[Market] = []
        groups: list[EventGroup] = []
        cursor = None
        while len(markets) < self.settings.kalshi_max_markets:
            params = {"limit": 200, "status": "open", "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            data = await self.http.get("/events", params=params)
            events = data.get("events", []) or []
            for ev in events:
                ms, g = self.parse_event(ev)
                markets.extend(ms)
                if g:
                    groups.append(g)
            cursor = data.get("cursor")
            if not cursor or not events:
                break
        self.status.markets = len(markets)
        self.status.connected = True
        self.status.last_success = utcnow()
        return markets, groups

    async def fetch_markets_sample(self):
        data = await self.http.get("/events", params={"limit": 5, "status": "open", "with_nested_markets": "true"})
        out = []
        for ev in data.get("events", []) or []:
            out.extend(self.parse_event(ev)[0])
        return out, []

    async def fetch_order_book(self, market: Market) -> OrderBook:
        data = await self.http.get(f"/markets/{market.external_id}/orderbook")
        book = parse_orderbook(market.key, data, market.payout)
        return book

    async def fetch_order_books(self, markets: list[Market]) -> list[OrderBook]:
        sem = asyncio.Semaphore(4)

        async def one(m: Market):
            async with sem:
                try:
                    return await self.fetch_order_book(m)
                except Exception as exc:  # noqa: BLE001
                    log.debug("kalshi book %s failed: %s", m.external_id, exc)
                    self.status.last_error = str(exc)
                    return None

        books = [b for b in await asyncio.gather(*(one(m) for m in markets)) if b]
        self.status.books = len(books)
        if books:
            self.status.last_success = utcnow()
        return books

    # ------------------------------------------------------------- streaming
    def _load_private_key(self):
        if self._private_key is not None:
            return self._private_key
        from cryptography.hazmat.primitives import serialization

        if self.settings.kalshi_private_key_pem:
            pem = self.settings.kalshi_private_key_pem.get_secret_value().encode()
        else:
            with open(self.settings.kalshi_private_key_path or "", "rb") as fh:
                pem = fh.read()
        self._private_key = serialization.load_pem_private_key(pem, password=None)
        return self._private_key

    def auth_headers(self, method: str, path: str) -> dict:
        """KALSHI-ACCESS-* headers: RSA-PSS(SHA256) over timestamp_ms + METHOD + path."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        ts = str(int(time.time() * 1000))
        key = self._load_private_key()
        sig = key.sign(
            (ts + method + path).encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.settings.kalshi_api_key_id or "",
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    async def subscribe(self, markets: list[Market], on_book: BookCallback, on_stale: StaleCallback, connect=None) -> None:
        """orderbook_delta channel with seq-gap detection and resubscribe.

        Requires credentials. Message handling accepts both dollar-string and
        legacy cent fields because Kalshi has been migrating field formats.
        """
        if connect is None and not self.settings.kalshi_credentials_configured:
            raise NotImplementedError("Kalshi WebSocket requires API credentials")
        import websockets

        path = "/trade-api/ws/v2"

        def default_connect():
            return websockets.connect(self.settings.kalshi_ws_url, additional_headers=self.auth_headers("GET", path), ping_interval=20)

        connect = connect or default_connect
        by_ticker = {m.external_id: m for m in markets}
        state: dict[str, dict] = {}
        self.status.streaming = True
        attempt = 0
        while True:
            last_seq: dict[int, int] = {}
            try:
                async with connect() as ws:
                    await ws.send(json.dumps({"id": 1, "cmd": "subscribe", "params": {"channels": ["orderbook_delta"], "market_tickers": list(by_ticker)}}))
                    self.status.stream_connected = True
                    attempt = 0
                    async for raw in ws:
                        msg = json.loads(raw)
                        sid, seq = msg.get("sid"), msg.get("seq")
                        if sid is not None and seq is not None:
                            if sid in last_seq and seq != last_seq[sid] + 1:
                                raise ConnectionError(f"sequence gap on sid {sid}: {last_seq[sid]} -> {seq}")
                            last_seq[sid] = seq
                        body = msg.get("msg") or {}
                        ticker = body.get("market_ticker")
                        if msg.get("type") == "orderbook_snapshot" and ticker:
                            state[ticker] = {
                                "yes": {lv.price: lv.size for lv in bids_to_levels(body.get("yes_dollars_fp") or body.get("yes_dollars") or body.get("yes") or [])},
                                "no": {lv.price: lv.size for lv in bids_to_levels(body.get("no_dollars_fp") or body.get("no_dollars") or body.get("no") or [])},
                            }
                        elif msg.get("type") == "orderbook_delta" and ticker in state:
                            side = body.get("side")
                            price = D(body.get("price_dollars") or (D(body.get("price")) / 100))
                            delta = D(body.get("delta_fp") or body.get("delta") or 0)
                            book = state[ticker][side]
                            new = book.get(price, Decimal(0)) + delta
                            if new <= 0:
                                book.pop(price, None)
                            else:
                                book[price] = new
                        else:
                            continue
                        mk = by_ticker.get(ticker)
                        if mk and ticker in state:
                            ob = parse_orderbook(
                                mk.key,
                                {"orderbook_fp": {"yes_dollars": [[p, s] for p, s in state[ticker]["yes"].items()],
                                                  "no_dollars": [[p, s] for p, s in state[ticker]["no"].items()]}},
                                mk.payout,
                            )
                            ob.source = "ws"
                            await on_book(ob)
            except asyncio.CancelledError:
                self.status.stream_connected = False
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Kalshi WS disconnected: %s", exc)
                self.status.last_error = f"websocket: {exc}"
            self.status.stream_connected = False
            state.clear()
            await on_stale([m.key for m in markets])
            attempt += 1
            await asyncio.sleep(min(60, 2 ** min(attempt, 6)))
