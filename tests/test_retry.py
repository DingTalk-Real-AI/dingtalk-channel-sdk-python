"""outbound.retry 场景测试：成功首试 / 指数退避重试 / 不可重试即停 / 耗尽 / 默认值。"""

from __future__ import annotations

import pytest

from dingtalk_channel_sdk.errors import ChannelError, ErrorCode
from dingtalk_channel_sdk.outbound.retry import RetryOptions, retry


def retryable() -> Exception:
    return ChannelError(ErrorCode.RATE_LIMITED, "rate limited")


def fatal() -> Exception:
    return ChannelError(ErrorCode.FORMAT_ERROR, "bad payload")


def fast() -> RetryOptions:
    return RetryOptions(max_attempts=3, base_delay_s=0.001)


async def test_retry_succeeds_first_attempt():
    attempts = 0

    async def op():
        nonlocal attempts
        attempts += 1
        return "ok"

    assert await retry(op, fast()) == "ok"
    assert attempts == 1


async def test_retry_retries_until_success():
    attempts = 0

    async def op():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise retryable()
        return "ok"

    assert await retry(op, fast()) == "ok"
    assert attempts == 3


async def test_retry_stops_on_non_retryable():
    attempts = 0

    async def op():
        nonlocal attempts
        attempts += 1
        raise fatal()

    with pytest.raises(ChannelError) as ei:
        await retry(op, fast())
    assert ei.value.code == ErrorCode.FORMAT_ERROR
    assert attempts == 1  # format_error 不重试


async def test_retry_exhausts_attempts():
    attempts = 0

    async def op():
        nonlocal attempts
        attempts += 1
        raise retryable()

    with pytest.raises(ChannelError):
        await retry(op, fast())
    assert attempts == 3  # MaxAttempts


async def retry_defaults_none():
    attempts = 0

    async def op():
        nonlocal attempts
        attempts += 1
        raise fatal()

    with pytest.raises(ChannelError):
        await retry(op, None)
    assert attempts == 1
