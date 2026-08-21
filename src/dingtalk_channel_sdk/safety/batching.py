"""消息批处理。"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

from ..normalize.message import IncomingMessage

BatchHandler = Callable[["BatchedMessage"], Awaitable[None]]


@dataclass
class BatchConfig:
    """配置消息批处理行为。"""

    # 批处理延迟（秒），默认 0.6s
    delay_s: float = 0.6
    # 长消息阈值（字符数）
    long_threshold_chars: int = 1000
    # 长消息延迟（秒），默认 2s
    long_delay_s: float = 2.0
    # 最大批处理消息数，默认 8
    max_messages: int = 8
    # 最大批处理字符数，默认 4000
    max_chars: int = 4000


@dataclass
class BatchedMessage:
    """批处理后的消息。"""

    # 合并后的消息
    message: IncomingMessage
    # 源消息 ID 列表
    source_ids: List[str] = field(default_factory=list)


class _ChatPipeline:
    """单个会话的批处理管道。"""

    def __init__(self, cfg: BatchConfig, scope: str, handler: BatchHandler):
        self.cfg = cfg
        self.scope = scope
        self.handler = handler
        self._buffer: List[IncomingMessage] = []
        self._buffer_chars: int = 0
        self._timer: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def push(self, msg: IncomingMessage) -> None:
        async with self._lock:
            self._buffer.append(msg)
            self._buffer_chars += len(msg.text)

            # 检查是否达到阈值
            if len(self._buffer) >= self.cfg.max_messages or self._buffer_chars >= self.cfg.max_chars:
                self._clear_timer()
                asyncio.create_task(self._flush())
                return

            # 设置延迟刷新
            self._clear_timer()
            delay = self.cfg.delay_s
            if self._buffer_chars >= self.cfg.long_threshold_chars:
                delay = self.cfg.long_delay_s

            self._timer = asyncio.create_task(self._delayed_flush(delay))

    async def _delayed_flush(self, delay: float) -> None:
        await asyncio.sleep(delay)
        async with self._lock:
            self._timer = None
            if self._buffer:
                asyncio.create_task(self._flush_locked())

    async def flush_now(self) -> None:
        async with self._lock:
            if self._buffer:
                self._clear_timer()
                asyncio.create_task(self._flush_locked())

    async def _flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return

        batch = list(self._buffer)
        self._buffer.clear()
        self._buffer_chars = 0

        # 合并消息
        merged = _merge_messages(batch)
        source_ids = [m.msg_id for m in batch]

        batched = BatchedMessage(message=merged, source_ids=source_ids)

        if self.handler is not None:
            try:
                await self.handler(batched)
            except Exception:
                pass

    def _clear_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    async def dispose(self) -> None:
        self._clear_timer()
        self._buffer.clear()


class MessageBatcher:
    """消息批处理器（按会话分组）。"""

    def __init__(self, cfg: BatchConfig, handler: BatchHandler):
        self.cfg = cfg
        self.handler = handler
        self._pipelines: Dict[str, _ChatPipeline] = {}
        self._lock = asyncio.Lock()

    async def push(self, msg: IncomingMessage) -> None:
        """添加消息到批处理队列。"""
        scope = msg.conversation_id or msg.msg_id
        pipeline = await self._get_or_create(scope)
        await pipeline.push(msg)

    async def _get_or_create(self, scope: str) -> _ChatPipeline:
        async with self._lock:
            if scope in self._pipelines:
                return self._pipelines[scope]
            p = _ChatPipeline(self.cfg, scope, self.handler)
            self._pipelines[scope] = p
            return p

    async def flush_all(self) -> None:
        """立即刷新所有批处理。"""
        async with self._lock:
            pipelines = list(self._pipelines.values())
        await asyncio.gather(*(p.flush_now() for p in pipelines))

    async def dispose(self) -> None:
        """清理所有批处理。"""
        await self.flush_all()
        async with self._lock:
            for p in self._pipelines.values():
                await p.dispose()
            self._pipelines.clear()


def _merge_messages(msgs: List[IncomingMessage]) -> IncomingMessage:
    """合并多条消息为一条（文本拼接 + 资源/提及合并去重）。"""
    if not msgs:
        raise ValueError("cannot merge empty message list")
    if len(msgs) == 1:
        return msgs[0]

    # 以最后一条为基础
    last = msgs[-1]
    merged = copy.copy(last)

    # 合并文本内容
    texts = [m.text for m in msgs if m.text]
    if texts:
        merged.text = "\n\n".join(texts)

    # 合并资源（媒体批处理：downloadCode 去重，保持到达顺序）
    resources = []
    seen_codes = set()
    for m in msgs:
        for r in m.resources or []:
            key = (r.get("downloadCode") if isinstance(r, dict) else None) or str(r)
            if key and key not in seen_codes:
                seen_codes.add(key)
                resources.append(r)
    if resources:
        merged.resources = resources

    # 合并 @提及（按用户去重）
    mentions = []
    seen_mentions = set()
    for m in msgs:
        for mention in m.mentions or []:
            key = mention.user_id or mention.name
            if key not in seen_mentions:
                seen_mentions.add(key)
                mentions.append(mention)
    if mentions:
        merged.mentions = mentions

    return merged
