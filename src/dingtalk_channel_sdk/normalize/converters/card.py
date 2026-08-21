"""卡片转换器：actionCard / interactiveCard。"""

from __future__ import annotations

from typing import Any, Dict


def convert_action_card(content: Dict[str, Any]) -> str:
    """组装 actionCard 的标题/正文/操作链接为可读文本。"""
    c = content or {}
    title = (c.get("title") or "").strip()
    body = (c.get("text") or "").strip()
    action_urls = [
        (item.get("actionUrl") or "").strip()
        for item in (c.get("actionUrlItemList") or [])
        if (item.get("actionUrl") or "").strip()
    ]
    sections = []
    if title:
        sections.append(title)
    if body:
        sections.append(body)
    if action_urls:
        if len(action_urls) == 1:
            sections.append(f"操作链接：{action_urls[0]}")
        else:
            sections.append("操作链接：\n- " + "\n- ".join(action_urls))
    return "\n\n".join(sections) if sections else "[actionCard消息]"


def convert_interactive_card(content: Dict[str, Any]) -> str:
    """提取交互卡片的自定义跳转链接。"""
    url = ((content or {}).get("biz_custom_action_url") or "").strip()
    return f"收到交互式卡片链接：{url}" if url else "[interactiveCard消息]"
