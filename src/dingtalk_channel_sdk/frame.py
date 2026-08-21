"""Stream 线协议帧结构（SPEC §2.2）。"""

from __future__ import annotations

from typing import Any, Dict

SUB_CALLBACK = "CALLBACK"
SUB_SYSTEM = "SYSTEM"


def success_ack(message_id: str, data: str = "") -> Dict[str, Any]:
    return {
        "code": 200,
        "headers": {"contentType": "application/json", "messageId": message_id},
        "message": "ok",
        "data": data if data else '{"success":true}',
    }


def topic_of(frame: Dict[str, Any]) -> str:
    return (frame or {}).get("headers", {}).get("topic", "")


def message_id_of(frame: Dict[str, Any]) -> str:
    return (frame or {}).get("headers", {}).get("messageId", "")
