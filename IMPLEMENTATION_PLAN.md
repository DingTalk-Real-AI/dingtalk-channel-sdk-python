# 钉钉 Channel SDK - Python 实现计划

## 项目结构

```
dingtalk-channel-sdk-python/
├── dingtalk_channel_sdk/
│   ├── __init__.py                 ✅ 已完成
│   ├── types.py                    ✅ 已完成
│   │
│   ├── safety/
│   │   ├── __init__.py
│   │   ├── stale_detector.py       ✅ 已完成
│   │   ├── seen_cache.py           ✅ 已完成
│   │   ├── policy_gate.py          🚧 待实现
│   │   ├── media_pipeline.py       🚧 待实现
│   │   ├── processing_lock.py      🚧 待实现
│   │   └── pipeline.py             🚧 待实现
│   │
│   └── channel.py                  🚧 待实现
│
├── tests/
│   ├── test_stale_detector.py      🚧 待实现
│   ├── test_seen_cache.py          🚧 待实现
│   ├── test_policy_gate.py         🚧 待实现
│   ├── test_media_pipeline.py      🚧 待实现
│   └── test_pipeline.py            🚧 待实现
│
├── examples/
│   ├── basic_usage.py              🚧 待实现
│   └── advanced_config.py          🚧 待实现
│
├── pyproject.toml                  🚧 待实现
├── setup.py                        🚧 待实现
├── README.md                       🚧 待实现
└── requirements.txt                🚧 待实现
```

---

## 已完成模块（3/8）

### 1. types.py ✅
- ✅ SafetyConfig、DedupConfig、PolicyConfig
- ✅ IncomingMessage、RejectEvent
- ✅ RejectReason 枚举
- ✅ 使用 dataclass 替代 Go struct
- ✅ 类型提示完整

### 2. stale_detector.py ✅
- ✅ StaleDetector 类
- ✅ is_stale() 方法
- ✅ 默认 30 分钟窗口
- ✅ 对标 Go 实现

### 3. seen_cache.py ✅
- ✅ SeenCache 类
- ✅ check_and_mark_async() 异步版本
- ✅ check_and_mark() 同步包装
- ✅ 内存 LRU 缓存（OrderedDict）
- ✅ Redis 接口定义（Protocol）
- ✅ 后台 sweep 清理
- ✅ content_fingerprint() 函数

---

## 待实现模块（5/8）

