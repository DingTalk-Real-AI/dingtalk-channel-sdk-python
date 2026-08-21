"""富文本（richText）转换器：拼接正文与 @提及。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def convert_rich_text(content: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """从 richText 数组提取拼接文本与 @提及（userId / 手机号）。"""
    mentions: List[Dict[str, Any]] = []
    parts: List[str] = []
    for item in (content or {}).get("richText", []):
        item_type = item.get("type", "")
        if item_type == "text":
            parts.append(item.get("text", ""))
        elif item_type == "at":
            for uid in item.get("atUserIds") or []:
                mentions.append({"userId": uid})
            for mob in item.get("atMobiles") or []:
                mentions.append({"userId": mob, "name": mob})
    return "".join(parts), mentions
