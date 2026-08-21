"""媒体批处理管线 - 对标 Go SDK internal/safety/media_pipeline.go"""

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional, Callable, Awaitable
from ..config import MediaBatchConfig
from ..normalize.message import IncomingMessage


# 类型别名
MediaFlushHandler = Callable[[IncomingMessage], Awaitable[None]]


@dataclass
class MediaBucket:
    """媒体批次桶"""
    sources: List[IncomingMessage]
    timer: Optional[asyncio.Task]


class MediaPipelineManager:
    """媒体批处理管理器
    
    用于合并连续上传的图片、文件、音视频等媒体消息。
    
    设计原理：
    - 批次键：(chatID, msgType) 二元组
    - 兼容运行：相同键、相同类型、在延迟窗口内
    - 不兼容推送：不同类型或文本消息介入时，先刷新当前批次
    - 容量上限：达到 max_items 时立即刷新
    """
    
    def __init__(self, cfg: MediaBatchConfig):
        """初始化媒体批处理管理器
        
        Args:
            cfg: 媒体批处理配置
        """
        self.cfg = cfg
        self.buckets: Dict[str, MediaBucket] = {}
        self._lock = asyncio.Lock()
        self.handler: Optional[MediaFlushHandler] = None
    
    def is_compatible(self, msg: IncomingMessage) -> bool:
        """检查消息是否为可批处理的媒体类型
        
        Args:
            msg: 入站消息
            
        Returns:
            True 表示可批处理的媒体类型
        """
        if not self.cfg.enabled:
            return False
        
        # 钉钉支持的媒体类型：picture, file, audio, video
        return msg.msg_type in ["picture", "file", "audio", "video"]
    
    async def push(
        self,
        msg: IncomingMessage,
        handler: MediaFlushHandler,
    ) -> None:
        """推送媒体消息到批次
        
        Args:
            msg: 媒体消息
            handler: 批次刷新回调
        """
        self.handler = handler
        key = self._batch_key(msg)
        
        async with self._lock:
            # 创建或获取批次桶
            if key not in self.buckets:
                self.buckets[key] = MediaBucket(
                    sources=[],
                    timer=None,
                )
            
            bucket = self.buckets[key]
            bucket.sources.append(msg)
            
            # 达到容量上限，立即刷新
            if len(bucket.sources) >= self.cfg.max_items:
                self._cancel_timer(bucket)
                await self._flush_bucket(key)
                return
            
            # 重置定时器
            self._cancel_timer(bucket)
            delay_seconds = float(self.cfg.delay_s)
            bucket.timer = asyncio.create_task(
                self._delayed_flush(key, delay_seconds)
            )
    
    async def flush_incompatible_for(self, msg: IncomingMessage) -> None:
        """刷新与当前消息不兼容的批次
        
        当非媒体消息到达时，刷新该会话的所有待处理批次，保证消息顺序。
        
        Args:
            msg: 当前消息
        """
        chat_id = msg.conversation_id
        
        async with self._lock:
            # 找到所有属于该会话的批次（首段精确匹配，避免 chatID 前缀碰撞）
            keys_to_flush = [k for k in self.buckets.keys() if self._chat_of(k) == chat_id]
            
            # 刷新所有找到的批次
            for key in keys_to_flush:
                await self._flush_bucket(key)
    
    async def _flush_bucket(self, key: str) -> None:
        """刷新指定批次
        
        Args:
            key: 批次键
        """
        bucket = self.buckets.pop(key, None)
        if not bucket or not bucket.sources:
            return
        
        # 取消定时器
        self._cancel_timer(bucket)
        
        # 合并消息
        merged = self._merge_sources(bucket.sources)
        
        # 同步调用 handler；异常记录日志，避免 fire-and-forget 静默丢失
        if self.handler:
            try:
                await self.handler(merged)
            except Exception as err:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning("media_pipeline: flush handler raised: %s", err)
    
    async def _delayed_flush(self, key: str, delay: float) -> None:
        """延迟刷新
        
        Args:
            key: 批次键
            delay: 延迟秒数
        """
        try:
            await asyncio.sleep(delay)
            async with self._lock:
                await self._flush_bucket(key)
        except asyncio.CancelledError:
            pass
    
    _SEP = "\x00"  # 不可见分隔符，避免 chatID 前缀碰撞

    def _batch_key(self, msg: IncomingMessage) -> str:
        """计算批次键：(chatID, msgType, replyParent) 三元组。

        reply 消息按被引用内容区分，不同引用目标不互相合并
        （批次键含引用上下文；钉钉无话题场景，故不含 thread 维度）。
        """
        import hashlib

        key = msg.conversation_id + self._SEP + msg.msg_type
        if msg.msg_type == "reply" and msg.content:
            digest = hashlib.sha256(
                msg.content if isinstance(msg.content, bytes) else str(msg.content).encode()
            ).hexdigest()[:16]
            key += self._SEP + digest
        return key

    def _chat_of(self, key: str) -> str:
        """批次键所属会话（首段精确匹配）。"""
        return key.split(self._SEP, 1)[0]
    
    def _merge_sources(self, sources: List[IncomingMessage]) -> IncomingMessage:
        """合并批次中的消息
        
        使用最后一条消息作为载体（最新的元数据），将所有源消息附加到 batched_sources。
        
        Args:
            sources: 源消息列表
            
        Returns:
            合并后的消息
        """
        if len(sources) == 1:
            return sources[0]
        
        # 使用最后一条消息作为基础（复制）
        import copy
        merged = copy.deepcopy(sources[-1])
        
        # 合并所有 Resources
        all_resources = []
        for msg in sources:
            all_resources.extend(msg.resources)
        merged.resources = all_resources
        
        # 更新文本提示
        count = len(sources)
        kind_name = self._kind_display_name(merged.msg_type)
        merged.text = f"[{count}个{kind_name}]"
        
        # 保留原始消息列表
        merged.batched_sources = sources
        
        return merged
    
    def _kind_display_name(self, msg_type: str) -> str:
        """获取媒体类型的显示名称
        
        Args:
            msg_type: 消息类型
            
        Returns:
            显示名称
        """
        names = {
            "picture": "图片",
            "file": "文件",
            "audio": "语音",
            "video": "视频",
        }
        return names.get(msg_type, "媒体")
    
    def _cancel_timer(self, bucket: MediaBucket) -> None:
        """取消定时器
        
        Args:
            bucket: 批次桶
        """
        if bucket.timer:
            bucket.timer.cancel()
            bucket.timer = None
    
    async def dispose(self) -> None:
        """释放资源，刷新所有待处理批次"""
        async with self._lock:
            for key in list(self.buckets.keys()):
                await self._flush_bucket(key)
