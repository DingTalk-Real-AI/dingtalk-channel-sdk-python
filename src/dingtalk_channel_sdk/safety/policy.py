"""策略门控。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from typing import TYPE_CHECKING

from ..identity import sender_matches
from ..normalize.message import CONVERSATION_GROUP, IncomingMessage

if TYPE_CHECKING:
    from ..bot_identity import BotIdentity


class RejectReason(str, Enum):
    """拒绝原因。"""

    GROUP_NOT_ALLOWED = "group_not_allowed"
    GROUP_BLOCKED = "group_blocked"
    GROUP_DISABLED = "group_disabled"
    NO_MENTION = "no_mention"
    MENTION_ALL = "mention_all_blocked"
    DM_DISABLED = "dm_disabled"
    DM_NOT_ALLOWED = "dm_not_allowed"
    DM_BLOCKED = "dm_blocked"
    SENDER_NOT_ALLOWED = "sender_not_allowed"
    SENDER_BLOCKED = "sender_blocked"
    SENDER_DENIED = "sender_denied"
    MENTION_ALL_BLOCKED = "mention_all_blocked"
    # 管线级拒绝原因（SafetyPipeline 使用）
    STALE = "stale"
    DUPLICATE = "duplicate"
    SELF_SENT = "self_sent"
    LOCK_CONTENTION = "lock_contention"


@dataclass
class PolicyDecision:
    """策略评估结果。"""

    allowed: bool
    reason: Optional[RejectReason] = None


@dataclass
class RejectEvent:
    """消息被策略拒绝时触发的事件。"""

    message_id: str
    chat_id: str
    sender_id: str
    reason: RejectReason


@dataclass
class PolicyConfig:
    """控制消息准入策略。"""

    # 群聊白名单（空 = 允许所有群）
    group_allowlist: List[str] = field(default_factory=list)
    # 群聊黑名单
    group_blocklist: List[str] = field(default_factory=list)
    # 群聊是否需要 @机器人（默认 True）
    require_mention: bool = True
    # 是否响应 @所有人（默认 False）
    respond_to_mention_all: bool = False
    # 单聊模式："open" | "disabled" | "allowlist" | "blocklist"
    dm_mode: str = "open"
    # 单聊白名单（dm_mode="allowlist" 时生效）
    dm_allowlist: List[str] = field(default_factory=list)
    # 单聊黑名单（dm_mode="blocklist" 时生效）
    dm_blocklist: List[str] = field(default_factory=list)
    # 按群（conversation_id）覆盖策略；显式条目可在白名单模式下放行该群，黑名单永不例外
    group_overrides: Dict[str, "GroupOverride"] = field(default_factory=dict)
    # 全局发送者白名单（设置后仅名单内 sender 可通过）
    allow_from: List[str] = field(default_factory=list)
    # 全局发送者黑名单（优先于 allow_from）
    deny_from: List[str] = field(default_factory=list)
    # 管理员列表：绕过所有策略限制
    admins: List[str] = field(default_factory=list)
    # 参与全局名单匹配的身份字段；默认仅 sender_id。
    # 例：("sender_staff_id", "sender_id") —— 名单里存 staffId 或 dingtalkId 均可命中。
    sender_identity_fields: Optional[List[str]] = None


@dataclass
class GroupOverride:
    """单群策略覆盖。零值字段沿用全局配置。"""

    # 显式禁用该群（False = 拒绝该群所有消息）
    enabled: Optional[bool] = None
    # 覆盖该群的 @机器人 要求
    require_mention: Optional[bool] = None
    # 该群内发送者白名单（设置后仅名单内 sender 可通过）
    allow_from: List[str] = field(default_factory=list)
    # 该群内发送者黑名单（先于 allow_from 检查，命中即拒绝）
    block_from: List[str] = field(default_factory=list)
    # 覆盖该群的 @所有人 响应
    respond_to_mention_all: Optional[bool] = None


class PolicyGate:
    """消息策略门控。"""

    def __init__(self, cfg: PolicyConfig):
        self._cfg = cfg
        self._lock = asyncio.Lock()
        self._bot: Optional["BotIdentity"] = None

    def set_bot_identity(self, bot: "BotIdentity") -> None:
        """记录机器人身份（供上层自回复过滤等使用）。"""
        self._bot = bot

    def get_bot_identity(self) -> Optional["BotIdentity"]:
        """获取已记录的机器人身份。"""
        return self._bot

    async def evaluate(self, msg: IncomingMessage) -> PolicyDecision:
        """评估消息是否允许通过。"""
        fields = self._cfg.sender_identity_fields

        # 管理员绕过所有策略（最高优先级）
        if sender_matches(msg, self._cfg.admins, fields):
            return PolicyDecision(allowed=True)

        # 全局发送者黑名单
        if sender_matches(msg, self._cfg.deny_from, fields):
            return PolicyDecision(allowed=False, reason=RejectReason.SENDER_DENIED)

        # 全局发送者白名单（设置后必须在名单内）
        if self._cfg.allow_from and not sender_matches(msg, self._cfg.allow_from, fields):
            return PolicyDecision(allowed=False, reason=RejectReason.SENDER_NOT_ALLOWED)

        if msg.conversation_type == CONVERSATION_GROUP:
            return self._evaluate_group(msg)
        return self._evaluate_dm(msg)

    def _evaluate_group(self, msg: IncomingMessage) -> PolicyDecision:
        """评估群聊消息。"""
        # 黑名单检查（最高优先级，群覆盖不可豁免）
        if msg.conversation_id in self._cfg.group_blocklist:
            return PolicyDecision(allowed=False, reason=RejectReason.GROUP_BLOCKED)

        # 群覆盖（显式条目可在白名单模式下放行该群）
        ov = self._cfg.group_overrides.get(msg.conversation_id)

        # 白名单检查：全局白名单命中，或存在显式群条目
        if self._cfg.group_allowlist:
            if msg.conversation_id not in self._cfg.group_allowlist and ov is None:
                return PolicyDecision(allowed=False, reason=RejectReason.GROUP_NOT_ALLOWED)

        if ov is not None and ov.enabled is False:
            return PolicyDecision(allowed=False, reason=RejectReason.GROUP_DISABLED)

        # @机器人检查（群覆盖优先）
        require_mention = self._cfg.require_mention
        if ov is not None and ov.require_mention is not None:
            require_mention = ov.require_mention
        if require_mention and not msg.is_in_at_list:
            return PolicyDecision(allowed=False, reason=RejectReason.NO_MENTION)

        # 群内发送者黑名单（先于白名单）
        if ov is not None and msg.sender_id in ov.block_from:
            return PolicyDecision(allowed=False, reason=RejectReason.SENDER_BLOCKED)

        # 群内发送者白名单
        if ov is not None and ov.allow_from:
            if msg.sender_id not in ov.allow_from:
                return PolicyDecision(allowed=False, reason=RejectReason.SENDER_NOT_ALLOWED)

        # @所有人检查（群覆盖优先，默认不响应）
        respond_to_all = self._cfg.respond_to_mention_all
        if ov is not None and ov.respond_to_mention_all is not None:
            respond_to_all = ov.respond_to_mention_all
        if msg.mention_all and not respond_to_all:
            return PolicyDecision(allowed=False, reason=RejectReason.MENTION_ALL)

        return PolicyDecision(allowed=True)

    def _evaluate_dm(self, msg: IncomingMessage) -> PolicyDecision:
        """评估单聊消息。"""
        mode = self._cfg.dm_mode or "open"

        if mode == "disabled":
            return PolicyDecision(allowed=False, reason=RejectReason.DM_DISABLED)

        if mode == "allowlist":
            if msg.sender_id not in self._cfg.dm_allowlist:
                return PolicyDecision(allowed=False, reason=RejectReason.DM_NOT_ALLOWED)

        if mode == "blocklist":
            if msg.sender_id in self._cfg.dm_blocklist:
                return PolicyDecision(allowed=False, reason=RejectReason.DM_BLOCKED)

        return PolicyDecision(allowed=True)

    async def update_config(self, cfg: PolicyConfig) -> None:
        """更新策略配置。"""
        async with self._lock:
            self._cfg = cfg

    async def get_config(self) -> PolicyConfig:
        """获取当前策略配置。"""
        async with self._lock:
            return self._cfg
