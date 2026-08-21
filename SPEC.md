# DingTalk Channel SDK — Four-Language Unified Specification (SPEC v0.1)

**English** | [简体中文](./SPEC.zh-CN.md)

> Positioning: **Session access layer decoupled from Agent runtime**. The SDK handles the "channel" dirty work,
> while developers only write "what the user says and what the bot replies".

## 0. Effect Parity Acceptance Checklist (Four Languages Uniformly Verified by This)

Verified by **terminal-observable behavior**, demonstrable item by item:

| # | User-Visible Effect | SDK Implementation |
|---|---|---|
| E1 | "Typing..." card appears **within seconds** after sending message (loading) | `reply.stream()` creates card immediately + delivers INPUTING (without waiting for first token) |
| E2 | Reply content **smoothly appends** typewriter-style | streaming interface + 800ms throttle + non-final frame tail newline removal (prevent flickering) |
| E3 | Loading disappears after completion, content **freezes as complete Markdown** | isFinalize final frame + flowStatus=3 + cardUpdateOptions |
| E4 | Card failure/QPS throttling **imperceptible to user** | Create failure → silent fallback to webhook text; QpsLimit → backoff 2s retry; no error popups |
| E5 | **Same experience in group chat/DM** | Same Reply API; delivery target auto-selected IM_GROUP/IM_ROBOT; group chat auto-strips @ prefix |
| E6 | **Never duplicate replies** to same message | Dual-layer deduplication (messageId+msgId, TTL 5min), discarded still returns ACK |
| E7 | **Card interaction loop**: button clicks reach Agent, can update card | OnCardAction (registration auto-subscribes /v1.0/card/instances/callback, all four languages have unit test coverage for dispatch & subscription) + reply updates |
| E8 | Bot **never goes offline** (network outage/server switch imperceptible) | Exponential backoff reconnect + SYSTEM/disconnect immediate reconnect + heartbeat keepalive + ACK prevents loss |
| E9 | **Media send/receive**: receive images/files downloadable; can upload media and embed | `reply.image(url)` (sampleImageMsg); `reply.uploadMedia()` (OAPI multipart, mediaId can `![..](mediaId)` embed in card); `reply.downloadURL()` |
| E10 | Markdown **rendering quality** (code blocks/tables/lists/quotes) | normalizeForCard normalization (see §7) |

> Effect demo script (attached in each language README): echo + streaming simulation (fake LLM emits token every 100ms) must present full E1→E3 process.

## 1. Responsibility Boundaries

**SDK responsibilities:**
1. Stream connection (establish, subscribe, heartbeat, disconnect reconnect, server disconnect handling)
2. Event parsing and deduplication (protocol layer messageId + business layer msgId, dual-layer, TTL 5 minutes)
3. Reply sending (sessionWebhook: text / Markdown)
4. AI card streaming output (create → deliver → INPUTING → streaming → FINISHED, typewriter effect)
5. Card API global rate limiting (token bucket + QpsLimit backoff retry)
6. Markdown normalization (adapt to DingTalk AI card renderer newline/table rules)

**SDK does NOT handle (left to Agent side):**
- Agent runtime (model / prompt / tool orchestration)
- Multi-user topic isolation and Session/context persistence
- Credential storage (only receives clientId/clientSecret)

## 2. Wire Protocol (Stream Mode)

### 2.1 Establish Connection
```
POST {apiBase}/v1.0/gateway/connections/open
{
  "clientId": "...", "clientSecret": "...",
  "ua": "dingtalk-channel-sdk-{lang}/v0.1.0",
  "localIp": "<first non-loopback IPv4>",
  "subscriptions": [ {"type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get"} ],
  "extras": {}
}
→ {"endpoint": "wss://...", "ticket": "..."}
```
HTTP headers: `Content-Type/Accept: application/json`, `User-Agent: dingtalk-channel-sdk-{lang}/v0.1.0`.
WebSocket connection: `{endpoint}?ticket=<url-encoded ticket>` (**ticket requires URL encoding**, same as official Python SDK; topic declared in open request, not in URL).

Subscription types: `CALLBACK` (callback) / `EVENT` (event) / `SYSTEM` (system, SDK internal uses `ping`, `disconnect`).

Fixed topics:
- Bot messages: `/v1.0/im/bot/messages/get` (CALLBACK)
- Card callbacks: `/v1.0/card/instances/callback` (CALLBACK, subscribed only when OnCardAction registered)

