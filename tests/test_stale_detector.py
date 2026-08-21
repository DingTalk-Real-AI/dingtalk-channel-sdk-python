"""测试 StaleDetector"""

import pytest
import time
from datetime import timedelta
from dingtalk_channel_sdk.safety.stale_detector import StaleDetector


def test_stale_detector_fresh_message():
    """测试新鲜消息"""
    detector = StaleDetector(timedelta(minutes=30))
    now_ms = int(time.time() * 1000)
    assert not detector.is_stale(now_ms)


def test_stale_detector_stale_message():
    """测试过期消息"""
    detector = StaleDetector(timedelta(minutes=10))
    old_ms = int((time.time() - 15 * 60) * 1000)  # 15 分钟前
    assert detector.is_stale(old_ms)


def test_stale_detector_boundary():
    """测试边界情况"""
    detector = StaleDetector(timedelta(minutes=10))
    
    # 刚好 10 分钟前（边界）
    boundary_ms = int((time.time() - 10 * 60) * 1000)
    # 可能刚好过期或未过期，取决于执行时间
    # 这里只验证不抛异常
    _ = detector.is_stale(boundary_ms)


def test_stale_detector_invalid_timestamp():
    """测试无效时间戳"""
    detector = StaleDetector()
    
    # 零值时间戳
    assert not detector.is_stale(0)
    
    # 负值时间戳
    assert not detector.is_stale(-1)


def test_stale_detector_default_window():
    """测试默认窗口（30分钟）"""
    detector = StaleDetector()
    
    # 25 分钟前（在窗口内）
    recent_ms = int((time.time() - 25 * 60) * 1000)
    assert not detector.is_stale(recent_ms)
    
    # 35 分钟前（超出窗口）
    old_ms = int((time.time() - 35 * 60) * 1000)
    assert detector.is_stale(old_ms)


def test_stale_detector_custom_window():
    """测试自定义窗口"""
    # 5 分钟窗口
    detector = StaleDetector(timedelta(minutes=5))
    
    # 3 分钟前
    recent_ms = int((time.time() - 3 * 60) * 1000)
    assert not detector.is_stale(recent_ms)
    
    # 7 分钟前
    old_ms = int((time.time() - 7 * 60) * 1000)
    assert detector.is_stale(old_ms)
