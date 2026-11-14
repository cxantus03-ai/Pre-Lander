"""Integration tests for odds collection using recorded real API payloads (mocked HTTP)."""
import json
from decimal import Decimal

import httpx
import pytest
import respx

from app.config import Settings
from app.connectors.http import ApiError, HttpClient
from app.connectors.kalshi import KalshiConnector, parse_orderbook
from app.connectors.polymarket import PolymarketConnector
from app.core.domain import FeeKind, MarketStatus, Side

from .conftest import load_fixture

S = Settings(polymarket_max_events=50, kalshi_max_markets=500, polymarket_rps=100, kalshi_rps=100)
GAMMA, CLOB, KALSHI = S.polymarket_gamma_url, S.polymarket_clob_url, S.kalshi_api_url


@respx.mock
async def test_polymarket_discovery_and_books():
    events = load_fixture("polymarket_events.json")
    respx.get(f"{GAMMA}/events/keyset").mock(return_value=httpx.Response(200, json=events))
    books = load_fixture("polymarket_books.json")
    respx.post(f"{CLOB}/books").mock(return_value=httpx.Response(200, json=books))
    c = PolymarketConnector(S)
    markets, groups = await c.fetch_markets()
    assert markets and all(m.exchange == "polymarket" for m in markets)
    m = markets[0]
    # original identifiers preserved
    raw = events["events"][0]["markets"][0]
    assert m.external_id == str(raw["id"]) and m.title == raw["question"]
    assert m.yes_token_id == json.loads(raw["clobTokenIds"])[0]
    assert m.fee.kind is FeeKind.POLYMARKET_SCHEDULE and not m.fee.verified  # fee formula unverified by default
    # multi-outcome negRisk event becomes a mutually exclusive group, not assumed exhaustive
    g = [g for g in groups if g.extra.get("neg_risk")][0]
    assert g.mutually_exclusive and g.exhaustive is not True

    ob = (await c.fetch_order_books([m]))[0]
    assert ob.depth_complete
    asks = ob.asks(Side.YES)
    assert asks == sorted(asks, key=lambda lv: lv.price)  # ascending
    bids = ob.bids(Side.YES)
    assert bids == sorted(bids, key=lambda lv: lv.price, reverse=True)
    # best YES ask from the CLOB book matches the raw response's lowest ask
    raw_yes = [b for b in books if b["asset_id"] == m.yes_token_id][0]
    assert asks[0].price == min(Decimal(a["price"]) for a in raw_yes["asks"])
    await c.close()


@respx.mock
async def test_polymarket_pagination_follows_cursor():
    page1 = {"events": load_fixture("polymarket_events.json")["events"][:1], "next_cursor": "abc"}
    page2 = {"events": load_fixture("polymarket_events.json")["events"][1:2], "next_cursor": None}
    route = respx.get(f"{GAMMA}/events/keyset").mock(side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
    c = PolymarketConnector(S)
    markets, _ = await c.fetch_markets()
    assert route.call_count == 2
    assert route.calls[1].request.url.params["after_cursor"] == "abc"
    assert len(markets) == 2
    await c.close()


@respx.mock
async def test_kalshi_discovery_fees_and_implied_asks():
    respx.get(f"{KALSHI}/series").mock(return_value=httpx.Response(200, json=load_fixture("kalshi_series.json")))
    respx.get(f"{KALSHI}/events").mock(return_value=httpx.Response(200, json=load_fixture("kalshi_events.json")))
    ob_raw = load_fixture("kalshi_orderbook.json")
    respx.get(url__regex=rf"{KALSHI}/markets/.+/orderbook").mock(return_value=httpx.Response(200, json=ob_raw))
    c = KalshiConnector(S)
    markets, groups = await c.fetch_markets()
    m = markets[0]
    assert m.exchange == "kalshi" and m.status is MarketStatus.OPEN
    assert m.fee.kind is FeeKind.KALSHI_QUADRATIC and m.fee.verified
    assert m.rules_primary
    me = [g for g in groups if g.mutually_exclusive]
    assert me and me[0].exhaustive is not True  # no catch-all -> completeness unverified

    ob = (await c.fetch_order_books([m]))[0]
    no_bids = [(Decimal(p), Decimal(q)) for p, q in ob_raw["orderbook_fp"]["no_dollars"]]
    best_no_bid = max(no_bids)
    # YES ask is implied from the best NO bid: 1 - p, same size
    assert ob.yes_asks[0].price == Decimal(1) - best_no_bid[0]
    assert ob.yes_asks[0].size == best_no_bid[1]
    await c.close()


def test_kalshi_empty_orderbook():
    ob = parse_orderbook("kalshi:X", {"orderbook_fp": {"yes_dollars": None, "no_dollars": []}})
    assert ob.yes_asks == [] and ob.no_asks == []


def test_kalshi_legacy_cent_orderbook():
    ob = parse_orderbook("kalshi:X", {"orderbook": {"yes": [[40, 10]], "no": [[55, 20]]}})
    assert ob.yes_asks[0].price == Decimal("0.45") and ob.no_asks[0].price == Decimal("0.60")


@respx.mock
async def test_rate_limit_retry_honours_retry_after():
    route = respx.get("https://api.example.test/x").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"ok": True})])
    c = HttpClient("https://api.example.test", 100, "test")
    assert await c.get("/x") == {"ok": True}
    assert route.call_count == 2
    await c.close()


@respx.mock
async def test_api_failure_raises_after_retries():
    respx.get("https://api.example.test/y").mock(return_value=httpx.Response(503))
    c = HttpClient("https://api.example.test", 100, "test", max_retries=1)
    c._backoff = staticmethod(lambda attempt: 0)  # type: ignore[assignment]
    with pytest.raises(ApiError):
        await c.get("/y")
    assert c.error_count == 2
    await c.close()


@respx.mock
async def test_client_error_not_retried():
    route = respx.get("https://api.example.test/z").mock(return_value=httpx.Response(404))
    c = HttpClient("https://api.example.test", 100, "test")
    with pytest.raises(ApiError) as e:
        await c.get("/z")
    assert e.value.status == 404 and route.call_count == 1
    await c.close()


def test_kalshi_auth_headers_sign_request(tmp_path):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    p = tmp_path / "k.pem"
    p.write_bytes(pem)
    c = KalshiConnector(Settings(kalshi_api_key_id="abc", kalshi_private_key_path=str(p)))
    h = c.auth_headers("GET", "/trade-api/ws/v2")
    import base64
    key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), (h["KALSHI-ACCESS-TIMESTAMP"] + "GET/trade-api/ws/v2").encode(),
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    assert h["KALSHI-ACCESS-KEY"] == "abc"
