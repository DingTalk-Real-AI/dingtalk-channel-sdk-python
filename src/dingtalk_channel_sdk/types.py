"""安全管线配置与共享类型再导出。

SafetyConfig 聚合去重/策略/媒体批处理等安全层配置（见 safety.pipeline）；
其余符号从各自定义模块再导出，便于 `from dingtalk_channel_sdk.types import ...` 单点引用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import List, Optional

from .bot_identity import BotIdentity
from .config import MediaBatchConfig
from .normalize.message import IncomingMessage
from .safety.batching import BatchConfig, BatchedMessage
from .safety.policy import (
    GroupOverride,
    PolicyConfig,
    PolicyDecision,
    RejectEvent,
    RejectReason,
)

__all__ = [
    "DedupConfig",
    "SafetyConfig",
    "default_safety_config",
    "IncomingMessage",
    "BotIdentity",
    "MediaBatchConfig",
    "BatchConfig",
    "BatchedMessage",
    "PolicyConfig",
    "GroupOverride",
    "PolicyDecision",
    "RejectEvent",
    "RejectReason",
]


@dataclass
class DedupConfig:
    """去重缓存配置。

    Attributes:
        ttl: 去重缓存 TTL（默认 12 小时）。
        max_entries: LRU 容量上限（默认 5000）。
        enable_fingerprint: 是否叠加内容指纹去重（默认开启）。
        sweep_interval: 后台清理间隔（默认 5 分钟）。
        redis_prefix: 可选 Redis 后端的 key 前缀。
    """

    ttl: timedelta = field(default_factory=lambda: timedelta(hours=12))
    max_entries: int = 5000
    enable_fingerprint: bool = True
    sweep_interval: timedelta = field(default_factory=lambda: timedelta(minutes=5))
    redis_prefix: str = "dd:seen:"


@dataclass
class SafetyConfig:
    """SafetyPipeline 统一安全配置。

    Attributes:
        dedup: 去重配置。
        policy: 策略门控配置。
        media_batch: 媒体批处理配置（无队列时的兜底路径）。
        stale_window: 过期消息窗口。
        lock_ttl_s: 处理锁 TTL（秒）。
        drop_self_sent: 是否丢弃机器人自己发出的消息。
    """

    dedup: DedupConfig = field(default_factory=DedupConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    media_batch: MediaBatchConfig = field(default_factory=MediaBatchConfig)
    stale_window: timedelta = field(default_factory=lambda: timedelta(minutes=30))
    lock_ttl_s: float = 300.0
    drop_self_sent: bool = True
    # 去重标记时机（默认 False = 入口即标记）。True 时"处理成功后标记"：
    # 入口只查不写，handler 成功返回才写入 seen —— 失败消息可重投重试；
    # 代价是同消息并发重投会撞处理锁（LOCK_CONTENTION）而非直接判重。
    mark_after_handler: bool = False


def default_safety_config() -> SafetyConfig:
    """返回默认安全配置。"""
    return SafetyConfig()
