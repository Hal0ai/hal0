# spec-hp-realtime — OpenAI Realtime WebSocket endpoint for hal0

Board lane **HP-realtime**: give the operator a spoken, barge-in conversation
with the box "à la LocalAI realtime" — a mic/speaker loop that can also *drive*
the box (the hal0-admin tool surface the user flagged as "almost all the needed
tools built"). Target client: the LocalAI Go demo
(`github.com/localai-org/localai-realtime-demo`, default branch `master`) — a
PortAudio duplex mic/speaker WS client with barge-in, server-VAD turn-taking,
and stdio-MCP tool support.

hal0 today has **no** realtime surface. It has batch REST voice, SSE-streamed
chat, the hal0-brain steward, and a streamable-HTTP admin MCP mount. This spec
settles how a `/v1/realtime` WebSocket is built on top of those, what the MVP
ships, and where the sharp edges are. **No code lands with this doc.**

This spec is evidence-first: every code claim carries a `file:line` citation at
`rework/spec-realtime` (fork of `rework/descar`). Where investigation was
blocked or the code is ambiguous, it says so rather than guessing.

---

## 1. Motivation

The user wants to talk to the box and have it both answer and *act* — load a
slot, pull a model, check hardware, move a board card — by voice. The
protocol to target is the OpenAI Realtime WS API as LocalAI implements it,
because the user's chosen client already speaks it. The build must reuse hal0's
existing voice slots (STT/TTS), one of its existing LLM legs, and — critically —
its existing tool-security posture (spec-kb23), rather than standing up a
parallel policy surface.

---

## 2. Current surfaces (cited)

### 2a. Voice slots — formats and streaming (load-bearing)

- **STT route** `POST /v1/audio/transcriptions` — `src/hal0/api/routes/v1.py:1240`.
  Accepts **multipart file upload only**; raw bytes forwarded verbatim
  (`_forward_multipart`, `v1.py:1603-1686`, `v1.py:1673`) precisely so WAV/mp3
  containers aren't corrupted (`v1.py:1611-1612`). It does **not** accept raw
  pcm — the payload must be a decodable container (wav/mp3/flac/ogg/m4a/webm,
  `v1.py:1568`). **Batch-only**: "STT responses aren't streamed today"
  (`v1.py:1548-1551`). Two backends: NPU FLM trio (`stt-npu`, `v1.py:1024`,
  `v1.py:1651-1664`) and the moonshine toolbox container.
- **moonshine** expects **16 kHz mono float32**
  (`packaging/toolbox/moonshine/moonshine_server.py:43`, resamples otherwise
  `:209-219`). It also exposes a **WebSocket `/v1/audio/stream`**
  (`moonshine_server.py:384-425`) that consumes **raw pcm16 LE @ 16 kHz** frames
  directly (`:401-407`) and emits partial `{"text","is_final":false}` JSON
  (`:418`) — the *only* real-time STT surface in-tree, but it lives on the
  container and is **not proxied through hal0's `/v1` gateway** (v1.py has no
  `/audio/stream` route).
- **TTS route** `POST /v1/audio/speech` — `v1.py:1264`. JSON `{model,input,voice}`.
- **kokoro** — native **24 kHz** (`packaging/toolbox/kokoro/kokoro_server.py:61`);
  `_encode_audio` can emit `pcm` = **signed 16-bit LE, mime `audio/L16`**
  (`kokoro_server.py:146-149`, `_FORMAT_MIME` `:134-140`) — i.e. exactly
  Realtime's pcm16 at 24 kHz. **Single blob, not chunked**
  (`Response(content=audio_bytes...)` `kokoro_server.py:249`).
- **qwen3tts** — same pcm int16 emit path (`qwen3tts_server.py:161-163`) but
  sample rate is **model-runtime `sr`, not a constant** (`:260-261`); non-stream
  blob (`:264`). **Could not verify** its emitted rate from code.
- **No reusable audio utilities in `src/hal0`.** `soundfile`/`librosa`/`scipy`/
  `wave` do not appear in the shipped package — all audio code lives in
  `packaging/toolbox/*` container servers (separate images, not importable).
  Resampling there is hand-rolled `np.interp` (`moonshine_server.py:209-219`).

### 2b. Chat legs

