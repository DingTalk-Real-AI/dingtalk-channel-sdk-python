"""发送者身份解析。

钉钉消息携带多个发送者标识（staffId / dingtalkId 即 senderId / unionId 等），
策略名单里存的可能是其中任意一种。本模块按字段优先级解析发送者身份，
并提供跨字段匹配，供策略门控与审计使用。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

# 解析顺序：企业内最稳定的 staffId 优先，其次通用 senderId（dingtalkId）
DEFAULT_IDENTITY_FIELDS: Sequence[str] = ("sender_staff_id", "sender_id")


def resolve_sender_identity(msg: Any, fields: Optional[Sequence[str]] = None) -> str:
    """按字段优先级解析发送者身份，返回第一个非空标识。

    Args:
        msg: 归一化后的 IncomingMessage（鸭子类型，按属性读取）。
        fields: 字段优先级，默认 (sender_staff_id, sender_id)。

    Returns:
        第一个非空标识；全部为空时返回空字符串。
    """
    for field in (fields or DEFAULT_IDENTITY_FIELDS):
        value = getattr(msg, field, "") or ""
        if value:
            return value
    return ""


def sender_matches(msg: Any, identities: Iterable[str], fields: Optional[Sequence[str]] = None) -> bool:
    """判断发送者是否命中身份名单：消息上任一身份字段与名单任一值相等即命中。

    Args:
        msg: 归一化后的 IncomingMessage。
        identities: 身份名单（管理员/白/黑名单）。
        fields: 参与匹配的身份字段；默认仅 sender_id（与既有策略行为一致）。

    Returns:
        True 表示命中。
    """
    identity_set = set(identities)
    if not identity_set:
        return False
    for field in (fields or ("sender_id",)):
        value = getattr(msg, field, "") or ""
        if value and value in identity_set:
            return True
    return False
