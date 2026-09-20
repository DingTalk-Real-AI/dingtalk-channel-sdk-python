"""Channel：组装 stream/reply/dedup/policy/lifecycle（SPEC §3/§4）。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import urllib.request
from datetime import timedelta
from typing import Any, Awaitable, Callable, List, Optional

from .compat import to_thread

from .safety.batching import BatchConfig, BatchedMessage, MessageBatcher
from .bot_identity import BotIdentity, BotIdentityProvider
from .card import CardClient
from .config import TOPIC_BOT_MESSAGE, TOPIC_CARD_CALLBACK, TRANSPORT_HTTP, USER_AGENT, Config
from .safety.dedup import Deduper
from .emotion import Emotion
from .errors import ChannelError, ErrorCode, classify_error
from .lifecycle import LifecycleHooks
from .media import OapiClient
from .normalize.message import CardAction, IncomingMessage, normalize_incoming
from .safety.policy import PolicyConfig, PolicyGate, RejectEvent, RejectReason
from .safety.processing_lock import ProcessingLock
from .reply import Reply
from .send import ProactiveSender, SendTarget
from .safety.ssrf_guard import assert_public_url
from .stream import StreamConn
from .token import TokenProvider
from .http_mode import verify_http_sign
from .safety.chat_queue import ChatQueueManager
from .safety.pipeline import PipelineOptions, SafetyPipeline
from .types import SafetyConfig
from .config import ChatQueueConfig, MediaBatchConfig

MessageHandler = Callable[[IncomingMessage, Reply], Awaitable[None]]
CardActionHandler = Callable[[CardAction, Reply], Awaitable[None]]
BatchMessageHandler = Callable[[BatchedMessage, Reply], Awaitable[None]]
RejectHandler = Callable[[RejectEvent], Awaitable[None]]


class _SSRFSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowlist: Optional[List[str]] = None):
        super().__init__()
        self.allowlist = allowlist

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_public_url(newurl, allowlist=self.allowlist)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class DingTalkChannel:
    """用法：

    ch = DingTalkChannel(client_id="ding...", client_secret="...")

    @ch.on_message
    async def handle(msg, reply):
        s = await reply.stream()
        await s.append("hello")
        await s.finish()

    await ch.start()
    """

    def __init__(self, client_id: str = "", client_secret: str = "", *, config: Optional[Config] = None, **overrides: Any):
        if config is not None:
            self.cfg = config
        else:
            self.cfg = Config(client_id=client_id, client_secret=client_secret, **overrides)
        if not self.cfg.client_id or not self.cfg.client_secret:
            raise ValueError("client_id and client_secret are required")
        self.tokens = TokenProvider(self.cfg)
        self.cards = CardClient(self.cfg, self.tokens)
        self.oapi = OapiClient(self.cfg)
        self.sender = ProactiveSender(self.cfg, self.cards)
        self.emotion = Emotion(self.cfg, self.cards)
        self.dedup = Deduper()

        # 新增组件
        self.lifecycle = LifecycleHooks()
        self.bot_identity = BotIdentityProvider(self.cfg, self.tokens)
        self.policy = PolicyGate(self.cfg.policy_config or PolicyConfig())
        self.processing_lock = ProcessingLock()
        # per-chat 串行队列（默认启用；批处理刷新与消息处理共享同会话串行）
        self.chat_queue = ChatQueueManager(
            queue_cfg=self.cfg.chat_queue or ChatQueueConfig(),
            media_batch=self.cfg.media_batch,
        )

        self._message_handler: Optional[MessageHandler] = None
        self._card_action_handler: Optional[CardActionHandler] = None
        self._batch_handler: Optional[BatchMessageHandler] = None
        self._reject_handler: Optional[RejectHandler] = None
        self._batcher: Optional[MessageBatcher] = None
        self._conv_locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

        # 安全管线：过期/去重/自回复/策略/锁/队列全在这里；
        # OnMessage/OnBatch 闭包动态读取注册状态，构造后注册依然生效
        self.pipeline = SafetyPipeline(
            SafetyConfig(
                policy=self.cfg.policy_config or PolicyConfig(),
                media_batch=self.cfg.media_batch or MediaBatchConfig(),
                stale_window=timedelta(seconds=self.cfg.stale_message_window_s or 1800),
                drop_self_sent=True,
            ),
            PipelineOptions(
                on_message=self._pipeline_on_message,
                on_batch=self._pipeline_on_batch,
                on_reject=self._pipeline_on_reject,
                chat_queue=self.chat_queue,
                has_on_batch=lambda: self._batch_handler is not None,
            ),
        )
        self.conn = StreamConn(self.cfg, self._dispatch, self.lifecycle)

    async def _proactive_reply(self, msg: IncomingMessage, msg_key: str, msg_param: dict) -> None:
        """webhook 失效时的主动发送兜底：群聊群发、单聊发给发送者。"""
        from .send import SendTarget

        if msg.conversation_type == "group":
            target = SendTarget(conversation_id=msg.conversation_id)
        else:
            target = SendTarget(user_id=msg.sender_id or msg.sender_staff_id)
        await self.sender.send(target, msg_key, msg_param)

    async def _pipeline_on_message(self, msg: IncomingMessage, sources: list) -> None:
        """管线 OnMessage 回调：构造 Reply 并执行业务处理器（异常分类记录）。"""
        reply = Reply(msg, self.cfg, self.tokens, self.cards, self.oapi, self._proactive_reply)
        if self._message_handler is None:
            return
        try:
            await self._message_handler(msg, reply)  # type: ignore[misc]
        except Exception as err:  # noqa: BLE001
            self.cfg.log(f"message handler error: {classify_error(err)}")

    async def _pipeline_on_batch(self, batch: BatchedMessage) -> None:
        """管线 OnBatch 回调：复用既有批处理投递路径。"""
        await self._invoke_batch_handler(batch)

    async def _pipeline_on_reject(self, event: RejectEvent) -> None:
        """管线拒绝回调：转发给业务注册的 reject handler。"""
        if self._reject_handler is None:
            return
        try:
            await self._reject_handler(event)
        except Exception as err:  # noqa: BLE001
            self.cfg.log(f"reject handler error: {err}")

    def on_message(self, handler: MessageHandler) -> MessageHandler:
        """装饰器或直接调用注册。"""
        self._message_handler = handler
        return handler

    def on_card_action(self, handler: CardActionHandler) -> CardActionHandler:
        self._card_action_handler = handler
        self.conn.want_card_topic = True
        return handler

    def on_batch_message(self, handler: BatchMessageHandler, batch_config: Optional[BatchConfig] = None) -> BatchMessageHandler:
        """注册批处理消息处理器。"""
        self._batch_handler = handler
        cfg = batch_config or BatchConfig()
        self._batcher = MessageBatcher(cfg, lambda batched: self._invoke_batch_handler(batched))
        # ChatQueue 批处理窗口参数与用户配置保持一致
        self.chat_queue.batch_cfg = cfg
        return handler

    def on_reject(self, handler: RejectHandler) -> RejectHandler:
        """注册策略拒绝回调。"""
        self._reject_handler = handler
        return handler

    async def _invoke_batch_handler(self, batched: BatchedMessage) -> None:
        if self._batch_handler is None:
            return
        fake_msg = IncomingMessage(conversation_id=batched.message.conversation_id, session_webhook="")
        reply = Reply(fake_msg, self.cfg, self.tokens, self.cards, self.oapi, self._proactive_reply)
        await self._batch_handler(batched, reply)

    async def start(self) -> None:
        """阻塞运行（自动重连，E8）。"""
        if self._message_handler is None and self._batch_handler is None:
            raise RuntimeError("on_message or on_batch_message handler must be registered before start()")
        if self.cfg.transport == TRANSPORT_HTTP:
            raise RuntimeError(
                "http mode has no long-running connection; "
                "call await ch.handle_http_callback(body, timestamp, sign) per HTTP request instead of start()"
            )
        await self.processing_lock.start_sweeper()
        await self.dedup.start_sweeper()
        await self.pipeline.start_sweepers()
        try:
            await self.conn.run()
        finally:
            await self.processing_lock.dispose()
            await self.dedup.dispose()
            await self.pipeline.dispose()
            if self._batcher:
                await self._batcher.dispose()
            await self.chat_queue.dispose()

    def close(self) -> None:
        self.conn.close()

    # 主动发消息（target: SendTarget）
    async def send_text(self, target: SendTarget, content: str) -> None:
        await self.sender.send_text(target, content)

    async def send_markdown(self, target: SendTarget, title: str, text: str) -> None:
        await self.sender.send_markdown(target, title, text)

    async def send_image(self, target: SendTarget, image_url: str) -> None:
        await self.sender.send_image(target, image_url)

    async def send_video(self, target: SendTarget, raw_video_media_id: str, raw_pic_media_id: str = "", duration_ms: int = 60000) -> None:
        await self.sender.send_video(target, raw_video_media_id, raw_pic_media_id, duration_ms)

    async def send_audio(self, target: SendTarget, raw_media_id: str, duration_ms: int = 60000) -> None:
        await self.sender.send_audio(target, raw_media_id, duration_ms)

    async def download_file(self, url: str, timeout: float = 60.0) -> bytes:
        """下载文件（SSRF 防护 DownloadFile，支持 cfg.ssrf_allowlist 白名单豁免）。"""
        assert_public_url(url, allowlist=self.cfg.ssrf_allowlist)

        def _fetch() -> bytes:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            opener = urllib.request.build_opener(_SSRFSafeRedirectHandler(allowlist=self.cfg.ssrf_allowlist))
            with opener.open(req, timeout=timeout) as resp:
                data = resp.read()
                cl = resp.headers.get("Content-Length")
                if cl is not None:
                    try:
                        expected = int(cl)
                        if len(data) != expected:
                            raise OSError(f"download truncated: expected {expected} bytes, got {len(data)}")
                    except ValueError:
                        pass
                return data

        return await to_thread(_fetch)

    async def download_file_to_file(self, url: str, dest_path: str, timeout: float = 60.0) -> int:
        """流式下载文件到本地路径，不整块载入内存。

        SSRF 防护同 download_file；父目录必须已存在；先写同目录临时文件再
        原子重命名，失败不落半截文件。返回写入的字节数。
        """
        assert_public_url(url, allowlist=self.cfg.ssrf_allowlist)

        def _fetch(cancel_event: threading.Event) -> int:
            dest = os.path.abspath(dest_path)
            parent = os.path.dirname(dest)
            if not os.path.isdir(parent):
                raise FileNotFoundError(f"parent directory does not exist: {parent}")
            n = 0
            fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(dest) + ".tmp-", dir=parent)
            tmp_open = True
            try:
                with os.fdopen(fd, "wb") as out:
                    tmp_open = False
                    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    opener = urllib.request.build_opener(_SSRFSafeRedirectHandler(allowlist=self.cfg.ssrf_allowlist))
                    with opener.open(req, timeout=timeout) as resp:
                        cl = resp.headers.get("Content-Length")
                        expected: Optional[int] = None
                        if cl is not None:
                            try:
                                expected = int(cl)
                            except ValueError:
                                expected = None
                        while True:
                            if cancel_event.is_set():
                                raise RuntimeError("download cancelled")
                            chunk = resp.read(64 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                            n += len(chunk)
                        if expected is not None and n != expected:
                            raise OSError(f"download truncated: expected {expected} bytes, got {n}")
                if cancel_event.is_set():
                    raise RuntimeError("download cancelled")
                os.replace(tmp, dest)
                tmp = None
                return n
            finally:
                if tmp_open:
                    os.close(fd)
                if tmp is not None:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

        cancel_ev = threading.Event()
        try:
            return await to_thread(_fetch, cancel_ev)
        except asyncio.CancelledError:
            cancel_ev.set()
            raise

    async def mark_thinking(self, conversation_id: str, msg_id: str) -> None:
        """在用户消息上打"🤔Thinking"状态章（仅人发的消息）。"""
        await self.emotion.mark_thinking(conversation_id, msg_id)

    async def mark_done(self, conversation_id: str, msg_id: str) -> None:
        """把"🤔Thinking"换成"🥳Done"（best-effort）。"""
        await self.emotion.mark_done(conversation_id, msg_id)

    async def get_bot_identity(self) -> Optional[BotIdentity]:
        """获取机器人身份信息（带缓存）。"""
        return await self.bot_identity.get()

    async def update_policy(self, cfg: PolicyConfig) -> None:
        """更新策略配置。"""
        await self.policy.update_config(cfg)
        await self.pipeline.update_policy(cfg)

    async def _dispatch(self, frame: dict) -> str:
        topic = (frame.get("headers") or {}).get("topic", "")
        if topic == TOPIC_BOT_MESSAGE:
            await self._handle_bot_message(frame)
        elif topic == TOPIC_CARD_CALLBACK:
            await self._handle_card_action(frame)
        else:
            self.cfg.log(f"unsubscribed topic {topic!r} ignored")
        return ""

    async def _handle_bot_message(self, frame: dict) -> None:
        try:
            msg = normalize_incoming(frame.get("data", ""))
        except Exception as err:  # noqa: BLE001
            self.cfg.log(f"bad bot message payload: {err}")
            return
        protocol_message_id = (frame.get("headers") or {}).get("messageId", "")
        await self._process_incoming(protocol_message_id, msg)

    async def handle_http_callback(self, body: bytes | str, timestamp: str, sign: str) -> None:
        """处理一帧 HTTP 模式回调（企业内部机器人）。

        验签失败/载荷非法抛 ChannelError（调用方回 401/400）；
        业务处理与 Stream 模式一致（吞错并快速返回）。
        """
        if self._message_handler is None and self._batch_handler is None:
            raise RuntimeError("on_message or on_batch_message handler must be registered first")
        verify_http_sign(
            self.cfg.client_secret,
            timestamp,
            sign,
            tolerance_s=self.cfg.http_timestamp_tolerance_s,
        )
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        try:
            msg = normalize_incoming(body)
        except Exception as err:  # noqa: BLE001
            raise ChannelError(ErrorCode.FORMAT_ERROR, f"http mode: bad bot message payload: {err}") from err
        # HTTP 模式回调无协议层投递 ID，两层去重均落 msgId。
        await self._process_incoming(msg.msg_id, msg)

    async def _process_incoming(self, protocol_message_id: str, msg: IncomingMessage) -> None:
        """传输无关的消息入口，委托给 SafetyPipeline：
        过期 → 去重 → 自回复 → 策略 → 锁 → 队列（串行/批处理）。Stream 与 HTTP 模式共用。
        """
        await self.pipeline.push_message(protocol_message_id, msg)

    async def _handle_card_action(self, frame: dict) -> None:
        if self._card_action_handler is None:
            return
        try:
            d = json.loads(frame.get("data", "") or "{}")
        except Exception:  # noqa: BLE001
            return
        action = CardAction(
            out_track_id=d.get("outTrackId", ""),
            user_id=d.get("userId", ""),
            data_content=d.get("dataContent"),
            raw=d,
        )
        from .normalize.message import IncomingMessage

        fake_msg = IncomingMessage(conversation_id=action.out_track_id, session_webhook="")

        async def _invoke() -> None:
            try:
                await self._card_action_handler(
                    action, Reply(fake_msg, self.cfg, self.tokens, self.cards, self.oapi, self._proactive_reply)
                )
            except Exception as err:  # noqa: BLE001
                classified = classify_error(err)
                self.cfg.log(f"card action handler error: {classified}")

        # 卡片回调走 PushAction：去重（投递 ID + 动作内容指纹，防换 ID 重放）
        # + 处理锁 + 同卡片串行
        from .safety.seen_cache import content_fingerprint

        action_fp = content_fingerprint(
            f"card:{action.out_track_id}", 0, action.user_id, json.dumps(action.data_content, sort_keys=True)
        )
        protocol_message_id = (frame.get("headers") or {}).get("messageId", "") or action.out_track_id
        await self.pipeline.push_action(protocol_message_id, f"card:{action.out_track_id}", _invoke, action_fp)

    async def dispatch_for_test(self, frame: dict) -> str:
        """测试/高级用法：直接注入一帧业务数据。"""
        return await self._dispatch(frame)