### 4. policy_gate.py 🚧
```python
class PolicyGate:
    """策略门控"""
    
    def __init__(self, cfg: PolicyConfig):
        self.cfg = cfg
        self.bot: Optional[BotIdentity] = None
    
    def evaluate(self, msg: IncomingMessage) -> PolicyDecision:
        """评估消息是否允许通过"""
        # 1. 管理员绕过
        if self._is_admin(msg.sender_staff_id):
            return PolicyDecision(allowed=True)
        
        # 2. 全局黑名单
        if self._is_denied(msg.sender_staff_id):
            return PolicyDecision(allowed=False, reason=RejectReason.SENDER_DENIED)
        
        # 3. 全局白名单
        if self.cfg.allow_from and not self._is_in_allow_from(msg.sender_staff_id):
            return PolicyDecision(allowed=False, reason=RejectReason.SENDER_NOT_ALLOWED)
        
        # 4. 按会话类型评估
        if msg.conversation_type == "group":
            return self._evaluate_group(msg)
        else:
            return self._evaluate_dm(msg)
    
    def _evaluate_group(self, msg: IncomingMessage) -> PolicyDecision:
        """评估群组消息"""
        # 群组黑名单
        if msg.conversation_id in self.cfg.group_blocklist:
            return PolicyDecision(allowed=False, reason=RejectReason.GROUP_BLOCKED)
        
        # 群组白名单
        if self.cfg.group_allowlist:
            if msg.conversation_id not in self.cfg.group_allowlist:
                # 检查是否有群组覆盖
                override = self.cfg.group_overrides.get(msg.conversation_id)
                if not override:
                    return PolicyDecision(allowed=False, reason=RejectReason.GROUP_NOT_ALLOWED)
        
        # 群组覆盖
        override = self.cfg.group_overrides.get(msg.conversation_id)
        if override:
            # 检查覆盖的黑名单
            if override.block_from and msg.sender_staff_id in override.block_from:
                return PolicyDecision(allowed=False, reason=RejectReason.SENDER_DENIED)
            
            # 检查覆盖的白名单
            if override.allow_from and msg.sender_staff_id not in override.allow_from:
                return PolicyDecision(allowed=False, reason=RejectReason.SENDER_NOT_ALLOWED)
            
            # @机器人检查
            require_mention = override.require_mention if override.require_mention is not None else self.cfg.require_mention
            if require_mention and not msg.is_in_at_list:
                return PolicyDecision(allowed=False, reason=RejectReason.NO_MENTION)
            
            # @all 检查
            respond_to_all = override.respond_to_mention_all if override.respond_to_mention_all is not None else self.cfg.respond_to_mention_all
            if msg.mention_all and not respond_to_all:
                return PolicyDecision(allowed=False, reason=RejectReason.MENTION_ALL_BLOCKED)
        else:
            # 全局配置
            if self.cfg.require_mention and not msg.is_in_at_list:
                return PolicyDecision(allowed=False, reason=RejectReason.NO_MENTION)
            
            if msg.mention_all and not self.cfg.respond_to_mention_all:
                return PolicyDecision(allowed=False, reason=RejectReason.MENTION_ALL_BLOCKED)
        
        return PolicyDecision(allowed=True)
    
    def _evaluate_dm(self, msg: IncomingMessage) -> PolicyDecision:
        """评估私聊消息"""
        if self.cfg.dm_mode == "disabled":
            return PolicyDecision(allowed=False, reason=RejectReason.DM_DISABLED)
        
        if self.cfg.dm_mode == "allowlist":
            if msg.sender_id not in self.cfg.dm_allowlist:
                return PolicyDecision(allowed=False, reason=RejectReason.DM_NOT_ALLOWED)
        
        if self.cfg.dm_mode == "blocklist":
            if msg.sender_id in self.cfg.dm_blocklist:
                return PolicyDecision(allowed=False, reason=RejectReason.DM_BLOCKED)
        
        return PolicyDecision(allowed=True)
    
    def _is_admin(self, staff_id: str) -> bool:
        return staff_id in self.cfg.admins if staff_id else False
    
    def _is_denied(self, staff_id: str) -> bool:
        return staff_id in self.cfg.deny_from if staff_id else False
    
    def _is_in_allow_from(self, staff_id: str) -> bool:
        return staff_id in self.cfg.allow_from if staff_id else False
    
    def update_config(self, cfg: PolicyConfig) -> None:
        """更新配置"""
        self.cfg = cfg
    
    def set_bot_identity(self, bot: BotIdentity) -> None:
        """设置机器人身份"""
        self.bot = bot
```