### 2.2 Data Frames
Incoming frame (WebSocket text):
```json
{"specVersion":"1.0","type":"CALLBACK|EVENT|SYSTEM","time":0,
 "headers":{"topic":"...","messageId":"...","contentType":"application/json","time":"..."},
 "data":"<JSON string>"}
```
ACK outgoing frame (**must reply**, otherwise server re-delivers; **ACK first**—reply immediately upon receipt with `{"success":true}`, process business asynchronously,
aligned with official connector: prevents server timeout re-delivery during Agent long tasks; duplicate delivery handled by dual-layer deduplication):

> **Server-side perspective evidence** (lippi-open-proxy source code, 2026-07-23 production incident retrospective cross-validation):
> ① Server push is `UnaryRequest`—**synchronously waits for ACK**, upstream timeout ~2s; if not received, **re-delivered via MetaQ (at-least-once)**,
> and re-delivery generates new messageId (business layer msgId deduplication is necessary, protocol layer single-layer is insufficient)—this SDK's ACK-first + dual-layer deduplication
> strictly aligns with this semantics. ② Heartbeat contract is **client ping, server auto pong** (gorilla default behavior); when server read loop blocks,
> pong stops, client should timeout reconnect—this SDK's 120s idle ping + 5s pong death detection is the client implementation of this contract.
> ③ Server ACK validation only requires headers non-empty + contains messageId; only receives/sends Text frames; ticket is server connectionId (URL query passed).
> ④ **Risk response**: 0723 incident proved server read loop has a mode blocked by late/duplicate ACKs (fix is on
> branch released 20260723, whether production deployed depends on release system; local master is stale snapshot, does not represent production version).
> Regardless of server fix status, client four defenses are necessary for production self-healing: exactly-once ACK per frame, ACK-first (no late ACKs), pong timeout death reconnect
> (only way out when server wedged), exponential backoff+jitter reconnect (prevent storm amplification). Latency-sensitive scenarios can reduce KeepAliveIdleMs
> (default 120s) to accelerate wedged detection.
```json
{"code":200,"headers":{"contentType":"application/json","messageId":"<same frame>"},
 "message":"ok","data":"{\"success\":true}"}
```
- `SYSTEM/ping`: Reply pong, `data` echo back.
- `SYSTEM/disconnect`: Close connection and immediately reconnect (server LB switch).

### 2.3 Heartbeat and Reconnect
- Send WebSocket protocol layer Ping after 120s idle, declare dead if no Pong received in 5s.
- Reconnect: exponential backoff 1s→2s→4s…capped at 30s (with jitter); reset on successful reconnect.
- Read loop exception/disconnect → auto-reconnect (configurable AutoReconnect=false).

> Comparison with official stream-sdk family: official heartbeat Go=120s idle+5s pong, Java(Netty)=60s idle+pong,
> Python=60s ping (no pong detection), Node=isAlive flag+terminate; this SDK uniformly adopts Go profile (120s+5s pong),
> stronger than Python/Node. Official reconnect is fixed 3s/10s, this SDK uses exponential backoff+jitter (aligned with official connector).

## 3. Event Model

### 3.1 IncomingMessage (After Normalization)
| Field | Source | Description |
|---|---|---|
| ConversationID | conversationId | Conversation ID |
| ConversationType | conversationType | "1"=DM "2"=group → normalized to `dm`/`group` |
| ConversationTitle | conversationTitle | Group name (group chat) |
| SenderID / SenderStaffID | senderId / senderStaffId | Encrypted ID / staff ID |
| SenderNick | senderNick | Nickname |
| SenderCorpID | senderCorpId | |
| Text | text.content | **@bot prefix whitespace removed and trimmed** |
| MsgType / Content | msgtype / content | Rich content (images/files etc. passed through) |
| AtUsers | atUsers[] | [{dingtalkId, staffId}] |
| SessionWebhook | sessionWebhook | Reply webhook (includes expiration SessionWebhookExpiredTime) |
| MsgID / CreateAt | msgId / createAt | Business dedup key / event timestamp |
| Raw | original data | |

### 3.2a Stale Message Filtering

Incoming messages with `createAt` older than `StaleMessageWindow` (default 30min, <=0 disables) are directly discarded (still returns ACK)—
old messages flooding in during reconnect storm/re-delivery backlog no longer trigger replies.
1. Protocol layer: `headers.messageId` (duplicate callbacks from same delivery)
2. Business layer: `data.msgId` (messageId changes but msgId remains same during server re-delivery)
TTL 5 minutes, LRU cleanup. Hit discarded (still returns ACK success).

