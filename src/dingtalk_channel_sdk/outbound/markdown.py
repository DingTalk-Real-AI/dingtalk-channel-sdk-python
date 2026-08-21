"""钉钉 AI 卡片渲染器 Markdown 归一化（SPEC §7 / E10），移植自官方 connector。"""

from __future__ import annotations

import re

_TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|?\s*:?-+:?\s*)+\|?\s*$")
_TABLE_ROW = re.compile(r"^\s*\|?.*\|.*\|?\s*$")
_BLOCK_START = re.compile(
    r"^(\s{0,3}(?:[-*+]|\d+[.)])[ ])|(\s{0,3}\|)|(\s{0,3}#{1,6}\s)|(\s{0,3}(?:[-*_])\s*(?:[-*_])\s*(?:[-*_]))"
)
_FENCE = re.compile(r"^\s{0,3}```")
_QUOTE = re.compile(r"^\s{0,3}>\s?")


def _is_divider(line: str) -> bool:
    return bool(line) and "|" in line and bool(_TABLE_DIVIDER.match(line))


def ensure_table_blank_lines(text: str) -> str:
    """表格分隔行前若无空行则插入（否则钉钉不渲染表格）。"""
    lines = re.sub(r"\r\n?", "\n", text).split("\n")
    out: list[str] = []
    for i, cur in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if (
            i > 0
            and _TABLE_ROW.match(cur)
            and _is_divider(nxt)
            and lines[i - 1].strip() != ""
            and not _TABLE_ROW.match(lines[i - 1])
        ):
            out.append("")
        out.append(cur)
    return "\n".join(out)


def fix_newlines(text: str) -> str:
    """单 \\n → <br>，按代码块/引用/块语法行约定处理。"""
    lines = re.sub(r"\r\n?", "\n", text).split("\n")

    # 1. 合并连续引用行（代码块外），<br> 连接，续行去 > 前缀。
    merged: list[str] = []
    pending: list[str] = []
    in_code = False

    def flush() -> None:
        if pending:
            merged.append("<br>".join(pending))
            pending.clear()

    for line in lines:
        is_fence = bool(_FENCE.match(line))
        if in_code:
            flush()
            merged.append(line)
            if is_fence:
                in_code = False
            continue
        if is_fence:
            flush()
            merged.append(line)
            in_code = True
            continue
        if _QUOTE.match(line):
            pending.append(line if not pending else _QUOTE.sub("", line, count=1))
        else:
            flush()
            merged.append(line)
    flush()

    # 2. 逐行决定分隔符。
    out_parts: list[str] = []
    in_code = False
    for i, cur in enumerate(merged):
        next_in_code = (not in_code) if _FENCE.match(cur) else in_code
        if i < len(merged) - 1:
            nxt = merged[i + 1]
            keep_nl = (
                next_in_code
                or cur == ""
                or nxt == ""
                or bool(_FENCE.match(nxt))
                or bool(_BLOCK_START.match(nxt))
            )
            out_parts.append(cur + ("\n" if keep_nl else "<br>"))
        else:
            out_parts.append(cur)
        in_code = next_in_code
    return "".join(out_parts)


def normalize_for_card(content: str) -> str:
    return fix_newlines(ensure_table_blank_lines(content))
