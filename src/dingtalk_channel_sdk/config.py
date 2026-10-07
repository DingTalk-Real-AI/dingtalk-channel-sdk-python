"""配置与常量（SPEC §8/§10）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from .safety.policy import PolicyConfig
from .a2ui import A2UIClient

DEFAULT_API_BASE = "https://api.dingtalk.com"
DEFAULT_OAPI_BASE = "https://oapi.dingtalk.com"
DEFAULT_CARD_TEMPLATE_ID = "02fcf2f4-5e02-4a85-b672-46d1f715543e.schema"
DEFAULT_STREAM_THROTTLE_S = 0.8
DEFAULT_CARD_WATCHDOG_S = 600.0  # 孤儿卡强制收口
DEFAULT_ERROR_COOLDOWN_S = 60.0  # 错误兜底文本冷却
DEFAULT_STALE_WINDOW_S = 1800.0  # 过期消息过滤
DEFAULT_TEXT_CHUNK_LIMIT = 3500  # 超长文本分片
DEFAULT_DEDUP_TTL_S = 300.0
DEFAULT_CARD_QPS = 20.0
QPS_BACKOFF_S = 2.0
KEEPALIVE_IDLE_S = 120.0
PONG_WAIT_S = 5.0
RECONNECT_BASE_S = 1.0
RECONNECT_MAX_S = 30.0
TOPIC_BOT_MESSAGE = "/v1.0/im/bot/messages/get"
TOPIC_CARD_CALLBACK = "/v1.0/card/instances/callback"
USER_AGENT = "dingtalk-channel-sdk-python/v0.1.0"

# 传输模式。对齐钉钉官方两种接收消息模式：stream 为默认长连接；http 为 HTTP 模式（dispatcher 形态：
# 验签与分发由 SDK 负责，HTTP 服务由外部提供，见 http_mode.py）。
TRANSPORT_STREAM = "stream"
TRANSPORT_HTTP = "http"

DEFAULT_HTTP_TIMESTAMP_TOLERANCE_S = 3600.0  # 验签时间戳容忍窗口（防重放）

logger = logging.getLogger("dingtalk_channel_sdk")


@dataclass
class ChatQueueConfig:
    """per-chat 串行队列配置（默认启用）。关闭后同会话消息可能并发处理、回复乱序。"""

    enabled: bool = True


@dataclass
class MediaBatchConfig:
    """媒体消息批处理（默认关闭）。

    启用后同会话连续媒体（图片/文件/音视频）在 delay_s 窗口内合并为一次投递，
    资源列表合并进 BatchedMessage.message.resources。
    """

    enabled: bool = False
    delay_s: float = 0.8
    max_items: int = 9


@dataclass
class RetryConfig:
    """出站重试参数（指数退避）。"""

    max_attempts: int = 3
    base_delay_s: float = 0.5


@dataclass
class OutboundHooks:
    """出站钩子：审计日志/合规过滤/埋点。

    - before_send(kind, target, payload)：发送前调用；返回非 None 时替换 payload。
    - after_send(kind, target, ok, error)：发送后调用（含失败）。
    kind: "reply" | "send"；target: 会话/接收者标识。
    """

    before_send: Optional[Callable[[str, str, Any], Any]] = None
    after_send: Optional[Callable[[str, str, bool, Optional[str]], None]] = None


@dataclass
class OutboundConfig:
    """出站配置：重试参数、钩子、统一页脚。"""

    retry: RetryConfig = field(default_factory=RetryConfig)
    hooks: OutboundHooks = field(default_factory=OutboundHooks)
    #: 统一页脚：追加到每条文本/Markdown 消息末尾（如免责声明）。
    footer: str = ""


@dataclass
class Config:
    client_id: str
    client_secret: str
    api_base: str = DEFAULT_API_BASE
    oapi_base: str = DEFAULT_OAPI_BASE
    card_template_id: str = DEFAULT_CARD_TEMPLATE_ID
    stream_throttle_s: float = DEFAULT_STREAM_THROTTLE_S
    card_watchdog_s: float = DEFAULT_CARD_WATCHDOG_S
    error_cooldown_s: float = DEFAULT_ERROR_COOLDOWN_S
    stale_message_window_s: float = DEFAULT_STALE_WINDOW_S
    text_chunk_limit: int = DEFAULT_TEXT_CHUNK_LIMIT
    card_qps: float = DEFAULT_CARD_QPS
    auto_reconnect: bool = True
    keepalive_idle_s: float = KEEPALIVE_IDLE_S
    # 空闲 ping 后等待 pong 的超时（秒）；超时视为连接僵死，主动断连重连
    pong_wait_s: float = 5.0
    debug: Optional[Callable[[str], None]] = field(default=None)
    policy_config: Optional[PolicyConfig] = field(default=None)
    #: 入站传输模式：TRANSPORT_STREAM（默认）或 TRANSPORT_HTTP（HTTP 模式）。
    transport: str = TRANSPORT_STREAM
    #: HTTP 模式验签时间戳容忍窗口秒数（默认 3600，<=0 关闭窗口检查）。
    http_timestamp_tolerance_s: float = DEFAULT_HTTP_TIMESTAMP_TOLERANCE_S
    #: per-chat 串行队列（默认启用；None = 默认启用）。
    chat_queue: Optional[ChatQueueConfig] = None
    #: 媒体批处理（默认关闭）。
    media_batch: Optional[MediaBatchConfig] = None
    #: 出站配置：重试参数、before_send/after_send 钩子、统一页脚。
    outbound: Optional[OutboundConfig] = None
    #: SSRF 白名单：命中的主机名跳过公网校验（支持通配符 *.example.com）。
    ssrf_allowlist: List[str] = field(default_factory=list)
    #: 显式配置的 A2UI 发送通道；例如 DwsA2UIClient。
    a2ui_client: Optional[A2UIClient] = None

    def __post_init__(self) -> None:
        if self.transport not in (TRANSPORT_STREAM, TRANSPORT_HTTP):
            raise ValueError(
                f"Config.transport: unknown transport {self.transport!r} (supported: stream, http)"
            )

    def log(self, msg: str) -> None:
        if self.debug:
            self.debug(msg)
        else:
            logger.debug(msg)