### 5. media_pipeline.py 🚧
```python
class MediaPipelineManager:
    """媒体批处理管理器"""
    
    def __init__(self, cfg: MediaBatchConfig):
        self.cfg = cfg
        self.buckets: Dict[str, MediaBucket] = {}
        self.lock = asyncio.Lock()
        self.handler: Optional[MediaFlushHandler] = None
    
    def is_compatible(self, msg: IncomingMessage) -> bool:
        """检查是否为可批处理的媒体类型"""
        if not self.cfg.enabled:
            return False
        return msg.msg_type in ["picture", "file", "audio", "video"]
    
    async def push(
        self,
        msg: IncomingMessage,
        handler: MediaFlushHandler,
    ) -> None:
        """推送媒体消息到批次"""
        self.handler = handler
        key = self._batch_key(msg)
        
        async with self.lock:
            if key not in self.buckets:
                self.buckets[key] = MediaBucket(
                    sources=[],
                    timer=None,
                )
            
            bucket = self.buckets[key]
            bucket.sources.append(msg)
            
            # 达到容量上限，立即刷新
            if len(bucket.sources) >= self.cfg.max_items:
                await self._flush_bucket(key)
                return
            
            # 重置定时器
            if bucket.timer:
                bucket.timer.cancel()
            
            bucket.timer = asyncio.create_task(
                self._delayed_flush(key, self.cfg.delay_ms / 1000)
            )
    
    async def flush_incompatible_for(self, msg: IncomingMessage) -> None:
        """刷新与当前消息不兼容的批次"""
        chat_id = msg.conversation_id
        
        async with self.lock:
            keys_to_flush = [k for k in self.buckets.keys() if k.startswith(chat_id)]
            for key in keys_to_flush:
                await self._flush_bucket(key)
    
    async def _flush_bucket(self, key: str) -> None:
        """刷新批次"""
        bucket = self.buckets.pop(key, None)
        if not bucket or not bucket.sources:
            return
        
        if bucket.timer:
            bucket.timer.cancel()
        
        merged = self._merge_sources(bucket.sources)
        
        if self.handler:
            await self.handler(merged)
    
    async def _delayed_flush(self, key: str, delay: float) -> None:
        """延迟刷新"""
        await asyncio.sleep(delay)
        async with self.lock:
            await self._flush_bucket(key)
    
    def _batch_key(self, msg: IncomingMessage) -> str:
        """计算批次键"""
        return f"{msg.conversation_id}:{msg.msg_type}"
    
    def _merge_sources(self, sources: List[IncomingMessage]) -> IncomingMessage:
        """合并消息"""
        if len(sources) == 1:
            return sources[0]
        
        # 使用最后一条消息作为基础
        merged = sources[-1]
        
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
        """获取类型显示名称"""
        names = {
            "picture": "图片",
            "file": "文件",
            "audio": "语音",
            "video": "视频",
        }
        return names.get(msg_type, "媒体")
    
    async def dispose(self) -> None:
        """释放资源"""
        async with self.lock:
            for key in list(self.buckets.keys()):
                await self._flush_bucket(key)

@dataclass
class MediaBucket:
    sources: List[IncomingMessage]
    timer: Optional[asyncio.Task]
```

### 6. processing_lock.py 🚧
```python
class ProcessingLock:
    """处理锁 - 防止并发处理"""
    
    def __init__(self, ttl: timedelta, sweep_interval: timedelta):
        self.ttl = ttl
        self.sweep_interval = sweep_interval
        self.locks: Dict[str, float] = {}
        self.lock = asyncio.Lock()
        self.closed = False
        self.sweep_task = asyncio.create_task(self._sweep_loop())
    
    def acquire(self, key: str) -> bool:
        """尝试获取锁"""
        now = time.time()
        
        # 检查是否已锁定
        if key in self.locks:
            # 检查是否过期
            if now - self.locks[key] < self.ttl.total_seconds():
                return False  # 仍被锁定
        
        # 获取锁
        self.locks[key] = now
        return True
    
    def release(self, key: str) -> None:
        """释放锁"""
        self.locks.pop(key, None)
    
    async def _sweep_loop(self) -> None:
        """后台清理循环"""
        while not self.closed:
            try:
                await asyncio.sleep(self.sweep_interval.total_seconds())
                await self._sweep()
            except asyncio.CancelledError:
                break
    
    async def _sweep(self) -> None:
        """清理过期锁"""
        now = time.time()
        ttl_seconds = self.ttl.total_seconds()
        
        async with self.lock:
            expired = [
                key for key, timestamp in self.locks.items()
                if now - timestamp > ttl_seconds
            ]
            for key in expired:
                del self.locks[key]
    
    def dispose(self) -> None:
        """释放资源"""
        self.closed = True
        if self.sweep_task:
            self.sweep_task.cancel()
```

