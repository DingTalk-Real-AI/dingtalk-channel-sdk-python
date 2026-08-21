"""真机全流程测试：被动回复 + 媒体上传 + 主动发消息。

用法：DD_CLIENT_ID=... DD_CLIENT_SECRET=... python example/fullflow.py
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys

from dingtalk_channel_sdk import DingTalkChannel, SendTarget

STEPS = 0


def passed(name: str) -> None:
    global STEPS
    STEPS += 1
    print(f"✅ PASS {name}")


def failed(name: str, err: Exception) -> None:
    print(f"❌ FAIL {name}: {err}")
    sys.exit(1)


# 8x8 红色 PNG（最小合法图片）
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAFklEQVR4nGP8z8Dwn4GBgYGJAQwAHxcCAmXfLkIAAAAASUVORK5CYII="
)


async def main() -> None:
    ch = DingTalkChannel(
        client_id=os.environ.get("DD_CLIENT_ID", ""),
        client_secret=os.environ.get("DD_CLIENT_SECRET", ""),
    )

    @ch.on_message
    async def handle(msg, reply):
        try:
            print(f'   收到消息: "{msg.text}" from {msg.sender_nick} ({msg.conversation_type})')
            passed(f"① stream 消息接收 (msgId={msg.msg_id})")

            await reply.text("fullflow: 文本回复 ok")
            passed("② webhook 文本回复")

            s = await reply.stream()
            if not s.card_delivered:
                failed("③a 卡片创建/投递（降级）", RuntimeError("card not delivered"))
            passed("③a 卡片创建+投递 (E1)")

            media = await reply.upload_media("image", "fullflow.png", TINY_PNG, "image/png")
            passed(f"③d 媒体上传 mediaId={media['mediaId']}")

            content = (
                "# fullflow 全流程\n\n"
                f"- 会话: `{msg.conversation_type}`\n"
                f"- 来自: {msg.sender_nick}\n\n下面是刚上传的图片：\n\n"
            )
            for t in content:
                await s.append(t)
                await asyncio.sleep(0.01)
            await s.finish(content + f"![uploaded]({media['downloadUrl']})\n")
            passed("③e 流式卡片+图片内嵌收口 (E2/E3/E9)")

            await ch.send_text(SendTarget(user_id=msg.sender_staff_id), "fullflow: 主动单聊文本 (SendText/batchSend)")
            passed("④a 主动单聊 SendText (batchSend)")
            await ch.send_markdown(SendTarget(user_id=msg.sender_staff_id), "主动通知", "**fullflow** 主动单聊 Markdown")
            passed("④b 主动单聊 SendMarkdown")

            if msg.conversation_type == "group":
                await ch.send_markdown(
                    SendTarget(conversation_id=msg.conversation_id, at_user_ids=[msg.sender_staff_id]),
                    "群通知",
                    "@你 fullflow 群发测试 (groupMessages/send)",
                )
                passed("⑤ 群发 SendMarkdown+@")
            else:
                print("   （单聊会话：⑤ 群发@用例跳过——把机器人拉进群 @它 再跑一次可测）")

            print(f"\n🎉 全流程完成：{STEPS} 步通过（主动消息请看钉钉会话）。")
            asyncio.get_running_loop().call_later(2, ch.close)
        except Exception as e:  # noqa: BLE001
            failed("fullflow", e)

    print("== fullflow(python)：给机器人发一条消息 ==")
    await ch.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