- **`POST /v1/chat/completions`** — `v1.py:969`. **Passthrough proxy**, no
  server-side tool loop by default; streams the upstream's OpenAI SSE verbatim
  (`data: {…delta.content}` … `[DONE]`, `v1.py:1015`, `:100-156`). A server-side
  tool loop exists **only** under `"omni": true` (`v1.py:1003-1006`), and that
  path returns **non-streaming JSON** (`v1.py:1168-1169`, `:1198-1203`).
  → plain leg = token deltas (great for incremental TTS); omni leg = one blob.
- **`POST /api/brain/chat`** — `src/hal0/api/routes/brain.py:22` →
  `run_brain_chat` (`src/hal0/brain/chat.py:1332`,
  `StreamingResponse text/event-stream` `:1341`). SSE contract (verbatim,
  `chat.py:36-51`): `{"type":"token","text"}`, `{"type":"thinking","text"}`,
  `{"type":"tool_call",…}`, `{"type":"tool_result",…}`, `{"type":"done"}`,
  `{"type":"error","message"}`; plus dispatch-layer `{"type":"approval_required",…}`
  (`chat.py:1260-1265`) and `{"type":"ping"}` (`chat.py:1301`). **Caveat:** the
  engine forces `body["stream"]=False` (`toolloop/engine.py:357`), so each
  `token` frame is the **full round's text**, not a per-token delta
  (`engine.py:388-391`) — round-chunked, not token-streamed.
- **Approval pause**: on a gated tool the brain turn blocks up to
  `_APPROVAL_WAIT_S = 300.0` (`chat.py:111`), polling every
  `_APPROVAL_POLL_S = 1.0` (`chat.py:112`), pinging every
  `_APPROVAL_PING_EVERY_S = 15.0` (`chat.py:113`) — loop `chat.py:1271-1319`.
  In a voice session this is **up to 5 minutes of silent dead air** with no
  assistant audio.
- **Shared engine** `run_tool_loop` — `toolloop/engine.py:322-331` — is the one
  core both legs use; it takes `tools`, `dispatch_fn`, `known_tool_names`.
- **Leg selection is by route, not by `model`**: brain lives only behind
  `/api/brain/chat`; `model:"hal0/brain"` on `/v1/chat/completions` resolves to
  the brain *slot* (`normalize/resolver.py:34`, chain `("brain","agent")`) but
  engages **none** of the steward's persona/tools/approval loop.

### 2c. hal0-admin MCP surface

- Built with the upstream `mcp` SDK's `FastMCP`
  (`src/hal0/mcp/admin.py:239`); server via
  `build_server(...)` (`admin.py:1599-1668`); dispatch entry
  `async def dispatch(...)` (`admin.py:927-1025`). Tiers:
  `AUTONOMOUS_READ_TOOLS` (`admin.py:254-314`), `AUTONOMOUS_WRITE_TOOLS`
  (`:318-342`), `GATED_TOOLS` (`:345-397`), floor `POLICY_NO_LOOSEN`
  (`:837-847`).
- **Transport = streamable-HTTP.** Mounted
  `app.mount("/mcp/admin", admin_server.streamable_http_app(), name="mcp-admin")`
  (`src/hal0/api/mcp_mount.py:250-251`); memory MCP at `/mcp/memory`
  (`:269-270`, only when a memory provider is wired). `create_app` calls
  `mount_mcp_servers` at `src/hal0/api/__init__.py:1808-1813`.
- **Identity** flows on the **`X-hal0-Agent`** header (Bearer auth was removed
  from the MCP layer, `mcp_mount.py:40-42`); optional `X-hal0-Private`
  (`:194-204`); a Bearer, if present, is only re-attached on the REST
  passthrough (`admin.py:764-772`). Used for **audit + namespace + gating**, not
  transport access control.
- **DNS-rebinding protection is ON, localhost-only, by default**
  (`_mcp_transport_security`, `mcp_mount.py:70-122`; `_LOCALHOST_HOSTS`
  `:55`). A non-localhost MCP client gets a bare `421 Invalid Host header`
  unless the operator sets `HAL0_MCP_ALLOWED_HOSTS` (or `*` to disable,
  `:99-101`). **This is the key gotcha for any off-box MCP client.**
