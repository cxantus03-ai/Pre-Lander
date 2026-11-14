"""DEMO / SIMULATED data connector.

Generates two *fictional* venues whose structure mimics Polymarket and Kalshi
so every part of the pipeline (matching, depth-aware sizing, alerts, history)
can be exercised without network access. Every market it produces has
``is_simulated=True``, its exchange id starts with ``demo_`` and its display
name says SIMULATED. Nothing here is, or is presented as, a real quote.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from ..config import Settings
from ..core.domain import EventGroup, FeeKind, FeeSpec, Market, MarketStatus, OrderBook, PriceLevel, utcnow
from .base import BookCallback, ExchangeConnector, StaleCallback

POLY = "demo_poly"
KALSHI = "demo_kalshi"
POLY_NAME = "SIM Poly-style venue"
KALSHI_NAME = "SIM Kalshi-style venue"

KALSHI_FEE = FeeSpec(FeeKind.KALSHI_QUADRATIC, rate=Decimal("0.07"), verified=True, source="simulated quadratic schedule")
POLY_FEE_FREE = FeeSpec(FeeKind.ZERO, verified=True, source="simulated fee-free market")
POLY_FEE_SCHED = FeeSpec(
    FeeKind.POLYMARKET_SCHEDULE, rate=Decimal("0.04"), exponent=Decimal(1), rounding_increment=Decimal("0.0001"),
    verified=False, source="simulated fee schedule", notes="Simulated unverified fee formula",
)


@dataclass
class PairSpec:
    slug: str
    category: str
    poly_title: str
    kalshi_title: str
    poly_rules: str
    kalshi_rules: str
    days: int
    prob: float
    poly_fee: FeeSpec = POLY_FEE_FREE
    kalshi_days: int | None = None  # different close => settlement mismatch scenario
    kalshi_sources: tuple[str, ...] = ()
    poly_sources: tuple[str, ...] = ()


def _d(days: int) -> str:
    return (utcnow() + timedelta(days=days)).strftime("%B %d, %Y").replace(" 0", " ")


def _specs() -> list[PairSpec]:
    s = []
    s.append(PairSpec("fed-cut-dec", "economics",
        "Will the Fed cut interest rates at the December FOMC meeting?",
        "Fed cuts interest rates at December FOMC meeting?",
        f"This market resolves Yes if the Federal Reserve announces a cut to the federal funds target range at the FOMC meeting concluding {_d(26)}. Resolution source: Federal Reserve press release.",
        f"If the Federal Reserve cuts the federal funds target range at the FOMC meeting concluding {_d(26)}, the market resolves to Yes. Source: Federal Reserve.",
        26, 0.42, poly_sources=("Federal Reserve",), kalshi_sources=("Federal Reserve",)))
    s.append(PairSpec("cpi-above-3", "economics",
        "Will CPI year-over-year inflation be above 3.0% for October?",
        "October CPI YoY above 3.0%?",
        f"Resolves Yes if the BLS reports October CPI-U year-over-year above 3.0% in the release on {_d(18)}. Source: Bureau of Labor Statistics.",
        f"If October CPI-U year-over-year inflation is above 3.0% as first reported by the Bureau of Labor Statistics on {_d(18)}, then the market resolves to Yes.",
        18, 0.37, poly_sources=("Bureau of Labor Statistics",), kalshi_sources=("Bureau of Labor Statistics",)))
    s.append(PairSpec("btc-100k", "crypto",
        "Will Bitcoin be above $100,000 on December 31?",
        "Bitcoin price above $100,000 on Dec 31?",
        f"Resolves Yes if the Coinbase BTC-USD 1-minute candle close at 12:00 PM ET on {_d(47)} is above $100,000. Source: Coinbase.",
        f"If the CF Benchmarks Bitcoin Real Time Index at 12:00 PM ET on {_d(47)} is above $100,000, the market resolves to Yes.",
        47, 0.55, poly_fee=POLY_FEE_SCHED, poly_sources=("Coinbase",), kalshi_sources=("CF Benchmarks",)))
    s.append(PairSpec("senate-bill", "politics",
        "Will the Senate pass the Infrastructure Renewal Act before January 1?",
        "Senate passes Infrastructure Renewal Act before Jan 1?",
        f"Resolves Yes if the U.S. Senate passes the Infrastructure Renewal Act by {_d(48)} 11:59 PM ET. Source: congress.gov.",
        f"If the Senate passes the Infrastructure Renewal Act before {_d(48)}, the market resolves to Yes. Source: congress.gov.",
        48, 0.31, poly_sources=("congress.gov",), kalshi_sources=("congress.gov",)))
    s.append(PairSpec("gov-shutdown", "politics",
        "Government shutdown before February?",
        "Will there be a US government shutdown before February?",
        f"Resolves Yes if a lapse in appropriations causes a federal government shutdown before {_d(78)} 11:59 PM ET. Source: Office of Management and Budget.",
        f"If a federal government shutdown begins before {_d(70)}, then the market resolves to Yes. Source: Office of Management and Budget.",
        78, 0.22, kalshi_days=70, poly_sources=("Office of Management and Budget",), kalshi_sources=("Office of Management and Budget",)))
    s.append(PairSpec("nba-final", "sports",
        "Will the Boston Comets win the 2027 Pro Basketball Championship?",
        "Boston Comets to win the 2027 Pro Basketball Championship?",
        f"Resolves Yes if the Boston Comets win the 2027 championship series concluding by {_d(220)}. If the season is cancelled the market resolves 50-50. Source: league official site.",
        f"If the Boston Comets win the 2027 championship, the market resolves to Yes. If the season is cancelled, the market resolves to a fair price. Source: league official site.",
        220, 0.18))
    s.append(PairSpec("hurricane", "weather",
        "Will a Category 4+ hurricane make US landfall before December 1?",
        "Category 4 or stronger hurricane US landfall before Dec 1?",
        f"Resolves Yes if the National Hurricane Center reports a Category 4 or stronger hurricane landfall in the US before {_d(17)}. Source: National Hurricane Center.",
        f"If a hurricane makes landfall in the United States at Category 4 or stronger before {_d(17)}, as reported by the National Hurricane Center, the market resolves to Yes.",
        17, 0.08, poly_sources=("National Hurricane Center",), kalshi_sources=("National Hurricane Center",)))
    s.append(PairSpec("box-office", "entertainment",
        "Will 'Starfall' gross over $150M domestic opening weekend?",
        "Will 'Starfall' gross above $150M in its domestic opening weekend?",
        f"Resolves Yes if Box Office Mojo reports a domestic opening weekend gross above $150 million for 'Starfall' (weekend ending {_d(9)}).",
        f"If 'Starfall' grosses above $150 million in its domestic opening weekend ending {_d(9)} according to Box Office Mojo, the market resolves to Yes.",
        9, 0.47, poly_sources=("Box Office Mojo",), kalshi_sources=("Box Office Mojo",)))
    s.append(PairSpec("unemployment", "economics",
        "Will the unemployment rate be 4.5% or higher in the November jobs report?",
        "November unemployment rate at or above 4.5%?",
        f"Resolves Yes if the unemployment rate published by the BLS on {_d(21)} is 4.5% or higher. Source: Bureau of Labor Statistics.",
        f"If the unemployment rate in the Employment Situation report released {_d(21)} is at or above 4.5%, the market resolves to Yes. Source: Bureau of Labor Statistics.",
        21, 0.29, poly_sources=("Bureau of Labor Statistics",), kalshi_sources=("Bureau of Labor Statistics",)))
    s.append(PairSpec("eth-5k", "crypto",
        "Will Ethereum reach $5,000 before January 1?",
        "Ethereum above $5,500 before Jan 1?",
        f"Resolves Yes if Ethereum trades at or above $5,000 on Binance ETH/USDT before {_d(48)}.",
        f"If the ETH price exceeds $5,500 per CF Benchmarks before {_d(48)}, resolves Yes.",
        48, 0.21, poly_sources=("Binance",), kalshi_sources=("CF Benchmarks",)))
    return s


MULTI = {
    "title": "Who will win the 2027 Northfield mayoral election?",
    "category": "politics",
    "days": 160,
    "outcomes": [("Alice Moreno", 0.38), ("Ben Okafor", 0.31), ("Carla Singh", 0.19), ("Other", 0.12)],
}
MULTI_INCOMPLETE = {
    "title": "Which studio will release the top-grossing film of 2027?",
    "category": "entertainment",
    "days": 300,
    "outcomes": [("Studio A", 0.33), ("Studio B", 0.29), ("Studio C", 0.24)],  # no catch-all
}
LADDER = {"title": "Gold price on Dec 31", "category": "finance", "days": 47, "strikes": [(2400, 0.72), (2500, 0.51), (2600, 0.30)]}


class _SimBook:
    def __init__(self, rng: random.Random, prob: float, tick: Decimal, depth_scale: int):
        self.rng = rng
        self.mid = prob
        self.tick = tick
        self.depth_scale = depth_scale
        self.edge = 0.0  # >0 pushes this venue's YES cheaper (creates cross-venue gaps)
        self.suspended_ticks = 0
        self.anchor: "_SimBook | None" = None  # equivalent market on the other venue

    def step(self) -> None:
        self.mid = min(0.95, max(0.05, self.mid + self.rng.gauss(0, 0.004)))
        if self.anchor is not None:  # equivalent contracts stay close (mean reversion)
            self.mid += 0.3 * (self.anchor.mid - self.mid)
        if self.edge > 0 and self.rng.random() < 0.08:
            self.edge = 0.0  # opportunity disappears
        if self.suspended_ticks > 0:
            self.suspended_ticks -= 1
        elif self.rng.random() < 0.004:
            self.suspended_ticks = self.rng.randint(3, 10)

    def levels(self, start: float, direction: int, n: int = 6) -> list[PriceLevel]:
        out = []
        t = float(self.tick)
        for i in range(n):
            p = round(start + direction * i * t * self.rng.choice([1, 1, 2]), 4)
            p = max(t, min(1 - t, p))
            size = Decimal(self.rng.randint(5, self.depth_scale)) * (i + 1)
            out.append(PriceLevel(Decimal(str(round(p / t) * t)).quantize(self.tick), size))
        return out

    def book(self, key: str, yes_shift: float = 0.0) -> OrderBook:
        t = float(self.tick)
        half = t * self.rng.choice([1, 1, 2])
        yes_ask0 = self.mid + half - yes_shift
        yes_bid0 = self.mid - half - yes_shift
        yes_asks = self.levels(yes_ask0, +1)
        yes_bids = self.levels(yes_bid0, -1)
        # NO side mirrors the YES side (as on real binary books)
        no_asks = [PriceLevel(Decimal(1) - lv.price, lv.size) for lv in yes_bids]
        no_bids = [PriceLevel(Decimal(1) - lv.price, lv.size) for lv in yes_asks]
        return OrderBook(key, yes_bids=yes_bids, yes_asks=yes_asks, no_bids=no_bids, no_asks=no_asks, source="demo", exchange_ts=utcnow()).normalise()


class DemoConnector(ExchangeConnector):
    """One connector that simulates both demo venues."""

    name = "demo"
    display_name = "DEMO / SIMULATED DATA"
    simulated = True
    supports_streaming = True

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.rng = random.Random(settings.demo_seed)
        self.markets: dict[str, Market] = {}
        self.groups: list[EventGroup] = []
        self.sims: dict[str, _SimBook] = {}
        self._build()
        self.status.connected = True
        self.status.notes.append("All prices are SIMULATED. No real exchange data is shown in DEMO mode.")

    def _mk(self, venue: str, ext: str, title: str, rules: str, category: str, days: int, fee: FeeSpec, sources=(), **kw) -> Market:
        tick = Decimal("0.01") if venue == KALSHI else Decimal("0.001")
        m = Market(
            exchange=venue,
            external_id=ext,
            title=title,
            rules_primary=rules,
            description=rules if venue == POLY else "",
            category=category,
            close_time=utcnow() + timedelta(days=days),
            expiration_time=utcnow() + timedelta(days=days),
            status=MarketStatus.OPEN,
            url=None,
            tick_size=tick,
            min_order_size=Decimal(1) if venue == KALSHI else Decimal(5),
            qty_increment=Decimal(1),
            fee=fee,
            volume=Decimal(self.rng.randint(10_000, 2_000_000)),
            resolution_sources=list(sources),
            is_simulated=True,
            **kw,
        )
        self.markets[m.key] = m
        return m

    def _build(self) -> None:
        for spec in _specs():
            pm = self._mk(POLY, f"SIM-PM-{spec.slug}", spec.poly_title, spec.poly_rules, spec.category, spec.days, spec.poly_fee, spec.poly_sources)
            km = self._mk(KALSHI, f"SIM-K-{spec.slug.upper()}", spec.kalshi_title, spec.kalshi_rules, spec.category,
                          spec.kalshi_days or spec.days, KALSHI_FEE, spec.kalshi_sources)
            km.can_close_early = True
            base = spec.prob
            self.sims[pm.key] = _SimBook(self.rng, base, pm.tick_size, 400)
            self.sims[km.key] = _SimBook(self.rng, base + self.rng.uniform(-0.01, 0.01), km.tick_size, 300)
            self.sims[km.key].anchor = self.sims[pm.key]

        for spec, venue, ext_prefix in ((MULTI, KALSHI, "SIM-K-MAYOR"), (MULTI_INCOMPLETE, POLY, "SIM-PM-STUDIO")):
            keys = []
            for name, prob in spec["outcomes"]:
                fee = KALSHI_FEE if venue == KALSHI else POLY_FEE_FREE
                m = self._mk(venue, f"{ext_prefix}-{name.split()[0].upper()}", f"{spec['title']} — {name}",
                             f"Resolves Yes if {name} wins. {spec['title']}", spec["category"], spec["days"], fee,
                             event_id=ext_prefix, event_title=spec["title"], outcome_label=name)
                self.sims[m.key] = _SimBook(self.rng, prob, m.tick_size, 250)
                keys.append(m.key)
            has_other = any(n == "Other" for n, _ in spec["outcomes"])
            self.groups.append(EventGroup(
                exchange=venue, event_id=ext_prefix, title=spec["title"], category=spec["category"], market_keys=keys,
                mutually_exclusive=True, exhaustive=True if has_other else None,
                exhaustive_reason="Simulated event with explicit catch-all outcome." if has_other else "No catch-all outcome; set may be incomplete.",
                is_simulated=True,
            ))

        keys = []
        for strike, prob in LADDER["strikes"]:
            m = self._mk(KALSHI, f"SIM-K-GOLD-T{strike}", f"Gold above ${strike} on Dec 31?",
                         f"If the LBMA gold PM price on Dec 31 is above ${strike}, resolves Yes.", LADDER["category"], LADDER["days"], KALSHI_FEE,
                         ("LBMA",), event_id="SIM-K-GOLD", event_title=LADDER["title"], outcome_label=f"Above ${strike}",
                         strike=Decimal(strike), strike_type="greater")
            self.sims[m.key] = _SimBook(self.rng, prob, m.tick_size, 200)
            keys.append(m.key)
        self.groups.append(EventGroup(exchange=KALSHI, event_id="SIM-K-GOLD", title=LADDER["title"], category="finance",
                                      market_keys=keys, mutually_exclusive=False, is_simulated=True,
                                      exhaustive_reason="Threshold ladder (not mutually exclusive)."))

    # ----------------------------------------------------------------------
    def _maybe_inject(self) -> None:
        """Occasionally make one venue's YES cheaper, creating a transient gap."""
        poly_keys = [k for k in self.sims if k.startswith(POLY) and "STUDIO" not in k]
        if self.rng.random() < 0.25 and poly_keys:
            k = self.rng.choice(poly_keys)
            self.sims[k].edge = self.rng.uniform(0.015, 0.06)
        if self.rng.random() < 0.05:
            for k, s in self.sims.items():
                if "MAYOR" in k:
                    s.edge = self.rng.uniform(0.01, 0.03)
        if self.rng.random() < 0.05:
            s = self.sims.get(f"{KALSHI}:SIM-K-GOLD-T2600")
            if s:
                s.edge = -0.25  # pushes the 2600 strike's NO cheap → ladder inconsistency

    def current_books(self) -> list[OrderBook]:
        books = []
        for key, sim in self.sims.items():
            m = self.markets[key]
            m.status = MarketStatus.SUSPENDED if sim.suspended_ticks > 0 else MarketStatus.OPEN
            b = sim.book(key, yes_shift=sim.edge)
            books.append(b)
        return books

    def tick(self) -> list[OrderBook]:
        for sim in self.sims.values():
            sim.step()
        self._maybe_inject()
        return self.current_books()

    async def fetch_markets(self):
        self.status.markets = len(self.markets)
        self.status.last_success = utcnow()
        return list(self.markets.values()), list(self.groups)

    async def fetch_order_books(self, markets: list[Market]) -> list[OrderBook]:
        wanted = {m.key for m in markets}
        books = [b for b in self.current_books() if b.market_key in wanted]
        self.status.books = len(books)
        self.status.last_success = utcnow()
        return books

    async def subscribe(self, markets: list[Market], on_book: BookCallback, on_stale: StaleCallback, connect=None) -> None:
        self.status.streaming = True
        self.status.stream_connected = True
        while True:
            await asyncio.sleep(self.settings.demo_tick_s)
            for b in self.tick():
                await on_book(b)
            self.status.last_success = utcnow()
