"""AI 卡片五步协议客户端（SPEC §5）+ 全局限流（SPEC §6）。"""

from __future__ import annotations

import asyncio
import json
import random
import string
import time
from typing import Any, Awaitable, Callable, Optional

from .config import Config
from .httpx import ApiError, http_json
from .outbound.markdown import normalize_for_card
from .ratelimit import TokenBucket
from .token import TokenProvider

FLOW_PROCESSING = "1"
FLOW_INPUTING = "2"
FLOW_FINISHED = "3"
FLOW_FAILED = "5"

_ALPHABET = string.ascii_lowercase + string.digits


def _rand_suffix(n: int) -> str:
    return "".join(random.choice(_ALPHABET) for _ in range(n))


class CardClient:
    def __init__(self, cfg: Config, tokens: TokenProvider):
        self.cfg = cfg
        self.tokens = tokens
        self.bucket = new_bucket(cfg)

    async def _call(self, method: str, path: str, body: Optional[dict]) -> None:
        await self._call_raw(method, path, body)

    async def _call_raw(self, method: str, path: str, body: Optional[dict]) -> Optional[dict]:
        async def do() -> Optional[dict]:
            token = await self.tokens.get()
            return await http_json(
                method,
                f"{self.cfg.api_base}{path}",
                {
                    "Content-Type": "application/json",
                    "x-acs-dingtalk-access-token": token,
                },
                body,
            )

        await self.bucket.wait_for()
        try:
            return await do()
        except ApiError as err:
            if err.is_qps_limit:
                self.bucket.trigger_backoff()
                await self.bucket.wait_for()
                return await do()
            raise

    async def _call_checked(self, method: str, path: str, body: Optional[dict]) -> None:
        """业务级校验（dws 生产实证）：卡片 API 会在 HTTP 200 里返回
        {"result":[{"success":false,...}]}，必须视为失败。"""
        raw = await self._call_raw(method, path, body)
        if raw is not None and '"success":false' in json.dumps(raw, ensure_ascii=False):
            raise ApiError(200, "BusinessFailure", "business failure inside http 200", raw)

    async def create_and_deliver(self, target: dict) -> dict:
        out_track_id = f"card_{int(time.time() * 1000)}_{_rand_suffix(8)}"
        await self._call(
            "POST",
            "/v1.0/card/instances",
            {
                "cardTemplateId": self.cfg.card_template_id,
                "outTrackId": out_track_id,
                "cardData": {"cardParamMap": {"config": '{"autoLayout":true}'}},
                "callbackType": "STREAM",
                "imGroupOpenSpaceModel": {"supportForward": True},
                "imRobotOpenSpaceModel": {"supportForward": True},
            },
        )
        if target["is_group"]:
            deliver = {
                "outTrackId": out_track_id,
                "userIdType": 1,
                "openSpaceId": f"dtv1.card//IM_GROUP.{target['conversation_id']}",
                "imGroupOpenDeliverModel": {"robotCode": target["robot_code"]},
            }
        else:
            deliver = {
                "outTrackId": out_track_id,
                "userIdType": 1,
                "openSpaceId": f"dtv1.card//IM_ROBOT.{target['user_id']}",
                "imRobotOpenDeliverModel": {
                    "spaceType": "IM_ROBOT",
                    "robotCode": target["robot_code"],
                    "extension": {"dynamicSummary": "true"},
                },
            }
        await self._call_checked("POST", "/v1.0/card/instances/deliver", deliver)
        return {"outTrackId": out_track_id, "inputingStarted": False}

    async def set_status(self, card: dict, status: str, content: str) -> None:
        body: dict[str, Any] = {
            "outTrackId": card["outTrackId"],
            "cardData": {
                "cardParamMap": {
                    "flowStatus": status,
                    "msgContent": content,
                    "staticMsgContent": "",
                    "sys_full_json_obj": '{"order":["msgContent"]}',
                    "config": '{"autoLayout":true}',
                }
            },
        }
        if status == FLOW_FINISHED:
            body["cardUpdateOptions"] = {"updateCardDataByKey": True}
        await self._call("PUT", "/v1.0/card/instances", body)

    async def stream(self, card: dict, content: str, finalize: bool) -> None:
        norm = normalize_for_card(content)
        if not finalize:
            norm = re_strip_trailing_newlines(norm)
        await self._call(
            "PUT",
            "/v1.0/card/streaming",
            {
                "outTrackId": card["outTrackId"],
                "guid": f"{int(time.time() * 1000)}_{_rand_suffix(6)}",
                "key": "msgContent",
                "content": norm,
                "isFull": True,
                "isFinalize": finalize,
                "isError": False,
            },
        )