- **Gated-tool result over MCP**: `dispatch` returns
  `{"status":"pending_approval","approval_id":…}` **inline and immediately**
  (`admin.py:1001-1013`); nothing waits on the MCP transport. Approvals resolve
  **out-of-band over REST**: `POST /api/agent/approvals/{id}/approve|deny`,
  `GET /api/agent/approvals[/events]` (`src/hal0/api/routes/approvals.py:89-200`),
  consumed by the dashboard bell.

### 2d. Platform: WS, auth, curated models

- **WS template** `board_ws.py` + `board.py:445-456`: `@router.websocket("/events")`,
  explicit `await websocket.accept()` (`board.py:455`), then two concurrent
  tasks — a receive-drain for disconnect detection and a poll/send loop
  (`board_ws.py:100-139`); no app-level ping (relies on 0.3 s poll traffic).
  The chat-proxy WS shows the reference ping cadence
  `WS_PING_INTERVAL_SECONDS = 20.0` (`chat_proxy.py:93`) and a second in-handler
  origin+cookie gate closing `4403` (`chat_proxy.py:412-437`).
- **Auth (KB-1)**: `AuthEnforcementMiddleware`
  (`src/hal0/api/auth.py:371-469`, installed `__init__.py:1424`) classifies via
  `security/exposure.py:classify` (first-match `RULES`, `exposure.py:126-282`);
  **unclassified path → `AuthClass.ADMIN`** (deny-by-default ratchet,
  `exposure.py:281-282`). WS scopes: pseudo-method `"GET"` (`auth.py:400-402`),
  Origin defence-in-depth for all websockets (`auth.py:408-418`), deny =
  **pre-accept `websocket.close code 4403`** (`auth.py:449-453`). Principal
  resolves generically from **cookie → `Authorization: Bearer` → `?api_key=`**
  for any scope (`resolve_principal`, `auth.py:229-261`) — the KB-1 WS story.
  Tiers `anon|client|admin` (`auth.py:52`), keys `HAL0_ADMIN_KEY`/`HAL0_CLIENT_KEY`
  (`auth.py:82-87`). **`require_auth_enabled()` defaults OFF** (`auth.py:157`) —
  on a stock box the middleware passes every request through untouched
  (`auth.py:394-396`).
- **SSE keepalive idiom**: yield `": keepalive\n\n"` on a `_KEEPALIVE_S = 15.0`
  receive-timeout (`events.py:54,173-178`).
- **Curated realtime-ish models**: `moonshine-small-streaming-en`
  (`asr`, backend `moonshine`) and `vibevoice-realtime-0.5b` (`tts`, backend
  `vibevoice`) exist **only as upstream-routed `HaloaiModel` seed rows**
  (`registry/seeds/haloai_models.json:333-351`), **deliberately excluded from
  the locally-pullable `CuratedModel` picks** (`registry/curated.py:712-718`) —
  their multi-file ONNX/diffusers shapes don't fit the single-file pull path.
  A moonshine **streaming** toolbox server exists (`moonshine_server.py`, CLI
  default `--model_arch small_streaming` `:432`); vibevoice is mapped onto the
  kokoro provider as "closest existing" (`capabilities/catalog.py:61`) with
  **no dedicated toolbox server found**. **Local realtime-slot servability of
  both models could not be confirmed from code** — they are catalogue/upstream
  entries, not wired local slots.

---

## 3. Protocol requirements (from investigation, not memory)

Sources: LocalAI docs `https://localai.io/features/openai-realtime/` (fetched)
and the demo repo `localai-org/localai-realtime-demo@master` source
(`realtime/client.go`, `mcp/config.go`, `cmd/assistant/tools_setup.go`, README —
fetched via `raw.githubusercontent.com`; the GitHub MCP is scoped to
`hal0ai/hal0` only and could not read the demo repo directly).

- **Endpoint**: `ws://<host>/v1/realtime?model=<id>` (demo default
  `ws://localhost:8080/v1/realtime`, flag `-ws-url`). Audio is **raw PCM in the
  WS messages**.
- **Audio format**: **pcm16**; demo `-sample-rate` default **24000**. The
  LocalAI docs did not state bit depth/rate numerically (**unverified there**);
  the demo's 24000 default and kokoro's 24 kHz pcm output line up, so **MVP fixes
  24 kHz mono pcm16 both directions**.