### 7. pipeline.py 🚧
```python
class SafetyPipeline:
    """统一安全管线 - 对标 Go SDK"""
    
    def __init__(
        self,
        cfg: SafetyConfig,
        on_message: MessageHandler,
        on_reject: Optional[RejectHandler] = None,
        bot_robot_code: Optional[str] = None,
    ):
        self.cfg = cfg
        self.on_message = on_message
        self.on_reject = on_reject
        self.bot_robot_code = bot_robot_code
        
        # 创建组件
        self.stale = StaleDetector(cfg.stale_window)
        self.seen = SeenCache(cfg.dedup)
        self.lock = ProcessingLock(cfg.lock_ttl, timedelta(minutes=1))
        self.policy = PolicyGate(cfg.policy)
        self.media = MediaPipelineManager(cfg.media_batch)
    
    async def push_message(
        self,
        proto_id: str,
        msg: IncomingMessage,
    ) -> None:
        """推送消息到完整安全管线"""
        # 1. 过期检测
        if self.stale.is_stale(msg.create_at):
            await self._emit_reject(msg, RejectReason.STALE)
            return
        
        # 2. 去重
        fingerprint = ""
        if self.cfg.dedup.enable_fingerprint:
            from .seen_cache import content_fingerprint
            fingerprint = content_fingerprint(
                msg.conversation_id,
                msg.create_at,
                msg.msg_type,
                msg.text,
            )
        
        if await self.seen.check_and_mark_async(proto_id, msg.msg_id, fingerprint):
            await self._emit_reject(msg, RejectReason.DUPLICATE)
            return
        
        # 3. 自回复过滤
        if self.cfg.drop_self_sent and self.bot_robot_code and msg.sender_id == self.bot_robot_code:
            await self._emit_reject(msg, RejectReason.SELF_SENT)
            return
        
        # 4. 策略门控
        decision = self.policy.evaluate(msg)
        if not decision.allowed:
            await self._emit_reject(msg, decision.reason)
            return
        
        # 5. 处理锁
        if not self.lock.acquire(msg.msg_id):
            await self._emit_reject(msg, RejectReason.LOCK_CONTENTION)
            return
        
        # 6. 媒体批处理
        if self.media.is_compatible(msg):
            await self.media.push(msg, self._media_flush_handler)
            return
        
        # 7. 非媒体消息：刷新待处理的媒体批次
        if self.media.cfg.enabled:
            await self.media.flush_incompatible_for(msg)
        
        # 8. 直接处理（TODO: 文本批处理）
        await self._message_flush_handler(msg, [msg])
    
    async def push_action(
        self,
        event_id: str,
        handler: Callable[[], Awaitable[None]],
    ) -> None:
        """推送动作到简化管线（卡片回调）"""
        # 1. 去重
        if await self.seen.check_and_mark_async(event_id):
            return
        
        # 2. 处理锁
        if not self.lock.acquire(event_id):
            return
        
        try:
            # 3. 执行
            await handler()
        finally:
            self.lock.release(event_id)
    
    async def push_light(
        self,
        event_id: str,
        handler: Callable[[], Awaitable[None]],
    ) -> None:
        """推送轻量事件（仅去重）"""
        if await self.seen.check_and_mark_async(event_id):
            return
        
        await handler()
    
    async def _media_flush_handler(self, merged: IncomingMessage) -> None:
        """媒体批次刷新回调"""
        try:
            sources = merged.batched_sources or [merged]
            await self.on_message(merged, sources)
        finally:
            self.lock.release(merged.msg_id)
    
    async def _message_flush_handler(
        self,
        merged: IncomingMessage,
        sources: List[IncomingMessage],
    ) -> None:
        """消息批次刷新回调"""
        try:
            await self.on_message(merged, sources)
        finally:
            for msg in sources:
                self.lock.release(msg.msg_id)
    
    async def _emit_reject(
        self,
        msg: IncomingMessage,
        reason: RejectReason,
    ) -> None:
        """触发拒绝事件"""
        if self.on_reject:
            event = RejectEvent(
                message_id=msg.msg_id,
                chat_id=msg.conversation_id,
                sender_id=msg.sender_id,
                reason=reason,
            )
            await self.on_reject(event)
    
    def set_bot_identity(self, robot_code: str) -> None:
        """设置机器人身份"""
        self.bot_robot_code = robot_code
        if robot_code:
            from ..types import BotIdentity
            self.policy.set_bot_identity(BotIdentity(robot_code=robot_code))
    
    def update_policy(self, cfg: PolicyConfig) -> None:
        """更新策略配置"""
        self.policy.update_config(cfg)
    
    async def dispose(self) -> None:
        """释放资源"""
        self.seen.dispose()
        self.lock.dispose()
        await self.media.dispose()
```

