"""E8 回归：服务端下发 SYSTEM/disconnect 后必须重连（而非整体退出）。"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import websockets

from dingtalk_channel_sdk import DingTalkChannel

TOPIC = "/v1.0/im/bot/messages/get"


async def test_reconnect_after_server_disconnect():
    connections = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.dumps({"endpoint": f"ws://127.0.0.1:{ws_port[0]}", "ticket": "t-1"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ws_port: list[int] = []

    async def ws_handler(ws):
        connections["n"] += 1
        if connections["n"] == 1:
            await ws.send(json.dumps({
                "type": "SYSTEM",
                "headers": {"topic": "disconnect", "messageId": "m-d"},
            }))
            try:
                await asyncio.wait_for(ws.recv(), timeout=5)  # 等 ACK
            except Exception:
                pass
            await ws.close()
        else:
            await ws.send(json.dumps({
                "type": "CALLBACK",
                "headers": {"topic": TOPIC, "messageId": "m-r", "contentType": "application/json"},
                "data": json.dumps({
                    "text": {"content": "after-reconnect"},
                    "msgId": "b-r",
                    "conversationId": "cid",
                    "conversationType": "1",
                    "sessionWebhook": "",
                }),
            }))
            try:
                await asyncio.wait_for(ws.recv(), timeout=5)
            except Exception:
                pass
            await ws.close()

    ws_server = await websockets.serve(ws_handler, "127.0.0.1", 0)
    ws_port.append(ws_server.sockets[0].getsockname()[1])

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
        deadline = asyncio.get_event_loop().time() + 8
        while not got and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)
    finally:
        ch.close()
        run_task.cancel()
        ws_server.close()
        await ws_server.wait_closed()
        httpd.shutdown()
        httpd.server_close()

    assert got == ["after-reconnect"]
    assert connections["n"] >= 2
