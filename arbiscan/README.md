# ArbiScan — Prediction-Market Arbitrage Scanner

ArbiScan collects public market data from **Polymarket** and **Kalshi**, matches equivalent contracts, and sizes YES/NO and multi-outcome arbitrage against real order-book depth with each exchange's fee schedule. It then classifies every result as **verified**, **potential** or **invalid**.

> **Research tool only.** ArbiScan never places orders, never logs in to exchange accounts and never moves funds; the code has no such capability. All profits shown are *theoretical models*. Even a verified mathematical arbitrage can fail because of execution (prices move, partial fills), resolution disputes, account or geographic restrictions, or exchange/counterparty risk.

---

## 1. Quick start

### Local development (SQLite, no Docker)

```bash
cd arbiscan/backend
python3 -m pip install -r requirements-dev.txt
cp ../.env.example .env          # optional; defaults work
uvicorn app.main:app --reload --port 8000
```

```bash
cd arbiscan/frontend
npm install
npm run dev                      # http://localhost:5173  (proxies /api and /ws to :8000)
```

If you build the frontend (`npm run build`), the backend serves it directly at http://localhost:8000.

Data mode is controlled by `ARBISCAN_DATA_MODE`:

| Value | Behaviour |
|---|---|
| `auto` (default) | Live data if Polymarket or Kalshi answers at start-up; otherwise DEMO |
| `live` | Real exchange data only; an outage shows the exchange as disconnected |
| `demo` | Two **fictional, clearly labelled** venues ("SIM Poly-style venue", "SIM Kalshi-style venue") with simulated books, suspensions and transient arbitrage |

Simulated data is never mixed with live data. Every simulated market carries `is_simulated=true`, a `SIMULATED` flag and a banner in the UI.

### Docker Compose (PostgreSQL + Redis + app)

```bash
cd arbiscan
cp .env.example .env             # set POSTGRES_PASSWORD at least
docker compose up --build        # http://localhost:8080
```

Migrations run automatically at start-up (`alembic upgrade head`). To run them manually:

```bash
cd backend && ARBISCAN_DATABASE_URL=postgresql+psycopg2://... alembic upgrade head
```

### Tests

```bash
cd backend && python3 -m pytest -q
```

```bash
cd backend && ARBISCAN_TEST_DATABASE_URL=postgresql+psycopg2://user@host/db python3 -m pytest -q tests/test_api.py   # same API suite on PostgreSQL
```

```bash
cd frontend && npm run build     # type-check + production build
```

---

## 2. Connecting the exchange APIs

### Polymarket: no credentials needed
ArbiScan uses only Polymarket's public read endpoints:

| Purpose | Endpoint |
|---|---|
| Market discovery and metadata | `GET https://gamma-api.polymarket.com/events/keyset?active=true&closed=false&limit=100&after_cursor=…` (nested markets, `clobTokenIds`, `feeSchedule`, rules in `description`) |
| Executable depth | `POST https://clob.polymarket.com/books` with `[{"token_id": …}]`, for both the YES token and the NO token |
| Real-time | `wss://ws-subscriptions-clob.polymarket.com/ws/market`, subscribe `{"assets_ids": [...], "type": "market"}`; `book` snapshots plus `price_change` deltas; `PING` every 10 s |

**Fees:** each Gamma market carries `feesEnabled` and `feeSchedule {rate, exponent, takerOnly}`. ArbiScan models the taker fee as `C·p·rate·(p(1−p))^exponent` per fill, rounded up. I could not open docs.polymarket.com from the build environment, so this formula is **unverified**. Fee-enabled Polymarket opportunities are therefore labelled `FEES_ESTIMATED` and can never be *verified*. Once you have checked the formula against the current docs, set `ARBISCAN_POLYMARKET_FEE_FORMULA_VERIFIED=true`. Markets with `feesEnabled=false` count as verified zero-fee.

### Kalshi: public REST needs no credentials; the WebSocket needs an API key

| Purpose | Endpoint |
|---|---|
| Discovery | `GET /trade-api/v2/events?status=open&with_nested_markets=true&limit=200&cursor=…` (category, `mutually_exclusive`, `settlement_sources`, `rules_primary/secondary`, `*_dollars` prices) |
| Fees | `GET /trade-api/v2/series` (`fee_type`, `fee_multiplier` per series), cached hourly |
| Depth | `GET /trade-api/v2/markets/{ticker}/orderbook` returns **bids only** (`orderbook_fp.yes_dollars` / `no_dollars`). ArbiScan derives asks the way the exchange defines them: YES ask = 1 − NO bid, at the same size |
| Real-time (optional) | `wss://api.elections.kalshi.com/trade-api/ws/v2`, channel `orderbook_delta`, with `seq` gap detection and resubscribe |