### 8. channel.py 🚧
```python
class Channel:
    """钉钉 Channel 客户端"""
    
    def __init__(self, config: ChannelConfig):
        self.config = config
        
        # 创建 SafetyPipeline
        self.pipeline = SafetyPipeline(
            cfg=config.safety,
            on_message=self._handle_message,
            on_reject=self._handle_reject,
        )
    
    async def _handle_message(
        self,
        msg: IncomingMessage,
        sources: List[IncomingMessage],
    ) -> None:
        """内部消息处理"""
        # 委托给用户的 handler
        if self.on_message_handler:
            await self.on_message_handler(msg, sources)
    
    async def _handle_reject(self, event: RejectEvent) -> None:
        """内部拒绝处理"""
        # 委托给用户的 handler
        if self.on_reject_handler:
            await self.on_reject_handler(event)
    
    def on_message(self, handler: MessageHandler) -> None:
        """注册消息处理器"""
        self.on_message_handler = handler
    
    def on_reject(self, handler: RejectHandler) -> None:
        """注册拒绝处理器"""
        self.on_reject_handler = handler
    
    async def start(self) -> None:
        """启动 Channel"""
        # TODO: 实现 Stream 连接
        pass
    
    async def close(self) -> None:
        """关闭 Channel"""
        await self.pipeline.dispose()
```

---

## 测试文件计划

### test_stale_detector.py
```python
import pytest
from dingtalk_channel_sdk.safety.stale_detector import StaleDetector
from datetime import timedelta
import time

def test_stale_detector_fresh_message():
    detector = StaleDetector(timedelta(minutes=30))
    now_ms = int(time.time() * 1000)
    assert not detector.is_stale(now_ms)

def test_stale_detector_stale_message():
    detector = StaleDetector(timedelta(minutes=10))
    old_ms = int((time.time() - 15 * 60) * 1000)  # 15 分钟前
    assert detector.is_stale(old_ms)

def test_stale_detector_invalid_timestamp():
    detector = StaleDetector()
    assert not detector.is_stale(0)
    assert not detector.is_stale(-1)
```

### test_seen_cache.py
```python
import pytest
from dingtalk_channel_sdk.safety.seen_cache import SeenCache, content_fingerprint
from dingtalk_channel_sdk.types import DedupConfig
from datetime import timedelta

@pytest.mark.asyncio
async def test_seen_cache_basic():
    cfg = DedupConfig(ttl=timedelta(minutes=5), max_entries=100)
    cache = SeenCache(cfg)
    
    # 首次见到
    assert not await cache.check_and_mark_async("key1")
    
    # 重复
    assert await cache.check_and_mark_async("key1")
    
    cache.dispose()

@pytest.mark.asyncio
async def test_seen_cache_multi_keys():
    cfg = DedupConfig(ttl=timedelta(minutes=5), max_entries=100)
    cache = SeenCache(cfg)
    
    # 三键去重
    assert not await cache.check_and_mark_async("proto1", "msg1", "fp1")
    assert await cache.check_and_mark_async("proto1", "msg1", "fp1")
    
    # 不同键
    assert not await cache.check_and_mark_async("proto2", "msg2", "fp2")
    
    cache.dispose()

def test_content_fingerprint():
    fp1 = content_fingerprint("chat1", 123456, "text", "hello")
    fp2 = content_fingerprint("chat1", 123456, "text", "hello")
    fp3 = content_fingerprint("chat1", 123456, "text", "world")
    
    assert fp1 == fp2
    assert fp1 != fp3
    assert len(fp1) == 16
```

