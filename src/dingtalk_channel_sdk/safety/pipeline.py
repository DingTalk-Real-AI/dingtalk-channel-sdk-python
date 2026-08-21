"""SafetyPipeline：安全管线统一门面。

三层推送接口：
- push_message: 完整管线（过期 → 去重 → 自回复 → 策略 → 锁 → 队列/批处理）
- push_action:  简化管线（去重 → 锁 → 串行）用于卡片回调等事件
- push_light:   最简管线（仅去重）用于轻量事件

队列集成：注入 ChatQueueManager 后，消息按会话串行（未注册 OnBatch）
或延迟合并（注册 OnBatch，媒体资源在窗口内自动合并）；
未注入时退化为直接调用，媒体消息走 MediaPipelineManager 合并。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Awaitable, Callable, List, Optional

from ..normalize.message import IncomingMessage
from ..types import DedupConfig, SafetyConfig
from .batching import BatchedMessage
from .chat_queue import ChatQueueManager
from .media_pipeline import MediaPipelineManager
from .policy import PolicyConfig, PolicyGate, RejectEvent, RejectReason
from .processing_lock import ProcessingLock
from .seen_cache import SeenCache, content_fingerprint
from .stale_detector import StaleDetector

logger = logging.getLogger(__name__)

MessageHandler = Callable[[IncomingMessage, List[IncomingMessage]], Awaitable[None]]
BatchDispatchHandler = Callable[[BatchedMessage], Awaitable[None]]
RejectHandler = Callable[[RejectEvent], Awaitable[None]]


class PipelineOptions:
    """SafetyPipeline 选项。

    OnMessage/OnBatch 均为闭包时，Channel 可在构造后动态注册处理器；
    HasOnBatch 用于在分发时动态判断 OnBatch 是否已注册（决定批处理/串行路径）。
    """

    def __init__(
        self,
        on_message: Optional[MessageHandler] = None,
        on_batch: Optional[BatchDispatchHandler] = None,
        on_reject: Optional[RejectHandler] = None,
        chat_queue: Optional[ChatQueueManager] = None,
        has_on_batch: Optional[Callable[[], bool]] = None,
        bot_robot_code: str = "",
    ) -> None:
        self.on_message = on_message
        self.on_batch = on_batch
        self.on_reject = on_reject
        self.chat_queue = chat_queue
        self.has_on_batch = has_on_batch
        self.bot_robot_code = bot_robot_code


class SafetyPipeline:
    """安全管线：整合过期/去重/策略/锁与队列分发。"""

    def __init__(self, cfg: SafetyConfig, opts: PipelineOptions) -> None:
        self.cfg = cfg
        self.stale = StaleDetector(cfg.stale_window)
        self.seen = SeenCache(cfg.dedup)
        self.lock = ProcessingLock(ttl=cfg.lock_ttl_s)
        self.policy = PolicyGate(cfg.policy)
        self.chat_queue = opts.chat_queue
        self.media = MediaPipelineManager(cfg.media_batch)
        self.drop_self_sent = cfg.drop_self_sent
        self.mark_after_handler = cfg.mark_after_handler
        self.bot_robot_code = opts.bot_robot_code
        self.on_message = opts.on_message
        self.on_batch = opts.on_batch
        self.has_on_batch = opts.has_on_batch
        self.on_reject = opts.on_reject

    # ── 分发路径决策 ──

    def _batch_mode(self) -> bool:
        """OnBatch 已提供且（动态探测）已注册时走批处理路径。"""
        if self.on_batch is None:
            return False
        if self.has_on_batch is not None:
            return self.has_on_batch()
        return True

    # ── 三层推送接口 ──

    async def push_message(self, protocol_message_id: str, msg: IncomingMessage) -> None:
        """推送消息到完整安全管线。

        顺序：过期检测 → 去重（ID + 内容指纹）→ 自回复过滤 →
        策略门控 → 处理锁 → 队列（串行/批处理）或直接/媒体分发。
        任一环节拒绝即返回并通过 OnReject 上报原因。
        """
        # 1. 过期检测
        if self.stale.is_stale(msg.create_at):
            await self._emit_reject(msg, RejectReason.STALE)
            return

        # 2. 去重：协议投递 ID + 业务 msgId；时间戳与内容齐备时叠加内容指纹，
        #    防止网关换投递 ID 重放同一条消息。create_at 缺失（0）时指纹无区分度，跳过。
        keys = [protocol_message_id, msg.msg_id]
        if self.cfg.dedup.enable_fingerprint and msg.text and msg.create_at > 0:
            keys.append(content_fingerprint(msg.conversation_id, msg.create_at, msg.msg_type, msg.text))
        # mark_after_handler 模式下入口只查不写，成功后才标记（失败可重投）
        if self.mark_after_handler:
            dup = await self.seen.has_async(*keys)
        else:
            dup = await self.seen.check_and_mark_async(*keys)
        if dup:
            await self._emit_reject(msg, RejectReason.DUPLICATE)
            return

        # 3. 自回复过滤（仅当机器人身份已知）
        if self.drop_self_sent and self.bot_robot_code and msg.sender_id == self.bot_robot_code:
            await self._emit_reject(msg, RejectReason.SELF_SENT)
            return

        # 4. 策略门控
        decision = await self.policy.evaluate(msg)
        if not decision.allowed:
            await self._emit_reject(msg, decision.reason or RejectReason.GROUP_NOT_ALLOWED)
            return

        # 5. 处理锁：防止同一消息并发处理
        lock_id = msg.msg_id or protocol_message_id
        if not await self.lock.acquire(lock_id):
            await self._emit_reject(msg, RejectReason.LOCK_CONTENTION)
            return

        # 6. 队列路径：per-chat 串行（OnMessage）或批处理（OnBatch，含媒体合并）
        if self.chat_queue is not None and self.chat_queue.queue_cfg.enabled:
            if self._batch_mode():
                self.chat_queue.push(msg.conversation_id, msg, self._batch_flush)
                return
            await self.chat_queue.run(msg.conversation_id, lambda: self._message_flush(msg, [msg]))
            return

        # 7. 无队列路径：媒体消息优先进入媒体批次
        if self.media.is_compatible(msg):
            await self.media.push(msg, self._media_flush)
            return
        # 非媒体消息到达时刷新同会话待处理媒体批次，保证消息顺序
        if self.media.cfg.enabled:
            await self.media.flush_incompatible_for(msg)

        # 8. 直接分发：注册了 OnBatch 则以单条批次投递（保持回调契约），否则逐条回调
        if self._batch_mode():
            await self._batch_flush(BatchedMessage(message=msg, source_ids=[msg.msg_id]))
            return
        await self._message_flush(msg, [msg])

    async def push_action(
        self,
        event_id: str,
        scope: str,
        handler: Callable[[], Awaitable[None]],
        *extra_dedup_keys: str,
    ) -> None:
        """推送动作事件（卡片回调等）：去重 → 锁 → 按 scope 串行执行。

        extra_dedup_keys 为附加去重键（如动作内容指纹）：任一键命中即判重，
        防止网关换投递 ID 重放同一动作。
        """
        keys = [event_id, *extra_dedup_keys]
        if self.mark_after_handler:
            dup = await self.seen.has_async(*keys)
        else:
            dup = await self.seen.check_and_mark_async(*keys)
        if dup:
            return
        if not await self.lock.acquire(event_id):
            return

        async def _runner() -> None:
            err: Optional[BaseException] = None
            try:
                await handler()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                err = exc
                logger.exception("safety: action handler raised: %s", exc)
            finally:
                await self.lock.release(event_id)
                if self.mark_after_handler and err is None:
                    await self.seen.add_async(*keys)

        if self.chat_queue is not None and self.chat_queue.queue_cfg.enabled:
            await self.chat_queue.run(scope, _runner)
            return
        await _runner()

    async def push_light(self, event_id: str, handler: Callable[[], Awaitable[None]]) -> None:
        """推送轻量事件（reaction 等）：仅去重后直接执行。"""
        if self.mark_after_handler:
            if await self.seen.has_async(event_id):
                return
        elif await self.seen.check_and_mark_async(event_id):
            return
        try:
            await handler()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("safety: light handler raised: %s", exc)
            return
        if self.mark_after_handler:
            await self.seen.add_async(event_id)

    # ── 分发回调（负责释放处理锁） ──

    async def _batch_flush(self, batch: BatchedMessage) -> None:
        """批处理分发回调：投递 OnBatch，释放全部源消息处理锁；成功才标记 seen。"""
        err: Optional[BaseException] = None
        try:
            if self.on_batch is not None:
                await self.on_batch(batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  # 业务异常不外泄，记录日志
            err = exc
            logger.exception("safety: on_batch handler raised: %s", exc)
        finally:
            for mid in batch.source_ids:
                await self.lock.release(mid)
            if self.mark_after_handler and err is None:
                for mid in batch.source_ids:
                    await self.seen.add_async(mid)

    async def _message_flush(self, merged: IncomingMessage, sources: List[IncomingMessage]) -> None:
        """消息分发回调：调用 OnMessage，释放全部源消息处理锁；成功才标记 seen。"""
        err: Optional[BaseException] = None
        try:
            if self.on_message is not None:
                await self.on_message(merged, sources)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  # 业务异常不外泄，记录日志
            err = exc
            logger.exception("safety: on_message handler raised: %s", exc)
        finally:
            for m in sources:
                await self.lock.release(m.msg_id)
            if self.mark_after_handler and err is None:
                for m in sources:
                    await self.seen.add_async(m.msg_id)

    async def _media_flush(self, merged: IncomingMessage) -> None:
        """媒体批次分发回调：调用 OnMessage，释放全部源消息处理锁；成功才标记 seen。"""
        sources = getattr(merged, "batched_sources", None) or [merged]
        await self._message_flush(merged, sources)

    async def _emit_reject(self, msg: IncomingMessage, reason: RejectReason) -> None:
        """触发拒绝事件回调；回调自身异常不外泄，避免影响主流程。"""
        if self.on_reject is None:
            return
        try:
            await self.on_reject(
                RejectEvent(message_id=msg.msg_id, chat_id=msg.conversation_id, sender_id=msg.sender_id, reason=reason)
            )
        except Exception as err:  # noqa: BLE001
            logger.warning("safety: on_reject handler raised: %s", err)

    # ── 运行时配置 ──

    def set_bot_identity(self, robot_code: str) -> None:
        """设置机器人身份（用于自回复过滤）。"""
        self.bot_robot_code = robot_code

    async def update_policy(self, cfg: PolicyConfig) -> None:
        """动态更新策略配置。"""
        await self.policy.update_config(cfg)

    async def start_sweepers(self) -> None:
        """启动后台清理（处理锁）。"""
        await self.lock.start_sweeper()

    async def dispose(self) -> None:
        """释放资源：先刷新队列待处理批次，再关闭各组件。"""
        if self.chat_queue is not None:
            await self.chat_queue.flush_all()
            await self.chat_queue.dispose()
        await self.media.dispose()
        self.seen.dispose()
        await self.lock.dispose()
