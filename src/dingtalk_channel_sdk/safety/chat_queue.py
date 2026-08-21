"""Per-chat 串行队列：同会话消息强制串行处理，批处理刷新同样按会话串行。"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .batching import BatchConfig, BatchedMessage, _merge_messages
from ..config import ChatQueueConfig, MediaBatchConfig
from ..normalize.message import IncomingMessage

BatchFlushHandler = Callable[[BatchedMessage], Awaitable[None]]
SerialTask = Callable[[], Awaitable[Any]]


class ChatQueue:
    """单 scope（conversation_id）的串行队列 + 批处理缓冲。

    - run(task)：同会话一次只执行一个任务（串行保证）。
    - push(msg, handler)：缓冲 + debounce，flush 亦走同一串行锁。
    - 媒体消息在 MediaBatchConfig.enabled=False 时立即刷新（保持既有行为），
      enabled=True 时与同会话媒体在窗口内合并（资源合并见 _merge_messages）。
    """

    def __init__(self, scope: str, cfg: BatchConfig, media_batch: Optional[MediaBatchConfig] = None) -> None:
        self.scope = scope
        self.cfg = cfg
        self.media_batch = media_batch
        self._buffer: List[IncomingMessage] = []
        self._buffer_chars = 0
        self._timer: Optional[asyncio.Task] = None
        self._pending: Optional[BatchFlushHandler] = None
        self._serial = asyncio.Lock()

    async def run(self, task: SerialTask) -> Any:
        """串行执行任务。"""
        async with self._serial:
            return await task()

    def push(self, msg: IncomingMessage, handler: BatchFlushHandler) -> None:
        self._buffer.append(msg)
        self._buffer_chars += len(msg.text or "")
        if self._pending is None:
            self._pending = handler

        media_enabled = self.media_batch is not None and self.media_batch.enabled
        max_items = self.media_batch.max_items if self.media_batch else 8

        if len(self._buffer) >= min(self.cfg.max_messages, max_items) or self._buffer_chars >= self.cfg.max_chars:
            self._clear_timer()
            asyncio.ensure_future(self._flush())
            return

        if self.cfg.delay_s <= 0:
            self._clear_timer()
            asyncio.ensure_future(self._flush())
            return

        self._clear_timer()
        delay = self.cfg.delay_s
        if self._buffer_chars >= self.cfg.long_threshold_chars:
            delay = self.cfg.long_delay_s
        # 媒体批处理启用时：媒体消息用媒体窗口
        if media_enabled and msg.resources:
            delay = self.media_batch.delay_s
        self._timer = asyncio.get_running_loop().create_task(self._delayed(delay))

    async def _delayed(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self._timer = None
        if self._buffer:
            asyncio.ensure_future(self._flush())

    async def _flush(self) -> None:
        async with self._serial:
            if not self._buffer:
                return
            batch = list(self._buffer)
            handler = self._pending
            self._buffer.clear()
            self._buffer_chars = 0
            self._pending = None
            if handler is None:
                return
            await handler(BatchedMessage(message=_merge_messages(batch), source_ids=[m.msg_id for m in batch]))

    async def flush_now(self) -> None:
        self._clear_timer()
        await self._flush()

    def _clear_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    async def dispose(self) -> None:
        self._clear_timer()
        await self._flush()


class ChatQueueManager:
    """scope → ChatQueue 的惰性注册表。"""

    def __init__(
        self,
        batch_cfg: Optional[BatchConfig] = None,
        queue_cfg: Optional[ChatQueueConfig] = None,
        media_batch: Optional[MediaBatchConfig] = None,
    ) -> None:
        from .batching import BatchConfig as _BC

        self.batch_cfg = batch_cfg or _BC()
        self.queue_cfg = queue_cfg or ChatQueueConfig()
        self.media_batch = media_batch
        self._queues: Dict[str, ChatQueue] = {}

    def _get(self, scope: str) -> ChatQueue:
        q = self._queues.get(scope)
        if q is None:
            q = ChatQueue(scope, self.batch_cfg, self.media_batch)
            self._queues[scope] = q
        return q

    async def run(self, scope: str, task: SerialTask) -> Any:
        return await self._get(scope).run(task)

    def push(self, scope: str, msg: IncomingMessage, handler: BatchFlushHandler) -> None:
        self._get(scope).push(msg, handler)

    async def flush_all(self) -> None:
        for q in list(self._queues.values()):
            await q.flush_now()

    async def dispose(self) -> None:
        for q in list(self._queues.values()):
            await q.dispose()
        self._queues.clear()
