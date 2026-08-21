# DingTalk Channel SDK Family · Project Overview

**English** | [简体中文](./OVERVIEW.zh-CN.md)

> Origin issue: [DingTalk-Real-AI/dingtalk-workspace-cli#796](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/issues/796)—"Will DingTalk release an integrated SDK like Channel in the future?"
> This project provides the answer: **Four languages, effect-aligned, ready to use**.

## 1. Deliverables

| Repository | Language | Dependencies | Tests |
|---|---|---|---|
| [DingTalk-Real-AI/dingtalk-channel-sdk-go](https://github.com/DingTalk-Real-AI/dingtalk-channel-sdk-go) | Go 1.22+ | gorilla/websocket | 19 tests (-race clean) |
| [DingTalk-Real-AI/dingtalk-channel-sdk-nodejs](https://github.com/DingTalk-Real-AI/dingtalk-channel-sdk-nodejs) | Node 18+ | ws | 12 tests |
| [DingTalk-Real-AI/dingtalk-channel-sdk-python](https://github.com/DingTalk-Real-AI/dingtalk-channel-sdk-python) | Python 3.10+ | websockets | 12 tests |
| [DingTalk-Real-AI/dingtalk-channel-sdk-java](https://github.com/DingTalk-Real-AI/dingtalk-channel-sdk-java) | JDK 8+ | Java-WebSocket + Gson | 12 tests |

Each repository contains: complete source code, unit tests, `SPEC.md` (four-language unified specification), streaming echo examples, **livecheck real integration program**, README, MIT LICENSE.

## 2. Positioning and Boundaries

**Session access layer decoupled from Agent runtime**. The SDK handles all the "channel" dirty work, developers only write "what the user says, what the bot replies":

- **Handles**: Stream connection (connect/heartbeat/exponential backoff reconnect/server disconnect self-healing), event dual-layer deduplication + stale message filtering, sessionWebhook replies (text/Markdown/image, automatic chunking for overlong content), AI card streaming output (typewriter, frame interval race prevention, watchdog orphan protection), card API global rate limiting and QpsLimit backoff, media upload/download and media messages (file/video/audio), Markdown normalization, proactive send (DM/group + @), 🤔Thinking/🥳Done status badges, explicit abort, error fallback cooldown
- **Does NOT handle**: Agent runtime (model/prompt/tool orchestration), conversation context persistence, credential storage, business operations (documents/spreadsheets/calendars—domain of dws CLI and skills)

## 3. Quick Start (Four Languages Isomorphic)

```go
ch := channel.New(channel.Config{ClientID: "ding...", ClientSecret: "..."})
ch.OnMessage(func(ctx context.Context, msg *channel.IncomingMessage, reply channel.Reply) error {
    s, _ := reply.Stream(ctx)              // "Typing..." card appears within seconds
    for _, tok := range myLLM(msg.Text) {
        _ = s.Append(tok)                  // Typewriter append (800ms throttle+trailing flush)
    }
    return s.Finish("")                    // Final frame freeze
})
ch.Start(ctx)
```

```js
ch.on('message', async (msg, reply) => { const s = await reply.stream(); ... await s.finish(); });
```
```python
@ch.on_message
async def handle(msg, reply): s = await reply.stream(); await s.append(tok); await s.finish()
```
```java
ch.onMessage((msg, reply) -> { CardStreamer s = reply.stream(); s.append(tok); s.finish(""); });
```

Non-streaming: `reply.Text/Markdown/Image`, attachment download `reply.DownloadURL`, media upload `reply.UploadMedia`;
Proactive send (independent of incoming messages): `ch.SendText/SendMarkdown/SendImage`, group send supports `AtUserIds/AtAll`;
Card interaction: `ch.OnCardAction` (registration auto-subscribes card topic).

## 4. Effect Parity (Acceptance Checklist E1–E10, see SPEC §0)

| | User-Visible Effect | Implementation |
|---|---|---|
| E1 | "Typing..." card appears within seconds after sending message | `stream()` creates card immediately+delivers INPUTING |
| E2 | Typewriter smooth append | streaming interface + 800ms throttle + **trailing flush** (no loss within window) + long interval 300ms batching |
| E3 | Loading disappears after completion, Markdown freezes | isFinalize final frame + FINISHED status |
| E4 | Card failure/rate limiting imperceptible to user | Silent fallback to webhook text; QpsLimit backoff 2s retry |
| E5 | Same experience in group chat/DM | Same Reply API; delivery target auto-selected; group chat strips @ prefix |
| E6 | Never duplicate replies | messageId+msgId dual-layer deduplication (TTL 5min) |
| E7 | Card interaction loop | OnCardAction + auto-subscribe |
| E8 | Never goes offline | Exponential backoff reconnect + disconnect immediate reconnect + 120s/5s heartbeat + ACK-first |
| E9 | Media send/receive | uploadMedia (OAPI multipart) / downloadURL / image reply |
| E10 | Markdown rendering quality | normalizeForCard (code blocks/tables/quotes DingTalk rendering rules) |

Each item has corresponding unit tests in all four languages; E8 has dedicated disconnect-reconnect e2e regression.

## 5. Protocol Fidelity (True Source, Not Documentation Speculation)

| Capability | True Source |
|---|---|
| Stream wire protocol (open/wss/frames/ACK/heartbeat/topic constants) | Official dingtalk-stream-sdk-go source code line-by-line comparison |
| AI card five-step protocol + rate limiting + Markdown normalization | Official connector (dingtalk-openclaw-connector) card.ts |
| Token (new/OAPI dual-track) and sessionWebhook payload | Official connector token.ts / messaging.ts + official documentation validation |
| Proactive send API | dws (dingtalk-workspace-cli) source code |
| Ticket encoding / localIp / UA headers | Four official stream SDKs cross-reference alignment |

Protocol-level issues fixed during review rounds: msgParam stringified JSON (official documentation requirement), Go version disconnect mis-stop, ACK-first semantics, ticket URL encoding.

## 6. Key Architecture Decision: Why Custom Transport Layer Instead of Official stream-sdk

1. **Official connector itself doesn't trust them**: DingTalk official connector source code sets `autoReconnect:false, keepAlive:false` all off and rewrites (issues #571/#536/#573)
2. **Four-language consistency is this project's acceptance standard**, while official four SDKs have inconsistent heartbeat/reconnect/encoding behavior, referencing them inherits divergence
3. **Dependency weight**: Official Java version pulls Netty multi-module, Python version bundles requests+aiohttp dual HTTP stack; custom implementation each language only 1–2 small dependencies, transport layer ~300 lines/language
4. Leaves evolution seam (`Channel → StreamConn → onFrame` single boundary), can add official adapter backend when upstream SDK matures

## 7. Quality Evidence

- **Tests**: Go 14 / Node 12 / Python 12 / Java 12 (BUILD SUCCESS), all include e2e (fake gateway+fake API) and disconnect-reconnect regression
- **Real integration**: Each language `example/livecheck` one-command verification (connect→receive message→text reply→card streaming full cycle→media upload, progressive PASS/FAIL):
  `DD_CLIENT_ID=... DD_CLIENT_SECRET=... go run ./example/livecheck` (Node `npm run live`; Python `python example/livecheck.py`; Java `mvn exec:java`)
- **Code review**: Three rounds (protocol consistency / effect parity / stream layer vs official SDK), fixed 8 issues, all have regression tests

## 8. Known Boundaries and Roadmap

- Real credential integration run pending execution (livecheck ready, one command)
- >20MB file chunked upload (v0.2)
- Card template default value is connector built-in template, external release should emphasize configurability
- Platform differentiators (reaction/comments/forwarding) follow DingTalk Open Platform evolution
- Optional: official stream-sdk adapter backend (`WithTransport` seam reserved)

## 9. Prerequisites

Create **enterprise internal application** in DingTalk developer backend and enable bot, obtain ClientID/ClientSecret. Stream mode requires no public IP or domain.

---
License: MIT | Specification: Each repo's `SPEC.md` | Version: v0.1.0 (2026-08)
