"""重试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TypeVar

from ..errors import is_retryable

T = TypeVar("T")


@dataclass
class RetryOptions:
    max_attempts: int = 3
    base_delay_s: float = 0.5


async def retry(op: Callable[[], Awaitable[T]], opts: Optional[RetryOptions] = None) -> T:
    """指数退避重试：delay = base_delay * 3^(attempt-1)。仅重试可重试错误。"""
    _opts = opts or RetryOptions()
    last_err: Exception = RuntimeError("retry: no attempts made")
    for attempt in range(1, _opts.max_attempts + 1):
        try:
            return await op()
        except Exception as err:  # noqa: BLE001
            last_err = err
            if attempt >= _opts.max_attempts or not is_retryable(err):
                raise
            delay = _opts.base_delay_s * (3 ** (attempt - 1))
            await asyncio.sleep(delay)
    raise last_err
