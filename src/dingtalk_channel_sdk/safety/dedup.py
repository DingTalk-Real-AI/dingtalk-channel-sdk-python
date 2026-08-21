"""双层去重：messageId（协议层）+ msgId（业务层），TTL 5min（SPEC §3.2 / E6）。

增强：LRU 容量上限 + 后台定期清扫。
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Optional


class Deduper:
    def __init__(
        self,
        ttl_s: float = 300.0,
        max_entries: int = 10000,
        sweep_interval_s: float = 300.0,
    ):
        self.ttl = ttl_s
        self.max_entries = max_entries
        self.sweep_interval = sweep_interval_s
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._sweeper_task: Optional[asyncio.Task] = None

    def check_and_mark(self, *keys: str) -> bool:
        """命中（重复）返回 True。"""
        now = time.time()
        # 内联 TTL 清扫（轻量，仅过期项；完整清扫由后台任务负责）
        for k in [k for k, ts in self._seen.items() if now - ts > self.ttl]:
            del self._seen[k]
        hit = False
        for k in keys:
            if k and k in self._seen:
                hit = True
                self._seen.move_to_end(k)  # LRU：移至末尾（最近使用）
        for k in keys:
            if k:
                self._seen[k] = now
                self._seen.move_to_end(k)
                # LRU 容量淘汰
                while len(self._seen) > self.max_entries:
                    self._seen.popitem(last=False)
        return hit

    async def start_sweeper(self) -> None:
        """启动后台清理任务。"""
        if self._sweeper_task is not None:
            return
        self._sweeper_task = asyncio.create_task(self._sweep_loop())

    async def stop_sweeper(self) -> None:
        """停止后台清理任务。"""
        if self._sweeper_task is not None:
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except asyncio.CancelledError:
                pass
            self._sweeper_task = None

    async def _sweep_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.sweep_interval)
                self._sweep()
        except asyncio.CancelledError:
            pass

    def _sweep(self) -> None:
        now = time.time()
        expired = [k for k, ts in self._seen.items() if now - ts > self.ttl]
        for k in expired:
            del self._seen[k]

    async def dispose(self) -> None:
        await self.stop_sweeper()
        self._seen.clear()
