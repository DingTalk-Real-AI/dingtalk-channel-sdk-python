"""出站 retry 接线 + stream 异步分发回归测试。"""

from __future__ import annotations

import asyncio
import json

from dingtalk_channel_sdk.config import Config, OutboundConfig, RetryConfig
from dingtalk_channel_sdk.errors import ChannelError, ErrorCode
from dingtalk_channel_sdk.normalize.message import IncomingMessage
from dingtalk_channel_sdk.reply import Reply
from dingtalk_channel_sdk.send import ProactiveSender, SendTarget
from dingtalk_channel_sdk.stream import StreamConn


def fast_cfg() -> Config:
    return Config(
        client_id="ding-test",
        client_secret="s",
        outbound=OutboundConfig(retry=RetryConfig(max_attempts=3, base_delay_s=0.001)),
    )


class StubTokens:
    async def get(self) -> str:
        return "tk"


async def test_reply_webhook_retries_on_rate_limit(monkeypatch):
    """webhook 回复遇 429（rate_limited）应重试并最终成功。"""
    import dingtalk_channel_sdk.reply as reply_mod

    calls = {"n": 0}

    async def fake_http_json(method, url, headers, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ChannelError(ErrorCode.RATE_LIMITED, "429")
        return {"ok": True}

    monkeypatch.setattr(reply_mod, "http_json", fake_http_json)

    cfg = fast_cfg()
    msg = IncomingMessage(conversation_id="c1", session_webhook="https://hook")
    r = Reply(msg, cfg, StubTokens(), None, None)
    await r.text("hello")

    assert calls["n"] == 2  # 首试 429 + 重试成功


async def test_reply_webhook_format_error_fails_fast(monkeypatch):
    """格式错误不可重试：只打一次。"""
    import dingtalk_channel_sdk.reply as reply_mod

    calls = {"n": 0}

    async def fake_http_json(method, url, headers, body):
        calls["n"] += 1
        raise ChannelError(ErrorCode.FORMAT_ERROR, "bad payload")

    monkeypatch.setattr(reply_mod, "http_json", fake_http_json)

    cfg = fast_cfg()
    msg = IncomingMessage(conversation_id="c1", session_webhook="https://hook")
    r = Reply(msg, cfg, StubTokens(), None, None)
    try:
        await r.text("hello")
        raise AssertionError("should raise")
    except ChannelError:
        pass
    assert calls["n"] == 1  # 不可重试即停


class StubCards:
    def __init__(self, fail_first: bool):
        self.fail_first = fail_first
        self.calls = 0

    async def _call(self, method, path, body):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise ChannelError(ErrorCode.RATE_LIMITED, "429")
        return {"success": True}


async def test_send_retries_on_rate_limit():
    """主动发送遇限流应重试成功。"""
    cfg = fast_cfg()
    cards = StubCards(fail_first=True)
    sender = ProactiveSender(cfg, cards)
    await sender.send_text(SendTarget(user_id="u-1"), "hi")
    assert cards.calls == 2


async def test_send_fails_fast_on_format_error():
    cfg = fast_cfg()

    class FatalCards(StubCards):
        async def _call(self, method, path, body):
            self.calls += 1
            raise ChannelError(ErrorCode.FORMAT_ERROR, "bad")

    cards = FatalCards(fail_first=False)
    sender = ProactiveSender(cfg, cards)
    try:
        await sender.send_text(SendTarget(user_id="u-1"), "hi")
        raise AssertionError("should raise")
    except ChannelError:
        pass
    assert cards.calls == 1


class FakeWS:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        pass


async def test_stream_dispatches_frame_asynchronously():
    """业务帧 ACK 先行、处理异步派发：handler 未完成时 ACK 已发出。"""
    conn = StreamConn(fast_cfg(), on_frame=None)
    done = {"flag": False}

    async def slow_handler(frame):
        await asyncio.sleep(0.05)
        done["flag"] = True

    conn.on_frame = slow_handler
    ws = FakeWS()
    frame = {
        "type": "CALLBACK",
        "headers": {"topic": "/v1.0/im/bot/messages/get", "messageId": "m-1"},
        "data": "{}",
    }
    ponged = await conn._handle_frame(ws, frame)
    assert ponged is False
    # ACK 已先行，业务仍在处理
    assert len(ws.sent) == 1 and json.loads(ws.sent[0])["headers"]["messageId"] == "m-1"
    assert done["flag"] is False
    await asyncio.sleep(0.08)
    assert done["flag"] is True


async def test_stream_handler_exception_contained():
    """业务帧处理异常不外泄、不影响 ACK。"""
    conn = StreamConn(fast_cfg(), on_frame=None)

    async def boom(frame):
        raise RuntimeError("handler boom")

    conn.on_frame = boom
    ws = FakeWS()
    frame = {
        "type": "CALLBACK",
        "headers": {"topic": "/v1.0/im/bot/messages/get", "messageId": "m-2"},
        "data": "{}",
    }
    ponged = await conn._handle_frame(ws, frame)
    assert ponged is False
    assert len(ws.sent) == 1
    await asyncio.sleep(0.01)  # 派发任务异常被 _dispatch 记录吞掉
