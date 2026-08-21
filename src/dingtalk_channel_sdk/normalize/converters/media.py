"""媒体转换器：picture / file / audio / video。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def convert_picture(content: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """提取图片资源；downloadCode 为钉钉侧下载凭证。"""
    dc = (content or {}).get("downloadCode", "")
    return "", [{"type": "image", "downloadCode": dc}] if dc else []


def convert_file(content: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """提取文件资源，文件名缺失时回退占位文案。"""
    c = content or {}
    file_name = c.get("fileName", "")
    dc = c.get("downloadCode", "")
    text = f"[文件: {file_name}]" if file_name else "[文件]"
    resources = [{"type": "file", "downloadCode": dc, "fileName": file_name, "recognition": ""}]
    return text, resources


def convert_audio(content: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """提取语音资源；有识别文本时优先透出识别结果。"""
    c = content or {}
    recognition = c.get("recognition") or "[语音消息]"
    resources = [
        {
            "type": "audio",
            "downloadCode": c.get("downloadCode", ""),
            "fileName": c.get("fileName", ""),
            "recognition": c.get("recognition", ""),
        }
    ]
    return recognition, resources


def convert_video(content: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """提取视频资源。"""
    c = content or {}
    resources = [
        {
            "type": "video",
            "downloadCode": c.get("downloadCode", ""),
            "fileName": c.get("fileName", ""),
            "recognition": "",
        }
    ]
    return "[视频]", resources