- **Client→server events the demo sends** (`realtime/client.go`):
  `session.update`, `input_audio_buffer.append`, `response.create` (only after a
  function-call result), `response.cancel`, `conversation.item.create`.
  **It never sends `input_audio_buffer.commit`** (verified — no `commit` in the
  source) and **hardcodes `turn_detection: {type: server_vad}`** — the demo has
  **no client-committed / `turn_detection:none` path at all**.
- **Server→client events the demo consumes**: `session.created`,
  `session.updated`, `input_audio_buffer.speech_started`,
  `input_audio_buffer.speech_stopped`,
  `conversation.item.input_audio_transcription.completed`, `response.created`,
  `response.done`, **`response.output_audio.delta`** (GA naming, not the older
  `response.audio.delta`), `response.function_call_arguments.done`, `error`.
  LocalAI additionally documents `response.output_audio_transcript.delta`,
  `conversation.item.input_audio_transcription.delta`, `session.created`,
  `input_audio_buffer.clear`, `conversation.item.delete/truncate`.
- **turn_detection**: LocalAI supports `server_vad` (default, silence-based) and
  `semantic_vad` (`eagerness: low|medium|high|auto`). The demo uses `server_vad`
  only.
- **Tools / MCP**: the demo's MCP config is the **standard `mcpServers` map with
  `{command, args, env}` only** (`mcp/config.go` `ServerSpec`) — **stdio
  transport, no `url`/`type`/HTTP fields**. Tools are fetched from the stdio
  servers, converted to Realtime function definitions, placed in
  `session.update` as `Tools: registry.ToolUnions()`, and **executed
  client-side**: on `response.function_call_arguments.done` the client runs the
  tool over its MCP bridge and returns the result via `conversation.item.create`
  + `response.create`. **The server never sees the tool executions** — its LLM
  leg only needs to *emit* function-call arguments. Auth to an MCP server is via
  the config's `env` map. (There is **no MCP mention in the LocalAI realtime
  docs** — MCP is a client-side demo feature.)
- **Backend model config** is a **pipeline yaml** (`gpt-realtime.yaml`):
  `pipeline: {vad, transcription, llm, tts}` — LocalAI stitches four models; the
  realtime layer is an orchestrator, not one model.
- **Session/auth handshake**: demo sends `-api-key` (default `sk-xxx`) as a
  Bearer; LocalAI docs give no auth detail (**unverified**). Barge-in is
  **client-side**: on local VAD the demo flushes its playback buffer and, if a
  response is generating, sends `response.cancel`.

**Decisive protocol facts for hal0**: (1) the stock demo *requires* `server_vad`
and cannot drive a `turn_detection:none`-only endpoint; (2) the demo reaches
tools over **stdio MCP executed client-side**, so it cannot natively call hal0's
**HTTP-mounted** `/mcp/admin` without an `mcp-remote`-style stdio↔streamable-HTTP
bridge; (3) 24 kHz pcm16 lines up with kokoro's pcm output but not moonshine's
16 kHz input.

---

## 4. Decisions

### a. Tool architecture — RECOMMENDED: **both, split by LLM leg; steward leg is the default "control the box" path**

Three coherent options, each measured against approval-gate UX, audit trail,
auth tier, and duplicated-policy risk:

1. **Client-side MCP** (the demo's native mode). Session declares tools; the
   client executes them over stdio MCP; the hal0 LLM leg only emits
   `function_call_arguments`. To reach hal0-admin the operator must run an
   `mcp-remote` stdio→streamable-HTTP bridge to `/mcp/admin`, set
   `HAL0_MCP_ALLOWED_HOSTS` for the box's hostname (default is localhost-only,
   §2c), and pass an admin key if auth is on. *Policy is unduplicated* — gating
   stays in `admin.dispatch`; a gated call returns `pending_approval` inline
   (`admin.py:1001-1013`) which the model narrates ("that needs approval"), and
   the operator resolves it via the REST bell. Audit stays server-side.
2. **Server-side steward leg** (`model:"hal0-brain"` → `run_brain_chat` tool
   loop). Tools are invisible to the client; the box gets the *exact* kb23
   posture — read-only default, `ApprovalQueue`, audit, injection resistance —
   for free. But the approval pause is **300 s of silent voice dead air**
   (`chat.py:111`, §2b) and the SSE is **round-chunked** (`engine.py:357`), so
   TTS can't start until a whole round completes. One policy source, zero client
   setup.
