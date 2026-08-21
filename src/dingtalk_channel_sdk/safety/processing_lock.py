"""处理锁。"""

from __future__ import annotations

import asyncio
import time
from typing import Dict


class ProcessingLock:
    """短时 TTL 内存锁，防止同一事件并发处理。"""

    def __init__(self, ttl: float = 300.0, sweep_interval: float = 60.0):
        """
        Args:
            ttl: 锁的有效期（秒），默认 5 分钟
            sweep_interval: 清理间隔（秒），默认 1 分钟
        """
        self._ttl = ttl
        self._sweep_interval = sweep_interval
        self._locks: Dict[str, float] = {}  # id -> expire_at
        self._lock = asyncio.Lock()
        self._sweeper_task: asyncio.Task | None = None

    async def acquire(self, id: str) -> bool:
        """获取锁，成功返回 True，已被持有返回 False。"""
        async with self._lock:
            now = time.time()
            expire_at = self._locks.get(id)
            if expire_at is not None and expire_at > now:
                return False
            self._locks[id] = now + self._ttl
            return True

    async def release(self, id: str) -> None:
        """释放锁。"""
        async with self._lock:
            self._locks.pop(id, None)

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
        """定期清理过期锁。"""
        try:
            while True:
                await asyncio.sleep(self._sweep_interval)
                await self._sweep()
        except asyncio.CancelledError:
            pass

    async def _sweep(self) -> None:
        """清理过期锁。"""
        async with self._lock:
            now = time.time()
            expired = [id for id, expire_at in self._locks.items() if expire_at <= now]
            for id in expired:
                del self._locks[id]

    async def dispose(self) -> None:
        """清理所有锁并停止后台任务。"""
        await self.stop_sweeper()
        async with self._lock:
            self._locks.clear()
