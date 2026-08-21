"""文本 / markdown 转换器。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def convert_text(content: Dict[str, Any]) -> str:
    """提取文本消息内容。"""
    return (content or {}).get("content", "")


def convert_markdown(content: Dict[str, Any]) -> str:
    """提取 markdown 正文。"""
    return (content or {}).get("text", "")
