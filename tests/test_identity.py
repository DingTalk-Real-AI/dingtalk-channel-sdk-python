"""发送者身份解析与策略身份字段测试。"""

from dingtalk_channel_sdk.identity import resolve_sender_identity, sender_matches
from dingtalk_channel_sdk.safety.policy import PolicyConfig, PolicyGate
from dingtalk_channel_sdk.types import IncomingMessage


def make_msg(sender_id="u-1", staff_id="staff-1"):
    return IncomingMessage(
        conversation_id="c1", conversation_type="group", sender_id=sender_id,
        sender_staff_id=staff_id, msg_id="m1", msg_type="text", text="hi", create_at=1, is_in_at_list=True,
    )


def test_resolve_priority():
    msg = make_msg(sender_id="u-1", staff_id="staff-1")
    assert resolve_sender_identity(msg) == "staff-1"          # staffId 优先
    assert resolve_sender_identity(msg, ("sender_id",)) == "u-1"  # 指定字段
    assert resolve_sender_identity(make_msg("", "")) == ""     # 全空


def test_sender_matches_default_sender_id_only():
    msg = make_msg(sender_id="u-1", staff_id="staff-1")
    assert sender_matches(msg, ["u-1"])            # 默认仅 sender_id
    assert not sender_matches(msg, ["staff-1"])    # 默认不匹配 staffId
    assert not sender_matches(msg, [])             # 空名单


def test_sender_matches_cross_fields():
    msg = make_msg(sender_id="u-1", staff_id="staff-1")
    assert sender_matches(msg, ["staff-1"], ("sender_staff_id", "sender_id"))
    assert sender_matches(msg, ["u-1"], ("sender_staff_id", "sender_id"))


async def test_policy_admin_via_staff_id_field():
    """名单存 staffId，配置身份字段后管理员命中绕过 DM 禁用。"""
    cfg = PolicyConfig(
        dm_mode="disabled",
        admins=["staff-1"],
        sender_identity_fields=["sender_staff_id"],
    )
    gate = PolicyGate(cfg)
    msg = make_msg()
    msg.conversation_type = "dm"
    decision = await gate.evaluate(msg)
    assert decision.allowed


async def test_policy_deny_cross_field():
    cfg = PolicyConfig(
        deny_from=["staff-9"],
        sender_identity_fields=["sender_staff_id", "sender_id"],
    )
    gate = PolicyGate(cfg)
    msg = make_msg(sender_id="u-1", staff_id="staff-9")
    decision = await gate.evaluate(msg)
    assert not decision.allowed


async def test_policy_default_fields_backward_compatible():
    """未配置身份字段时维持旧行为：仅 sender_id 参与全局名单。"""
    cfg = PolicyConfig(deny_from=["staff-9"])
    gate = PolicyGate(cfg)
    msg = make_msg(sender_id="u-1", staff_id="staff-9")
    assert (await gate.evaluate(msg)).allowed  # staffId 不在默认匹配范围
