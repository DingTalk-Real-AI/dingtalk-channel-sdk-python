"""全局令牌桶 + QpsLimit 退避（SPEC §6）。"""

from __future__ import annotations

import asyncio
import time

from .config import QPS_BACKOFF_S


class TokenBucket:
    def __init__(self, rate: float):
        self.rate = rate
        self.tokens = rate
        self.last_refill = time.monotonic()
        self.backoff_until = 0.0

    def _refill(self, now: float) -> None:
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_refill = now

    async def wait_for(self) -> None:
        while True:
            now = time.monotonic()
            if now < self.backoff_until:
                await asyncio.sleep(self.backoff_until - now)
                continue
            self._refill(now)
            if self.tokens >= 1:
                self.tokens -= 1
                return
            await asyncio.sleep((1 - self.tokens) / self.rate)

    def trigger_backoff(self, backoff_s: float = QPS_BACKOFF_S) -> None:
        self.backoff_until = time.monotonic() + backoff_s
        self.tokens = 0
        self.last_refill = self.backoff_until
