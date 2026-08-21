"""机器人身份。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .token import TokenProvider


@dataclass
class BotIdentity:
    """机器人身份信息。"""

    robot_code: str
    robot_name: str
    avatar: str = ""


class BotIdentityProvider:
    """机器人身份提供者（带缓存）。"""

    def __init__(self, cfg: Config, tokens: TokenProvider):
        self.cfg = cfg
        self.tokens = tokens
        self._cache_ttl = 30 * 60  # 30 分钟
        self._min_refresh_interval = 60  # 1 分钟
        self._identity: Optional[BotIdentity] = None
        self._fetched_at: float = 0
        self._last_failure_at: float = 0
        self._lock = asyncio.Lock()

    async def get(self) -> Optional[BotIdentity]:
        """获取机器人身份（带缓存）。"""
        if self._is_cache_fresh():
            return self._identity

        async with self._lock:
            # Double-check after acquiring lock
            if self._is_cache_fresh():
                return self._identity

            now = time.time()
            if self._should_throttle_refresh(now):
                return self._identity

            try:
                identity = await self._fetch()
                self._identity = identity
                self._fetched_at = time.time()
                self._last_failure_at = 0
                return identity
            except Exception as err:
                self._last_failure_at = now
                if self._identity is not None:
                    # 有旧缓存，返回旧的（降级）
                    self.cfg.log(f"failed to refresh bot identity, using stale cache: {err}")
                    return self._identity
                self.cfg.log(f"failed to fetch bot identity: {err}")
                return None

    def _is_cache_fresh(self) -> bool:
        if self._identity is None:
            return False
        if self._cache_ttl <= 0:
            return True
        return (time.time() - self._fetched_at) < self._cache_ttl

    def _should_throttle_refresh(self, now: float) -> bool:
        if self._last_failure_at == 0:
            return False
        if self._min_refresh_interval <= 0:
            return False
        return (now - self._last_failure_at) < self._min_refresh_interval

    async def _fetch(self) -> BotIdentity:
        """从 API 获取机器人身份信息。"""
        from .httpx import http_json

        token = await self.tokens.get()
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }

        resp = await http_json(
            "GET",
            f"{self.cfg.api_base}/v1.0/robot/robotInfo",
            headers,
            None,
        )

        robot_code = resp.get("robotCode", "") or self.cfg.client_id
        robot_name = resp.get("robotName", "") or "bot"
        avatar = resp.get("avatar", "")

        return BotIdentity(
            robot_code=robot_code,
            robot_name=robot_name,
            avatar=avatar,
        )
