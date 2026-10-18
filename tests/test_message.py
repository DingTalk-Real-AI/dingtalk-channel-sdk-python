"""消息归一化单测：richText / picture / file / audio / video / actionCard / unknown type。"""

from __future__ import annotations

import json

from dingtalk_channel_sdk.normalize.message import normalize_incoming


def test_normalize_richtext():
    data = json.dumps({
        "conversationId": "cid-1",
        "conversationType": "2",
        "msgId": "rt-1",
        "senderStaffId": "staff-1",
        "senderNick": "John",
        "sessionWebhook": "",
        "isInAtList": True,
        "msgtype": "richText",
        "content": {
            "richText": [
                {"type": "text", "text": "hello "},
                {"type": "text", "text": "world"},
                {"type": "at", "atUserIds": ["staff-2"]}
            ]
        }
    })
    msg = normalize_incoming(data)
    assert msg.text == "hello world"
    assert any(m.get("userId") == "staff-2" for m in msg.mentions)


def test_normalize_richtext_with_attachment_without_text_type():
    """钉钉带附件的 richText 消息：顶层无 text 字段，且 richText 文本段无 type 字段。"""
    # 群聊：@机器人 + 文本 + 图片
    data_group = json.dumps({
        "conversationId": "cid-1",
        "conversationType": "2",
        "msgId": "rt-2",
        "senderStaffId": "staff-1",
        "senderNick": "John",
        "sessionWebhook": "",
        "isInAtList": True,
        "msgtype": "richText",
        "content": {
            "richText": [
                {"text": "@机器人 请帮我看看这张图"},
                {"downloadCode": "dc-pic-1", "type": "picture", "pictureDownloadCode": "pdc-1"},
            ]
        },
    })
    msg_group = normalize_incoming(data_group)
    assert msg_group.text == "请帮我看看这张图"
    assert msg_group.resources == [{"type": "image", "downloadCode": "dc-pic-1"}]

    # 单聊真实样本：图片段在前 + {"text": "\n"} + {"text": "请解释下"}（均无 type 字段）
    data_dm = json.dumps({
        "senderPlatform": "Win",
        "conversationId": "cidXxxxxx=",
        "chatbotCorpId": "dingxxxxxx",
        "chatbotUserId": "$:LWCP_v1:$xXxxxx",
        "openThreadId": "cidXxxxxx=",
        "msgId": "msgsXxxxxxx",
        "senderNick": "Xxxxx",
        "isAdmin": True,
        "senderStaffId": "0000000000-1234334534",
        "sessionWebhookExpiredTime": 1792294060566,
        "createAt": 1792288660182,
        "content": {
            "richText": [
                {
                    "pictureDownloadCode": "EJiwxxxxx",
                    "downloadCode": "mIoxxxxx",
                    "type": "picture",
                },
                {
                    "text": "\n",
                },
                {
                    "text": "请解释下",
                },
            ]
        },
        "senderCorpId": "dingxxxxxx",
        "conversationType": "1",
        "senderId": "$:LWCP_v1:$Xxxxxx",
        "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=xxxx",
        "robotCode": "dingjgxxxxxxxxxxxxxxx",
        "msgtype": "richText",
    })
    msg_dm = normalize_incoming(data_dm)
    assert msg_dm.text == "请解释下"
    assert msg_dm.resources == [{"type": "image", "downloadCode": "mIoxxxxx"}]




def test_normalize_picture():
    data = json.dumps({
        "conversationId": "cid-1",
        "conversationType": "2",
        "msgId": "pic-1",
        "senderStaffId": "staff-1",
        "senderNick": "John",
        "sessionWebhook": "",
        "isInAtList": True,
        "msgtype": "picture",
        "content": {"downloadCode": "dc-abc123"}
    })
    msg = normalize_incoming(data)
    assert len(msg.resources) == 1
    assert msg.resources[0]["type"] == "image"
    assert msg.resources[0]["downloadCode"] == "dc-abc123"


def test_normalize_unknown_type():
    data = json.dumps({
        "conversationId": "cid-1",
        "conversationType": "2",
        "msgId": "unk-1",
        "senderStaffId": "staff-1",
        "senderNick": "John",
        "sessionWebhook": "",
        "isInAtList": True,
        "msgtype": "someNewType",
        "content": {"content": "fallback text"}
    })
    msg = normalize_incoming(data)
    assert msg.text == "fallback text"


def test_normalize_file():
    data = json.dumps({
        "conversationId": "cid-1",
        "conversationType": "2",
        "msgId": "file-1",
        "senderStaffId": "staff-1",
        "senderNick": "John",
        "sessionWebhook": "",
        "isInAtList": True,
        "msgtype": "file",
        "content": {"downloadCode": "dc-file", "fileName": "report.pdf"}
    })
    msg = normalize_incoming(data)
    assert msg.text == "[文件: report.pdf]"
    assert len(msg.resources) == 1
    assert msg.resources[0]["type"] == "file"
    assert msg.resources[0]["downloadCode"] == "dc-file"
    assert msg.resources[0]["fileName"] == "report.pdf"


def test_normalize_audio():
    data = json.dumps({
        "conversationId": "cid-1",
        "conversationType": "2",
        "msgId": "audio-1",
        "senderStaffId": "staff-1",
        "senderNick": "John",
        "sessionWebhook": "",
        "isInAtList": True,
        "msgtype": "audio",
        "content": {
            "downloadCode": "dc-audio",
            "recognition": "你好",
            "fileName": "voice.amr"
        }
    })
    msg = normalize_incoming(data)
    assert msg.text == "你好"
    assert len(msg.resources) == 1
    assert msg.resources[0]["type"] == "audio"
    assert msg.resources[0]["downloadCode"] == "dc-audio"
    assert msg.resources[0]["recognition"] == "你好"


def test_normalize_video():
    data = json.dumps({
        "conversationId": "cid-1",
        "conversationType": "2",
        "msgId": "video-1",
        "senderStaffId": "staff-1",
        "senderNick": "John",
        "sessionWebhook": "",
        "isInAtList": True,
        "msgtype": "video",
        "content": {"downloadCode": "dc-video", "fileName": "clip.mp4"}
    })
    msg = normalize_incoming(data)
    assert msg.text == "[视频]"
    assert len(msg.resources) == 1
    assert msg.resources[0]["type"] == "video"
    assert msg.resources[0]["downloadCode"] == "dc-video"


def test_normalize_action_card():
    data = json.dumps({
        "conversationId": "cid-1",
        "conversationType": "2",
        "msgId": "card-1",
        "senderStaffId": "staff-1",
        "senderNick": "John",
        "sessionWebhook": "",
        "isInAtList": True,
        "msgtype": "actionCard",
        "content": {
            "title": "会议通知",
            "text": "下午3点开会",
            "actionUrlItemList": [{"actionUrl": "https://example.com/join"}]
        }
    })
    msg = normalize_incoming(data)
    assert "会议通知" in msg.text
    assert "下午3点开会" in msg.text
