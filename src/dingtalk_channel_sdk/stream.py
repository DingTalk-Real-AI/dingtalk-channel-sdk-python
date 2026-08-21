"""Stream 长连接：open → wss → 心跳 → 重连 → ACK（SPEC §2 / E8）。"""

from __future__ import annotations

import asyncio
import json
import random
import socket
from urllib.parse import quote_plus
from typing import Any, Awaitable, Callable, Optional

import websockets

from .config import (
    RECONNECT_BASE_S,
    RECONNECT_MAX_S,
    TOPIC_BOT_MESSAGE,
    TOPIC_CARD_CALLBACK,
    USER_AGENT,
    Config,
)
from .frame import SUB_CALLBACK, SUB_SYSTEM, success_ack
from .lifecycle import LifecycleHooks

OnFrame = Callable[[dict], Awaitable[Optional[str]]]


def _first_lan_ip() -> str:
    """第一块非 loopback IPv4（官方 SDK 同款上报字段）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        try:
            import socket as _s
            return _s.gethostbyname(_s.gethostname())
        except OSError:
            return ""


def _backoff_delay(attempt: int) -> float:
    d = min(RECONNECT_BASE_S * (2**attempt), RECONNECT_MAX_S)
    return d + random.random()


class StreamConn:
    def __init__(self, cfg: Config, on_frame: OnFrame, lifecycle: Optional[LifecycleHooks] = None):
        self.cfg = cfg
        self.on_frame = on_frame
        self.lifecycle = lifecycle
        self.ws: Optional[Any] = None
        self.stopped = False
        self.want_card_topic = False
        # 本次 _run_once 是否成功建立过连接（用于退避归零）；
        # _ever_connected 标记历史上是否连过（重连成功时触发 reconnected 而非重复 ready）
        self._had_connection = False
        self._ever_connected = False

    def build_subscriptions(self) -> list:
        """构建订阅列表（公开以便测试验证 E7 订阅自动添加）。"""
        subs = [
            {"type": SUB_SYSTEM, "topic": "ping"},
            {"type": SUB_SYSTEM, "topic": "disconnect"},
            {"type": SUB_CALLBACK, "topic": TOPIC_BOT_MESSAGE},
        ]
        if self.want_card_topic:
            subs.append({"type": SUB_CALLBACK, "topic": TOPIC_CARD_CALLBACK})
        return subs

    async def _open(self) -> tuple[str, str]:
        from .httpx import http_json

        subs = self.build_subscriptions()
        out = await http_json(
            "POST",
            f"{self.cfg.api_base}/v1.0/gateway/connections/open",
            {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
            {
                "clientId": self.cfg.client_id,
                "clientSecret": self.cfg.client_secret,
                "subscriptions": subs,
                "ua": USER_AGENT,
                "localIp": _first_lan_ip(),
            },
        )
        endpoint, ticket = out.get("endpoint", ""), out.get("ticket", "")
        if not endpoint or not ticket:
            raise RuntimeError("gateway open: empty endpoint/ticket")
        return endpoint, ticket

    async def run(self) -> None:
        """阻塞运行；断开按配置重连。"""
        attempt = 0
        while True:
            err = await self._run_once()
            if self._had_connection:
                # 成功建立过连接：退避计数归零，重连从最小间隔重新开始
                attempt = 0
            self._had_connection = False
            if self.stopped:
                return
            if not self.cfg.auto_reconnect:
                if self.lifecycle:
                    self.lifecycle.fire_error(err or RuntimeError("stream closed"))
                raise err or RuntimeError("stream closed")
            delay = _backoff_delay(attempt)
            attempt += 1
            self.cfg.log(f"stream disconnected ({err}), reconnect in {delay:.1f}s")
            if self.lifecycle:
                self.lifecycle.fire_reconnecting()
            await asyncio.sleep(delay)

    async def _run_once(self) -> Optional[Exception]:
        try:
            endpoint, ticket = await self._open()
            async with websockets.connect(
                f"{endpoint}?ticket={quote_plus(ticket)}", ping_interval=None, max_size=8 * 1024 * 1024
            ) as ws:
                self.ws = ws
                self._had_connection = True
                self.cfg.log("stream connected")
                if self.lifecycle:
                    if self._ever_connected:
                        self.lifecycle.fire_reconnected()
                    else:
                        self.lifecycle.fire_ready()
                self._ever_connected = True
                return await self._read_loop(ws)
        except Exception as err:  # noqa: BLE001 — 连接层错误统一走重连
            if self.lifecycle:
                self.lifecycle.fire_error(err)
            return err
        finally:
            self.ws = None
            if self.lifecycle:
                self.lifecycle.fire_disconnected()

    async def _read_loop(self, ws: Any) -> Optional[Exception]:
        idle_task: Optional[asyncio.Task[None]] = None

        async def keepalive() -> None:
            await asyncio.sleep(self.cfg.keepalive_idle_s)
            # websockets 的 ping() 返回一个 waiter，收到 pong 时完成；
            # wait_for 超时即 pong 未回 → 判定僵死连接，主动断连触发重连
            try:
                await asyncio.wait_for(ws.ping(), timeout=self.cfg.pong_wait_s)
            except asyncio.TimeoutError:
                self.cfg.log(f"stream pong timeout ({self.cfg.pong_wait_s}s), reconnecting")
                await ws.close()
            except Exception as err:  # noqa: BLE001 — ping 发送失败同样触发重连
                self.cfg.log(f"stream keepalive ping failed: {err}")
                await ws.close()

        def reset_idle() -> None:
            nonlocal idle_task
            if idle_task:
                idle_task.cancel()
            idle_task = asyncio.create_task(keepalive())

        try:
            async for raw in ws:
                reset_idle()
                try:
                    frame = json.loads(raw)
                    ponged = await self._handle_frame(ws, frame)
                    if ponged:
                        return None  # disconnect topic：正常退出触发重连
                except Exception as err:  # noqa: BLE001
                    self.cfg.log(f"bad frame: {err}")
            return None
        except Exception as err:  # noqa: BLE001
            return err
        finally:
            if idle_task:
                idle_task.cancel()

    async def _handle_frame(self, ws: Any, frame: dict) -> bool:
        """返回 True 表示收到 SYSTEM/disconnect（调用方据此退出触发重连）。"""
        topic = (frame.get("headers") or {}).get("topic", "")
        message_id = (frame.get("headers") or {}).get("messageId", "")

        if frame.get("type") == SUB_SYSTEM and topic == "ping":
            ack = success_ack(message_id)
            ack["data"] = frame.get("data") or ""
            await ws.send(json.dumps(ack))
            return False
        if frame.get("type") == SUB_SYSTEM and topic == "disconnect":
            await ws.send(json.dumps(success_ack(message_id)))
            await ws.close()
            return True

        # ACK 先行（对齐官方 connector）：立即确认；业务处理派发为独立任务，
        # 长任务不阻塞读循环与心跳，重复投递由双层去重兜底（E6）
        await ws.send(json.dumps(success_ack(message_id)))
        if self.on_frame:
            asyncio.create_task(self._dispatch(frame))
        return False

    async def _dispatch(self, frame: dict) -> None:
        """异步执行业务帧处理；异常记录不外泄。"""
        try:
            await self.on_frame(frame)
        except Exception as err:  # noqa: BLE001
            self.cfg.log(f"frame handler error: {err}")

    def close(self) -> None:
        self.stopped = True
