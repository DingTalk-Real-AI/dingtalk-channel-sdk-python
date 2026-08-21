"""媒体上传：OAPI gettoken + multipart /media/upload（对比官方 connector media/common.ts 移植，E9）。"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request

from .config import Config

DEFAULT_OAPI_BASE = "https://oapi.dingtalk.com"


class OapiClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._token = ""
        self._expires_at = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        q = urllib.parse.urlencode({"appkey": self.cfg.client_id, "appsecret": self.cfg.client_secret})
        with urllib.request.urlopen(f"{self.cfg.oapi_base}/gettoken?{q}", timeout=15) as resp:
            out = json.loads(resp.read())
        if out.get("errcode") != 0 or not out.get("access_token"):
            raise RuntimeError(f"oapi gettoken: errcode={out.get('errcode')} {out.get('errmsg', '')}")
        self._token = out["access_token"]
        self._expires_at = time.time() + float(out.get("expires_in") or 7200)
        return self._token

    async def upload_media(self, media_type: str, filename: str, data: bytes, content_type: str = "") -> dict:
        """上传媒体文件，返回 {"mediaId": ...}（去前导 @）。

        media_type: image | file | video | voice
        """
        token = await asyncio.to_thread(self._get_token)
        if not content_type:
            content_type = "image/jpeg" if media_type == "image" else "application/octet-stream"

        boundary = "----DingTalkChannelSDK" + format(time.time_ns() % 10**12, "012d")
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="media"; filename="{filename}"\r\n'.encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                data,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        q = urllib.parse.urlencode({"access_token": token, "type": media_type})
        def _do_upload() -> dict:
            req = urllib.request.Request(
                f"{self.cfg.oapi_base}/media/upload?{q}",
                data=body,
                method="POST",
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"media/upload: http {e.code} {e.read().decode('utf-8', 'replace')}") from e

        out = await asyncio.to_thread(_do_upload)
        if out.get("errcode") not in (0, None):
            raise RuntimeError(f"media/upload: errcode={out.get('errcode')} {out.get('errmsg', '')}")
        media_id = out.get("media_id") or ""
        if not media_id:
            raise RuntimeError("media/upload: no media_id in response")
        if media_id.startswith("@"):
            media_id = media_id[1:]
        # AI 卡片内嵌必须用完整 URL（openclaw connector 新版 media.ts 实证），裸 mediaId 不渲染
        return {
            "mediaId": media_id,
            "type": out.get("type"),
            "createdAt": out.get("created_at"),
            "downloadUrl": f"https://down.dingtalk.com/media/{media_id}",
        }
