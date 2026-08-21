"""生命周期钩子。"""

from __future__ import annotations

from typing import Callable, List


class LifecycleHooks:
    """管理连接生命周期钩子。"""

    def __init__(self):
        self._on_ready: List[Callable[[], None]] = []
        self._on_error: List[Callable[[Exception], None]] = []
        self._on_reconnecting: List[Callable[[], None]] = []
        self._on_reconnected: List[Callable[[], None]] = []
        self._on_disconnected: List[Callable[[], None]] = []

    def on_ready(self, fn: Callable[[], None]) -> None:
        """注册连接就绪回调。"""
        self._on_ready.append(fn)

    def on_error(self, fn: Callable[[Exception], None]) -> None:
        """注册连接错误回调。"""
        self._on_error.append(fn)

    def on_reconnecting(self, fn: Callable[[], None]) -> None:
        """注册重连中回调。"""
        self._on_reconnecting.append(fn)

    def on_reconnected(self, fn: Callable[[], None]) -> None:
        """注册重连成功回调。"""
        self._on_reconnected.append(fn)

    def on_disconnected(self, fn: Callable[[], None]) -> None:
        """注册断开连接回调。"""
        self._on_disconnected.append(fn)

    def fire_ready(self) -> None:
        """触发所有 on_ready 回调。"""
        for fn in self._on_ready:
            try:
                fn()
            except Exception:
                pass

    def fire_error(self, err: Exception) -> None:
        """触发所有 on_error 回调。"""
        for fn in self._on_error:
            try:
                fn(err)
            except Exception:
                pass

    def fire_reconnecting(self) -> None:
        """触发所有 on_reconnecting 回调。"""
        for fn in self._on_reconnecting:
            try:
                fn()
            except Exception:
                pass

    def fire_reconnected(self) -> None:
        """触发所有 on_reconnected 回调。"""
        for fn in self._on_reconnected:
            try:
                fn()
            except Exception:
                pass

    def fire_disconnected(self) -> None:
        """触发所有 on_disconnected 回调。"""
        for fn in self._on_disconnected:
            try:
                fn()
            except Exception:
                pass