3. **Both**, selected by the session's `model` (or a session flag): a generic
   `model` → plain streamed chat leg with any client-declared session tools
   forwarded and executed client-side (option 1); `model:"hal0-brain"` → the
   server-side steward leg (option 2).

**RECOMMENDED: option 3, with the steward leg as the default path for
"controlling the box".** Rationale: the steward already encapsulates the entire
tool-security posture (spec-kb23) — re-exposing it under Realtime keeps **one**
policy/approval/audit source and duplicates nothing, whereas a new Realtime-native
tool dispatcher would re-implement gating and invite drift. Client-side MCP stays
available for power users who bring their own tools, and costs hal0 nothing but a
function-call passthrough. The split is a one-line leg selector, not two policy
engines.

### b. MVP increment 1 scope — RECOMMENDED

- **Route**: `WS /v1/realtime` (query `?model=`), new
  `src/hal0/api/routes/realtime.py`, registered in `create_app`. Matches the
  demo default so no client reconfig.
- **Events — accept (client→server)**: `session.update`,
  `input_audio_buffer.append`, `input_audio_buffer.commit`, `response.create`,
  `response.cancel`, `conversation.item.create`. **Reject with `error`** (typed,
  unimplemented): `input_audio_buffer.clear`, `conversation.item.delete`,
  `conversation.item.truncate`.
- **Events — emit (server→client)**: `session.created`, `session.updated`,
  `conversation.item.input_audio_transcription.completed`, `response.created`,
  `response.output_audio_transcript.delta`, `response.output_audio.delta`,
  `response.function_call_arguments.done` (passthrough-tools sessions only),
  `response.done`, `error`. (Server-VAD `speech_started/stopped` deferred to
  increment 2.)
- **turn_detection**: **`none` only (client-committed)** for the increment-1
  *contract* — the server accepts `append`+`commit` then runs a turn. **Flagged
  conflict**: the stock Go demo hardcodes `server_vad` and never commits (§3),
  so it **cannot** exercise this MVP — increment 1 is validated by a synthetic CI
  harness (decision d), not the demo. The demo becomes a usable harness only once
  `server_vad` lands (increment 2). This is called out again as an open question
  (§6) because it directly trades against decision (d)'s "demo as harness."
- **Audio conversion plan**:
  - *Inbound* (client pcm16@24k → STT): buffer appended frames; on `commit`,
    **wrap the pcm16 buffer in a WAV/RIFF header** (small new helper — the batch
    STT route needs a container, not raw pcm, §2a) and `POST
    /v1/audio/transcriptions`. moonshine internally resamples 24k→16k
    (`moonshine_server.py:209-219`), so the gateway need not resample for MVP.
    Emit `…transcription.completed` with the returned text.
  - *Outbound* (LLM text → client pcm16@24k): `POST /v1/audio/speech` with
    **kokoro** and `response_format:"pcm"` → int16 LE @ 24 kHz single blob
    (`kokoro_server.py:146-149,61`) → **slice into ~20 ms frames**, base64, emit
    as `response.output_audio.delta`. Kokoro's 24 kHz matches the client with
    **no resample**. (qwen3tts deferred — runtime `sr` unverified, §2a.)
- **Session auth**: add one `security/exposure.py` row —
  `_Rule("realtime", _prefix("/v1/realtime"), AuthClass.CLIENT, None)` — placed
  with the other `/v1` inference rules (realtime is inference, CLIENT tier, like
  chat). Without the row the deny-by-default ratchet would lock it to ADMIN
  (`exposure.py:281-282`). WS principal already resolves `?api_key=`/`Bearer`
  generically (`auth.py:252-259`); the Go demo's `-api-key` Bearer works, and a
  browser can use `?api_key=`. Auth is OFF by default in the shipped posture
  (`auth.py:157`), so MVP works out-of-box on the LAN and *tightens* correctly
  when `HAL0_REQUIRE_AUTH=1`.
