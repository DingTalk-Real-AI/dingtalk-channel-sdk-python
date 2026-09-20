from __future__ import annotations

import asyncio
import functools
import sys
from typing import Any, Callable, TypeVar

T = TypeVar("T")


async def to_thread(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Python 3.8+ compatible asyncio.to_thread helper."""
    if sys.version_info >= (3, 9):
        return await asyncio.to_thread(func, *args, **kwargs)
    loop = asyncio.get_running_loop()
    pfunc = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(None, pfunc)
