"""极简异步 HTTP 客户端（标准库实现，避免引入第三方依赖）。"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any, Dict, Optional


class ApiError(Exception):
    """钉钉 API 错误；is_qps_limit 判定 403 + code 含 QpsLimit（SPEC §6）。"""

    def __init__(self, status: int, code: str = "", message: str = "", body: Any = None):
        super().__init__(f"dingtalk api error: http={status} code={code} {message}")
        self.status = status
        self.code = code
        self.body = body

    @property
    def is_qps_limit(self) -> bool:
        return self.status == 403 and isinstance(self.code, str) and "QpsLimit" in self.code


def _request_sync(method: str, url: str, headers: Dict[str, str], body: Optional[dict]) -> Dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    if status >= 400:
        try:
            err = json.loads(raw)
        except Exception:
            err = {}
        if isinstance(err, dict):
            raise ApiError(status, str(err.get("code", "")), str(err.get("message", "")), err)
        raise ApiError(status, body=raw.decode("utf-8", "replace"))
    if not raw:
        return {}
    return json.loads(raw)


async def http_json(method: str, url: str, headers: Optional[Dict[str, str]] = None,
                    body: Optional[dict] = None) -> Dict[str, Any]:
    return await asyncio.to_thread(_request_sync, method, url, headers or {}, body)
