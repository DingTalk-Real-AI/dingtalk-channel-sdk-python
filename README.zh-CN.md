# dingtalk-channel-sdk-python

[English](./README.md) | **简体中文**

钉钉 Channel SDK（Python 版）——与 Agent runtime 解耦的会话接入层：Stream 长连接、入站事件归一化、统一安全管线、AI 卡片流式回复，一个高阶 Channel 全部覆盖（原生 async/await）。

要求 Python 3.8+。

## 安装

```bash
pip install dingtalk-channel-sdk        # 发布后可用；本地：pip install -e .
```

## 最小示例

```python
import asyncio
import os

from dingtalk_channel_sdk import DingTalkChannel

ch = DingTalkChannel(os.environ["DD_CLIENT_ID"], os.environ["DD_CLIENT_SECRET"])


@ch.on_message
async def echo(msg, reply):
    await reply.text(f"received: {msg.text}")


asyncio.run(ch.start())
```

`await ch.start()` 建立 Stream 长连接并阻塞运行（自动重连）；回复走 sessionWebhook，不依赖公网入口。

## 核心特性

- **AI 卡片流式回复**：`await reply.stream()` 立即出"输入中"卡片，`await s.append(tok)` 逐字追加（800ms 节流），`await s.finish()` 定格；孤儿 watchdog 强制收口、卡片失败自动降级文本、超长内容分片续发
- **统一安全管线 SafetyPipeline**：三层推送接口（消息/卡片回调/轻量事件）；三键去重（投递 ID + msgId + 内容指纹）、策略门控（管理员/全局名单/逐群覆盖/@all）、per-chat 串行与批处理、连续媒体窗口合并
- **出站可靠性**：webhook 回复与主动发送指数退避重试；webhook 过期或目标撤回自动转主动发送兜底
- **双传输模式**：Stream（默认，无需公网）与 HTTP 模式（官方验签内置）
- **livecheck 一键真机验收**

流式回复只要三行：

```python
s = await reply.stream()
async for tok in my_llm(msg.text):
    await s.append(tok)
await s.finish()
```

## 文档

| 主题 | 内容 |
|------|------|
| [SPEC.md](./SPEC.md) | 四语言统一契约与 E1–E10 效果验收清单 |
| [GUIDE.md](./GUIDE.md) | 接入指南：让 Agent 接入群聊/单聊 |
| [OVERVIEW.md](./OVERVIEW.md) | 架构分层与模块总览 |
| 高级配置 | 策略 / 钩子 / 批处理 / 出站 / HTTP 模式（见下方「高级配置」） |

## 示例

| 示例 | 说明 |
|------|------|
| `example/echo.py` | 最小回声机器人 |
| `example/fullflow.py` | 全功能：主动发送 + 媒体上传内嵌 |
| `example/livecheck.py` | 真机一键验收 |

## 包边界

业务代码通常只需导入根包：

```python
from dingtalk_channel_sdk import DingTalkChannel, Reply, IncomingMessage
```

内部模块属实现细节，不在兼容性承诺范围内。

## 高级配置

| 配置 | 默认 | 说明 |
|------|------|------|
| `policy_config` | 全开放 | 准入策略：@要求、群/发送者黑白名单、管理员、逐群覆盖、`sender_identity_fields` |
| `chat_queue` | 启用 | 同会话消息强制串行 |
| `media_batch` | 关闭 | 连续图片/文件/音视频窗口内合并投递 |
| `outbound` | — | 统一页脚、before/after-send 钩子、重试参数 |
| `ssrf_allowlist` | — | 内网 CDN 等下载 URL 豁免 |
| `transport` | `stream` | `http` = HTTP 模式（`await ch.handle_http_callback(body, timestamp, sign)`） |
| `ch.on_reject` | — | 拒绝事件回调（含原因），可观测所有被丢弃消息 |

主动发送：`await ch.send_text(SendTarget(user_id="staff-1"), "你好")`（群聊用 `conversation_id`，支持 @）。

## 本地开发

```bash
pip install -e ".[dev]"
pytest                # 108 个测试
```

真实联调：`DD_CLIENT_ID=... DD_CLIENT_SECRET=... python example/livecheck.py`

## License

MIT
