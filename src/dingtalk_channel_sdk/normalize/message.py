"""事件归一化（SPEC §3 / E5）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .converters.card import convert_action_card, convert_interactive_card
from .converters.media import convert_audio, convert_file, convert_picture, convert_video
from .converters.reply import convert_reply
from .converters.richtext import convert_rich_text
from .converters.text import convert_markdown, convert_text

CONVERSATION_DM = "dm"
CONVERSATION_GROUP = "group"


class IncomingMessage:
    def __init__(self, **kwargs: Any):
        self.conversation_id: str = kwargs.get("conversation_id", "")
        self.conversation_type: str = kwargs.get("conversation_type", CONVERSATION_GROUP)
        self.conversation_title: str = kwargs.get("conversation_title", "")
        self.sender_id: str = kwargs.get("sender_id", "")
        self.sender_staff_id: str = kwargs.get("sender_staff_id", "")
        self.sender_nick: str = kwargs.get("sender_nick", "")
        self.sender_corp_id: str = kwargs.get("sender_corp_id", "")
        self.text: str = kwargs.get("text", "")
        self.msg_type: str = kwargs.get("msg_type", "text")
        self.content: Optional[Any] = kwargs.get("content")
        self.at_users: List[Dict[str, str]] = kwargs.get("at_users") or []
        self.resources: List[Dict[str, Any]] = kwargs.get("resources") or []
        self.mentions: List[Dict[str, Any]] = kwargs.get("mentions") or []
        self.mention_all: bool = kwargs.get("mention_all", False)
        self.session_webhook: str = kwargs.get("session_webhook", "")
        self.webhook_expired_at: int = kwargs.get("webhook_expired_at", 0)
        self.msg_id: str = kwargs.get("msg_id", "")
        self.create_at: int = kwargs.get("create_at", 0)
        self.is_admin: bool = kwargs.get("is_admin", False)
        self.is_in_at_list: bool = kwargs.get("is_in_at_list", False)
        self.raw: Dict[str, Any] = kwargs.get("raw") or {}

    def __repr__(self) -> str:  # 调试友好；不泄露 session_webhook
        return f"IncomingMessage(msg_id={self.msg_id!r}, text={self.text!r}, type={self.conversation_type!r})"


class CardAction:
    def __init__(self, out_track_id: str, user_id: str, data_content: Any, raw: Dict[str, Any]):
        self.out_track_id = out_track_id
        self.user_id = user_id
        self.data_content = data_content
        self.raw = raw


def parse_content(
    msg_type: str, content: dict, at_users: list
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按消息类型分发到对应 converter，提取文本/资源/提及。

    钉钉机器人回调支持: text / richText / picture / file / audio / video /
    markdown / actionCard / interactiveCard / reply。
    converter 实现见 normalize/converters/（按类型一文件）。
    """
    text = ""
    resources: List[Dict[str, Any]] = []
    mentions: List[Dict[str, Any]] = []

    if msg_type == "text":
        text = convert_text(content)
    elif msg_type == "richText":
        text, mentions, resources = convert_rich_text(content)
    elif msg_type == "picture":
        text, resources = convert_picture(content)
    elif msg_type == "file":
        text, resources = convert_file(content)
    elif msg_type == "audio":
        text, resources = convert_audio(content)
    elif msg_type == "video":
        text, resources = convert_video(content)
    elif msg_type == "markdown":
        text = convert_markdown(content)
    elif msg_type == "actionCard":
        text = convert_action_card(content)
    elif msg_type == "interactiveCard":
        text = convert_interactive_card(content)
    elif msg_type == "reply":
        text = convert_reply(content)
    else:
        text = (content or {}).get("content", "")

    return text, resources, mentions


def normalize_incoming(data: Any) -> IncomingMessage:
    d = json.loads(data) if isinstance(data, str) else (data or {})
    conversation_type = CONVERSATION_DM if d.get("conversationType") == "1" else CONVERSATION_GROUP
    msg_type = d.get("msgtype", "text")
    raw_content = d.get("content")
    at_users = d.get("atUsers") or []

    if isinstance(raw_content, str):
        try:
            raw_content = json.loads(raw_content)
        except (json.JSONDecodeError, ValueError):
            raw_content = {}
    if not isinstance(raw_content, dict):
        raw_content = {}

    parsed_text, resources, mentions = parse_content(msg_type, raw_content, at_users)

    if msg_type == "text":
        text = (d.get("text") or {}).get("content", "").strip()
    else:
        text = parsed_text.strip()

    if conversation_type == CONVERSATION_GROUP and text.startswith("@"):
        i = text.find(" ")
        if i >= 0:
            text = text[i + 1 :].strip()

    mention_all = False
    for u in at_users:
        mentions.append({"userId": u.get("dingtalkId", ""), "staffId": u.get("staffId", "")})
        if u.get("staffId") == "all":
            mention_all = True

    return IncomingMessage(
        conversation_id=d.get("conversationId", ""),
        conversation_type=conversation_type,
        conversation_title=d.get("conversationTitle", ""),
        sender_id=d.get("senderId", ""),
        sender_staff_id=d.get("senderStaffId", ""),
        sender_nick=d.get("senderNick", ""),
        sender_corp_id=d.get("senderCorpId", ""),
        text=text,
        msg_type=msg_type,
        content=raw_content,
        at_users=at_users,
        resources=resources,
        mentions=mentions,
        mention_all=mention_all,
        session_webhook=d.get("sessionWebhook", ""),
        webhook_expired_at=d.get("sessionWebhookExpiredTime", 0),
        msg_id=d.get("msgId", ""),
        create_at=d.get("createAt", 0),
        is_admin=bool(d.get("isAdmin")),
        is_in_at_list=bool(d.get("isInAtList")),
        raw=d,
    )