Taker fee: `ceil_to_cent(0.07 × fee_multiplier × C × P × (1−P))`, computed per fill (conservative).

To enable the Kalshi WebSocket feed (REST polling works without it):
1. Sign in at kalshi.com → **Account & security → API Keys → Create key**. Choose read-only access if it is offered.
2. Save the downloaded private key file (for example `secrets/kalshi.pem`; it is git-ignored).
3. In the **backend** environment set `ARBISCAN_KALSHI_API_KEY_ID=<key id>` and either `ARBISCAN_KALSHI_PRIVATE_KEY_PATH=/path/kalshi.pem` or `ARBISCAN_KALSHI_PRIVATE_KEY_PEM`.
   Requests are signed with RSA-PSS/SHA-256 over `timestamp_ms + METHOD + path`, sent as `KALSHI-ACCESS-KEY/-SIGNATURE/-TIMESTAMP` headers.

ArbiScan never asks for exchange passwords, wallet seed phrases or private wallet keys, and none of these values are ever sent to the browser.

### Rate limits and cadence
Each exchange gets its own token-bucket limiter (`ARBISCAN_*_RPS`, default 8 req/s). Retries back off exponentially and honour `Retry-After` on 429/5xx; 4xx errors are not retried. Discovery runs every `ARBISCAN_DISCOVERY_INTERVAL_S` (300 s). Order books are polled every `ARBISCAN_BOOK_POLL_INTERVAL_S` (15 s) for up to `ARBISCAN_MAX_WATCHED_BOOKS` markets, prioritised as: legs of live opportunities, then matched pairs whose listing prices look promising, then other matches, then mutually exclusive event groups. Streamed books skip REST polling, but every 4th round still reconciles them over REST. All four values can be changed at runtime on the Settings page.

---

## 3. How detection works

```
connectors ──► MarketStore (in-memory, optional Redis mirror) ──► Scanner ──► Alerts / WebSocket / DB history
   │                ▲                         ▲
   └─ discovery ────┴── Matching engine ──────┘
```

### Strategies (every one is a "covered structure")
Every leg is bought in the same quantity Q. Each structure lists the mutually exclusive real-world scenarios and what every leg pays in each one. Profit is the **worst-case** scenario payout minus total cost and fees.

| Type | Structure | Guaranteed payout per set |
|---|---|---|
| A: cross-exchange | Polymarket YES + Kalshi NO (and the reverse) for a matched pair | $1 |
| B: same-market | YES + NO of one contract | $1 |
| C: multi-outcome | YES on every outcome of a mutually exclusive event | $1 **only if the set is exhaustive** (explicit catch-all); otherwise $0, so it is rejected |
| C′: all-NO | NO on every outcome of a mutually exclusive set | N−1 |
| D: implication | A ⇒ B (for example "above $110k" ⇒ "above $100k"): buy YES(B) + NO(A) | $1 |

### Depth- and fee-aware sizing (`app/core/arbitrage.py`)
- Uses `Decimal` arithmetic throughout and walks the ask ladder on every leg (VWAP; no single displayed price is applied to a whole bankroll).
- The marginal cost of one more set rises as you go deeper into the book. Sizing stops at the last order-book segment where marginal cost (price + marginal fee) is still below the payout. That size is the *optimal* quantity.
- It also reports top-of-book quantity, total liquidity, the largest size that is still profitable, and the largest size your constraints allow: per-exchange balances (never pooled), total cap, % of bankroll, slippage limit, max contracts, minimum order size and quantity increments.
- Fees are rounded up per fill. If fee rounding makes a small trade unprofitable, it is rejected.

### Classification (`app/services/scanner.py`)
- **verified** requires all of: a settlement-verified structure (manual approval, or automated checks with zero differences); verified fees; fresh, open, full-depth books on every leg; and positive worst-case net profit.
- **potential** means the modelled net profit is positive but something is unverified: settlement review pending, estimated fees, an inferred structure, or a price divergence above 25 points.
- **invalid** means there is a gross edge, but fees, depth or settlement remove it. These are counted in analytics and never shown as active.
- State: `active`, `stale` (a quote is older than `STALE_AFTER_S`, or its feed disconnected), `unavailable` (suspended or closed), or `expired` (disappeared).

