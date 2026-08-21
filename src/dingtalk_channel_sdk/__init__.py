"""dingtalk-channel-sdk-python 公共入口。"""

from .safety.batching import BatchConfig, BatchedMessage, MessageBatcher
from .bot_identity import BotIdentity, BotIdentityProvider
from .card import ApiError as _ApiErrorAlias  # noqa: F401（re-export 由 httpx 提供）
from .channel import DingTalkChannel
from .config import (
    ChatQueueConfig,
    Config,
    MediaBatchConfig,
    OutboundConfig,
    OutboundHooks,
    RetryConfig,
)
from .errors import ChannelError, ErrorCode, classify_error, is_format_error, is_reply_target_gone, is_retryable
from .httpx import ApiError
from .lifecycle import LifecycleHooks
from .outbound.markdown import ensure_table_blank_lines, fix_newlines, normalize_for_card
from .media import OapiClient
from .normalize.message import CONVERSATION_DM, CONVERSATION_GROUP, CardAction, IncomingMessage, normalize_incoming, parse_content
from .safety.policy import GroupOverride, PolicyConfig, PolicyDecision, PolicyGate, RejectEvent, RejectReason
from .safety.processing_lock import ProcessingLock
from .reply import Reply
from .outbound.retry import RetryOptions, retry
from .send import ProactiveSender, SendTarget
from .emotion import EMOTION_DONE, EMOTION_THINKING, Emotion
from .outbound.splitter import split_with_code_fences
from .safety.ssrf_guard import assert_public_url

__all__ = [
    "DingTalkChannel",
    "Config",
    "Reply",
    "OapiClient",
    "ProactiveSender",
    "SendTarget",
    "Emotion",
    "EMOTION_THINKING",
    "EMOTION_DONE",
    "ApiError",
    "IncomingMessage",
    "CardAction",
    "CONVERSATION_DM",
    "CONVERSATION_GROUP",
    "normalize_incoming",
    "parse_content",
    "normalize_for_card",
    "fix_newlines",
    "ensure_table_blank_lines",

    "BotIdentity",
    "BotIdentityProvider",
    "LifecycleHooks",
    "PolicyConfig",
    "PolicyDecision",
    "PolicyGate",
    "RejectEvent",
    "RejectReason",
    "ProcessingLock",
    "ChannelError",
    "ErrorCode",
    "classify_error",
    "is_retryable",
    "is_reply_target_gone",
    "is_format_error",
    "BatchConfig",
    "BatchedMessage",
    "MessageBatcher",

    "assert_public_url",
    "retry",
    "RetryOptions",
    "split_with_code_fences",
    "ChatQueueConfig",
    "MediaBatchConfig",
    "OutboundConfig",
    "OutboundHooks",
    "RetryConfig",
    "__version__",
]

__version__ = "0.1.0"
