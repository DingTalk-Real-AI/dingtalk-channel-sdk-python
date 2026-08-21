"""测试 PolicyGate"""

import pytest
from dingtalk_channel_sdk.safety.policy_gate import PolicyGate
from dingtalk_channel_sdk.types import (
    PolicyConfig,
    GroupOverride,
    IncomingMessage,
    BotIdentity,
    RejectReason,
)


async def test_policy_gate_admin_bypass():
    """测试管理员绕过"""
    cfg = PolicyConfig(
        admins=["admin123"],
        dm_mode="disabled",  # DM 禁用
    )
    gate = PolicyGate(cfg)
    
    # 管理员在 DM 禁用模式下仍可通过
    msg = IncomingMessage(
        conversation_id="chat1",
        conversation_type="dm",
        sender_id="admin123",
        sender_staff_id="admin123",
        msg_id="msg1",
        msg_type="text",
        text="test",
        create_at=123456,
    )
    
    decision = await gate.evaluate(msg)
    assert decision.allowed


async def test_policy_gate_global_sender_control():
    """测试全局发送者控制"""
    cfg = PolicyConfig(
        deny_from=["blocked_user"],
        allow_from=["allowed_user1", "allowed_user2"],
    )
    gate = PolicyGate(cfg)
    
    # 黑名单用户被拒绝
    msg1 = IncomingMessage(
        conversation_id="chat1",
        conversation_type="dm",
        sender_id="blocked_user",
        sender_staff_id="blocked_user",
        msg_id="msg1",
        msg_type="text",
        text="test",
        create_at=123456,
    )
    decision1 = await gate.evaluate(msg1)
    assert not decision1.allowed
    assert decision1.reason == RejectReason.SENDER_DENIED
    
    # 白名单用户通过
    msg2 = IncomingMessage(
        conversation_id="chat1",
        conversation_type="dm",
        sender_id="allowed_user1",
        sender_staff_id="allowed_user1",
        msg_id="msg2",
        msg_type="text",
        text="test",
        create_at=123456,
    )
    decision2 = await gate.evaluate(msg2)
    assert decision2.allowed
    
    # 不在白名单的用户被拒绝
    msg3 = IncomingMessage(
        conversation_id="chat1",
        conversation_type="dm",
        sender_id="random_user",
        sender_staff_id="random_user",
        msg_id="msg3",
        msg_type="text",
        text="test",
        create_at=123456,
    )
    decision3 = await gate.evaluate(msg3)
    assert not decision3.allowed
    assert decision3.reason == RejectReason.SENDER_NOT_ALLOWED


async def test_policy_gate_dm_mode():
    """测试 DM 模式"""
    # 禁用模式
    cfg1 = PolicyConfig(dm_mode="disabled")
    gate1 = PolicyGate(cfg1)
    
    msg = IncomingMessage(
        conversation_id="chat1",
        conversation_type="dm",
        sender_id="user1",
        sender_staff_id="staff1",
        msg_id="msg1",
        msg_type="text",
        text="test",
        create_at=123456,
    )
    
    decision1 = await gate1.evaluate(msg)
    assert not decision1.allowed
    assert decision1.reason == RejectReason.DM_DISABLED
    
    # 白名单模式
    cfg2 = PolicyConfig(
        dm_mode="allowlist",
        dm_allowlist=["user1"],
    )
    gate2 = PolicyGate(cfg2)
    
    decision2 = await gate2.evaluate(msg)
    assert decision2.allowed
    
    # 黑名单模式
    cfg3 = PolicyConfig(
        dm_mode="blocklist",
        dm_blocklist=["user1"],
    )
    gate3 = PolicyGate(cfg3)
    
    decision3 = await gate3.evaluate(msg)
    assert not decision3.allowed
    assert decision3.reason == RejectReason.DM_BLOCKED