def re_strip_trailing_newlines(s: str) -> str:
    return s.rstrip("\n") if s.endswith("\n") else s


# 模块级单例桶：同进程内所有 CardClient 共享（SPEC §6 全局限流）。
_bucket: Optional[TokenBucket] = None


def new_bucket(cfg: Config) -> TokenBucket:
    global _bucket
    if _bucket is None or _bucket.rate != cfg.card_qps:
        _bucket = TokenBucket(cfg.card_qps)
    return _bucket


class CardStreamer:
    """流式卡片句柄：append 节流、finish 收口、fail 置错（E1–E4）。"""

    def __init__(
        self,
        client: CardClient,
        card: Optional[dict],
        fallback: Callable[[str], Awaitable[None]],
        throttle_s: float,
        deliver_rest: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.client = client
        self.card = card
        self.fallback = fallback
        # deliver_rest 超长内容超出单帧上限后，剩余部分的 webhook 续发通道（由 Reply 注入）
        self.deliver_rest = deliver_rest
        self.throttle = throttle_s
        self.accumulated = ""
        self.last_update = 0.0
        self.closed = False
        self._pending_task: Optional[asyncio.Task] = None
        self._frame_count = 0
        self._last_frame_at = 0.0
        self._watchdog_task: Optional[asyncio.Task] = None
        self._aborted = False

    # flush-controller 效果参数
    LONG_GAP_THRESHOLD_S = 2.0
    LONG_GAP_BATCH_S = 0.3
    # dws 实证：帧间隔防"内容加载失败"竞态；单帧内容上限
    FRAME_GAP_S = 0.5
    MAX_CONTENT = 20000

    @property
    def card_delivered(self) -> bool:
        """卡片是否真实创建并投递成功（诊断用；False 表示处于降级模式）。"""
        return self.card is not None

    async def append(self, delta: str) -> None:
        if self.closed:
            raise RuntimeError("streamer already closed")
        self.accumulated += delta
        if self.card is None:
            return  # 卡片不可用（E4 降级模式）：仅累积，finish 时走降级
        now = time.monotonic()
        elapsed = now - self.last_update
        if elapsed >= self.throttle and elapsed > self.LONG_GAP_THRESHOLD_S:
            # 长间隔（工具调用/思考）后：延迟攒批
            self._schedule_pending(self.LONG_GAP_BATCH_S)
        elif elapsed >= self.throttle:
            self.last_update = now
            await self._update(self.accumulated, finalize=False)
        elif self._pending_task is None:
            # 窗口内不丢弃：trailing flush，内容最终必达
            self._schedule_pending(self.throttle - elapsed)

    def _schedule_pending(self, delay_s: float) -> None:
        if self._pending_task is not None:
            return

        async def _flush() -> None:
            await asyncio.sleep(max(0.001, delay_s))
            self._pending_task = None
            if self.closed or self.card is None:
                return
            self.last_update = time.monotonic()
            try:
                await self._update(self.accumulated, finalize=False)
            except Exception:
                pass

        self._pending_task = asyncio.create_task(_flush())

    async def _update(self, content: str, finalize: bool) -> None:
        if self.card is None:
            raise RuntimeError("card unavailable")
        # 首帧与投递间、终帧与上帧间留出间隔（防"内容加载失败"竞态，dws 实证）
        if self._frame_count == 0 or finalize:
            elapsed = time.monotonic() - self._last_frame_at
            if elapsed < self.FRAME_GAP_S:
                await asyncio.sleep(self.FRAME_GAP_S - elapsed)
        if len(content) > self.MAX_CONTENT:
            content = content[: self.MAX_CONTENT]  # rune 安全截断
        if not self.card["inputingStarted"]:
            await self.client.set_status(self.card, FLOW_INPUTING, normalize_for_card(content))
            self.card["inputingStarted"] = True
        self._frame_count += 1
        self._last_frame_at = time.monotonic()
        self._reset_watchdog()
        await self.client.stream(self.card, content, finalize)

    # ---- 孤儿卡看门狗（connector 同款防线） ----
    def _arm_watchdog(self) -> None:
        self._reset_watchdog()

    def _reset_watchdog(self) -> None:
        cfg = getattr(self.client.cfg, "card_watchdog_s", 0) or 0
        if cfg <= 0 or self.card is None:
            return
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()

        async def _fire() -> None:
            await asyncio.sleep(cfg)
            if self.closed or self.card is None:
                return
            self.closed = True  # 密封
            content = self.accumulated
            try:
                await self.client.stream(self.card, content, True)
                await self.client.set_status(self.card, FLOW_FINISHED, normalize_for_card(content))
            except Exception:
                pass

        self._watchdog_task = asyncio.create_task(_fire())

    def _clear_watchdog(self) -> None:
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None

    async def abort(self) -> None:
        """显式中止：密封流，卡片置 FAILED；幂等。"""
        if self.closed:
            return
        self.closed = True
        self._aborted = True
        self._clear_watchdog()
        if self._pending_task is not None:
            self._pending_task.cancel()
            self._pending_task = None
        if self.card is None:
            return
        try:
            await self.client.stream(self.card, self.accumulated, True)
            await self.client.set_status(self.card, FLOW_FAILED, normalize_for_card(self.accumulated))
        except Exception:
            pass

    async def finish(self, text: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        self._clear_watchdog()
        if self._pending_task is not None:
            self._pending_task.cancel()
            self._pending_task = None
        if text:
            self.accumulated = text
        content = self.accumulated
        if self.card is None:
            await self._fallback(content)
            return
        try:
            await self._update(content, finalize=True)
            await self.client.set_status(self.card, FLOW_FINISHED, normalize_for_card(content))
        except Exception:
            await self._fallback(content)  # E4：降级保证用户拿到回复
            raise
        # 超长内容：卡片单帧截断（MAX_CONTENT）后，剩余部分经 webhook 分片续发，
        # 避免尾部静默丢失。best-effort，失败不影响卡片收口结果。
        if len(content) > self.MAX_CONTENT and self.deliver_rest is not None:
            try:
                await self.deliver_rest(content[self.MAX_CONTENT:])
            except Exception as err:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning("card overflow remainder deliver failed: %s", err)

    async def fail(self, err_text: str) -> None:
        if self.closed:
            return
        self.closed = True
        self._clear_watchdog()
        if self._pending_task is not None:
            self._pending_task.cancel()
            self._pending_task = None
        if self.card is not None:
            try:
                if not self.card["inputingStarted"]:
                    await self.client.set_status(self.card, FLOW_INPUTING, "")
                    self.card["inputingStarted"] = True
                await self.client.stream(self.card, err_text, True)
                await self.client.set_status(self.card, FLOW_FAILED, normalize_for_card(err_text))
            except Exception:
                pass
        await self._fallback(err_text)

    async def _fallback(self, text: str) -> None:
        if not text.strip():
            return
        try:
            await self.fallback(text)
        except Exception:
            pass
