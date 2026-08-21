"""ChatQueue 串行 / 媒体批处理 / SSRF 白名单 / 出站钩子（四特性）。"""

import asyncio

import pytest

from dingtalk_channel_sdk.safety.batching import BatchConfig
from dingtalk_channel_sdk.channel import DingTalkChannel
from dingtalk_channel_sdk.config import (
    ChatQueueConfig,
    MediaBatchConfig,
    OutboundConfig,
    OutboundHooks,
)
from dingtalk_channel_sdk.errors import ChannelError
from dingtalk_channel_sdk.normalize.message import IncomingMessage
from dingtalk_channel_sdk.safety.ssrf_guard import assert_public_url


def bot_frame_dict(msg_id: str, text: str = "hi", resources=None) -> dict:
    import json

    d = {
        "conversationId": "cid-1",
        "conversationType": "2",
        "msgId": msg_id,
        "senderStaffId": "staff-1",
        "isInAtList": True,
        "msgtype": "text",
        "text": {"content": text},
    }
    return {"headers": {"topic": "/v1.0/im/bot/messages/get", "messageId": "m-" + msg_id}, "data": json.dumps(d)}


# ── ChatQueue 串行 ──


@pytest.mark.asyncio
async def test_chat_queue_serial_order():
    ch = DingTalkChannel(client_id="id", client_secret="sec", chat_queue=ChatQueueConfig(enabled=True))
    order = []

    @ch.on_message
    async def handle(msg, reply):
        order.append(msg.msg_id)
        await asyncio.sleep(0.01)  # 模拟慢处理

    for i in range(3):
        await ch.dispatch_for_test(bot_frame_dict(f"q-{i}"))

    assert order == ["q-0", "q-1", "q-2"]  # 同会话严格按序


@pytest.mark.asyncio
async def test_chat_queue_disabled_falls_back():
    ch = DingTalkChannel(client_id="id", client_secret="sec", chat_queue=ChatQueueConfig(enabled=False))
    calls = []

    @ch.on_message
    async def handle(msg, reply):
        calls.append(msg.msg_id)

    await ch.dispatch_for_test(bot_frame_dict("d-1"))
    assert calls == ["d-1"]  # 旧路径仍同步生效


@pytest.mark.asyncio
async def test_chat_queue_batch_serial_flush():
    """批处理 flush 与消息处理共享同会话串行。"""
    ch = DingTalkChannel(client_id="id", client_secret="sec")
    ch.on_batch_message(lambda b: asyncio.sleep(0), batch_config=BatchConfig(delay_s=0.02, max_messages=2))
    batches = []
    ch._batch_handler = lambda batched, reply: batches.append(list(batched.source_ids)) or asyncio.sleep(0)
    # 直接通过内部 batch flush 验证串行锁存在
    q = ch.chat_queue._get("cid-1")
    assert q._serial is not None


# ── 媒体批处理 ──


@pytest.mark.asyncio
async def test_media_batch_merges_resources():
    ch = DingTalkChannel(
        client_id="id",
        client_secret="sec",
        media_batch=MediaBatchConfig(enabled=True, delay_s=0.05),
    )
    got = []

    async def batch_handler(batched, reply):
        got.append(batched.message.resources)

    ch.on_batch_message(batch_handler, batch_config=BatchConfig(delay_s=0.05, max_messages=8))

    import json

    for i in range(2):
        d = {
            "conversationId": "cid-m",
            "conversationType": "2",
            "msgId": f"img-{i}",
            "senderStaffId": "staff-1",
            "isInAtList": True,
            "msgtype": "picture",
            "content": {"downloadCode": f"code-{i}", "pictureDownloadCode": f"code-{i}"},
        }
        await ch.dispatch_for_test(
            {"headers": {"topic": "/v1.0/im/bot/messages/get", "messageId": f"m-img-{i}"}, "data": json.dumps(d)}
        )

    await ch.chat_queue.flush_all()
    await asyncio.sleep(0.05)
    assert len(got) == 1
    assert len(got[0]) == 2  # 两个资源合并进同一批次


@pytest.mark.asyncio
async def test_media_batch_disabled_keeps_text_batching():
    """媒体批处理禁用：媒体消息与文本一样走普通批处理窗口（既有行为不变）。"""
    ch = DingTalkChannel(client_id="id", client_secret="sec", media_batch=MediaBatchConfig(enabled=False))
    count = []

    async def batch_handler(batched, reply):
        count.append(len(batched.source_ids))

    ch.on_batch_message(batch_handler, batch_config=BatchConfig(delay_s=0.05))

    import json

    for i in range(2):
        d = {
            "conversationId": "cid-m2",
            "conversationType": "2",
            "msgId": f"img-{i}",
            "senderStaffId": "staff-1",
            "isInAtList": True,
            "msgtype": "picture",
            "content": {"downloadCode": f"c{i}", "pictureDownloadCode": f"c{i}"},
        }
        await ch.dispatch_for_test(
            {"headers": {"topic": "/v1.0/im/bot/messages/get", "messageId": f"mm-{i}"}, "data": json.dumps(d)}
        )
    await ch.chat_queue.flush_all()
    await asyncio.sleep(0.05)
    assert count == [2]  # 同窗口合并（与旧行为一致）


# ── SSRF 白名单 ──


def test_ssrf_allowlist_exact():
    with pytest.raises(ChannelError):
        assert_public_url("http://internal.corp/file")
    # 白名单豁免（不做 DNS/内网校验）
    assert_public_url("http://internal.corp/file", allowlist=["internal.corp"])


def test_ssrf_allowlist_wildcard():
    assert_public_url("http://cdn.internal.corp/x", allowlist=["*.internal.corp"])
    assert_public_url("http://a.b.corp.cn/x", allowlist=["*.corp.cn"])
    with pytest.raises(ChannelError):
        assert_public_url("http://other.corp/x", allowlist=["*.internal.corp"])


def test_ssrf_loopback_still_blocked():
    with pytest.raises(ChannelError):
        assert_public_url("http://127.0.0.1/x", allowlist=["internal.corp"])


# ── 出站钩子 + 页脚 ──


@pytest.mark.asyncio
async def test_outbound_hooks_and_footer():
    seen = []
    out = OutboundConfig(
        footer="由 AI 生成，仅供参考",
        hooks=OutboundHooks(
            before_send=lambda kind, target, payload: seen.append(("before", kind, payload)) or payload,
            after_send=lambda kind, target, ok, err: seen.append(("after", kind, ok)),
        ),
    )
    ch = DingTalkChannel(client_id="id", client_secret="sec", outbound=out)

    msg = IncomingMessage(conversation_id="cid-f", session_webhook="http://example.invalid/hook")
    reply = ch._reply_for_test(msg) if hasattr(ch, "_reply_for_test") else None
    from dingtalk_channel_sdk.reply import Reply

    reply = Reply(msg, ch.cfg, ch.tokens, ch.cards, ch.oapi)
    # webhook 端点不可达 → after_send(False)；payload 应含页脚
    payload_holder = {}

    def before(kind, target, payload):
        payload_holder.update(payload)
        return payload

    ch.cfg.outbound.hooks.before_send = before
    with pytest.raises(Exception):
        await reply.text("hello")
    kinds = [s[0] for s in seen]
    assert "after" in kinds
    # before_send 看到的 payload 已含页脚
    assert payload_holder["content"].endswith("由 AI 生成，仅供参考")
