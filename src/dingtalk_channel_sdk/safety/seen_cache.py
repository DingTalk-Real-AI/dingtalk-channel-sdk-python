"""增强版去重缓存 - 对标 Go SDK internal/safety/seen_cache.go"""

import asyncio
import hashlib
import time
from collections import OrderedDict
from datetime import timedelta
from typing import Optional, Protocol
from ..types import DedupConfig


class RedisClient(Protocol):
    """Redis 客户端接口"""
    
    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        ...
    
    async def setex(self, key: str, seconds: int, value: str) -> None:
        """设置键值对并指定过期时间"""
        ...
    
    async def close(self) -> None:
        """关闭连接"""
        ...


class SeenCache:
    """增强版去重缓存
    
    特性：
    - 三键去重：协议ID + 业务msgId + 内容指纹（SHA-256）
    - 双层缓存：内存LRU（快速路径）+ 可选Redis（多实例共享）
    - TTL + LRU：默认 12 小时 TTL，5000 条容量限制
    - 后台清理：定期 sweep 过期条目
    """
    
    def __init__(
        self,
        cfg: DedupConfig,
        redis: Optional[RedisClient] = None,
        redis_prefix: str = "",
    ):
        """初始化去重缓存
        
        Args:
            cfg: 去重配置
            redis: 可选的 Redis 客户端
            redis_prefix: Redis 键前缀
        """
        self.cfg = cfg
        self.redis = redis
        self.redis_prefix = redis_prefix or cfg.redis_prefix
        
        # 内存 LRU 缓存
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()
        
        # 后台清理任务：无运行事件循环（同步构造）时跳过，由首次异步调用环境兜底
        self._sweep_task: Optional[asyncio.Task] = None
        self._closed = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and cfg.sweep_interval.total_seconds() > 0:
            self._sweep_task = loop.create_task(self._sweep_loop())
    
    async def has_async(self, *keys: str) -> bool:
        """只查不写：任一键已存在即返回 True（L1 命中或 L2 回填，不标记）。"""
        valid = [k for k in keys if k]
        if not valid:
            return False
        async with self._lock:
            for key in valid:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return True
            if self.redis is not None:
                for key in valid:
                    if await self.redis.exists(self.redis_prefix + key):
                        self._add_to_memory(key)
                        return True
            return False

    async def add_async(self, *keys: str) -> None:
        """标记键为已见（全部写入 L1，best-effort 写 L2）。"""
        valid = [k for k in keys if k]
        if not valid:
            return
        async with self._lock:
            for key in valid:
                self._add_to_memory(key)
                if self.redis is not None:
                    try:
                        await self.redis.setex(self.redis_prefix + key, int(self.cfg.ttl.total_seconds()), "1")
                    except Exception:  # noqa: BLE001
                        pass

    async def check_and_mark_async(self, *keys: str) -> bool:
        """检查并标记为已见：任一键已存在即视为重复，否则全部标记。

        Args:
            keys: 去重键（协议投递 ID、业务 msgId、内容指纹等）。

        Returns:
            True 表示重复，False 表示首次出现（已标记）。
        """
        valid = [k for k in keys if k]
        if not valid:
            return False

        now = time.time()
        async with self._lock:
            # L1: 内存 LRU，任一键命中即重复
            for key in valid:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return True

            # L2: 可选 Redis 后端，命中回填内存
            if self.redis is not None:
                for key in valid:
                    if await self.redis.exists(self.redis_prefix + key):
                        self._add_to_memory(key)
                        return True

            # 首次出现：全部标记
            for key in valid:
                self._add_to_memory(key)
                if self.redis is not None:
                    try:
                        await self.redis.setex(self.redis_prefix + key, int(self.cfg.ttl.total_seconds()), "1")
                    except Exception:  # noqa: BLE001  # Redis 写失败不影响本地去重
                        pass
            return False

    def _add_to_memory(self, key: str) -> None:
        """添加到内存缓存（需持有锁）"""
        now = time.time()
        self._cache[key] = now
        
        # LRU 容量限制
        if len(self._cache) > self.cfg.max_entries:
            # 移除最旧的条目
            self._cache.popitem(last=False)
    
    async def _sweep_loop(self) -> None:
        """后台清理循环"""
        interval_seconds = self.cfg.sweep_interval.total_seconds()
        
        while not self._closed:
            try:
                await asyncio.sleep(interval_seconds)
                await self._sweep()
            except asyncio.CancelledError:
                break
            except Exception:
                pass  # 忽略错误，继续循环
    
    async def _sweep(self) -> None:
        """清理过期条目"""
        now = time.time()
        ttl_seconds = self.cfg.ttl.total_seconds()
        
        async with self._lock:
            # 找出过期的键
            expired = [
                key for key, timestamp in self._cache.items()
                if now - timestamp > ttl_seconds
            ]
            
            # 删除
            for key in expired:
                del self._cache[key]
    
    def dispose(self) -> None:
        """释放资源"""
        self._closed = True
        
        if self._sweep_task:
            self._sweep_task.cancel()


def content_fingerprint(
    conversation_id: str,
    create_at: int,
    msg_type: str,
    content: str,
) -> str:
    """计算内容指纹（SHA-256）
    
    Args:
        conversation_id: 会话ID
        create_at: 创建时间戳
        msg_type: 消息类型
        content: 内容
        
    Returns:
        16字符的 hex 指纹
    """
    h = hashlib.sha256()
    h.update(conversation_id.encode('utf-8'))
    h.update(str(create_at).encode('utf-8'))
    h.update(msg_type.encode('utf-8'))
    h.update(content.encode('utf-8'))
    
    return h.hexdigest()[:16]
