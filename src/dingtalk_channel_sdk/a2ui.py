"""按 dingtalk-aicard 的公开接入方式，通过显式配置的 DWS 身份发送 A2UI。"""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence, Union

A2UI_FLOW_STATUSES = (
    "PROCESSING", "INPUTTING", "FINISH", "EXECUTING", "ERROR",
    "ABORTED", "TIMEOUT", "CONFIRMING", "CONFIRMED",
)
_OPERATIONS = ("createSurface", "updateComponents", "updateDataModel", "deleteSurface")
_MISSING_ID = "回执未包含可用的 bizId；请保留回执并核实服务端标识，不要自动重发创建请求。"
Messages = Sequence[Union[str, Dict[str, Any]]]


def _reject_json_constant(value: str):
    raise ValueError("JSON 不允许非有限数值")


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{name} 必须是非空标识")
    return value.strip()


def serialize_a2ui_messages(messages: Messages) -> str:
    """只检查消息信封；组件和完整创建状态使用 dingtalk-aicard 校验。"""
    if not isinstance(messages, (list, tuple)) or not messages:
        raise ValueError("A2UI 消息必须是非空数组")
    strings: List[str] = []
    for index, message in enumerate(messages):
        try:
            encoded = message if isinstance(message, str) else json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            parsed = json.loads(encoded, parse_constant=_reject_json_constant)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"A2UI 消息 {index} 不是有效 JSON") from None
        if not isinstance(parsed, dict) or parsed.get("version") != "v1.0":
            raise ValueError(f"A2UI 消息 {index} 必须是 version=v1.0 的对象")
        keys = [key for key in _OPERATIONS if key in parsed]
        if len(keys) != 1 or not isinstance(parsed[keys[0]], dict):
            raise ValueError(f"A2UI 消息 {index} 必须包含一个操作")
        _identifier(parsed[keys[0]].get("surfaceId"), "surfaceId")
        strings.append(encoded)
    content = json.dumps(strings, ensure_ascii=False, separators=(",", ":"))
    if len(content.encode("utf-8")) > 65536:
        raise ValueError("DWS A2UI content 超过 64 KiB")
    return content


def _envelope_chain(receipt: Dict[str, Any]) -> List[Dict[str, Any]]:
    chain = []
    value: Any = receipt
    for _ in range(5):
        if not isinstance(value, dict):
            break
        chain.append(value)
        if (value.get("success") is False or value.get("ok") is False or value.get("isError") is True or value.get("error")
                or value.get("dry_run") is True or value.get("dryRun") is True
                or ("outcome" in value and value["outcome"] not in ("success", "pending"))):
            raise RuntimeError("DWS 返回失败回执")
        value = value.get("data") if value.get("data") is not None else value.get("result")
    if not any(item.get("success") is True or item.get("ok") is True for item in chain):
        raise RuntimeError("DWS 回执未明确确认接受请求；发送结果可能未知，请核实后再重试")
    return chain


@dataclass(frozen=True)
class A2UICardResult:
    biz_id: Optional[str]
    receipt: Dict[str, Any]
    update_warning: Optional[str] = None


class A2UIClient(Protocol):
    """可注入发送通道；接收目标使用 open_dingtalk_id 或 conversation_id。"""

    async def send_card(self, target: Dict[str, str], messages: Messages) -> A2UICardResult: ...
    async def update_card(self, biz_id: str, messages: Messages, flow_status: str) -> Dict[str, Any]: ...


class DwsA2UIClient:
    """可选 DWS 发送通道。DWS 单独安装、登录，profile 决定发送身份。"""

    def __init__(self, command: Sequence[str] = ("dws",), profile: Optional[str] = None, timeout_s: float = 30.0):
        if isinstance(command, str) or not command or any(not isinstance(part, str) or not part or "\0" in part for part in command):
            raise ValueError("command 必须是非空字符串数组")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s 必须大于 0")
        self.command = tuple(command)
        self.profile = _identifier(profile, "profile") if profile is not None else None
        if self.profile and "," in self.profile:
            raise ValueError("A2UI 发送只允许一个 DWS Profile")
        self.timeout_s = timeout_s

    async def _invoke(self, args: List[str]) -> Dict[str, Any]:
        argv = list(self.command) + ["chat", "message"] + args + ["--format=json", "--yes"]
        if self.profile:
            argv.append(f"--profile={self.profile}")
        try:
            process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            raise RuntimeError("DWS 无法启动；请检查安装和 command 配置") from None

        async def read_output():
            chunks = []
            size = 0
            while True:
                chunk = await process.stdout.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 8 * 1024 * 1024:
                    raise RuntimeError("DWS 回执超过 8 MiB；发送结果可能未知")
                chunks.append(chunk)
            await process.wait()
            return b"".join(chunks)

        try:
            stdout = await asyncio.wait_for(read_output(), timeout=self.timeout_s)
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            if process.returncode is None:
                process.kill()
            await process.wait()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise RuntimeError("DWS 执行超时；发送结果可能未知，请核实后再重试") from None
        except RuntimeError:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError("DWS 执行失败；发送结果可能未知，请核实后再重试")
        try:
            receipt = json.loads(stdout)
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError("DWS 输出不是 JSON；发送结果可能未知，请保留现场核实") from None
        if not isinstance(receipt, dict):
            raise RuntimeError("DWS 回执必须是 JSON 对象")
        _envelope_chain(receipt)
        return receipt

    async def send_card(self, target: Dict[str, str], messages: Messages) -> A2UICardResult:
        if not isinstance(target, dict) or any(key not in ("open_dingtalk_id", "conversation_id") for key in target):
            raise ValueError("A2UI target 使用 open_dingtalk_id 或 conversation_id，不接受 user_id")
        dm, group = "open_dingtalk_id" in target, "conversation_id" in target
        if dm == group:
            raise ValueError("A2UI target 必须恰好选择一个接收目标")
        flag = "open-dingtalk-id" if dm else "conversation-id"
        target_id = _identifier(target["open_dingtalk_id" if dm else "conversation_id"], flag)
        content = serialize_a2ui_messages(messages)
        receipt = await self._invoke(["send-a2ui-card", f"--{flag}={target_id}", f"--content={content}"])
        biz_id = None
        for value in reversed(_envelope_chain(receipt)):
            try:
                biz_id = _identifier(value.get("bizId"), "bizId")
                break
            except ValueError:
                pass
        return A2UICardResult(biz_id, receipt, None if biz_id else _MISSING_ID)

    async def update_card(self, biz_id: str, messages: Messages, flow_status: str) -> Dict[str, Any]:
        card_id = _identifier(biz_id, "bizId")
        status = str(flow_status).strip().upper()
        if status in tuple(str(i) for i in range(1, 10)):
            status = A2UI_FLOW_STATUSES[int(status) - 1]
        if status not in A2UI_FLOW_STATUSES:
            raise ValueError("不支持的 A2UI flow_status")
        content = serialize_a2ui_messages(messages)
        return await self._invoke(["update-a2ui-card", f"--biz-id={card_id}", f"--content={content}", f"--flow-status={status}"])
