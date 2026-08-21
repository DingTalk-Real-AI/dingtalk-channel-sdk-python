"""E7 卡片回调 + E9 媒体上传 单测。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dingtalk_channel_sdk import DingTalkChannel

TOPIC_CARD = "/v1.0/card/instances/callback"


async def test_card_action_dispatch():
    ch = DingTalkChannel(client_id="a", client_secret="b", card_qps=100)
    got = []

    @ch.on_card_action
    async def handler(action, reply):
        got.append(action)

    await ch.dispatch_for_test(
        {
            "type": "CALLBACK",
            "headers": {"topic": TOPIC_CARD, "messageId": "m-c1"},
            "data": json.dumps({"outTrackId": "card_123", "userId": "u-1", "dataContent": {"action": "confirm"}}),
        }
    )
    assert len(got) == 1
    assert got[0].out_track_id == "card_123"
    assert got[0].user_id == "u-1"
    assert got[0].data_content["action"] == "confirm"


async def test_card_topic_subscription():
    ch = DingTalkChannel(client_id="a", client_secret="b", card_qps=100)
    assert ch.conn.want_card_topic is False

    @ch.on_card_action
    async def handler(action, reply):
        pass

    assert ch.conn.want_card_topic is True
    # 订阅构建可测（E7：注册即自动订阅）
    from dingtalk_channel_sdk.config import TOPIC_BOT_MESSAGE, TOPIC_CARD_CALLBACK

    topics = {s["topic"] for s in ch.conn.build_subscriptions()}
    assert TOPIC_CARD_CALLBACK in topics
    assert TOPIC_BOT_MESSAGE in topics
    assert "ping" in topics


async def test_upload_media():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def _send(self, v):
            body = json.dumps(v).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 — gettoken
            calls.append({"url": self.path})
            self._send({"errcode": 0, "access_token": "oapi-tok", "expires_in": 7200})

        def do_POST(self):  # noqa: N802 — media/upload
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            calls.append({"url": self.path, "contentType": self.headers.get("Content-Type"), "body": raw})
            assert "multipart/form-data" in (self.headers.get("Content-Type") or "")
            assert b'name="media"' in raw
            assert b"fake-jpeg-bytes" in raw
            self._send({"errcode": 0, "media_id": "@MEDIA_7", "type": "image"})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    ch = DingTalkChannel(
        client_id="ding-test", client_secret="s", api_base=base, oapi_base=base,
        stream_throttle_s=0.01, card_qps=100,
    )
    result = {}

    @ch.on_message
    async def handler(msg, reply):
        result.update(await reply.upload_media("image", "a.jpg", b"fake-jpeg-bytes"))

    await ch.dispatch_for_test(
        {
            "type": "CALLBACK",
            "headers": {"topic": "/v1.0/im/bot/messages/get", "messageId": "m-1"},
            "data": json.dumps({"msgId": "b-1", "sessionWebhook": "", "isInAtList": True}),
        }
    )

    assert "appkey=ding-test" in calls[0]["url"]
    assert "access_token=oapi-tok" in calls[1]["url"]
    assert "type=image" in calls[1]["url"]
    assert result["mediaId"] == "MEDIA_7"  # 去前导 @
    server.shutdown()


async def test_proactive_send():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            calls.append({"path": self.path, "body": json.loads(raw or b"{}")})
            body = b'{"accessToken":"tok","expireIn":7200}' if self.path.endswith("/accessToken") else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    from dingtalk_channel_sdk import SendTarget

    ch = DingTalkChannel(client_id="ding-test", client_secret="s", api_base=base,
                         stream_throttle_s=0.01, card_qps=100)
    await ch.send_text(SendTarget(user_id="staff-1"), "hello")
    await ch.send_markdown(SendTarget(conversation_id="cid-g", at_user_ids=["u1", "u2"]), "", "# hello")
    with pytest.raises(ValueError):
        await ch.send_text(SendTarget(), "x")

    sends = [c for c in calls if c["path"].startswith("/v1.0/robot/")]
    assert sends[0]["path"] == "/v1.0/robot/oToMessages/batchSend"
    assert sends[0]["body"]["userIds"] == ["staff-1"]
    assert isinstance(sends[0]["body"]["msgParam"], str)
    assert sends[1]["path"] == "/v1.0/robot/groupMessages/send"
    assert sends[1]["body"]["atUserIds"] == ["u1", "u2"]
    assert sends[1]["body"]["openConversationId"] == "cid-g"
    server.shutdown()
