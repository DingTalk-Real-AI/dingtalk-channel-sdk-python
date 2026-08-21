"""群级策略覆盖：显式条目放行、黑名单不可豁免、逐群 require_mention/allow_from。"""

import pytest

from dingtalk_channel_sdk.normalize.message import CONVERSATION_GROUP, IncomingMessage
from dingtalk_channel_sdk.safety.policy import GroupOverride, PolicyConfig, PolicyGate, RejectReason


def _msg(cid: str, sender: str = "staff-1", in_at_list: bool = True) -> IncomingMessage:
    return IncomingMessage(
        conversation_id=cid,
        conversation_type=CONVERSATION_GROUP,
        sender_id=sender,
        is_in_at_list=in_at_list,
    )


@pytest.mark.asyncio
async def test_override_admits_group_in_allowlist_mode():
    gate = PolicyGate(
        PolicyConfig(
            group_allowlist=["cid-allowed"],
            group_overrides={"cid-other": GroupOverride()},
        )
    )
    assert (await gate.evaluate(_msg("cid-other"))).allowed
    d = await gate.evaluate(_msg("cid-unknown"))
    assert not d.allowed and d.reason == RejectReason.GROUP_NOT_ALLOWED


@pytest.mark.asyncio
async def test_blocklist_never_overridden():
    gate = PolicyGate(
        PolicyConfig(
            group_blocklist=["cid-bad"],
            group_overrides={"cid-bad": GroupOverride(enabled=True)},
        )
    )
    d = await gate.evaluate(_msg("cid-bad"))
    assert not d.allowed and d.reason == RejectReason.GROUP_BLOCKED


@pytest.mark.asyncio
async def test_override_disable():
    gate = PolicyGate(PolicyConfig(group_overrides={"cid-1": GroupOverride(enabled=False)}))
    d = await gate.evaluate(_msg("cid-1"))
    assert not d.allowed and d.reason == RejectReason.GROUP_DISABLED


@pytest.mark.asyncio
async def test_override_require_mention():
    gate = PolicyGate(
        PolicyConfig(
            require_mention=True,
            group_overrides={
                "cid-1": GroupOverride(require_mention=False),
                "cid-2": GroupOverride(require_mention=True),
            },
        )
    )
    assert (await gate.evaluate(_msg("cid-1", in_at_list=False))).allowed
    d = await gate.evaluate(_msg("cid-2", in_at_list=False))
    assert not d.allowed and d.reason == RejectReason.NO_MENTION
    d = await gate.evaluate(_msg("cid-3", in_at_list=False))
    assert not d.allowed and d.reason == RejectReason.NO_MENTION


@pytest.mark.asyncio
async def test_override_allow_from():
    gate = PolicyGate(PolicyConfig(group_overrides={"cid-1": GroupOverride(allow_from=["staff-1"])}))
    assert (await gate.evaluate(_msg("cid-1", sender="staff-1"))).allowed
    d = await gate.evaluate(_msg("cid-1", sender="staff-2"))
    assert not d.allowed and d.reason == RejectReason.SENDER_NOT_ALLOWED


@pytest.mark.asyncio
async def test_override_block_from_beats_allow_from():
    gate = PolicyGate(
        PolicyConfig(
            group_overrides={
                "cid-1": GroupOverride(allow_from=["staff-1", "staff-2"], block_from=["staff-2"])
            }
        )
    )
    assert (await gate.evaluate(_msg("cid-1", sender="staff-1"))).allowed
    d = await gate.evaluate(_msg("cid-1", sender="staff-2"))
    assert not d.allowed and d.reason == RejectReason.SENDER_BLOCKED


@pytest.mark.asyncio
async def test_override_e2e_through_pipeline():
    """被覆盖禁用的群消息走完整管线：on_reject 收到 GROUP_DISABLED，处理器不触发。"""
    from dingtalk_channel_sdk.channel import DingTalkChannel

    ch = DingTalkChannel(
        client_id="id",
        client_secret="sec",
        policy_config=PolicyConfig(group_overrides={"cid-1": GroupOverride(enabled=False)}),
    )
    events = []

    async def on_reject(event):
        events.append(event)

    ch.on_reject(on_reject)
    called = []

    @ch.on_message
    async def handle(msg, reply):
        called.append(msg.msg_id)

    await ch.dispatch_for_test(
        {
            "headers": {"topic": "/v1.0/im/bot/messages/get", "messageId": "m-ov"},
            "data": '{"conversationId":"cid-1","conversationType":"2","msgId":"b-ov",'
            '"senderStaffId":"staff-1","isInAtList":true,"msgtype":"text",'
            '"text":{"content":"hi"}}',
        }
    )
    assert len(events) == 1
    assert events[0].reason == RejectReason.GROUP_DISABLED
    assert called == []
