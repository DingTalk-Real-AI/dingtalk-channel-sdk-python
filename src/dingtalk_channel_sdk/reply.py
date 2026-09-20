"""Reply：sessionWebhook 回复 + AI 卡片流式（SPEC §4）。"""

from __future__ import annotations

import json
import time

from typing import Optional

from .card import CardClient, CardStreamer

_err_lim_last: dict = {}


def _error_cooldown_pass(key: str, cooldown_s: float) -> bool:
    import time as _t

    if not cooldown_s or cooldown_s <= 0:
        return True
    now = _t.monotonic()
    t = _err_lim_last.get(key)
    if t is not None and now - t < cooldown_s:
        return False
    _err_lim_last[key] = now
    return True
from .config import Config
from .httpx import http_json
from .outbound.retry import RetryOptions, retry
from .normalize.message import CONVERSATION_GROUP, IncomingMessage
from .token import TokenProvider


class Reply:
    def __init__(self, msg: IncomingMessage, cfg: Config, tokens: TokenProvider, cards: CardClient, oapi=None,
                 proactive=None):
        self.msg = msg
        self.cfg = cfg
        self.tokens = tokens
        self.cards = cards
        self.oapi = oapi
        # proactive 为 webhook 失效（过期/撤回）时的主动发送兜底，由 Channel 注入：
        # 群聊按 openConversationId 群发，单聊发给消息发送者
        self._proactive = proactive

    @staticmethod
    def _chunk_text(text: str, limit: int) -> list:
        """超长文本按 newline 边界切分。"""
        runes = list(text)
        if limit <= 0 or len(runes) <= limit:
            return [text]
        chunks = []
        rest = runes
        while rest:
            if len(rest) <= limit:
                chunks.append("".join(rest))
                break
            cut = -1
            for i in range(limit, limit // 2 - 1, -1):
                if rest[i] == "\n":
                    cut = i + 1
                    break
            if cut <= 0:
                cut = limit
            chunks.append("".join(rest[:cut]))
            rest = rest[cut:]
        return chunks

    def _apply_outbound(self, msg_key: str, msg_param: dict) -> dict:
        """出站钩子 + 统一页脚（OutboundConfig）：before_send 可改写 payload，footer 追加到文本。"""
        out = self.cfg.outbound
        if out is None:
            return msg_param
        param = msg_param
        if out.footer:
            param = dict(msg_param)
            if "content" in param and isinstance(param["content"], str) and msg_key == "sampleText":
                param["content"] = param["content"] + "\n\n" + out.footer
            elif "text" in param and isinstance(param["text"], str) and msg_key == "sampleMarkdown":
                param["text"] = param["text"] + "\n\n---\n" + out.footer
        if out.hooks.before_send is not None:
            replaced = out.hooks.before_send("reply", self.msg.conversation_id or "", param)
            if replaced is not None:
                param = replaced
        return param

    async def _webhook(self, msg_key: str, msg_param: dict) -> None:
        msg_param = self._apply_outbound(msg_key, msg_param)

        # webhook 已过期（sessionWebhook 有时效）或缺失：直接走主动发送兜底
        expired = self.msg.webhook_expired_at > 0 and time.time() * 1000 > self.msg.webhook_expired_at
        if (not self.msg.session_webhook or expired) and self._proactive is not None:
            self.cfg.log("reply webhook unavailable (missing/expired), falling back to proactive send")
            await self._proactive(self.msg, msg_key, msg_param)
            return

        limit = self.cfg.text_chunk_limit or 0
        if limit > 0:
            content = msg_param.get("content") if isinstance(msg_param, dict) else None
            text = msg_param.get("text") if isinstance(msg_param, dict) else None
            if isinstance(content, str) and len(content) > limit:
                for c in self._chunk_text(content, limit):
                    await self._deliver_once(msg_key, {"content": c})
                return
            if isinstance(text, str) and len(text) > limit:
                for t in self._chunk_text(text, limit):
                    await self._deliver_once(msg_key, {"title": msg_param.get("title"), "text": t})
                return
        await self._deliver_once(msg_key, msg_param)

    async def _deliver_once(self, msg_key: str, msg_param: dict) -> None:
        """单次投递：webhook 优先；目标失效（撤回/过期 404）时转主动发送兜底。"""
        try:
            await self._webhook_once(msg_key, msg_param)
        except Exception as err:  # noqa: BLE001
            from .errors import is_reply_target_gone

            if self._proactive is not None and is_reply_target_gone(err):
                self.cfg.log(f"reply webhook target gone ({err}), falling back to proactive send")
                await self._proactive(self.msg, msg_key, msg_param)
                return
            raise

    async def _webhook_once(self, msg_key: str, msg_param: dict) -> None:
        if not self.msg.session_webhook:
            raise RuntimeError("reply: session_webhook missing")
        out = self.cfg.outbound
        try:
            token = await self.tokens.get()

            async def _do_send() -> None:
                await http_json(
                    "POST",
                    self.msg.session_webhook,
                    {
                        "Content-Type": "application/json",
                        "x-acs-dingtalk-access-token": token,
                    },
                    {"msgKey": msg_key, "msgParam": json.dumps(msg_param)},  # 官方要求字符串化 JSON
                )

            # 出站重试：仅可重试错误（限流/超时/未知）指数退避；格式错误立即失败
            retry_cfg = out.retry if out is not None else None
            opts = RetryOptions(
                max_attempts=retry_cfg.max_attempts if retry_cfg else 3,
                base_delay_s=retry_cfg.base_delay_s if retry_cfg else 0.5,
            )
            await retry(_do_send, opts)
            if out is not None and out.hooks.after_send is not None:
                out.hooks.after_send("reply", self.msg.conversation_id or "", True, None)
        except Exception as err:  # noqa: BLE001
            if out is not None and out.hooks.after_send is not None:
                out.hooks.after_send("reply", self.msg.conversation_id or "", False, str(err))
            raise

    async def text(self, content: str) -> None:
        await self._webhook("sampleText", {"content": content})

    async def markdown(self, title: str, text: str) -> None:
        await self._webhook("sampleMarkdown", {"title": title or _first_line_title(text), "text": text})

    async def image(self, image_url: str) -> None:
        await self._webhook("sampleImageMsg", {"photoURL": image_url})

    async def download_url(self, download_code: str, msg_id: str) -> str:
        """换取消息附件下载地址（E9）。"""
        token = await self.tokens.get()
        out = await http_json(
            "POST",
            f"{self.cfg.api_base}/v1.0/robot/messageFiles/download",
            {"x-acs-dingtalk-access-token": token},
            {"downloadCode": download_code, "robotCode": self.cfg.client_id},
        )
        return out.get("downloadUrl", "")

    async def upload_media(self, media_type: str, filename: str, data: bytes, content_type: str = "") -> dict:
        """上传媒体文件，返回 {"mediaId": ...}（E9）。media_type: image|file|video|voice。"""
        if self.oapi is None:
            raise RuntimeError("oapi client not wired")
        return await self.oapi.upload_media(media_type, filename, data, content_type)

    async def stream(self) -> CardStreamer:
        """立即创建并投递 AI 卡片（E1）。失败时返回带降级的 streamer（E4）。"""
        target = {
            "is_group": self.msg.conversation_type == CONVERSATION_GROUP,
            "conversation_id": self.msg.conversation_id,
            "user_id": self.msg.sender_staff_id or self.msg.sender_id,
            "robot_code": self.cfg.client_id,
        }
        def _fallback(text: str):
            if not _error_cooldown_pass(self.msg.conversation_id, self.cfg.error_cooldown_s):
                return None
            return self.text(text)

        async def _deliver_rest(text: str) -> None:
            # 复用文本回复链路：自动按 text_chunk_limit 分片（含代码围栏感知切分）
            await self.text(text)

        streamer = CardStreamer(self.cards, None, _fallback, self.cfg.stream_throttle_s, _deliver_rest)
        try:
            streamer.card = await self.cards.create_and_deliver(target)
        except Exception as err:  # 卡片创建失败 → 静默降级
            self.cfg.log(f"card create failed, fallback to webhook text: {err}")
        return streamer


def _first_line_title(text: str) -> str:
    for line in str(text or "").split("\n"):
        t = line.lstrip("#*-> \t")
        if t:
            return t[:20]
    return "Message"
