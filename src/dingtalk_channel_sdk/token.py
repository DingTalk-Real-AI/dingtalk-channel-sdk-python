"""access token 获取与缓存（SPEC §8）。"""

from __future__ import annotations

import time

from .config import Config
from .httpx import http_json


class TokenProvider:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._token = ""
        self._expires_at = 0.0

    async def get(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        out = await http_json(
            "POST",
            f"{self.cfg.api_base}/v1.0/oauth2/accessToken",
            {"Content-Type": "application/json"},
            {"appKey": self.cfg.client_id, "appSecret": self.cfg.client_secret},
        )
        token = out.get("accessToken")
        if not token:
            raise RuntimeError(f"accessToken: empty token in response: {out}")
        self._token = token
        self._expires_at = time.time() + float(out.get("expireIn") or 7200)
        return self._token