## 4. Reply API (Four-Language Consistent Semantics)

```
reply.text(content)                     → sessionWebhook, msgKey=sampleText
reply.markdown(title, text)             → sessionWebhook, msgKey=sampleMarkdown
reply.image(url)                        → sessionWebhook, msgKey=sampleImageMsg
reply.downloadURL(downloadCode,msgId)   → GET /v1.0/robot/messageFiles/download
reply.uploadMedia(type,name,data[,ct]) → OAPI upload, returns mediaId (see §9a)
s = reply.stream()                      → Immediately create AI card (E1: "typing..." card first)
s.append(delta) / s.append(fullText)    → Streaming update (accumulation semantics decided by caller)
s.finish() / s.finish(fullText)         → Final frame + FINISHED
s.fail(errText)                         → FINISHED(flowStatus=5) or fallback text
```

**Overlong chunking**: `TextChunkLimit` (default 3500, <=0 disables)—
text/Markdown replies exceeding limit split by **newline boundary** and sent multiple times (hard cut if no suitable newline), content not lost.

sessionWebhook payload:
```json
{"msgKey":"sampleText","msgParam":"{\"content\":\"...\"}"}
{"msgKey":"sampleMarkdown","msgParam":"{\"title\":\"...\",\"text\":\"...\"}"}
```
**Note**: `msgParam` must be **stringified JSON** (official documentation requirement, object form returns 400).
Header: `x-acs-dingtalk-access-token: <token>`.

## 4a. Proactive Send

Independent of incoming messages, Agent can initiate anytime:

```
channel.SendText(target, text)
channel.SendMarkdown(target, title, text)      // target can include @: AtUserIds/AtDingtalkIds/AtAll
channel.SendImage(target, imageURL)            // requires publicly accessible URL
```

- DM (target.UserID): `POST /v1.0/robot/oToMessages/batchSend` `{robotCode, userIds:[...], msgKey, msgParam}`
- Group (target.ConversationID): `POST /v1.0/robot/groupMessages/send` `{robotCode, openConversationId, msgKey, msgParam, atUserIds?, atOpendingtalkIds?, isAtAll?}`

## 4b. Policy Gating and Group-Level Overrides

Global policy (PolicyConfig): group allow/block lists, `RequireMention` (default true), DM mode
(open/allowlist/blocklist/disabled) and corresponding lists.

**Group overrides (GroupOverrides)**: Per-conversationId group override—`Enabled` (explicit disable),
`RequireMention` (@ requirement for this group), `AllowFrom`/`BlockFrom` (group sender allow/block lists, block list takes precedence).
Evaluation order (four languages consistent):

1. Global block list (highest priority, group overrides cannot exempt)
2. Allow list admission: global allow list hit, **or explicit group entry exists** (explicit entry can admit this group in allow list mode)
3. `Enabled=false` → reject (`group_disabled`)
4. @bot check (group override takes precedence over global)
5. `BlockFrom` → `AllowFrom` (group sender filtering)

## 5. AI Card Protocol (Five Steps)

Template ID default: `02fcf2f4-5e02-4a85-b672-46d1f715543e.schema` (official AI card, configurable).

1. **Create** `POST /v1.0/card/instances`
   `{cardTemplateId, outTrackId: "card_{ts}_{rand}", cardData:{cardParamMap:{config:"{\"autoLayout\":true}"}}, callbackType:"STREAM", imGroupOpenSpaceModel:{supportForward:true}, imRobotOpenSpaceModel:{supportForward:true}}`
