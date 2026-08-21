"""过期消息检测（SPEC §3.2）。

过滤网关重投递/重连回放的陈旧消息：超过窗口的消息直接拒绝处理。
create_at <= 0 视为时间未知，保守放行。
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Optional

DEFAULT_STALE_WINDOW = timedelta(minutes=30)


class StaleDetector:
    """过期消息检测器。"""

    def __init__(self, stale_window: Optional[timedelta] = None):
        """初始化检测器。

        Args:
            stale_window: 过期窗口，默认 30 分钟。
        """
        self.stale_window = stale_window or DEFAULT_STALE_WINDOW
        self.stale_window_ms = int(self.stale_window.total_seconds() * 1000)

    def is_stale(self, create_at: int) -> bool:
        """检测消息是否过期。

        Args:
            create_at: 消息创建时间戳（毫秒）。

        Returns:
            True 表示已超过窗口，应拒绝处理。
        """
        if create_at <= 0:
            return False
        return (int(time.time() * 1000) - create_at) > self.stale_window_ms