- **LLM leg selection**: by `model` — a configured realtime/generic model id →
  plain `/v1/chat/completions` streamed leg (token deltas → TTS); the sentinel
  **`model:"hal0-brain"` → steward leg** (`run_brain_chat`). MVP may ship the
  plain leg first and the steward leg behind the same selector.
- **TTS chunking**: MVP slices the single kokoro blob and **burst-emits** all
  `response.output_audio.delta` frames (client buffers/paces playback); real-time
  server-side pacing deferred.
- **Cancellation**: `response.cancel` → abort the in-flight LLM stream, stop
  emitting `output_audio.delta`, discard any buffered TTS, emit
  `response.done` (status cancelled). This is the minimum barge-in hook the demo
  already drives client-side.

### c. Increment 2+ (post-core) — RECOMMENDED sequencing

1. **Server VAD** (unblocks the stock demo). Needs a VAD model — LocalAI's
   pipeline uses `silero-vad` (§3). Options: a small in-gateway
   `silero-vad-onnx` run on appended frames, or a new toolbox VAD server. Emits
   `speech_started`/`speech_stopped`, auto-commits, and auto-`response.create`.
   **Required before the Go demo can be the acceptance harness.**
2. **Streaming STT**: proxy the existing moonshine container WS
   `/v1/audio/stream` (pcm16@16k, partial results, `moonshine_server.py:384-425`)
   through the gateway, emitting incremental
   `conversation.item.input_audio_transcription.delta`. Feasibility: the
   streaming server exists and the curated `moonshine-small-streaming-en` row
   exists, **but is upstream-routed, not locally pullable** (`curated.py:712-718`),
   so a local streaming slot needs new pull/toolbox work first — **servability
   unconfirmed** (§2d).
