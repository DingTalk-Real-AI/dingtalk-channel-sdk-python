# dingtalk-channel-sdk-python

**English** | [简体中文](./README.zh-CN.md)

DingTalk Channel SDK (Python) — a conversation access layer decoupled from any agent runtime: Stream long connection, inbound event normalization, a unified safety pipeline, and streaming AI-card replies, all behind one high-level Channel (native async/await).

Requires Python 3.8+.

## Install

```bash
pip install dingtalk-channel-sdk        # after release; locally: pip install -e .
```

## Minimal Example

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

`await ch.start()` establishes the Stream long connection and blocks (auto-reconnect); replies go through the sessionWebhook, no public ingress required.

## Highlights

- **Streaming AI-card replies**: `await reply.stream()` delivers a "typing" card immediately, `await s.append(tok)` streams tokens (800ms throttle), `await s.finish()` freezes the card; orphan watchdog, text fallback on card failure, chunked continuation for over-long content
- **Unified SafetyPipeline**: three-tier push interface (message / card callback / light event); three-key dedup (delivery ID + msgId + content fingerprint), policy gate (admins / global lists / per-group overrides / @all), per-chat serialization and batching, consecutive-media window merging
- **Outbound reliability**: webhook replies and proactive sends retry with exponential backoff; expired or revoked webhooks automatically fall back to proactive send
- **Dual transport**: Stream (default) and HTTP mode (official signature verification built in)
- **livecheck**: one-command verification against the real environment

Streaming in three lines:

```python
s = await reply.stream()
async for tok in my_llm(msg.text):
    await s.append(tok)
await s.finish()
```

## Documentation

| Topic | Content |
|-------|---------|
| [SPEC.md](./SPEC.md) | Shared four-language contract and the E1–E10 acceptance checklist |
| [GUIDE.md](./GUIDE.md) | Integration guide: bring your agent into DMs and group chats |
| [OVERVIEW.md](./OVERVIEW.md) | Architecture layers and module overview |
| Advanced config | Policy / hooks / batching / outbound / HTTP mode (see below) |

## Examples

| Example | Description |
|---------|-------------|
| `example/echo.py` | Minimal echo bot |
| `example/fullflow.py` | Full feature: proactive send + media upload & embedding |
| `example/livecheck.py` | One-command live verification |

## Package Boundaries

Application code typically imports only the root package:

```python
from dingtalk_channel_sdk import DingTalkChannel, Reply, IncomingMessage
```

Internal modules are implementation details and carry no compatibility promise.

## Advanced Config

| Option | Default | Description |
|--------|---------|-------------|
| `policy_config` | allow all | Admission policy: @-mention requirement, group/sender allow-block lists, admins, per-group overrides, `sender_identity_fields` |
| `chat_queue` | enabled | Strict per-conversation serialization |
| `media_batch` | disabled | Merge consecutive pictures/files/audio/video within a window |
| `outbound` | — | Unified footer, before/after-send hooks, retry options |
| `ssrf_allowlist` | — | Exempt internal CDN download URLs |
| `transport` | `stream` | `http` = HTTP mode (`await ch.handle_http_callback(body, timestamp, sign)`) |
| `ch.on_reject` | — | Reject-event callback (with reason) for full observability of dropped messages |

Proactive send: `await ch.send_text(SendTarget(user_id="staff-1"), "hello")` (groups use `conversation_id`, @ mentions supported).

## Development

```bash
pip install -e ".[dev]"
pytest                # 108 tests
```

Live check: `DD_CLIENT_ID=... DD_CLIENT_SECRET=... python example/livecheck.py`

## License

MIT
