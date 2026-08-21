"""测试 SeenCache"""

import pytest
import asyncio
from datetime import timedelta
from dingtalk_channel_sdk.safety.seen_cache import SeenCache, content_fingerprint
from dingtalk_channel_sdk.types import DedupConfig


@pytest.mark.asyncio
async def test_seen_cache_basic():
    """测试基础去重"""
    cfg = DedupConfig(ttl=timedelta(minutes=5), max_entries=100)
    cache = SeenCache(cfg)
    
    # 首次见到
    assert not await cache.check_and_mark_async("key1")
    
    # 重复
    assert await cache.check_and_mark_async("key1")
    
    cache.dispose()


@pytest.mark.asyncio
async def test_seen_cache_multi_keys():
    """测试多键去重"""
    cfg = DedupConfig(ttl=timedelta(minutes=5), max_entries=100)
    cache = SeenCache(cfg)
    
    # 三键去重
    assert not await cache.check_and_mark_async("proto1", "msg1", "fp1")
    assert await cache.check_and_mark_async("proto1", "msg1", "fp1")
    
    # 不同键
    assert not await cache.check_and_mark_async("proto2", "msg2", "fp2")
    # 部分键重叠（proto1 已标记）：逐键语义下任意键命中即重复
    assert await cache.check_and_mark_async("proto1", "msg2", "fp1")
    
    cache.dispose()


@pytest.mark.asyncio
async def test_seen_cache_empty_keys():
    """测试空键处理"""
    cfg = DedupConfig(ttl=timedelta(minutes=5), max_entries=100)
    cache = SeenCache(cfg)
    
    # 空键列表
    assert not await cache.check_and_mark_async()
    
    # 包含空字符串的键
    assert not await cache.check_and_mark_async("key1", "", "key2")
    assert await cache.check_and_mark_async("key1", "", "key2")
    
    cache.dispose()


@pytest.mark.asyncio
async def test_seen_cache_lru_eviction():
    """测试 LRU 淘汰"""
    cfg = DedupConfig(ttl=timedelta(minutes=5), max_entries=3)
    cache = SeenCache(cfg)
    
    # 添加 4 个键（超过容量）
    await cache.check_and_mark_async("key1")
    await cache.check_and_mark_async("key2")
    await cache.check_and_mark_async("key3")
    await cache.check_and_mark_async("key4")  # 应该淘汰 key1
    
    # 检查缓存大小（最多 3 个）
    async with cache._lock:
        assert len(cache._cache) == 3
    
    cache.dispose()


@pytest.mark.asyncio
async def test_seen_cache_ttl():
    """测试 TTL 过期"""
    cfg = DedupConfig(ttl=timedelta(milliseconds=100), max_entries=100, sweep_interval=timedelta(milliseconds=50))
    cache = SeenCache(cfg)
    
    # 添加键
    await cache.check_and_mark_async("key1")
    assert await cache.check_and_mark_async("key1")
    
    # 等待过期
    await asyncio.sleep(0.15)
    
    # 触发 sweep（或直接检查）
    await cache._sweep()
    
    # 应该已过期
    assert not await cache.check_and_mark_async("key1")
    
    cache.dispose()


def test_content_fingerprint():
    """测试内容指纹"""
    fp1 = content_fingerprint("chat1", 123456, "text", "hello")
    fp2 = content_fingerprint("chat1", 123456, "text", "hello")
    fp3 = content_fingerprint("chat1", 123456, "text", "world")
    fp4 = content_fingerprint("chat2", 123456, "text", "hello")
    
    # 相同内容应该有相同指纹
    assert fp1 == fp2
    
    # 不同内容应该有不同指纹
    assert fp1 != fp3
    assert fp1 != fp4
    
    # 指纹长度应该是 16
    assert len(fp1) == 16


def test_content_fingerprint_empty():
    """测试空内容指纹"""
    fp = content_fingerprint("", 0, "", "")
    assert len(fp) == 16
    assert isinstance(fp, str)
