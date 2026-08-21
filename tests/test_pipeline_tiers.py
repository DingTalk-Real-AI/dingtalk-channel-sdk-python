"""SafetyPipeline 三层接口 / 去重标记时机 / 并发场景测试。

对标场景：push 三层分发、动作去重与同 scope 串行、reaction 并发去重、
seen 标记时机（入口标记 vs 处理成功后标记）、卡片回调两层去重。
"""

from __future__ import annotations

import asyncio
import json
import time

from dingtalk_channel_sdk.channel import DingTalkChannel
from dingtalk_channel_sdk.config import Config
from dingtalk_channel_sdk.normalize.message import IncomingMessage
from dingtalk_channel_sdk.safety.chat_queue import ChatQueueManager
from dingtalk_channel_sdk.safety.pipeline import PipelineOptions, SafetyPipeline
from dingtalk_channel_sdk.safety.batching import BatchConfig
from dingtalk_channel_sdk.config import ChatQueueConfig, MediaBatchConfig
from dingtalk_channel_sdk.types import DedupConfig, SafetyConfig


def make_msg(msg_id: str, text: str = "hello", create_at: int | None = None) -> IncomingMessage:
    return IncomingMessage(
        conversation_id="chat-1",
        conversation_type="group",
        sender_id="user-1",
        sender_staff_id="staff-1",
        msg_id=msg_id,
        msg_type="text",
        text=text,
        create_at=create_at if create_at is not None else int(time.time() * 1000),
        is_in_at_list=True,
    )


def make_pipeline(on_message=None, on_batch=None, queue: ChatQueueManager | None = None,
                  mark_after: bool = False) -> SafetyPipeline:
    cfg = SafetyConfig(
        dedup=DedupConfig(),
        media_batch=MediaBatchConfig(),  # 默认关闭 → 直接分发路径
        mark_after_handler=mark_after,
    )
    opts = PipelineOptions(
        on_message=on_message,
        on_batch=on_batch,
        chat_queue=queue,
    )
    return SafetyPipeline(cfg, opts)


# ── 三层接口 ──


async def test_push_message_reaches_handler():
    calls: list[str] = []

    async def on_msg(msg, sources):
        calls.append(msg.msg_id)

    p = make_pipeline(on_message=on_msg)
    await p.push_message("p-1", make_msg("m-1"))
    assert calls == ["m-1"]
    await p.dispose()


async def test_push_action_dedup_and_different_events():
    runs: list[str] = []

    async def act(ev):
        runs.append(ev)
        return None

    p = make_pipeline()
    await p.push_action("evt-1", "card:c1", lambda ev="e1": act(ev))
    await p.push_action("evt-1", "card:c1", lambda ev="e1": act(ev))  # 重复投递
    await p.push_action("evt-2", "card:c1", lambda ev="e2": act(ev))
    await p.dispose()
    assert len(runs) == 2  # evt-1 去重后一次 + evt-2 一次


async def test_push_action_serial_per_scope():
    """同 scope 动作串行（并发重入为 0）。"""
    active = 0
    max_active = 0
    lock = asyncio.Lock()

    q = ChatQueueManager(batch_cfg=BatchConfig(delay_s=0.01), queue_cfg=ChatQueueConfig(enabled=True),
                         media_batch=MediaBatchConfig())
    p = make_pipeline(queue=q)

    async def slow():
        nonlocal active, max_active
        active += 1
        async with lock:
            max_active = max(max_active, active)
        await asyncio.sleep(0.03)
        active -= 1

    await asyncio.gather(*[p.push_action(f"evt-{i}", "card:same", slow) for i in range(5)])
    await p.dispose()
    assert max_active == 1


async def test_push_light_dedup_and_concurrent_reactions():
    """轻量事件：同 ID 去重，不同 ID 并发全部执行（reaction 场景）。"""
    runs = 0

    def inc():
        nonlocal runs
        runs += 1
        return None

    async def inc_async():
        inc()

    p = make_pipeline()
    await p.push_light("react-1", inc_async)
    await p.push_light("react-1", inc_async)  # 重复 reaction
    await asyncio.gather(*[p.push_light(f"react-{i}", inc_async) for i in range(32)])
    await p.dispose()
    assert runs == 32  # 1 首投 + gather 中 31 个新事件（react-1 与首投同名，正确去重）


# ── seen 标记时机 ──


async def test_mark_timing_default_marks_on_entry():
    """默认模式：入口即标记，失败后重投被判重（防重复消费优先）。"""
    calls = 0

    async def handler(msg, sources):
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    p = make_pipeline(on_message=handler)
    msg = make_msg("m-fail")
    await p.push_message("p-fail", msg)
    await p.push_message("p-fail-2", msg)  # 换投递 ID 重投
    await p.dispose()
    assert calls == 1


