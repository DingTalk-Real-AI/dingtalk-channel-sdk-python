"""回复消息转换器：正文 + 被引用消息摘要。"""

from __future__ import annotations

import json
from typing import Any, Dict


def _quote_summary(replied_msg: Dict[str, Any]) -> str:
    """生成被引用消息摘要；content 可能是 JSON 字符串或对象。"""
    replied_content = replied_msg.get("content")
    if isinstance(replied_content, str):
        try:
            replied_content = json.loads(replied_content)
        except (json.JSONDecodeError, ValueError):
            replied_content = {}
    if not isinstance(replied_content, dict):
        replied_content = {}
    replied_type = replied_msg.get("msgType", "text")
    if replied_type == "text":
        quoted = (replied_content.get("text") or "").strip()
    elif replied_type == "audio":
        quoted = replied_content.get("recognition") or "[语音消息]"
    elif replied_type == "file":
        quoted = f"[文件: {replied_content.get('fileName', 'unknown')}]"
    elif replied_type == "video":
        quoted = "[视频]"
    elif replied_type == "markdown":
        quoted = (replied_content.get("text") or "").strip()
    else:
        quoted = f"[{replied_type}消息]"
    return f"[引用] {quoted}" if quoted else ""


def convert_reply(content: Dict[str, Any]) -> str:
    """组装回复消息：正文 + 引用摘要（逐行拼接）。"""
    c = content or {}
    body = (c.get("text") or "").strip()
    replied_msg = c.get("repliedMsg")
    quoted = _quote_summary(replied_msg) if replied_msg else ""
    return f"{body}\n{quoted}" if quoted else (body or "[引用消息]")
