"""webhook 失效双降级 + 卡片超长续发测试。"""

from __future__ import annotations

import asyncio
import time

import pytest

from dingtalk_channel_sdk.card import CardStreamer
from dingtalk_channel_sdk.config import Config, OutboundConfig, RetryConfig
from dingtalk_channel_sdk.errors import ChannelError, ErrorCode
from dingtalk_channel_sdk.normalize.message import IncomingMessage
from dingtalk_channel_sdk.reply import Reply


def make_cfg() -> Config:
    return Config(
        client_id="ding-test",
        client_secret="s",
        outbound=OutboundConfig(retry=RetryConfig(max_attempts=3, base_delay_s=0.001)),
    )


class StubTokens:
    async def get(self) -> str:
        return "tk"


def make_reply(msg: IncomingMessage, proactive) -> Reply:
    return Reply(msg, make_cfg(), StubTokens(), None, None, proactive)


async def test_expired_webhook_falls_back_to_proactive(monkeypatch):
    """webhook 已过时效 → 不打 webhook，直接主动发送。"""
    import dingtalk_channel_sdk.reply as reply_mod

    http_calls = {"n": 0}

    async def fake_http_json(method, url, headers, body):
        http_calls["n"] += 1
        return {}

    monkeypatch.setattr(reply_mod, "http_json", fake_http_json)

    proactive = {"calls": []}

    async def fake_proactive(msg, msg_key, msg_param):
        proactive["calls"].append((msg_key, msg_param))

    msg = IncomingMessage(
        conversation_id="cid-1", conversation_type="group",
        session_webhook="https://hook",
        webhook_expired_at=(time.time() - 3600) * 1000,
    )
    r = make_reply(msg, fake_proactive)
    await r.text("hello")

    assert http_calls["n"] == 0  # 过期预检：webhook 直接跳过
    assert len(proactive["calls"]) == 1
    assert proactive["calls"][0][0] == "sampleText"
    assert proactive["calls"][0][1] == {"content": "hello"}


async def test_target_gone_falls_back_to_proactive(monkeypatch):
    """webhook 返回 target_revoked（404）→ 转主动发送兜底。"""
    import dingtalk_channel_sdk.reply as reply_mod

    http_calls = {"n": 0}

    async def fake_http_json(method, url, headers, body):
        http_calls["n"] += 1
        raise ChannelError(ErrorCode.TARGET_REVOKED, "404 target gone")

    monkeypatch.setattr(reply_mod, "http_json", fake_http_json)

    proactive = {"calls": []}

    async def fake_proactive(msg, msg_key, msg_param):
        proactive["calls"].append(msg_key)

    msg = IncomingMessage(conversation_id="cid-1", conversation_type="group", session_webhook="https://hook")
    r = make_reply(msg, fake_proactive)
    await r.text("hello")

    assert http_calls["n"] == 1  # target_revoked 不可重试，单次即停
    assert proactive["calls"] == ["sampleText"]


async def test_other_errors_do_not_fall_back(monkeypatch):
    """非目标失效错误（format_error）不触发兜底，直接抛出。"""
    import dingtalk_channel_sdk.reply as reply_mod

    async def fake_http_json(method, url, headers, body):
        raise ChannelError(ErrorCode.FORMAT_ERROR, "bad payload")

    monkeypatch.setattr(reply_mod, "http_json", fake_http_json)

    async def fake_proactive(msg, msg_key, msg_param):
        raise AssertionError("must not fall back")

    msg = IncomingMessage(conversation_id="cid-1", conversation_type="group", session_webhook="https://hook")
    r = make_reply(msg, fake_proactive)
    with pytest.raises(ChannelError):
        await r.text("hello")


class _StubCfg:
    card_watchdog_s = 0

class StubCardClient:
    def __init__(self):
        self.cfg = _StubCfg()
        self.streamed: list[tuple[str, bool]] = []

    async def set_status(self, card, status, content):
        pass

    async def stream(self, card, content, finalize):
        self.streamed.append((content, finalize))


async def test_card_finish_overflow_delivers_remainder():
    """超长内容：卡片终帧截断 MAX_CONTENT，剩余经 deliver_rest 续发。"""
    client = StubCardClient()
    rest_delivered: list[str] = []

    async def deliver_rest(text: str) -> None:
        rest_delivered.append(text)

    async def fallback(text: str) -> None:
        raise AssertionError("不应走降级")

    streamer = CardStreamer(client, {"inputingStarted": True, "cardData": {}}, fallback, 0.0, deliver_rest)
    streamer.card["inputingStarted"] = True

    tail = "尾" * 800
    content = "头" * 20000 + tail
    await streamer.finish(content)

    # 卡片只收到截断后的 20000
    assert len(client.streamed[-1][0]) == 20000
    assert client.streamed[-1][1] is True  # 终帧
    # 剩余 800 rune 经续发通道原样送达
    assert rest_delivered == [tail]


async def test_card_finish_within_limit_no_remainder():
    """未超限不触发续发。"""
    client = StubCardClient()
    rest_delivered: list[str] = []

    async def deliver_rest(text: str) -> None:
        rest_delivered.append(text)

    async def fallback(text: str) -> None:
        raise AssertionError("不应走降级")

    streamer = CardStreamer(client, {"inputingStarted": True}, fallback, 0.0, deliver_rest)
    await streamer.finish("短内容")
    assert rest_delivered == []
