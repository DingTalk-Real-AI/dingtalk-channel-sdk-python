"""富文本（richText）转换器：拼接正文、@提及与内嵌媒体资源。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def convert_rich_text(content: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """从 richText 数组提取拼接文本、@提及（userId / 手机号）与内嵌媒体资源。

    picture/file 段提取为资源（picture 段值即下载码）。脏数据防御：段值非
    字符串或下载码为空时跳过该段，不影响其余段落；同一下载码在单条消息内
    去重。
    """
    mentions: List[Dict[str, Any]] = []
    resources: List[Dict[str, Any]] = []
    seen: set = set()
    parts: List[str] = []
    for item in (content or {}).get("richText", []):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "")
        if item_type == "text":
            text = item.get("text", "")
            if isinstance(text, str):
                parts.append(text)
        elif item_type == "at":
            for uid in item.get("atUserIds") or []:
                mentions.append({"userId": uid})
            for mob in item.get("atMobiles") or []:
                mentions.append({"userId": mob, "name": mob})
        elif item_type == "picture":
            code = item.get("picture")
            if isinstance(code, str) and code and code not in seen:
                seen.add(code)
                resources.append({"type": "image", "downloadCode": code})
        elif item_type == "file":
            code = item.get("downloadCode")
            name = item.get("fileName")
            if isinstance(code, str) and code and code not in seen:
                seen.add(code)
                resources.append(
                    {"type": "file", "downloadCode": code, "fileName": name if isinstance(name, str) else ""}
                )
    return "".join(parts), mentions, resources
