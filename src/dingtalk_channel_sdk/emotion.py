"""消息表情回应（"🤔Thinking"状态章）——移植自 dws connect_card.go（hermes 同款）。

仅支持人发的消息（机器人自己的消息会 500）；best-effort，不为装饰失败整条回复。
"""

from __future__ import annotations

from .card import CardClient
from .config import Config

EMOTION_THINKING = "🤔Thinking"
EMOTION_DONE = "🥳Done"


class Emotion:
    def __init__(self, cfg: Config, cards: CardClient):
        self.cfg = cfg
        self.cards = cards

    async def _send(self, conversation_id: str, msg_id: str, name: str, recall: bool) -> None:
        if not conversation_id or not msg_id:
            raise ValueError("emotion needs openConversationId and openMsgId")
        path = "/v1.0/robot/emotion/recall" if recall else "/v1.0/robot/emotion/reply"
        await self.cards._call("POST", path, {
            "robotCode": self.cfg.client_id,
            "openConversationId": conversation_id,
            "openMsgId": msg_id,
            "emotionType": 2,
            "emotionName": name,
            "textEmotion": {
                "emotionId": "2659900",
                "emotionName": name,
                "text": name,
                "backgroundId": "im_bg_1",
            },
        })

    async def mark_thinking(self, conversation_id: str, msg_id: str) -> None:
        await self._send(conversation_id, msg_id, EMOTION_THINKING, recall=False)

    async def mark_done(self, conversation_id: str, msg_id: str) -> None:
        try:
            await self._send(conversation_id, msg_id, EMOTION_THINKING, recall=True)
        except Exception:
            pass
        await self._send(conversation_id, msg_id, EMOTION_DONE, recall=False)
