"""normalize converters per-type 细测（对标 Go converters_*_test.go）。

逐类型断言文本/资源/提及的提取行为；信封层（@剥离/mentionAll/会话类型）见 test_message.py。
"""

from __future__ import annotations

from dingtalk_channel_sdk.normalize.message import parse_content


# ── text / markdown ──


def test_convert_text():
    assert parse_content("text", {"content": "hello"}, []) == ("hello", [], [])
    assert parse_content("text", {}, [])[0] == ""


def test_convert_markdown():
    assert parse_content("markdown", {"text": "# 标题"}, [])[0] == "# 标题"
    assert parse_content("markdown", {}, [])[0] == ""


def test_convert_unknown_type_fallback():
    assert parse_content("futureType", {"content": "fallback"}, [])[0] == "fallback"


# ── richText ──


def test_convert_rich_text():
    content = {
        "richText": [
            {"type": "text", "text": "你好 "},
            {"type": "at", "atUserIds": ["u1", "u2"], "atMobiles": ["13800000000"]},
            {"type": "text", "text": "看这张图"},
        ]
    }
    text, _, mentions = parse_content("richText", content, [])
    assert text == "你好 看这张图"
    assert mentions == [
        {"userId": "u1"},
        {"userId": "u2"},
        {"userId": "13800000000", "name": "13800000000"},
    ]


def test_convert_rich_text_empty():
    text, _, mentions = parse_content("richText", {}, [])
    assert text == "" and mentions == []


# ── 媒体：picture / file / audio / video ──


def test_convert_picture():
    text, resources, _ = parse_content("picture", {"downloadCode": "dc-1"}, [])
    assert text == ""
    assert resources == [{"type": "image", "downloadCode": "dc-1"}]


def test_convert_file():
    text, resources, _ = parse_content("file", {"downloadCode": "dc-2", "fileName": "report.pdf"}, [])
    assert text == "[文件: report.pdf]"
    assert resources[0]["type"] == "file" and resources[0]["fileName"] == "report.pdf"
    # 文件名缺失回退
    text, _, _ = parse_content("file", {"downloadCode": "dc-3"}, [])
    assert text == "[文件]"


def test_convert_audio():
    # 有识别文本：优先透出
    text, resources, _ = parse_content("audio", {"downloadCode": "dc-4", "recognition": "开会要点"}, [])
    assert text == "开会要点"
    assert resources[0]["recognition"] == "开会要点"
    # 无识别文本：占位
    text, _, _ = parse_content("audio", {"downloadCode": "dc-5"}, [])
    assert text == "[语音消息]"


def test_convert_video():
    text, resources, _ = parse_content("video", {"downloadCode": "dc-6", "fileName": "clip.mp4"}, [])
    assert text == "[视频]"
    assert resources[0]["type"] == "video" and resources[0]["fileName"] == "clip.mp4"


# ── 卡片：actionCard / interactiveCard ──


def test_convert_action_card():
    content = {
        "title": "验收",
        "text": "请确认",
        "actionUrlItemList": [{"actionUrl": "https://a"}, {"actionUrl": "https://b"}],
    }
    text, _, _ = parse_content("actionCard", content, [])
    assert text == "验收\n\n请确认\n\n操作链接：\n- https://a\n- https://b"
    # 单链接 / 空卡片
    assert parse_content("actionCard", {"text": "t", "actionUrlItemList": [{"actionUrl": "https://x"}]}, [])[0] \
        == "t\n\n操作链接：https://x"
    assert parse_content("actionCard", {}, [])[0] == "[actionCard消息]"


def test_convert_interactive_card():
    assert parse_content("interactiveCard", {"biz_custom_action_url": "https://jump"}, [])[0] \
        == "收到交互式卡片链接：https://jump"
    assert parse_content("interactiveCard", {}, [])[0] == "[interactiveCard消息]"


# ── reply（含引用摘要矩阵） ──


def test_convert_reply_text_quote():
    content = {"text": "这是我的回复", "repliedMsg": {"msgType": "text", "content": "{\"text\": \"被引用的话\"}"}}
    text, _, _ = parse_content("reply", content, [])
    assert text == "这是我的回复\n[引用] 被引用的话"


def test_convert_reply_quote_matrix():
    cases = [
        ({"msgType": "audio", "content": "{\"recognition\": \"语音内容\"}"}, "[引用] 语音内容"),
        ({"msgType": "audio", "content": "{}"}, "[引用] [语音消息]"),
        ({"msgType": "file", "content": "{\"fileName\": \"a.zip\"}"}, "[引用] [文件: a.zip]"),
        ({"msgType": "video", "content": "{}"}, "[引用] [视频]"),
        ({"msgType": "markdown", "content": "{\"text\": \"引用md\"}"}, "[引用] 引用md"),
        ({"msgType": "superMsg", "content": "{}"}, "[引用] [superMsg消息]"),
    ]
    # 非法 JSON content → 按空对象处理 → 摘要为空 → 仅正文（既有行为）
    text, _, _ = parse_content("reply", {"text": "r", "repliedMsg": {"msgType": "text", "content": "不是json"}}, [])
    assert text == "r"
    for replied, want in cases:
        text, _, _ = parse_content("reply", {"text": "r", "repliedMsg": replied}, [])
        assert text == f"r\n{want}", f"replied={replied} text={text!r} want={want!r}"


def test_convert_reply_body_only():
    assert parse_content("reply", {"text": "只有正文"}, [])[0] == "只有正文"
    assert parse_content("reply", {}, [])[0] == "[引用消息]"


# ── richText 附件资源（对齐 lark channel-sdk 富文本附件区）──


def test_convert_rich_text_resources():
    content = {
        "richText": [
            {"type": "text", "text": "图1 "},
            {"type": "picture", "picture": "dc-1"},
            {"type": "picture", "picture": "dc-1"},
            {"type": "picture", "picture": "dc-2"},
            {"type": "file", "downloadCode": "dc-3", "fileName": "report.pdf"},
            {"type": "text", "text": " 图2"},
        ]
    }
    text, resources, _ = parse_content("richText", content, [])
    assert text == "图1  图2"
    assert resources == [
        {"type": "image", "downloadCode": "dc-1"},
        {"type": "image", "downloadCode": "dc-2"},
        {"type": "file", "downloadCode": "dc-3", "fileName": "report.pdf"},
    ]


def test_convert_rich_text_resources_dirty_data():
    content = {
        "richText": [
            {"type": "picture", "picture": 123},
            {"type": "picture", "picture": ""},
            {"type": "picture"},
            {"type": "file", "downloadCode": 42},
            {"type": "text", "text": "ok"},
            "junk-segment",
        ]
    }
    text, resources, _ = parse_content("richText", content, [])
    assert text == "ok"
    assert resources == []