async def test_mark_timing_after_handler_allows_redelivery():
    """mark_after_handler：失败不标记 → 重投重试；成功后标记 → 再投判重。"""
    calls = 0
    fail = True

    async def handler(msg, sources):
        nonlocal calls
        calls += 1
        if fail:
            raise RuntimeError("transient")

    p = make_pipeline(on_message=handler, mark_after=True)
    msg = make_msg("m-redeliver")
    await p.push_message("p-1", msg)  # 失败 → 未标记
    await p.push_message("p-2", msg)  # 重投 → 再处理
    fail = False

    async def ok_handler(m, s):
        nonlocal calls
        calls += 1

    p.on_message = ok_handler
    await p.push_message("p-3", msg)  # 成功 → 标记
    await p.push_message("p-4", msg)  # 判重
    await p.dispose()
    assert calls == 3


async def test_mark_timing_action_and_light():
    """push_action / push_light 同样支持"成功后标记"语义。"""
    runs = 0
    fail = True

    async def act():
        nonlocal runs
        runs += 1
        if fail:
            raise RuntimeError("boom")

    p = make_pipeline(mark_after=True)
    await p.push_action("a-1", "s", act)  # 失败，未标记
    await p.push_action("a-1", "s", act)  # 重投 → 再执行
    fail = False
    await p.push_action("a-1", "s", act)  # 成功 → 标记
    await p.push_action("a-1", "s", act)  # 判重

    async def light():
        nonlocal runs
        runs += 1

    await p.push_light("l-1", light)
    await p.push_light("l-1", light)  # 判重
    await p.dispose()
    assert runs == 4


# ── 卡片回调两层去重（channel 集成） ──


def card_frame(message_id: str, out_track_id: str) -> dict:
    return {
        "type": "CALLBACK",
        "headers": {
            "topic": "/v1.0/card/instances/callback",
            "messageId": message_id,
            "contentType": "application/json",
        },
        "data": json.dumps(
            {"outTrackId": out_track_id, "userId": "u-1", "dataContent": {"action": "confirm"}}
        ),
    }


async def test_card_action_dedup_two_layers():
    """同 messageId 重复投递、换 messageId 重放同动作，都只处理一次。"""
    ch = DingTalkChannel(client_id="ding-test", client_secret="s", config=Config(
        client_id="ding-test", client_secret="s",
        stale_message_window_s=0,
        chat_queue=ChatQueueConfig(enabled=True),
    ))
    calls = 0

    @ch.on_card_action
    async def on_action(action, reply):
        nonlocal calls
        calls += 1

    await ch.dispatch_for_test(card_frame("m-c1", "card_1"))
    await ch.dispatch_for_test(card_frame("m-c1", "card_1"))  # 网关重复投递
    await ch.dispatch_for_test(card_frame("m-c2", "card_1"))  # 换投递 ID 重放
    await asyncio.sleep(0.05)
    assert calls == 1


# ── 媒体批次键：前缀碰撞 / reply 感知 ──


async def test_media_flush_prefix_collision():
    """chatID 前缀碰撞回归：cid-1 的消息不得误刷 cid-12 的媒体批次。"""
    from dingtalk_channel_sdk.safety.media_pipeline import MediaPipelineManager
    from dingtalk_channel_sdk.config import MediaBatchConfig as MBC

    mgr = MediaPipelineManager(MBC(enabled=True, delay_s=10.0))
    flushed: list[str] = []

    async def handler(merged):
        flushed.append(merged.conversation_id)

    await mgr.push(IncomingMessage(conversation_id="cid-12", msg_type="picture",
                                   text="", resources=[{"type": "image"}]), handler)
    # cid-1 是 cid-12 前缀：不应触发刷新
    await mgr.flush_incompatible_for(IncomingMessage(conversation_id="cid-1", msg_type="text"))
    assert flushed == []

    # 真正同会话：触发刷新
    await mgr.flush_incompatible_for(IncomingMessage(conversation_id="cid-12", msg_type="text"))
    await asyncio.sleep(0.05)
    assert flushed == ["cid-12"]


async def test_media_reply_parent_key():
    """reply 消息按引用内容分桶：不同引用目标形成独立批次。"""
    from dingtalk_channel_sdk.safety.media_pipeline import MediaPipelineManager
    from dingtalk_channel_sdk.config import MediaBatchConfig as MBC

    mgr = MediaPipelineManager(MBC(enabled=True, delay_s=0.08))
    flushes = 0

    async def handler(merged):
        nonlocal flushes
        flushes += 1

    await mgr.push(IncomingMessage(conversation_id="c1", msg_type="reply",
                                   text="", content={"repliedMsg": {"msgId": "r1"}},
                                   resources=[{"type": "image"}]), handler)
    await mgr.push(IncomingMessage(conversation_id="c1", msg_type="reply",
                                   text="", content={"repliedMsg": {"msgId": "r2"}},
                                   resources=[{"type": "image"}]), handler)
    await asyncio.sleep(0.15)
    assert flushes == 2