### Market matching (`app/matching/`)
1. **Candidate discovery:** an IDF-weighted inverted index over normalised tokens and entities, with a date-proximity filter.
2. **Semantic comparison:** token, entity and threshold similarity, plus an optional LLM second opinion (`ARBISCAN_LLM_MATCHING_ENABLED`, Claude via the `anthropic` SDK). The LLM is **advisory only**: it can add caution but can never verify a match.
3. **Settlement checks:**
   - **Hard fails:** different participants, opposite predicates (cut/hike, win/lose, above/below), different thresholds, different cutoff dates, different outcomes, distinctive wording on both sides.
   - **Warnings:** partial wording differences, different resolution sources or time zones, different cancellation/tie/50-50/fair-price clauses, early-close differences, negated (inverted) phrasing.
4. **Classification:** `verified_compatible` / `likely_compatible` / `incompatible` / `insufficient_information`. In live testing with real data, most true pairs land in *likely_compatible* and need one click of human review (Market Explorer → Match Review). This is intentional. Manual decisions are stored in `match_verifications` and are never overwritten by automation.

---

## 4. Features

- Polymarket connector: Gamma keyset pagination, CLOB batch books, market WebSocket with reconnect, stale marking and snapshot reconciliation.
- Kalshi connector: nested-events discovery, series fee schedules, implied-ask order books, legacy cent-format compatibility, optional signed WebSocket with sequence-gap detection.
- DEMO connector: simulated books, liquidity changes, suspensions, transient opportunities, an exhaustive and a non-exhaustive multi-outcome event, and a strike ladder.
- Matching engine with manual approve / reject / correct-orientation / reset, and manual match creation.
- Arbitrage engine: binary, same-market, complete-set, all-NO and implication structures, with depth walking, fee rounding, increments, minimum sizes, per-exchange budgets and slippage limits.
- Interactive calculator: per opportunity (live books) and manual (typed ladders and fee models).
- Capital management: total capital, per-exchange balances, reserved capital, per-trade and open-allocation caps, minimum profit and ROI, slippage, settlement horizon.
- Alerts: rule-based, with in-app, browser desktop and optional SMTP email delivery, de-duplication, per-rule cooldowns and a global rate limit. Events include new verified or potential opportunities, ROI and profit thresholds, liquidity becoming executable, disappearance, becoming unprofitable after fees, large price moves, exchange disconnects, stale data and settlement changes.
- History: markets, rules, fee schedules, order-book snapshots and levels, price history, opportunity lifecycle and snapshots, matches and reviews, notifications, manually recorded trades and settlements.
- Analytics (pandas): opportunities per day, average and highest ROI, duration histogram, disappeared count, invalidations by fees and by settlement, exchange pairs, categories, signal frequency, price and spread history with cross-exchange differences. **Realized** P&L comes only from manually recorded trades and is kept separate from theoretical figures.
- API: REST at `/api/*`, docs at `/docs`, WebSocket at `/ws`; all request bodies are validated.

## 5. Configuration
Every variable is listed in [`.env.example`](.env.example). Secrets (Kalshi key, SMTP password, Anthropic key) are `SecretStr`, read only on the backend, and never returned by any endpoint. `/api/status` exposes only a whitelisted set of non-secret settings.

## 6. Known limitations and risks
- **Matching is conservative by design.** Real cross-exchange pairs almost always differ in wording, so they need human review before any opportunity can be *verified*. The text heuristics can miss true matches or pair similar-looking ones; those are flagged but not verified.
- **The Polymarket fee formula is unverified** (see §2). Leave `POLYMARKET_FEE_FORMULA_VERIFIED=false` until you have checked it.
- Polymarket may collect fees in shares rather than USDC. Fees are modelled as USDC cost, which can understate the cost when fees are taken in shares.
- Kalshi WebSocket message handling follows the documented channel, but I could not test it against the live service without credentials. REST polling is the tested default.
- Books are fetched for at most `MAX_WATCHED_BOOKS` markets per poll. At 8 req/s, polling a few hundred Kalshi books takes tens of seconds, so quotes can be up to one poll interval old (they are flagged stale beyond `STALE_AFTER_S`).
- Kalshi fractional order sizes are treated as whole contracts (conservative). Polymarket's `orderMinSize` is enforced.
- Collateral, deposit/withdrawal costs, USDC/USD conversion, geographic eligibility and account limits cannot be read from public APIs. Enter what you know in Capital Management.
- Multi-outcome sets count as exhaustive only when the exchange marks them mutually exclusive **and** they contain an explicit catch-all outcome.
- Single-user application: there is no authentication layer. Do not expose it to the internet without putting it behind your own authentication.
