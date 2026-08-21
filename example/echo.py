"""演示 E1–E3：流式打字机回复（假 LLM 每 15ms 吐一个字符）。

运行：DD_CLIENT_ID=xxx DD_CLIENT_SECRET=xxx python example/echo.py
"""

import asyncio
import os

from dingtalk_channel_sdk import DingTalkChannel


async def main() -> None:
    client_id = os.environ.get("DD_CLIENT_ID", "")
    client_secret = os.environ.get("DD_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise SystemExit("需要环境变量 DD_CLIENT_ID / DD_CLIENT_SECRET")

    ch = DingTalkChannel(client_id=client_id, client_secret=client_secret)

    @ch.on_message
    async def handle(msg, reply):
        s = await reply.stream()  # E1：立即出"输入中"卡片

        answer = (
            f"**收到：{msg.text}**\n\n"
            f"- 单聊/群聊: `{msg.conversation_type}`\n"
            f"- 发送者: {msg.sender_nick}\n"
            '```python\nprint("hello dingtalk channel")\n```'
        )
        for token in answer:  # 假流式
            await s.append(token)
            await asyncio.sleep(0.015)
        await s.finish(answer)  # E3：终帧 + FINISHED

    @ch.on_card_action
    async def on_card(action, reply):
        await reply.text("按钮被点击")

    print("channel started, waiting for messages...")
    try:
        await ch.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        ch.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
