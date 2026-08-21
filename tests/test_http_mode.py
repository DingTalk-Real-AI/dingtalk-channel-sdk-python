"""HTTP 模式传输：验签、幂等分发、start() 引导。"""

import base64
import hashlib
import hmac
import json
import time

import pytest

from dingtalk_channel_sdk.channel import DingTalkChannel
from dingtalk_channel_sdk.errors import ChannelError
from dingtalk_channel_sdk.http_mode import verify_http_sign

SECRET = "sec"


def _sign(timestamp: str, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _body(msg_id: str, text: str = "hi") -> bytes:
    return json.dumps(
        {
            "conversationId": "cid-w",
            "conversationType": "1",
            "msgId": msg_id,
            "senderStaffId": "staff-1",
            "sessionWebhook": "",
            "text": {"content": text},
            "isInAtList": True,
            "msgtype": "text",
        }
    ).encode()


def test_verify_http_sign():
    ts = str(int(time.time() * 1000))
    ts_sec = str(int(time.time()))
    verify_http_sign(SECRET, ts, _sign(ts))
    verify_http_sign(SECRET, ts_sec, _sign(ts_sec))  # 秒级时间戳兼容
    with pytest.raises(ChannelError, match="signature mismatch"):
        verify_http_sign(SECRET, ts, _sign(ts, secret="wrong"))
    old = str(int((time.time() - 7200) * 1000))
    with pytest.raises(ChannelError, match="tolerance"):
        verify_http_sign(SECRET, old, _sign(old))
    verify_http_sign(SECRET, old, _sign(old), tolerance_s=0)  # <=0 关闭窗口
    with pytest.raises(ChannelError, match="invalid timestamp"):
        verify_http_sign(SECRET, "abc", _sign("abc"))


@pytest.mark.asyncio
async def test_handle_http_callback_dispatch_and_dedup():
    ch = DingTalkChannel(client_id="id", client_secret=SECRET)
    calls = []

    @ch.on_message
    async def handle(msg, reply):
        calls.append(msg.text)

    ts = str(int(time.time() * 1000))
    sign = _sign(ts)
    body = _body("w-1")
    await ch.handle_http_callback(body, ts, sign)
    await ch.handle_http_callback(body, ts, sign)  # 重试重推 → 去重
    assert calls == ["hi"]

    with pytest.raises(ChannelError, match="signature"):
        await ch.handle_http_callback(body, ts, "bad-sign")
    with pytest.raises(ChannelError, match="payload"):
        await ch.handle_http_callback(b"{bad", ts, sign)


@pytest.mark.asyncio
async def test_start_redirects_in_http_mode():
    ch = DingTalkChannel(client_id="id", client_secret=SECRET, transport="http")

    @ch.on_message
    async def handle(msg, reply):
        return None

    with pytest.raises(RuntimeError, match="handle_http_callback"):
        await ch.start()
