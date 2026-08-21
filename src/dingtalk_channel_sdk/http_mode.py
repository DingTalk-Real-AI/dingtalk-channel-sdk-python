"""HTTP 模式传输（dispatcher 形态）：验签与消息分发由 SDK 负责，HTTP 服务由外部提供。

钉钉企业内部机器人 HTTP 模式回调：
- 请求头 ``timestamp`` + ``sign``，其中 ``sign = Base64(HmacSHA256(appSecret, timestamp + "\\n" + appSecret))``
- 请求体与 Stream 模式 data 载荷同构（chatbot schema）
- 重试会导致重复推送，由 DedupCache 幂等吸收
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from .errors import ChannelError, ErrorCode

DEFAULT_HTTP_TIMESTAMP_TOLERANCE_S = 3600.0  # 验签时间戳容忍窗口（防重放）


def _timestamp_ms(timestamp: str) -> int:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as err:
        raise ChannelError(ErrorCode.PERMISSION_DENIED, "http mode: invalid timestamp header") from err
    if ts < 10**11:  # 秒级时间戳统一换算为毫秒
        ts *= 1000
    return ts


def verify_http_sign(
    secret: str,
    timestamp: str,
    sign: str,
    tolerance_s: float = DEFAULT_HTTP_TIMESTAMP_TOLERANCE_S,
    now_s: float | None = None,
) -> None:
    """校验签名与时间戳窗口（<=0 关闭窗口检查），失败抛 ChannelError（PERMISSION_DENIED）。"""
    if not timestamp or not sign:
        raise ChannelError(ErrorCode.PERMISSION_DENIED, "http mode: missing timestamp/sign headers")
    ts_ms = _timestamp_ms(timestamp)
    if tolerance_s > 0:
        now_ms = int((now_s if now_s is not None else time.time()) * 1000)
        age_ms = abs(now_ms - ts_ms)
        if age_ms > tolerance_s * 1000:
            raise ChannelError(ErrorCode.PERMISSION_DENIED, "http mode: timestamp outside tolerance window")
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), f"{timestamp}\n{secret}".encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    if not hmac.compare_digest(expected, sign):
        raise ChannelError(ErrorCode.PERMISSION_DENIED, "http mode: signature mismatch")
