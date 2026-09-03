"""download_file_to_file 流式落盘单测（对齐 lark channel-sdk downloadResourceToFile）。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dingtalk_channel_sdk import DingTalkChannel

MEDIA = b"dingtalk-media-bytes" * 512


def _fake_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/v1.0/oauth2/accessToken":
                self._json(200, {"accessToken": "tok-1", "expireIn": 7200})
            elif self.path == "/media.bin":
                body = MEDIA
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json(404, {})

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def _channel(base):
    return DingTalkChannel(
        client_id="ding-test",
        client_secret="s",
        api_base=base,
        ssrf_allowlist=["127.0.0.1"],
    )


async def test_download_file_to_file(tmp_path):
    server = _fake_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        ch = _channel(base)

        dest = tmp_path / "media.bin"
        n = await ch.download_file_to_file(f"{base}/media.bin", str(dest))
        assert n == len(MEDIA)
        assert dest.read_bytes() == MEDIA

        # 原有内存下载语义保持不变
        assert await ch.download_file(f"{base}/media.bin") == MEDIA
    finally:
        server.shutdown()


async def test_download_file_to_file_missing_parent(tmp_path):
    ch = DingTalkChannel(client_id="a", client_secret="b", ssrf_allowlist=["127.0.0.1"])
    dest = tmp_path / "no-such-dir" / "media.bin"
    with pytest.raises(FileNotFoundError):
        await ch.download_file_to_file("http://127.0.0.1:1/x", str(dest))
    assert not (tmp_path / "no-such-dir").exists()


async def test_download_file_to_file_ssrf_blocked(tmp_path):
    ch = DingTalkChannel(client_id="a", client_secret="b")
    with pytest.raises(Exception):
        await ch.download_file_to_file("http://127.0.0.1:1/x", str(tmp_path / "x.bin"))
