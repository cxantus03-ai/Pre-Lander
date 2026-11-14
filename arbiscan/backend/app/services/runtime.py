"""Runtime orchestration: collection loops, matching, scanning, alerts, history."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from ..config import Settings
from ..connectors.base import ExchangeConnector
from ..connectors.demo import KALSHI as DEMO_KALSHI
from ..connectors.demo import POLY as DEMO_POLY
from ..connectors.demo import DemoConnector
from ..connectors.kalshi import KalshiConnector
from ..connectors.polymarket import PolymarketConnector
from ..core.arbitrage import Constraints
from ..core.domain import D, Market, OrderBook, utcnow
from ..db import session_scope
from ..matching.llm import LLMJudge, apply_llm_opinion
from ..matching.matcher import MatchStatus, match_markets
from ..models import AppSetting, BalanceSettings, RecordedSettlement, RecordedTrade
from .alerts import AlertEngine
from .cache import SnapshotCache
from .notify import EmailSender, WebSocketHub
from .persistence import Persistence, load_balance_settings
from .scanner import Scanner, ScanEvent
from .store import MarketStore

log = logging.getLogger(__name__)

RUNTIME_KEYS = {
    "book_poll_interval_s": (5, 600),
    "discovery_interval_s": (60, 3600),
    "stale_after_s": (5, 900),
    "max_watched_books": (10, 2000),
}


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = MarketStore()
        self.persistence = Persistence()
        self.hub = WebSocketHub()
        self.alerts = AlertEngine(self.hub, EmailSender(settings), settings.alert_cooldown_s)
        self.cache = SnapshotCache(settings.redis_url)
        self.connectors: dict[str, ExchangeConnector] = {}
        self.exchange_to_connector: dict[str, str] = {}
        self.mode = "starting"
        self.mode_reason = ""
        self.cfg = {k: getattr(settings, k) for k in RUNTIME_KEYS}
        self.balance: BalanceSettings | None = None
        self.open_allocation = Decimal(0)
        self.scanner = Scanner(self.store, self.cfg["stale_after_s"], self.constraints, self.max_days)
        self._tasks: list[asyncio.Task] = []
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._stream_watch: dict[str, frozenset] = {}
        self._scan_event = asyncio.Event()
        self._full_scan = True
        self._last_broadcast = 0.0
        self.matching = {"running": False, "last_run": None, "pairs": 0, "duration_s": None}
        self._connector_ok: dict[str, bool] = {}
        self.started_at = utcnow()
        self.llm = LLMJudge(settings)
        self._llm_cache: dict[str, dict] = {}

    # ------------------------------------------------------------- settings
    def load_persisted_settings(self) -> None:
        with session_scope() as s:
            row = s.get(AppSetting, "runtime")
            if row:
                for k, v in (row.value or {}).items():
                    if k in RUNTIME_KEYS:
                        self.cfg[k] = v
        self.scanner.stale_after_s = self.cfg["stale_after_s"]

    def update_runtime_settings(self, values: dict) -> dict:
        for k, v in values.items():
            if k not in RUNTIME_KEYS or v is None:
                continue
            lo, hi = RUNTIME_KEYS[k]
            self.cfg[k] = max(lo, min(hi, int(v)))
        self.scanner.stale_after_s = self.cfg["stale_after_s"]
        with session_scope() as s:
            row = s.get(AppSetting, "runtime") or AppSetting(key="runtime", value={})
            row.value = dict(self.cfg)
            s.merge(row)
        return dict(self.cfg)

    def reload_balance(self) -> None:
        self.balance = load_balance_settings()
        with session_scope() as s:
            q = select(func.coalesce(func.sum(RecordedTrade.total_cost + RecordedTrade.total_fees), 0)).outerjoin(
                RecordedSettlement, RecordedSettlement.trade_id == RecordedTrade.id).where(RecordedSettlement.id.is_(None))
            self.open_allocation = D(s.scalar(q) or 0)
        self._full_scan = True
        self._scan_event.set()

    def constraints(self, _opp=None) -> Constraints:
        b = self.balance
        if b is None:
            return Constraints()
        budgets = {k: D(v) for k, v in (b.exchange_balances or {}).items()}
        for ex in self.store.exchanges():
            budgets.setdefault(ex, Decimal(0))  # an exchange with no stated balance has no usable capital
        total = D(b.total_capital)
        caps = [max(total - D(b.reserved_capital or 0) - self.open_allocation, Decimal(0))]
        if b.max_allocation_per_trade:
            caps.append(D(b.max_allocation_per_trade))
        if b.max_pct_per_trade:
            caps.append(total * D(b.max_pct_per_trade) / 100)
        if b.max_total_open_allocation:
            caps.append(max(D(b.max_total_open_allocation) - self.open_allocation, Decimal(0)))
        return Constraints(
            exchange_budgets=budgets,
            max_total_outlay=min(caps),
            max_slippage_pct=D(b.max_slippage_pct) if b.max_slippage_pct is not None else None,
            min_profit=D(b.min_profit) if b.min_profit else None,
            min_roi_pct=D(b.min_roi_pct) if b.min_roi_pct else None,
        )

    def max_days(self) -> int | None:
        return self.balance.max_days_to_settlement if self.balance else None

    # ---------------------------------------------------------------- start
    async def resolve_mode(self) -> None:
        s = self.settings
        if s.data_mode == "demo":
            self.mode, self.mode_reason = "demo", "ARBISCAN_DATA_MODE=demo"
            return
        live: list[ExchangeConnector] = []
        if s.polymarket_enabled:
            live.append(PolymarketConnector(s))
        if s.kalshi_enabled:
            live.append(KalshiConnector(s))
        if s.data_mode == "live":
            self.mode, self.mode_reason = "live", "ARBISCAN_DATA_MODE=live"
            for c in live:
                self.connectors[c.name] = c
            return
        oks = await asyncio.gather(*(asyncio.wait_for(c.healthcheck(), 25) for c in live), return_exceptions=True)
        reachable = [c for c, ok in zip(live, oks) if ok is True]
        if reachable:
            self.mode = "live"
            self.mode_reason = "auto: reachable " + ", ".join(c.name for c in reachable)
            for c in live:
                self.connectors[c.name] = c
        else:
            self.mode, self.mode_reason = "demo", "auto: no exchange API reachable — showing SIMULATED data"
            for c in live:
                await c.close()

    async def start(self) -> None:
        AlertEngine.ensure_default_rules()
        self.alerts.reload()
        self.load_persisted_settings()
        await self.cache.connect()
        await self.resolve_mode()
        if self.mode == "demo":
            demo = DemoConnector(self.settings)
            self.connectors = {demo.name: demo}
            self.exchange_to_connector = {DEMO_POLY: demo.name, DEMO_KALSHI: demo.name}
        else:
            self.exchange_to_connector = {name: name for name in self.connectors}
        self.balance = load_balance_settings()
        if not self.balance.exchange_balances:
            half = D(self.balance.total_capital) / 2
            exs = list(self.exchange_to_connector)
            with session_scope() as s:
                row = s.get(BalanceSettings, 1)
                row.exchange_balances = {e: str(half) for e in exs}
        self.reload_balance()
        for m in await asyncio.to_thread(self.persistence.load_manual_matches):
            self.store.put_match(m)
        log.info("ArbiScan starting in %s mode (%s)", self.mode, self.mode_reason)
        for name, conn in self.connectors.items():
            self._tasks.append(asyncio.create_task(self._discovery_loop(conn), name=f"discovery:{name}"))
        self._tasks.append(asyncio.create_task(self._book_poll_loop(), name="book-poll"))
        self._tasks.append(asyncio.create_task(self._scan_loop(), name="scan"))
        self._tasks.append(asyncio.create_task(self._maintenance_loop(), name="maintenance"))

    async def stop(self) -> None:
        for t in list(self._stream_tasks.values()) + self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, *self._stream_tasks.values(), return_exceptions=True)
        for c in self.connectors.values():
            await c.close()
        await self.cache.close()

    # ------------------------------------------------------------ discovery
    def _horizon_ok(self, m: Market) -> bool:
        if m.close_time is None:
            return True
        return m.close_time <= utcnow() + timedelta(days=self.settings.max_close_horizon_days)

    async def _discovery_loop(self, conn: ExchangeConnector) -> None:
        while True:
            try:
                markets, groups = await conn.fetch_markets()
                markets = [m for m in markets if self._horizon_ok(m)]
                keys = {m.key for m in markets}
                groups = [g for g in groups if any(k in keys for k in g.market_keys)]
                exchanges = {m.exchange for m in markets} or {conn.name}
                self.store.replace_exchange_markets(exchanges, markets, groups)
                conn.status.connected = True
                conn.status.last_error = None
                try:
                    await asyncio.to_thread(self.persistence.upsert_markets, markets, groups)
                except Exception:  # noqa: BLE001 - storage problems must not stop live collection
                    log.exception("market persistence failed for %s", conn.name)
                await self.run_matching()
                self._restart_streams()
                self._full_scan = True
                self._scan_event.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("discovery failed for %s", conn.name)
                conn.status.connected = False
                conn.status.last_error = f"discovery: {exc}"
            await asyncio.sleep(self.cfg["discovery_interval_s"] if not conn.simulated else 3600)

    def _exchange_pairs(self) -> list[tuple[str, str]]:
        return [(DEMO_POLY, DEMO_KALSHI)] if self.mode == "demo" else [("polymarket", "kalshi")]

    async def run_matching(self) -> None:
        if self.matching["running"]:
            return
        self.matching["running"] = True
        t0 = utcnow()
        try:
            results = []
            for ea, eb in self._exchange_pairs():
                A = [m for m in self.store.markets.values() if m.exchange == ea]
                B = [m for m in self.store.markets.values() if m.exchange == eb]
                if not A or not B:
                    continue
                results += await asyncio.to_thread(
                    match_markets, A, B,
                    min_candidate=self.settings.match_min_candidate_score,
                    per_market=self.settings.match_max_candidates_per_market,
                    tol_days=1,
                )
            if self.llm.enabled:  # optional, advisory-only second opinion
                todo = [r for r in results if r.status in (MatchStatus.LIKELY, MatchStatus.VERIFIED) and r.id not in self._llm_cache]
                for r in todo[: self.settings.llm_max_reviews_per_cycle]:
                    a, b = self.store.markets.get(r.market_a), self.store.markets.get(r.market_b)
                    if a and b:
                        op = await self.llm.judge(a, b)
                        if op:
                            self._llm_cache[r.id] = op
                for r in results:
                    apply_llm_opinion(r, self._llm_cache.get(r.id))
            if results or self.store.matches:
                self.store.set_matches(results)
                await asyncio.to_thread(self.persistence.save_matches, results)
            self.matching.update(pairs=len(self.store.matches), last_run=utcnow().isoformat(),
                                 duration_s=round((utcnow() - t0).total_seconds(), 2))
        finally:
            self.matching["running"] = False

    # ----------------------------------------------------------------- books
    def _prioritise_from_listings(self) -> None:
        """Rank matched pairs by listing top-of-book so promising ones get depth first."""
        scored = []
        for r in self.store.matches.values():
            a, b = self.store.markets.get(r.market_a), self.store.markets.get(r.market_b)
            if not a or not b:
                continue
            ta, tb = a.extra.get("top") or {}, b.extra.get("top") or {}
            combos = [("yes_ask", "no_ask"), ("no_ask", "yes_ask")] if r.orientation == "same" else [("yes_ask", "yes_ask"), ("no_ask", "no_ask")]
            best = None
            for x, y in combos:
                if ta.get(x) and tb.get(y):
                    c = D(ta[x]) + D(tb[y])
                    best = c if best is None else min(best, c)
            if best is not None:
                scored.append((best, r))
        scored.sort(key=lambda t: t[0])
        prio = set()
        for cost, r in scored[: max(20, self.cfg["max_watched_books"] // 4)]:
            if cost < Decimal("1.03"):
                prio |= {r.market_a, r.market_b}
        for o in self.scanner.opps.values():
            if o.state in ("active", "stale", "unavailable"):
                prio |= {l["market_key"] for l in o.legs}
        self.store.priority = prio

    async def _book_poll_loop(self) -> None:
        await asyncio.sleep(1)
        rounds = 0
        while True:
            try:
                self._prioritise_from_listings()
                watch = self.store.watch_list(self.cfg["max_watched_books"])
                by_conn: dict[str, list[Market]] = {}
                now = utcnow()
                reconcile = rounds % 4 == 0  # periodic full REST reconciliation even for streamed books
                for m in watch:
                    cname = self.exchange_to_connector.get(m.exchange)
                    if not cname:
                        continue
                    conn = self.connectors[cname]
                    b = self.store.books.get(m.key)
                    streamed_fresh = (conn.status.stream_connected and b is not None and b.source == "ws"
                                      and not b.force_stale and b.age_seconds(now) < self.cfg["stale_after_s"] / 2)
                    if streamed_fresh and not reconcile:
                        continue
                    by_conn.setdefault(cname, []).append(m)
                results = await asyncio.gather(*(self.connectors[c].fetch_order_books(ms) for c, ms in by_conn.items()), return_exceptions=True)
                for (cname, _), res in zip(by_conn.items(), results):
                    conn = self.connectors[cname]
                    if isinstance(res, Exception):
                        conn.status.last_error = f"order books: {res}"
                        log.warning("book fetch failed for %s: %s", cname, res)
                        continue
                    conn.status.connected = True
                    for b in res:
                        self.store.set_book(b)
                if by_conn:
                    self._scan_event.set()
                rounds += 1
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("book poll loop error")
            await asyncio.sleep(self.cfg["book_poll_interval_s"])

    # --------------------------------------------------------------- streams
    def _restart_streams(self) -> None:
        watch = self.store.watch_list(self.cfg["max_watched_books"])
        for cname, conn in self.connectors.items():
            if not conn.supports_streaming:
                continue
            if cname == "polymarket" and not self.settings.polymarket_ws_enabled:
                continue
            if cname == "kalshi" and not self.settings.kalshi_credentials_configured:
                conn.status.notes = ["WebSocket streaming disabled: requires Kalshi API key. Using REST polling."]
                continue
            mine = [m for m in watch if self.exchange_to_connector.get(m.exchange) == cname][:250]
            if conn.simulated:
                mine = list(self.store.markets.values())
            ids = frozenset(m.key for m in mine)
            if not ids or (cname in self._stream_tasks and not self._stream_tasks[cname].done() and self._stream_watch.get(cname) == ids):
                continue
            old = self._stream_tasks.pop(cname, None)
            if old:
                old.cancel()
            self._stream_watch[cname] = ids
            self._stream_tasks[cname] = asyncio.create_task(self._run_stream(conn, mine), name=f"stream:{cname}")

    async def _run_stream(self, conn: ExchangeConnector, markets: list[Market]) -> None:
        async def on_book(book: OrderBook) -> None:
            self.store.set_book(book)
            self._scan_event.set()

        async def on_stale(keys: list[str]) -> None:
            self.store.mark_stale(keys)
            self._scan_event.set()
            await self.alerts.process([], [{"type": "stale_data", "title": f"{conn.display_name} stream disconnected",
                                            "body": f"{len(keys)} markets marked stale; reconnecting and reconciling from REST.", "key": conn.name}])

        try:
            await conn.subscribe(markets, on_book, on_stale)
        except NotImplementedError:
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("stream for %s crashed", conn.name)

    # ------------------------------------------------------------------ scan
    async def _scan_loop(self) -> None:
        while True:
            await self._scan_event.wait()
            self._scan_event.clear()
            await asyncio.sleep(self.settings.scan_debounce_ms / 1000)
            try:
                dirty = self.store.take_dirty()
                full, self._full_scan = self._full_scan, False
                events = self.scanner.scan(dirty, full=full)
                await self.handle_events(events)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("scan loop error")

    async def handle_events(self, events: list[ScanEvent], force_broadcast: bool = False) -> None:
        if events:
            await self.alerts.process(events)
            lifecycle = [e.opp for e in events if e.kind != "updated"]
            if lifecycle:
                await asyncio.to_thread(self.persistence.upsert_opportunities, lifecycle, True)
        now = asyncio.get_running_loop().time()
        if events and (force_broadcast or now - self._last_broadcast > 1.0):
            self._last_broadcast = now
            summaries = [o.summary() for o in self.scanner.list()]
            await self.hub.broadcast({"type": "opportunities", "data": summaries, "summary": self.dashboard_summary()})
            await self.cache.put_opportunities(summaries)

    # ----------------------------------------------------------- maintenance
    async def _maintenance_loop(self) -> None:
        last_hist = last_snap = 0.0
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(5)
            try:
                events = self.scanner.refresh_staleness()
                await self.handle_events(events, force_broadcast=True)
                await self._check_connector_health()
                t = loop.time()
                if t - last_hist >= self.settings.price_history_interval_s:
                    last_hist = t
                    cutoff = utcnow() - timedelta(seconds=self.settings.price_history_interval_s)
                    books = [b for b in self.store.books.values() if b.received_at >= cutoff]
                    sim = {k: m.is_simulated for k, m in self.store.markets.items()}
                    if books:
                        await asyncio.to_thread(self.persistence.write_price_history, books, sim)
                if t - last_snap >= self.settings.snapshot_interval_s:
                    last_snap = t
                    active = [o for o in self.scanner.list()]
                    await asyncio.to_thread(self.persistence.upsert_opportunities, active, True)
                    leg_keys = {l["market_key"] for o in active for l in o.legs}
                    books = [self.store.books[k] for k in leg_keys if k in self.store.books]
                    if books:
                        await asyncio.to_thread(self.persistence.write_book_snapshots, books)
                        await self.cache.put_books([b.to_dict(5) for b in books])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("maintenance loop error")

    async def _check_connector_health(self) -> None:
        system = []
        for name, c in self.connectors.items():
            last = c.status.last_success
            ok = c.status.connected and last is not None and (utcnow() - last).total_seconds() < max(120, 4 * self.cfg["book_poll_interval_s"])
            prev = self._connector_ok.get(name)
            if prev is True and not ok:
                system.append({"type": "exchange_disconnected", "title": f"{c.display_name} disconnected",
                               "body": c.status.last_error or "No successful response recently.", "key": name})
            self._connector_ok[name] = ok
        if system:
            await self.alerts.process([], system)

    # ---------------------------------------------------------------- views
    def connector_statuses(self) -> list[dict]:
        out = []
        for name, c in self.connectors.items():
            d = c.status.to_dict()
            d["healthy"] = self._connector_ok.get(name, c.status.connected)
            out.append(d)
        return out

    def dashboard_summary(self) -> dict:
        opps = self.scanner.list()
        active = [o for o in opps if o.state == "active"]
        verified = [o for o in active if o.classification == "verified"]
        potential = [o for o in active if o.classification == "potential"]
        rois = [o.metric("net_roi") for o in verified + potential]
        profits = [o.metric("max_profit") for o in verified + potential]
        cats = {m.category for m in self.store.markets.values()}
        last_book = self.store.last_book_update
        return {
            "mode": self.mode,
            "mode_reason": self.mode_reason,
            "simulated": self.mode == "demo",
            "active_verified": len(verified),
            "active_potential": len(potential),
            "stale_or_unavailable": len([o for o in opps if o.state in ("stale", "unavailable")]),
            "highest_net_roi": max(rois) if rois else None,
            "average_net_roi": (sum(rois) / len(rois)) if rois else None,
            "highest_profit": max(profits) if profits else None,
            "total_profitable_capital": sum(o.metric("capital") for o in verified + potential),
            "total_profitable_contracts": sum(float(o.sizing.get("optimal_qty") or 0) for o in verified + potential),
            "connected_exchanges": sum(1 for c in self.connectors.values() if c.status.connected),
            "exchanges": self.store.exchanges(),
            "monitored_markets": len(self.store.markets),
            "books_tracked": len(self.store.books),
            "matches": len(self.store.matches),
            "categories": sorted(cats),
            "last_market_update": last_book.isoformat() if last_book else None,
            "last_scan": self.scanner.last_scan.isoformat() if self.scanner.last_scan else None,
            "connectors": self.connector_statuses(),
            "counters": self.scanner.counters,
            "matching": self.matching,
        }