2. **Deliver** `POST /v1.0/card/instances/deliver`
   - Group: `{outTrackId, userIdType:1, openSpaceId:"dtv1.card//IM_GROUP.{conversationId}", imGroupOpenDeliverModel:{robotCode}}`
   - DM: `{outTrackId, userIdType:1, openSpaceId:"dtv1.card//IM_ROBOT.{senderStaffId||senderId}", **imRobotOpenDeliverModel**:{spaceType:"IM_ROBOT", robotCode, extension:{dynamicSummary:"true"}}}`
   - robotCode = clientId
   - ⚠️ DM field must be `imRobotOpenDeliverModel` (Deliver not Space; official connector's `imRobotOpenSpaceModel` variant rejected by production: `400 param.spaceDeliverModelEmpty`—2026-08 real device evidence, dws source code authoritative)
   - ⚠️ **Business-level validation**: deliver returns `{"result":[{"success":false,...}]}` within HTTP 200 (dws production evidence "observed live"), SDK must scan body for `"success":false` and treat as failure (this SDK's create/deliver both do callChecked)
3. **First frame set INPUTING** `PUT /v1.0/card/instances`
   `{outTrackId, cardData:{cardParamMap:{flowStatus:"2", msgContent:<norm>, staticMsgContent:"", sys_full_json_obj:"{\"order\":[\"msgContent\"]}", config:"{\"autoLayout\":true}"}}}`
4. **Streaming update** `PUT /v1.0/card/streaming`
   `{outTrackId, guid:"{ts}_{rand}", key:"msgContent", content:<norm>, isFull:true, isFinalize:<bool>, isError:false}`
   Non-final frames remove trailing consecutive newlines (prevent flickering).
5. **Close FINISHED** `PUT /v1.0/card/instances`
   First send streaming with isFinalize=true, then set `{outTrackId, cardData:{cardParamMap:{flowStatus:"3", msgContent, ...}}, cardUpdateOptions:{updateCardDataByKey:true}}`

flowStatus: 1=PROCESSING 2=INPUTING 3=FINISHED 4=EXECUTING 5=FAILED.

**Frame rhythm (dws connect_card.go evidence ported)**:
- **500ms frame interval**: Must leave gap between first content frame and delivery, between final frame and previous frame—back-to-back competes with client card pull,
  intermittently renders "content load failed" (this race condition killed dws #407 card implementation)
- **Single frame content limit 20000** (rune-safe truncation, hermes MAX_MESSAGE_LENGTH equivalent)

**Status badge (aligned with dws/hermes)**: `MarkThinking/MarkDone` sets text emotion on **user message**
(`POST /v1.0/robot/emotion/reply|recall`, emotionType=2, emotionId=2659900):
"🤔Thinking" indicates processing, "🥳Done" indicates completion; only supports human messages (bot messages return 500); best-effort does not block reply.

**Throttling**:
- Single card streaming update minimum interval 800ms (DingTalk card has same-card concurrency protection, official connector production value)
- **Updates within window not discarded**: Schedule trailing flush (`delay = throttle - elapsed`), content eventually delivered, avoiding "output ends at window tail → screen stuck until finish"
- **Long interval batching**: After >2s without updates (tool call/thinking gap), first flush delays 300ms to batch, first screen shows meaningful text rather than 1-2 characters
- Flush and finish concurrently safe: pending flush automatically voided after closed
**Failure fallback**: Create/deliver failure → silent fallback to sessionWebhook text; finish failure → fallback send accumulated text.

> **Template scope (dws A/B evidence)**: Card templates are app-scoped—hermes proprietary template (c629162a-...) renders "content load failed" for other apps;
> default template (02fcf2f4...) is openclaw connector's public template, usable across apps.
**Truth exposure**: `streamer.CardDelivered()/cardDelivered/card_delivered/cardDelivered()` returns whether card actually delivered successfully (diagnostics/livecheck use, prevents fallback mode false positive).

**Three lines of defense**:
- **Watchdog** (`CardWatchdog`, default 10min): Timer starts after card established, refreshes on successful frame; timeout without closing → force finish+seal—
  when upstream Agent hangs/dispatch doesn't return, card won't spin forever (connector CARD_WATCHDOG_TIMEOUT equivalent)
- **Explicit abort** `Abort()/abort()`: External interrupt scenarios, seal stream+card set FAILED,
  mutually exclusive with Finish (normal close)/Fail (error text) and idempotent
- **Error fallback cooldown** (`ErrorCooldown`, default 60s): Same session error fallback text sent only once within 60s, prevents error spam
  (connector deliveredErrorTypes+ERROR_COOLDOWN equivalent)

## 6. Rate Limiting (Global Token Bucket)

- Capacity/rate: Default 20 QPS (official limit ~40, conservative value, configurable).
- Detection: HTTP 403 and response body code string contains `QpsLimit`.
- Strategy: Backoff 2s (empty tokens) → retry once after getting new token; streaming retry uses new guid.

## 7. Markdown Normalization (normalizeForCard)

DingTalk AI card renderer conventions (outside code blocks):
- Single `\n` → `<br>`; `\n\n` paragraph preserved
- Code block ``` interior: preserve `\n`
- Markdown block syntax lines (list `- / 1.`, table `|`, heading `#`, separator) preserve preceding `\n`
- Consecutive quote lines `>`: Merge into single line with `<br>` connection, continuation lines strip `>` prefix
- Insert empty line before table separator row if none exists (otherwise won't render)

## 8. Token

`POST /v1.0/oauth2/accessToken` `{appKey, appSecret}` → `{accessToken, expireIn}`;
cached by clientId, refresh 60s before expiration. Header uniform `x-acs-dingtalk-access-token`.

## 9a. Media Upload (OAPI, Comparison with Official Connector media/common.ts)

1. **OAPI token**: `GET {oapiBase}/gettoken?appkey=&appsecret=` → `{errcode:0, access_token, expires_in}` (cached, refresh 60s early; oapiBase default `https://oapi.dingtalk.com`, independent from new API token)
2. **Upload**: `POST {oapiBase}/media/upload?access_token=&type={image|file|video|voice}`
   multipart/form-data, field name **`media`** (includes filename), Content-Type image uses `image/jpeg`, others `application/octet-stream`
3. **Response**: `{errcode:0, media_id, type, created_at}`; **remove leading `@` from media_id** before use
4. **Media delivery capability matrix (2026-08 real device experiment finalized)**:
   - OAPI `media/upload` produced mediaId **has no public URL**—`down.dingtalk.com/media/<id>` (including @/adding extension variants) **all 404** (freshly uploaded immediately verified),
     therefore **embedding OAPI uploaded images in cards not feasible**; card embedding only works for **already publicly accessible URLs** (like images already in DingTalk media library)
   - **Reliable delivery = independent media messages** (aligned with official connector sendVideo/sendAudio/sendFileProactive and dws current behavior—dws deprecated old upload command and explicitly stated in migration notes "file message delivers, does not render inline image"):
     `SendFile` (sampleFile: mediaId+fileName+fileType), `SendVideo` (sampleVideo: videoMediaId+picMediaId+duration),
     `SendAudio` (sampleAudio: mediaId+duration)—all three require uploadMedia returned **RawMediaID (with @)**
   - `SendImage` (sampleImageMsg) only accepts public photoURL
   - >20MB files use chunked upload (v0.2 roadmap)

**Real integration (livecheck)**: Each language provides `example/livecheck` (Go: `go run ./example/livecheck`;
Node: `npm run live`; Python: `python example/livecheck.py`;
Java: `mvn -q compile exec:java -Dexec.mainClass=...LiveCheck`).
Set `DD_CLIENT_ID/DD_CLIENT_SECRET` (optional `DD_UPLOAD_FILE`) then send a message to the bot,
progressively PASS/FAIL: connect → receive message → text reply (token) → card create (E1) → streaming full cycle (E2/E3) → media upload.

## 9. Four-Language API Reference

| | Go | Node.js | Python | Java |
|---|---|---|---|---|
| Create | `channel.New(cfg)` | `new DingTalkChannel(cfg)` | `DingTalkChannel(cfg)` | `DingTalkChannel.create(cfg)` |
| Receive msg | `ch.OnMessage(func(ctx, msg, reply))` | `ch.on('message', async (msg, reply) => {})` | `@ch.on_message` / `ch.on_message(fn)` | `ch.onMessage((msg, reply) -> {})` |
| Card callback | `ch.OnCardAction(...)` | `ch.on('cardAction', ...)` | `ch.on_card_action(fn)` | `ch.onCardAction(...)` |
| Start | `ch.Start(ctx)` | `await ch.start()` | `await ch.start()` | `ch.start()` / `startAsync()` |
| Stream reply | `st, _ := reply.Stream(); st.Append/Finish` | `const s = reply.stream(); await s.append/finish` | `s = await reply.stream(); await s.append/finish` | `CardStreamer s = reply.stream(); s.append/finish` |

## Directory Structure (Four-Layer Packaging)

```
dingtalk_channel_sdk/
├── (root)             Public API & assembly: channel config stream frame token card
│                      reply send lifecycle bot_identity emotion ratelimit
│                      http_mode media httpx errors __init__ (top-level re-export)
├── normalize/         Inbound normalization: message
├── safety/            Admission & stability: policy dedup processing_lock chat_queue
│                      batching ssrf_guard
└── outbound/          Outbound processing: retry splitter markdown (card rendering preprocessing)
```
Dependency direction strictly unidirectional: root → subpackages; safety → normalize/root config; top-level `__init__` re-export maintains
existing usage of `from dingtalk_channel_sdk import X` unchanged.

## 10. Version and Naming

- Repository: `dingtalk-channel-sdk-{go,nodejs,python,java}` (aligned with dingtalk-stream-sdk-* family)
- UA: `dingtalk-channel-sdk-{lang}/v0.1.0`
- License: MIT
