"""Shared async HTTP client with token-bucket rate limiting and safe retries."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


class RateLimiter:
    """Simple async token bucket."""

    def __init__(self, rate_per_s: float, burst: int | None = None):
        self.rate = max(rate_per_s, 0.1)
        self.capacity = burst or max(1, int(rate_per_s))
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()
        self.penalty_until = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                if now < self.penalty_until:
                    await asyncio.sleep(self.penalty_until - now)
                    continue
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)

    def back_off(self, seconds: float) -> None:
        self.penalty_until = max(self.penalty_until, time.monotonic() + seconds)


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class HttpClient:
    """GET/POST JSON with retry on 429/5xx/network errors (idempotent reads only)."""

    def __init__(self, base_url: str, rate_per_s: float, name: str, timeout: float = 20.0, max_retries: int = 4):
        self.name = name
        self.limiter = RateLimiter(rate_per_s)
        self.max_retries = max_retries
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": "ArbiScan/1.0 (market-data research)"},
            follow_redirects=True,
        )
        self.request_count = 0
        self.error_count = 0
        self.last_error: str | None = None
        self.last_success: float | None = None

    async def close(self) -> None:
        await self.client.aclose()

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None, headers: dict | None = None) -> Any:
        attempt = 0
        while True:
            await self.limiter.acquire()
            self.request_count += 1
            try:
                resp = await self.client.request(method, path, params=params, json=json, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                self.error_count += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= self.max_retries:
                    raise ApiError(f"{self.name} {path}: network error {exc}") from exc
                await asyncio.sleep(self._backoff(attempt))
                attempt += 1
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                self.error_count += 1
                self.last_error = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else self._backoff(attempt)
                if resp.status_code == 429:
                    self.limiter.back_off(wait)
                    log.warning("%s rate limited on %s; backing off %.1fs", self.name, path, wait)
                if attempt >= self.max_retries:
                    raise ApiError(f"{self.name} {path}: HTTP {resp.status_code}", resp.status_code)
                await asyncio.sleep(wait)
                attempt += 1
                continue
            if resp.status_code >= 400:
                self.error_count += 1
                self.last_error = f"HTTP {resp.status_code}"
                raise ApiError(f"{self.name} {path}: HTTP {resp.status_code} {resp.text[:200]}", resp.status_code)
            self.last_success = time.time()
            try:
                return resp.json()
            except ValueError as exc:
                raise ApiError(f"{self.name} {path}: invalid JSON") from exc

    async def get(self, path: str, params: dict | None = None, headers: dict | None = None) -> Any:
        return await self.request("GET", path, params=params, headers=headers)

    async def post(self, path: str, json: Any) -> Any:
        return await self.request("POST", path, json=json)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, (2**attempt) * 0.5) + random.uniform(0, 0.5)
