"""使用 DWS 的指定 Profile 发送示例卡片并完成原卡片，不需要机器人凭据。"""
import asyncio
import json
import os
from pathlib import Path
from dingtalk_channel_sdk import DwsA2UIClient


async def main():
    profile, recipient = os.environ.get("DWS_PROFILE"), os.environ.get("DWS_OPEN_DINGTALK_ID")
    if not profile or not recipient:
        raise RuntimeError("请设置 DWS_PROFILE 和 DWS_OPEN_DINGTALK_ID")
    client = DwsA2UIClient(command=[os.environ.get("DWS_BIN", "dws")], profile=profile)
    folder = Path(__file__).resolve().parent
    messages = json.loads((folder / "a2ui-card.json").read_text(encoding="utf-8"))
    result = await client.send_card({"open_dingtalk_id": recipient}, messages)
    if not result.biz_id:
        raise RuntimeError(result.update_warning)
    delta = json.loads((folder / "a2ui-update.json").read_text(encoding="utf-8"))
    await client.update_card(result.biz_id, delta, "FINISH")
    print(f"示例卡片已完成，bizId={result.biz_id}")


if __name__ == "__main__":
    asyncio.run(main())
