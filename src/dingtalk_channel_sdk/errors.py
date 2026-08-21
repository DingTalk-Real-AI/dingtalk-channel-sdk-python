"""错误分类。"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    """结构化错误码。"""

    TARGET_REVOKED = "target_revoked"
    PERMISSION_DENIED = "permission_denied"
    FORMAT_ERROR = "format_error"
    RATE_LIMITED = "rate_limited"
    QPS_LIMITED = "qps_limited"
    SEND_TIMEOUT = "send_timeout"
    SSRF_BLOCKED = "ssrf_blocked"
    UNKNOWN = "unknown"


class ChannelError(Exception):
    """结构化错误，携带错误码、原始错误和上下文。"""

    def __init__(self, code: ErrorCode, message: str, cause: Optional[Exception] = None):
        self.code = code
        self.message = message
        self.cause = cause
        super().__init__(str(self))

    def __str__(self) -> str:
        s = f"ChannelError(code={self.code.value}): {self.message}"
        if self.cause is not None:
            s += f" | cause: {self.cause}"
        return s

    def __repr__(self) -> str:
        return f"ChannelError(code={self.code.value!r}, message={self.message!r})"


def _classify_from_status(status: int) -> Optional[ErrorCode]:
    if status == 400:
        return ErrorCode.FORMAT_ERROR
    if status in (401, 403):
        return ErrorCode.PERMISSION_DENIED
    if status == 404:
        return ErrorCode.TARGET_REVOKED
    if status == 429:
        return ErrorCode.RATE_LIMITED
    return None


def classify_error(err: Exception) -> ChannelError:
    """将原始错误分类为结构化 ChannelError。"""
    if isinstance(err, ChannelError):
        return err

    msg = str(err).lower()

    # 尝试从 API 错误中提取 status code
    status = getattr(err, "status", None) or getattr(err, "status_code", None)
    if status is not None:
        # 检查 QPS 限制
        err_msg = str(err).lower()
        if "qps" in err_msg or "flow control" in err_msg:
            return ChannelError(ErrorCode.QPS_LIMITED, str(err), err)
        code = _classify_from_status(int(status))
        if code is not None:
            return ChannelError(code, str(err), err)

    # 从错误消息推断
    if "status 429" in msg or "too many requests" in msg:
        return ChannelError(ErrorCode.RATE_LIMITED, str(err), err)
    if "status 401" in msg or "status 403" in msg:
        return ChannelError(ErrorCode.PERMISSION_DENIED, str(err), err)
    if "status 400" in msg:
        return ChannelError(ErrorCode.FORMAT_ERROR, str(err), err)
    if "status 404" in msg:
        return ChannelError(ErrorCode.TARGET_REVOKED, str(err), err)

    if "timeout" in msg or "deadline exceeded" in msg:
        return ChannelError(ErrorCode.SEND_TIMEOUT, str(err), err)

    return ChannelError(ErrorCode.UNKNOWN, str(err), err)


def is_retryable(err: Exception) -> bool:
    """判断错误是否可重试。"""
    if isinstance(err, ChannelError):
        return err.code in (
            ErrorCode.RATE_LIMITED,
            ErrorCode.QPS_LIMITED,
            ErrorCode.UNKNOWN,
            ErrorCode.SEND_TIMEOUT,
        )
    return True


def is_reply_target_gone(err: Exception) -> bool:
    """判断是否为回复目标已撤回。"""
    return isinstance(err, ChannelError) and err.code == ErrorCode.TARGET_REVOKED


def is_format_error(err: Exception) -> bool:
    """判断是否为格式错误。"""
    return isinstance(err, ChannelError) and err.code == ErrorCode.FORMAT_ERROR