3. **Barge-in server-side**: on `speech_started`, auto-cancel the current
   response (reuses (b)'s cancellation).
4. **Latency budget**: prefer the token-streaming plain leg for TTS chunking; the
   steward leg's round-chunking (`engine.py:357`) and 300 s approval pause are
   the two worst offenders — mitigate by speaking an `approval_required` prompt
   ("that needs your approval — check the bell") instead of dead air, and by
   emitting per-round audio. Realtime TTS via `vibevoice-realtime-0.5b` is a
   stretch goal — **no dedicated toolbox server found; servability unconfirmed**
   (§2d).

### d. Acceptance — RECOMMENDED

- **CI (no audio hardware) is the increment-1 gate.** Event-contract tests under
  `tests/realtime/` drive the WS with FastAPI's `TestClient` websocket and
  **synthetic pcm16 fixtures** (silence, a sine tone). Assert the full handshake:
  `session.created` → `session.update`/`session.updated` → `append`×N +
  `commit` → `…transcription.completed` → `response.created` →
  `response.output_audio.delta`×N → `response.done`; plus `response.cancel`
  mid-response → prompt cessation + `response.done(cancelled)`; plus the
  exposure-CI row assertion. STT/TTS slots are faked (the tests exercise the
  *event contract and audio framing*, not model quality). This runs the
  `turn_detection:none` MVP without a mic.
- **Go demo as the human harness** (increment 2+, once server VAD lands):
  - *Steward architecture*: run the demo with `-model hal0-brain`, **no**
    `-mcp-config` (tools are server-side). Session `turn_detection:server_vad`.
  - *Client-side-MCP architecture*: run with `-mcp-config` pointing at an
    `mcp-remote` bridge to `http://<box>/mcp/admin` (set
    `HAL0_MCP_ALLOWED_HOSTS=<box-host>`, and `env: {Authorization: "Bearer
    <admin-key>"}` in the `mcpServers` entry if auth is on).
  - The demo **cannot** validate the `turn_detection:none` MVP (§3) — do not
    make it the increment-1 gate.

### e. Fences + increment sizing — RECOMMENDED

- **Owned (new, no collision)**: `src/hal0/api/routes/realtime.py` (WS handler),
  `src/hal0/realtime/` (session state machine, event schemas, pcm↔wav/frame
  helpers), `tests/realtime/`, config `[realtime]` section
  (`src/hal0/config/schema.py`).
- **Shared / collision classes (coordinate)**:
  - `src/hal0/security/exposure.py` — **owned by the KB-1 lane**; the one
    `/v1/realtime` CLIENT row must be added through that lane (the exposure-CI
    test asserts the OPEN set exactly and every route classifies, so an
    un-coordinated edit fails CI). *Highest collision risk.*
  - `src/hal0/api/__init__.py` `create_app` — `include_router` registration line
    (shared with every router lane).
  - `src/hal0/config/schema.py` — shared config surface.
- **Read-only consumers (no edits)**: `v1.py` STT/TTS routes, `brain/chat.py`,
  `toolloop/engine.py`, `mcp/admin.py`, `packaging/toolbox/*`. The endpoint calls
  these over loopback HTTP / imports the brain entrypoint — it does not modify
  them.
- **Est. test surface (medium)**: event-contract state-machine tests
  (~10-15 cases: handshake, each accepted/rejected event, cancellation, error
  paths), audio-helper unit tests (pcm↔wav round-trip, framing/slicing), auth
  exposure-row test, leg-selector test (plain vs `hal0-brain`). One build lane;
  increments 1 (core + none-mode + CI) and 2 (server VAD + streaming STT +
  demo harness) are cleanly separable.

---

## 5. Increment plan (summary)

| Inc | Ships | Gate |
|-----|-------|------|
| **1 — core** | `WS /v1/realtime`; `turn_detection:none`; append/commit/response.create/cancel/session.update/conversation.item.create; kokoro pcm@24k out + WAV-wrap STT in; plain-leg + `hal0-brain`-leg selector; exposure CLIENT row | synthetic-pcm16 event-contract CI (decision d) |
| **2 — demo-drivable** | server VAD (silero) → `speech_started/stopped` + auto-commit; streaming STT proxy of moonshine WS; server-side barge-in | stock Go demo, both architectures (decision d) |
| **3 — stretch** | `semantic_vad`; realtime TTS (vibevoice) if servable; per-round TTS pacing; approval-prompt-as-speech | latency budget met; operator UX review |

---

## 6. Risks & open questions

1. **`turn_detection:none` MVP vs "demo as harness" are in direct tension.** The
   stock demo requires `server_vad` and never commits (§3), so the increment-1
   `none`-only contract can only be exercised by synthetic CI, not the user's
   chosen client. **Open question for the operator**: is a synthetic-only
   increment 1 acceptable, or should `server_vad` (increment 2) be pulled into
   the MVP so the real demo drives it from day one? This reshapes the increment
   boundary.
2. **Off-box MCP is not plug-and-play.** hal0-admin is HTTP-mounted with
   localhost-only DNS-rebinding by default (§2c) and the demo speaks stdio MCP
   (§3) — client-side MCP against the box needs an `mcp-remote` bridge **plus**
   `HAL0_MCP_ALLOWED_HOSTS` widening **plus** (if auth on) an admin key. This is
   real operator setup, not a config flag.
3. **Steward voice UX is unshaped for audio.** 300 s silent approval pause
   (`chat.py:111`) and round-chunked SSE (`engine.py:357`) are genuinely poor for
   voice; mitigations (spoken approval prompt, per-round audio) are increment 3.
4. **Curated realtime models' local servability is unconfirmed** (§2d):
   `moonshine-small-streaming-en` and `vibevoice-realtime-0.5b` are
   upstream-routed catalogue rows deliberately kept out of the pullable path;
   only the moonshine *streaming toolbox server* is confirmed to exist. Streaming
   STT and realtime TTS depend on pull/toolbox work not yet landed.
5. **qwen3tts output sample rate is model-runtime, not a constant**
   (`qwen3tts_server.py:260`) — MVP fixes on kokoro (24 kHz) to avoid an
   unverified resample; qwen3tts as a realtime TTS backend needs its emitted rate
   measured first.
6. **No audio DSP in `src/hal0`** (§2a) — pcm↔wav framing and any resampling are
   new gateway code (stdlib `wave`/`struct` suffice for WAV-wrap + framing;
   avoid pulling scipy/librosa for MVP).
7. **LocalAI docs were thin** on exact audio bit depth/rate, the full
   `session.update` JSON, and any auth handshake (all returned "not specified");
   the numbers used here come from the **demo source** (24000, pcm16, Bearer
   `sk-xxx`), which is the actual harness. The GitHub MCP could not read the demo
   repo (scoped to `hal0ai/hal0`); demo facts came from `raw.githubusercontent.com`
   fetches of `master`.