async def test_policy_gate_group_allowlist():
    """测试群组白名单"""
    cfg = PolicyConfig(
        group_allowlist=["group1", "group2"],
    )
    gate = PolicyGate(cfg)
    
    # 在白名单中
    msg1 = IncomingMessage(
        conversation_id="group1",
        conversation_type="group",
        sender_id="user1",
        sender_staff_id="staff1",
        msg_id="msg1",
        msg_type="text",
        text="test",
        create_at=123456,
        is_in_at_list=True,
    )
    decision1 = await gate.evaluate(msg1)
    assert decision1.allowed
    
    # 不在白名单中
    msg2 = IncomingMessage(
        conversation_id="group3",
        conversation_type="group",
        sender_id="user1",
        sender_staff_id="staff1",
        msg_id="msg2",
        msg_type="text",
        text="test",
        create_at=123456,
        is_in_at_list=True,
    )
    decision2 = await gate.evaluate(msg2)
    assert not decision2.allowed
    assert decision2.reason == RejectReason.GROUP_NOT_ALLOWED


async def test_policy_gate_require_mention():
    """测试 @机器人要求"""
    cfg = PolicyConfig(
        require_mention=True,
    )
    gate = PolicyGate(cfg)
    
    # 没有 @机器人
    msg1 = IncomingMessage(
        conversation_id="group1",
        conversation_type="group",
        sender_id="user1",
        sender_staff_id="staff1",
        msg_id="msg1",
        msg_type="text",
        text="test",
        create_at=123456,
        is_in_at_list=False,
    )
    decision1 = await gate.evaluate(msg1)
    assert not decision1.allowed
    assert decision1.reason == RejectReason.NO_MENTION
    
    # 有 @机器人
    msg2 = IncomingMessage(
        conversation_id="group1",
        conversation_type="group",
        sender_id="user1",
        sender_staff_id="staff1",
        msg_id="msg2",
        msg_type="text",
        text="test",
        create_at=123456,
        is_in_at_list=True,
    )
    decision2 = await gate.evaluate(msg2)
    assert decision2.allowed


async def test_policy_gate_mention_all():
    """测试 @all 响应"""
    cfg = PolicyConfig(
        respond_to_mention_all=False,
        require_mention=False,
    )
    gate = PolicyGate(cfg)
    
    # @all 消息
    msg = IncomingMessage(
        conversation_id="group1",
        conversation_type="group",
        sender_id="user1",
        sender_staff_id="staff1",
        msg_id="msg1",
        msg_type="text",
        text="test",
        create_at=123456,
        mention_all=True,
    )
    
    decision = await gate.evaluate(msg)
    assert not decision.allowed
    assert decision.reason == RejectReason.MENTION_ALL_BLOCKED


async def test_policy_gate_group_override():
    """测试群组覆盖"""
    cfg = PolicyConfig(
        require_mention=True,
        group_overrides={
            "special_group": GroupOverride(
                require_mention=False,
                allow_from=["user1"],
            ),
        },
    )
    gate = PolicyGate(cfg)
    
    # 特殊群组：不要求 @，但有发送者白名单
    msg = IncomingMessage(
        conversation_id="special_group",
        conversation_type="group",
        sender_id="user1",
        sender_staff_id="user1",
        msg_id="msg1",
        msg_type="text",
        text="test",
        create_at=123456,
        is_in_at_list=False,  # 没有 @
    )
    
    decision = await gate.evaluate(msg)
    assert decision.allowed


async def test_policy_gate_bot_identity():
    """测试 Bot 身份管理"""
    cfg = PolicyConfig()
    gate = PolicyGate(cfg)
    
    # 初始没有 bot
    assert gate.get_bot_identity() is None
    
    # 设置 bot
    bot = BotIdentity(robot_code="robot123", robot_name="TestBot")
    gate.set_bot_identity(bot)
    
    # 获取 bot
    retrieved = gate.get_bot_identity()
    assert retrieved is not None
    assert retrieved.robot_code == "robot123"
    assert retrieved.robot_name == "TestBot"


async def test_policy_gate_update_config():
    """测试配置更新"""
    cfg1 = PolicyConfig(dm_mode="disabled")
    gate = PolicyGate(cfg1)
    
    msg = IncomingMessage(
        conversation_id="chat1",
        conversation_type="dm",
        sender_id="user1",
        sender_staff_id="staff1",
        msg_id="msg1",
        msg_type="text",
        text="test",
        create_at=123456,
    )
    
    # 最初被拒绝
    decision1 = await gate.evaluate(msg)
    assert not decision1.allowed
    
    # 更新配置
    cfg2 = PolicyConfig(dm_mode="open")
    await gate.update_config(cfg2)
    
    # 现在允许
    decision2 = await gate.evaluate(msg)
    assert decision2.allowed
