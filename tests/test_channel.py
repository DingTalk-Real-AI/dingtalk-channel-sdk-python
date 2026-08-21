"""效果验收单测：E1–E6/E9（不依赖真实钉钉）。"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dingtalk_channel_sdk import DingTalkChannel

TOPIC = "/v1.0/im/bot/messages/get"


class FakeAPI:
    def __init__(self):
        self.state = {
            "create": 0,
            "deliver": 0,
            "instances": {},
            "streams": [],
            "webhook": [],
            "fail_create": False,
        }
        api = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, v, status=200):
                body = json.dumps(v).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path.endswith("/oauth2/accessToken"):
                    api.state["token"] = api.state.get("token", 0) + 1
                    self._send({"accessToken": "tok-1", "expireIn": 7200})
                elif self.path == "/v1.0/card/instances":
                    api.state["create"] += 1
                    if api.state["fail_create"]:
                        self._send({"code": "InternalError"}, 500)
                    else:
                        self._send({})
                elif self.path == "/v1.0/card/instances/deliver":
                    api.state["deliver"] += 1
                    self._send({})
                elif self.path == "/v1.0/card/streaming":
                    api.state["streams"].append(body)
                    self._send({})
                elif self.path == "/webhook":
                    api.state["webhook"].append(body)
                    self._send({"errcode": 0})
                else:
                    self._send({}, 404)

            def do_PUT(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/v1.0/card/instances":
                    api.state["instances"][body.get("outTrackId", "?")] = (
                        body.get("cardData", {}).get("cardParamMap", {}).get("msgContent", "")
                    )
                    self._send({})
                elif self.path == "/v1.0/card/streaming":
                    api.state["streams"].append(body)
                    self._send({})
                else:
                    self._send({}, 404)

            def log_message(self, *args):  # 静默
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def api():
    fake = FakeAPI()
    yield fake
    fake.stop()


def make_channel(api, **kw):
    return DingTalkChannel(
        client_id="ding-test",
        client_secret="s",
        api_base=api.base,
        stream_throttle_s=0.01,
        card_qps=100,
        **kw,
    )


def bot_frame(message_id: str, msg_id: str, text: str, webhook: str) -> dict:
    return {
        "type": "CALLBACK",
        "headers": {"topic": TOPIC, "messageId": message_id, "contentType": "application/json"},
        "data": json.dumps(
            {
                "conversationId": "cid-1",
                "conversationType": "2",
                "msgId": msg_id,
                "senderStaffId": "staff-1",
                "senderNick": "John",
                "sessionWebhook": webhook,
                "text": {"content": text},
                "msgtype": "text",
                "isInAtList": True,
            }
        ),
    }


async def test_dedup_both_layers(api):
    calls = []
    ch = make_channel(api)

    @ch.on_message
    async def handler(msg, reply):
        calls.append(msg.text)

    await ch.dispatch_for_test(bot_frame("m-1", "b-1", "hi", api.base + "/webhook"))
    await ch.dispatch_for_test(bot_frame("m-1", "b-1", "hi", api.base + "/webhook"))  # 协议层重复
    await ch.dispatch_for_test(bot_frame("m-2", "b-1", "hi", api.base + "/webhook"))  # 业务层重复
    await ch.dispatch_for_test(bot_frame("m-3", "b-2", "hi", api.base + "/webhook"))
    assert calls == ["hi", "hi"]


async def test_stream_lifecycle_and_at_strip(api):
    got = {}
    ch = make_channel(api)

    @ch.on_message
    async def handler(msg, reply):
        got["text"] = msg.text
        s = await reply.stream()
        await s.append("Hello ")
        await s.append("World")  # 节流合并
        import asyncio

        await asyncio.sleep(0.03)
        await s.append("!")
        await s.finish()

    await ch.dispatch_for_test(bot_frame("m-1", "b-1", "@bot 你好", api.base + "/webhook"))

    assert got["text"] == "你好"  # E5：@ 剥离
    assert api.state["create"] == 1
    assert api.state["deliver"] == 1
    assert any(s.get("isFinalize") and "World" in s.get("content", "") for s in api.state["streams"])  # E3
    assert any("Hello" in c for c in api.state["instances"].values())


async def test_stream_fallback_on_card_failure(api):
    api.state["fail_create"] = True
    ch = make_channel(api)

    @ch.on_message
    async def handler(msg, reply):
        s = await reply.stream()
        await s.append("final answer")
        try:
            await s.finish()
        except Exception:
            pass

    await ch.dispatch_for_test(bot_frame("m-1", "b-1", "hi", api.base + "/webhook"))
    assert api.state["webhook"], "fallback webhook not called"
    last = api.state["webhook"][-1]
    assert last["msgKey"] == "sampleText"
    assert json.loads(last["msgParam"])["content"] == "final answer"


async def test_webhook_reply_msg_keys(api):
    ch = make_channel(api)

    @ch.on_message
    async def handler(msg, reply):
        await reply.text("plain")
        await reply.markdown("T", "# md")
        await reply.image("https://x/y.png")

    await ch.dispatch_for_test(bot_frame("m-1", "b-1", "hi", api.base + "/webhook"))
    assert [w["msgKey"] for w in api.state["webhook"]] == [
        "sampleText",
        "sampleMarkdown",
        "sampleImageMsg",
    ]


async def test_trailing_flush(api):
    """节流窗口内的 append 不丢弃，安排 trailing flush。"""
    ch = DingTalkChannel(client_id="a", client_secret="b", api_base=api.base,
                         stream_throttle_s=0.2, card_qps=100)

    @ch.on_message
    async def handler(msg, reply):
        s = await reply.stream()
        await s.append("first")   # 立即刷
        await s.append("chunk2")  # 窗口内 → trailing flush

    await ch.dispatch_for_test(bot_frame("m-1", "b-1", "hi", api.base + "/webhook"))

    import time as _t
    deadline = _t.time() + 3
    while _t.time() < deadline:
        if any(s.get("content") == "firstchunk2" for s in api.state["streams"]):
            break
        await asyncio.sleep(0.02)
    assert any(s.get("content") == "firstchunk2" for s in api.state["streams"]), (
        "trailing flush did not deliver in-window content"
    )
