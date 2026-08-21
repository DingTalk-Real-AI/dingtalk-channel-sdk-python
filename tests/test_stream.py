"""E8 + SPEC §2：假网关全链路（open → wss → 帧分发 → ACK / SYSTEM pong）。"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import websockets

from dingtalk_channel_sdk import DingTalkChannel

TOPIC = "/v1.0/im/bot/messages/get"


async def test_stream_end_to_end():
    # 1) HTTP 部分：gateway open 端点。
    ws_port_holder: dict[str, int] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.dumps(
                {"endpoint": f"ws://127.0.0.1:{ws_port_holder['port']}", "ticket": "t-1"}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    acks: list[dict] = []

    # 2) WebSocket 部分：发消息 + SYSTEM ping，收 ACK。
    async def ws_handler(ws):
        await ws.send(
            json.dumps(
                {
                    "type": "CALLBACK",
                    "headers": {"topic": TOPIC, "messageId": "m-9", "contentType": "application/json"},
                    "data": json.dumps(
                        {
                            "text": {"content": "ping"},
                            "msgId": "b-9",
                            "conversationId": "cid",
                            "conversationType": "1",
                            "sessionWebhook": "",
                        }
                    ),
                }
            )
        )
        await ws.send(
            json.dumps(
                {"type": "SYSTEM", "headers": {"topic": "ping", "messageId": "m-ping"}, "data": "keepalive"}
            )
        )
        try:
            for _ in range(2):
                acks.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=5)))
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass

    ws_server = await websockets.serve(ws_handler, "127.0.0.1", 0)
    ws_port_holder["port"] = ws_server.sockets[0].getsockname()[1]

    got: list[str] = []
    ch = DingTalkChannel(
        client_id="ding-test",
        client_secret="s",
        api_base=f"http://127.0.0.1:{httpd.server_port}",
        keepalive_idle_s=30,
        stream_throttle_s=0.01,
        card_qps=100,
    )

    @ch.on_message
    async def handler(msg, reply):
        got.append(msg.text)

    run_task = asyncio.create_task(ch.start())
    try:
        await asyncio.wait_for(_wait_for(lambda: len(acks) >= 2 and got), timeout=5)
    finally:
        ch.close()
        run_task.cancel()
        ws_server.close()
        await ws_server.wait_closed()
        httpd.shutdown()
        httpd.server_close()

    assert got == ["ping"]
    assert len(acks) == 2
    for a in acks:
        assert a["code"] == 200
        assert a["headers"]["messageId"]
    assert any(a["data"] == "keepalive" for a in acks), "SYSTEM ping 未回显 pong"


async def _wait_for(pred, timeout: float = 5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")
