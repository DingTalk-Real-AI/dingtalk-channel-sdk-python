"""规范化层：原始回调 → IncomingMessage，以及卡片 Markdown 规范化。"""

from .message import (
    CONVERSATION_DM,
    CONVERSATION_GROUP,
    CardAction,
    IncomingMessage,
    normalize_incoming,
    parse_content,
)

__all__ = [
    "CONVERSATION_DM",
    "CONVERSATION_GROUP",
    "CardAction",
    "IncomingMessage",
    "normalize_incoming",
    "parse_content",
    "normalize_for_card",
    "fix_newlines",
    "ensure_table_blank_lines",
]
