"""真实钉钉联调一键验证（live check）。

用法：
    DD_CLIENT_ID=ding... DD_CLIENT_SECRET=... [DD_UPLOAD_FILE=/path/img.png] \
        python example/livecheck.py

流程：① 配置加载 ② Stream 连接 ③ 收消息 ④ 文本回复（token 链路）
⑤ 流式 AI 卡片全生命周期（E1–E3）⑥（可选）媒体上传。每步 PASS/FAIL。
"""

from __future__ import annotations

import asyncio
import os
import sys

from dingtalk_channel_sdk import DingTalkChannel

STEPS = 0


def passed(name: str) -> None:
    global STEPS
    STEPS += 1
    print(f"✅ PASS {name}")


def failed(name: str, err: Exception) -> None:
    print(f"❌ FAIL {name}: {err}")
    print(f"完成 {STEPS} 步后失败。凭据/应用配置请核对：https://open-dev.dingtalk.com")
    sys.exit(1)


async def main() -> None:
    client_id = os.environ.get("DD_CLIENT_ID", "")
    client_secret = os.environ.get("DD_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("需要环境变量 DD_CLIENT_ID / DD_CLIENT_SECRET")
        sys.exit(2)

    print("== dingtalk-channel-sdk-python livecheck ==")
    passed(f"config loaded (clientId={client_id[:8]}...)")

    ch = DingTalkChannel(client_id=client_id, client_secret=client_secret)

    @ch.on_message
    async def handle(msg, reply):
        try:
            print(f'   收到消息: "{msg.text}" from {msg.sender_nick} ({msg.conversation_type})')
            passed(f"stream message received (msgId={msg.msg_id})")

            await reply.text("livecheck: text reply ok")  # 隐含新版 token
            passed("text reply via sessionWebhook (token path verified)")

            s = await reply.stream()
            if not s.card_delivered:
                failed("E1 卡片创建/投递（已降级文本——查 deliver 载荷/权限）", RuntimeError("card not delivered"))
            passed("AI card created & delivered (E1)")

            content = (
                "# livecheck 流式验证\n\n"
                f"- 单聊/群聊: `{msg.conversation_type}`\n"
                f"- 来自: {msg.sender_nick}\n\n"
            )

            upload_file = os.environ.get("DD_UPLOAD_FILE", "")
            if upload_file:
                mt = "image" if upload_file.lower().endswith((".png", ".jpg", ".jpeg")) else "file"
                with open(upload_file, "rb") as f:
                    data = f.read()
                media = await reply.upload_media(mt, os.path.basename(upload_file), data)
                passed(f"media upload, mediaId={media['mediaId']}")
                content += f"![uploaded]({media['downloadUrl']})\n" if mt == "image" else f"- 上传文件 mediaId: `{media['mediaId']}`\n"

            for token in content:
                await s.append(token)
                await asyncio.sleep(0.015)
            await s.finish(content)
            passed("AI card streaming lifecycle (E2/E3)")

            print(f"\n🎉 全部 {STEPS} 步通过：真实钉钉联调验证成功。")
            asyncio.get_running_loop().call_later(2, ch.close)
        except Exception as e:  # noqa: BLE001
            failed("livecheck", e)

    print("waiting for a message — 在钉钉里给机器人发一句话...")
    await ch.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
