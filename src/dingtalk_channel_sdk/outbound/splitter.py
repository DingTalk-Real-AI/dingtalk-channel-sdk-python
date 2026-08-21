"""长文本切分。"""

from __future__ import annotations

import re
from typing import List, Optional

_FENCE_RE = re.compile(r"^```(\w*)$")
_HEADING_RE = re.compile(r"^#{1,6}\s")


def split_with_code_fences(text: str, limit: int) -> List[str]:
    """按 limit 切分文本，保持代码围栏完整。

    - 文本短于 limit 时直接返回 [text]
    - 按行切分，追踪 ``` 围栏状态
    - 缓冲超 limit 时 flush：如围栏未闭合则追加闭合 ```，下一段重新开启
    - 缓冲 >75% 满时优先在标题行（^#{1,6}\\s）前断行
    """
    if len(text) <= limit:
        return [text]

    lines = text.split("\n")
    chunks: List[str] = []
    buffer: List[str] = []
    buf_len = 0
    fence_lang: Optional[str] = None  # None = 围栏外；str = 围栏内

    def _flush() -> None:
        nonlocal buffer, buf_len
        if not buffer:
            return
        chunk = "\n".join(buffer)
        if fence_lang is not None:
            chunk += "\n```"
        chunks.append(chunk)
        buffer = []
        buf_len = 0

    def _reopen() -> None:
        nonlocal buf_len
        if fence_lang is not None:
            line = f"```{fence_lang}"
            buffer.append(line)
            buf_len = len(line)

    for line in lines:
        fence_match = _FENCE_RE.match(line)
        line_len = len(line) + (1 if buffer else 0)

        # 缓冲 >75% 且遇到标题行（仅在围栏外）→ 优先断行
        if (
            fence_lang is None
            and _HEADING_RE.match(line)
            and buffer
            and buf_len > limit * 0.75
        ):
            _flush()
            _reopen()

        # 硬限制：加入该行将超限 → flush
        if buffer and buf_len + line_len > limit:
            _flush()
            _reopen()

        # 更新围栏状态
        if fence_match:
            if fence_lang is None:
                fence_lang = fence_match.group(1) or ""
            else:
                fence_lang = None

        buffer.append(line)
        buf_len += line_len

    _flush()
    return chunks
