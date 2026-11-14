"""Test 12: feed disconnection -> stale, reconnect, reconcile order-book state."""
import asyncio
import json

from app.config import Settings
from app.connectors.polymarket import PolymarketConnector
from app.core.domain import Market, MarketStatus, Side


class FakeWS:
    def __init__(self, messages, fail_after=True):
        self.messages = list(messages)
        self.sent = []
        self.fail_after = fail_after

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, m):
        self.sent.append(m)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.messages:
            await asyncio.sleep(0)
            return self.messages.pop(0)
        if self.fail_after:
            raise ConnectionError("socket dropped")
        await asyncio.sleep(3600)


def market():
    return Market(exchange="polymarket", external_id="1", title="Q", status=MarketStatus.OPEN, yes_token_id="Y", no_token_id="N")


def book_msg(tok, asks, bids):
    return {"event_type": "book", "asset_id": tok, "timestamp": "1794631171001",
            "asks": [{"price": p, "size": s} for p, s in asks], "bids": [{"price": p, "size": s} for p, s in bids]}


async def test_disconnect_marks_stale_reconnects_and_reconciles():
    c = PolymarketConnector(Settings())
    m = market()
    c._token_index = {"Y": (m.key, "yes"), "N": (m.key, "no")}
    first = FakeWS([
        json.dumps([book_msg("Y", [("0.45", "100")], [("0.44", "50")]), book_msg("N", [("0.56", "50")], [("0.55", "100")])]),
        json.dumps({"event_type": "price_change", "price_changes": [{"asset_id": "Y", "price": "0.45", "size": "0", "side": "SELL"},
                                                                   {"asset_id": "Y", "price": "0.46", "size": "30", "side": "SELL"}]}),
    ])
    second = FakeWS([json.dumps([book_msg("Y", [("0.50", "10")], [("0.49", "5")]), book_msg("N", [("0.51", "5")], [("0.50", "10")])])], fail_after=False)
    sockets = iter([first, second])
    books, stale_calls = [], []

    async def on_book(b):
        books.append(b)

    async def on_stale(keys):
        stale_calls.append(keys)

    import app.connectors.polymarket as pm
    orig_sleep = asyncio.sleep

    async def fast_sleep(t):
        await orig_sleep(0)

    pm.asyncio.sleep = fast_sleep  # skip reconnect back-off
    try:
        task = asyncio.create_task(c.subscribe([m], on_book, on_stale, connect=lambda: next(sockets)))
        for _ in range(200):
            await orig_sleep(0)
            if len(books) >= 3:
                break
        task.cancel()
    finally:
        pm.asyncio.sleep = orig_sleep

    # 1st snapshot, delta (0.45 level removed, 0.46 added), then the post-reconnect snapshot
    assert books[1].best_ask(Side.YES).price.__str__() == "0.46"
    assert stale_calls == [[m.key]]  # disconnect marked the market stale
    assert books[-1].best_ask(Side.YES).price.__str__() == "0.50"  # state replaced by fresh snapshot
    assert json.loads(first.sent[0]) == {"assets_ids": ["Y", "N"], "type": "market"}
    assert len(second.sent) >= 1  # re-subscribed after reconnect


def test_price_change_before_snapshot_is_ignored():
    c = PolymarketConnector(Settings())
    c._token_index = {"Y": ("polymarket:1", "yes")}
    assert c.apply_ws_message({"event_type": "price_change", "price_changes": [{"asset_id": "Y", "price": "0.5", "size": "1", "side": "BUY"}]}) == set()
