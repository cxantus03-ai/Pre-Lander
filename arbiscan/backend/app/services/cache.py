"""Optional Redis mirror of real-time snapshots.

If ARBISCAN_REDIS_URL is set, the latest order books and opportunity list are
written to Redis (and published on ``arbiscan:updates``) so other processes —
e.g. additional API replicas — can read real-time state. Without Redis the
app runs entirely from its in-process store.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


class SnapshotCache:
    def __init__(self, url: str | None):
        self.url = url
        self.client = None
        self.ok = False

    async def connect(self) -> None:
        if not self.url:
            return
        try:
            import redis.asyncio as redis

            self.client = redis.from_url(self.url, decode_responses=True)
            await self.client.ping()
            self.ok = True
            log.info("Redis snapshot cache connected")
        except Exception as exc:  # noqa: BLE001
            log.warning("Redis unavailable (%s); continuing without it", exc)
            self.client, self.ok = None, False

    async def put_books(self, books: list[dict]) -> None:
        if not self.ok:
            return
        try:
            mapping = {b["market_key"]: json.dumps(b) for b in books}
            if mapping:
                await self.client.hset("arbiscan:books", mapping=mapping)
        except Exception as exc:  # noqa: BLE001
            log.debug("redis put_books failed: %s", exc)

    async def put_opportunities(self, opps: list[dict]) -> None:
        if not self.ok:
            return
        try:
            data = json.dumps(opps, default=str)
            await self.client.set("arbiscan:opportunities", data)
            await self.client.publish("arbiscan:updates", json.dumps({"type": "opportunities", "count": len(opps)}))
        except Exception as exc:  # noqa: BLE001
            log.debug("redis put_opportunities failed: %s", exc)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
