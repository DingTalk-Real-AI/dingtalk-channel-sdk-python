"""主动发消息（不依赖入站消息）。

API 形态与 dws 源码验证一致：
- 单聊：POST /v1.0/robot/oToMessages/batchSend {robotCode, userIds, msgKey, msgParam}
- 群聊：POST /v1.0/robot/groupMessages/send {robotCode, openConversationId, msgKey, msgParam, atUserIds?, atOpendingtalkIds?, isAtAll?}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from .card import CardClient
from .config import Config
from .outbound.retry import RetryOptions, retry


@dataclass
class SendTarget:
    """userId（单聊）与 conversation_id（群聊 openConversationId）二选一。"""

    user_id: str = ""
    conversation_id: str = ""
    at_user_ids: List[str] = field(default_factory=list)
    at_dingtalk_ids: List[str] = field(default_factory=list)
    at_all: bool = False


class ProactiveSender:
    def __init__(self, cfg: Config, cards: CardClient):
        self.cfg = cfg
        self.cards = cards  # 复用 token/QPS 限流/重试

    async def send(self, target: SendTarget, msg_key: str, msg_param: dict) -> None:
        # 出站钩子 + 统一页脚（OutboundConfig）
        out = self.cfg.outbound
        if out is not None:
            if out.footer:
                param = dict(msg_param)
                if msg_key == "sampleText" and isinstance(param.get("content"), str):
                    param["content"] = param["content"] + "\n\n" + out.footer
                elif msg_key == "sampleMarkdown" and isinstance(param.get("text"), str):
                    param["text"] = param["text"] + "\n\n---\n" + out.footer
                msg_param = param
            if out.hooks.before_send is not None:
                replaced = out.hooks.before_send("send", target.user_id or target.conversation_id or "", msg_param)
                if replaced is not None:
                    msg_param = replaced

        target_id = target.user_id or target.conversation_id or ""
        try:
            await self._send_inner(target, msg_key, msg_param)
            if out is not None and out.hooks.after_send is not None:
                out.hooks.after_send("send", target_id, True, None)
        except Exception as err:  # noqa: BLE001
            if out is not None and out.hooks.after_send is not None:
                out.hooks.after_send("send", target_id, False, str(err))
            raise

    async def _send_inner(self, target: SendTarget, msg_key: str, msg_param: dict) -> None:
        param = json.dumps(msg_param, ensure_ascii=False)
        if bool(target.user_id) == bool(target.conversation_id):
            raise ValueError("SendTarget: 恰好设置 user_id（单聊）或 conversation_id（群聊）之一")
        retry_cfg = self.cfg.outbound.retry if self.cfg.outbound is not None else None
        opts = RetryOptions(
            max_attempts=retry_cfg.max_attempts if retry_cfg else 3,
            base_delay_s=retry_cfg.base_delay_s if retry_cfg else 0.5,
        )
        if target.user_id:
            await retry(lambda: self.cards._call("POST", "/v1.0/robot/oToMessages/batchSend", {
                "robotCode": self.cfg.client_id,
                "userIds": [target.user_id],
                "msgKey": msg_key,
                "msgParam": param,
            }), opts)
            return
        body = {
            "robotCode": self.cfg.client_id,
            "openConversationId": target.conversation_id,
            "msgKey": msg_key,
            "msgParam": param,
        }
        if target.at_user_ids:
            body["atUserIds"] = target.at_user_ids
        if target.at_dingtalk_ids:
            body["atOpendingtalkIds"] = target.at_dingtalk_ids
        if target.at_all:
            body["isAtAll"] = True
        await retry(lambda: self.cards._call("POST", "/v1.0/robot/groupMessages/send", body), opts)

    async def send_text(self, target: SendTarget, content: str) -> None:
        await self.send(target, "sampleText", {"content": content})

    async def send_markdown(self, target: SendTarget, title: str, text: str) -> None:
        await self.send(target, "sampleMarkdown", {"title": title or _first_line_title(text), "text": text})

    async def send_video(self, target: SendTarget, raw_video_media_id: str, raw_pic_media_id: str = "", duration_ms: int = 60000) -> None:
        """视频消息（sampleVideo，对齐官方 connector sendVideoProactive）。mediaId 均为带 @ 的 RawMediaID。"""
        await self.send(target, "sampleVideo", {
            "duration": str(duration_ms),
            "videoMediaId": raw_video_media_id,
            "videoType": "mp4",
            "picMediaId": raw_pic_media_id,
        })

    async def send_audio(self, target: SendTarget, raw_media_id: str, duration_ms: int = 60000) -> None:
        """音频消息（sampleAudio，对齐官方 connector sendAudioProactive）。"""
        await self.send(target, "sampleAudio", {"mediaId": raw_media_id, "duration": str(duration_ms)})

    async def send_image(self, target: SendTarget, image_url: str) -> None:
        await self.send(target, "sampleImageMsg", {"photoURL": image_url})


def _first_line_title(text: str) -> str:
    for line in str(text or "").split("\n"):
        t = line.lstrip("#*-> \t")
        if t:
            return t[:20]
    return "Message"