---

## 依赖管理

### requirements.txt
```
# 核心依赖
aiohttp>=3.8.0
redis>=4.5.0  # 可选，用于 Redis 缓存

# 开发依赖
pytest>=7.0.0
pytest-asyncio>=0.21.0
pytest-cov>=4.0.0
black>=23.0.0
mypy>=1.0.0
flake8>=6.0.0
```

### pyproject.toml
```toml
[build-system]
requires = ["setuptools>=65.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "dingtalk-channel-sdk"
version = "0.1.0"
description = "钉钉 Channel SDK - 企业级安全管线"
authors = [{name = "DingTalk Team"}]
requires-python = ">=3.8"
dependencies = [
    "aiohttp>=3.8.0",
]

[project.optional-dependencies]
redis = ["redis>=4.5.0"]
dev = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.0.0",
    "black>=23.0.0",
    "mypy>=1.0.0",
    "flake8>=6.0.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.black]
line-length = 100
target-version = ['py38', 'py39', 'py310', 'py311']

[tool.mypy]
python_version = "3.8"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
```

---

## 使用示例

### examples/basic_usage.py
```python
import asyncio
from dingtalk_channel_sdk import SafetyPipeline, IncomingMessage, RejectEvent
from dingtalk_channel_sdk.types import default_safety_config

async def handle_message(msg: IncomingMessage, sources: list) -> None:
    print(f"收到消息: {msg.text}")

async def handle_reject(event: RejectEvent) -> None:
    print(f"消息被拒绝: {event.message_id}, 原因: {event.reason}")

async def main():
    # 创建安全管线
    pipeline = SafetyPipeline(
        cfg=default_safety_config(),
        on_message=handle_message,
        on_reject=handle_reject,
        bot_robot_code="robot123",
    )
    
    # 模拟推送消息
    msg = IncomingMessage(
        conversation_id="chat1",
        conversation_type="group",
        sender_id="user1",
        sender_staff_id="staff1",
        msg_id="msg1",
        msg_type="text",
        text="Hello!",
        create_at=int(time.time() * 1000),
    )
    
    await pipeline.push_message("proto1", msg)
    
    # 清理
    await pipeline.dispose()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 实现优先级

### 阶段 1：核心模块（1-2天）
1. ✅ types.py
2. ✅ stale_detector.py
3. ✅ seen_cache.py
4. 🚧 policy_gate.py
5. 🚧 processing_lock.py

### 阶段 2：管线集成（1天）
6. 🚧 media_pipeline.py
7. 🚧 pipeline.py

### 阶段 3：Channel 集成（1天）
8. 🚧 channel.py

### 阶段 4：测试与文档（1-2天）
9. 🚧 所有测试文件
10. 🚧 README.md
11. 🚧 示例代码

**总预计时间**：4-6天

---

## 下一步行动

1. **完成 policy_gate.py** - 最复杂的模块
2. **完成 processing_lock.py** - 较简单
3. **完成 media_pipeline.py** - 中等复杂度
4. **完成 pipeline.py** - 整合所有模块
5. **编写测试** - 确保质量
6. **文档和示例** - 易于使用

---

**当前进度**：3/8 模块完成（38%）  
**预计完成时间**：4-6天全职工作
