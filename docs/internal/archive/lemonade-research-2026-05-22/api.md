# Lemonade API contracts for hal0 v0.2

Author: API Architect agent
Date: 2026-05-22
Upstream pin: `lemonade-sdk/lemonade@7af26f75` (HEAD of `main`, 2026-05-21)
Pairs with: `lemonade-spike-findings-2026-05-22.md`, `lemonade-repo-deep-dive-2026-05-22.md`, `src/hal0/lemonade/client.py`

This doc is the wire-contract surface only. Slot wiring is in `architect.md`; UI flows are in `ui.md`; dev/docs survey is in `researcher.md`.

---

## 1. `/v1/load` canonical body

### Required field — only one

```cpp
// repo: src/cpp/server/server.cpp::handle_load() (~L3068-3183)
model_name = request_json["model_name"];  // unconditional accessor → throws on null/missing
```

`model_name` is the only mandatory field. Every other field is optional and read via `request_json.value(key, default)`. The spike's `"type must be string, but is null"` was nlohmann's exception against a missing/null `model_name`, not a documented Lemonade error.

### Optional fields — filtered by recipe

Per-recipe whitelist (`repo: src/cpp/server/recipe_options.cpp::get_keys_for_recipe`):

- `llamacpp`: `ctx_size`, `llamacpp_device`, `llamacpp_backend`, `llamacpp_args`, `merge_args`
- `whispercpp`: `whispercpp_backend`, `whispercpp_args`, `merge_args`
- `flm`: `ctx_size`, `flm_args`, `merge_args`
- `ryzenai-llm`: `ctx_size` only
- `sd-cpp`: `sd-cpp_backend`, `sdcpp_args`, `steps`, `cfg_scale`, `width`, `height`, `sampling_method`, `flow_shift`, `merge_args`
- `vllm`: `ctx_size`, `vllm_backend`, `vllm_args`, `merge_args`
- `kokoro` / `collection.omni`: no load-time options

Top-level on every recipe: `save_options` (bool, default `false`).

### Sentinel handling

`is_empty_option` (in `recipe_options.cpp`) treats `""`, `"auto"`, and integer `-1` as "use default". **`null` is NOT a sentinel** — null is filtered in `to_cli_options` downstream, but `request_json["model_name"]` accesses unconditionally and nlohmann throws `"type must be string, but is null"`. **Rule: omit the key entirely, or send a typed sentinel. Never send JSON null.**

### CLI-equivalent shape (reverse-engineered)

`repo: src/cpp/cli/lemonade_client.cpp::load_model` builds the body as `recipe_options` (a JSON object containing only explicitly-set fields), then injects `model_name` and `save_options`. **Unset fields are absent, not null.** POST to `/api/v1/load` (= `/v1/load`; both path prefixes work).

`/v1/load` is **declarative**: source comment confirms "no-op if already loaded with matching options, reload only if options differ". hal0's idle-unload + reload-on-options-change driver doesn't need to track state — Lemonade no-ops correctly with identical options. `/v1/load` IS the "ensure loaded" verb.

### Python helper sketch

Existing `src/hal0/lemonade/client.py::LemonadeClient.load()` is close but its `llamacpp_args` kwarg types as `list[str]` — wire format is **single string**. Extend with a typed-dict `LoadOptions`:

```python
# Addition to src/hal0/lemonade/client.py
from typing import Literal, TypedDict

class LoadOptions(TypedDict, total=False):
    ctx_size: int                                       # -1 = default
    llamacpp_backend: Literal["rocm", "vulkan", "cpu", "metal"]
    llamacpp_device: str                                # "" = default
    llamacpp_args: str                                  # reserved-args forbidden
    whispercpp_backend: Literal["cpu", "vulkan", "npu"]
    whispercpp_args: str
    flm_args: str
    sd_cpp_backend: str                                 # remap → "sd-cpp_backend"
    sdcpp_args: str
    steps: int; cfg_scale: float; width: int; height: int
    sampling_method: str; flow_shift: float
    vllm_backend: str; vllm_args: str
    merge_args: bool                                    # default true

async def load(self, model_name: str, *,
               options: LoadOptions | None = None,
               save_options: bool = False) -> dict:
    body: dict = {"model_name": model_name, "save_options": save_options}
    for k, v in (options or {}).items():
        if v is None: continue                          # NEVER send null
        body["sd-cpp_backend" if k == "sd_cpp_backend" else k] = v
    # POST /v1/load
```

Hal0's `extra_args` slot field maps to `llamacpp_args` as a joined string. PR-3 `SlotConfig→LoadOptions` adapter belongs in `provider.py`; keep client transport-only.

---

## 2. Embed registration recipe

`extra_models_dir` auto-discovery labels GGUFs as `["custom"]` only (`repo: src/cpp/Extra-Models-Dir-Spec.md`). Type is label-driven (`repo: src/cpp/include/lemon/model_types.h::get_model_type_from_labels`), so embed GGUFs there load as LLM and the embed endpoint 501s. **Only reliable path: register via `/v1/pull` with `embedding: true`**, which adds the `embeddings` label.

### Step-by-step

1. Best path: let `/v1/pull` download direct to lemond's cache. Pre-staging into `extra_models_dir` works but is subject to §4.

2. Register:

```bash
curl -X POST http://127.0.0.1:9100/v1/pull -H "Authorization: Bearer ${LEMONADE_API_KEY}" \
  -d '{"model_name":"user.nomic-embed-text-v1-q8_0",
       "checkpoint":"nomic-ai/nomic-embed-text-v1-GGUF:Q8_0",
       "recipe":"llamacpp","embedding":true}'
```

3. Verify via `/v1/models?show_all=true` — entry should show `labels` containing `"embeddings"` and `type: "embedding"`.

4. Load:

```bash
curl -X POST http://127.0.0.1:9100/v1/load -H "Authorization: Bearer ${LEMONADE_API_KEY}" \
  -d '{"model_name":"user.nomic-embed-text-v1-q8_0",
       "llamacpp_backend":"rocm","ctx_size":8192}'
```

`--embeddings` is injected by the router because the model carries the `embeddings` label; **do not pass via `llamacpp_args`** (reserved).

### Wire format

Field constraints on the registering POST:

| Field        | Type   | Notes                                                                |
|--------------|--------|----------------------------------------------------------------------|
| `model_name` | string | **MUST start with `user.`** to avoid registry collisions             |
| `checkpoint` | string | `<owner>/<repo>` or `<owner>/<repo>:<quantization>`                  |
| `recipe`     | string | `llamacpp` for any GGUF embedder                                     |
| `embedding`  | bool   | `true` → adds `embeddings` label → typed EMBEDDING at classify time  |
| `stream`     | bool   | optional; `true` = SSE progress events                               |
| `subscribe`  | bool   | optional; default `true` (synchronous)                               |

### Verification

`POST /v1/embeddings` with `{"model": "user.nomic-embed-text-v1-q8_0", "input": "ping"}` should return a normal OpenAI embeddings response. If it 501s, the model loaded as LLM — re-check that `/v1/models?show_all=true` shows label `embeddings`.

---

## 3. Rerank registration recipe

Same pattern as §2; flag is `reranking: true`. Spike's failure on `pqnet/bge-reranker-v2-m3-Q8_0-GGUF` was the bundled `server_models.json` entry's HF resolution (see §4). **Workaround: register under `user.*` from a verified-good checkpoint, bypass the built-in entry.**

```bash
curl -X POST http://127.0.0.1:9100/v1/pull -H "Authorization: Bearer ${LEMONADE_API_KEY}" \
  -d '{"model_name":"user.bge-reranker-v2-m3-q4_k_m",
       "checkpoint":"gpustack/bge-reranker-v2-m3-GGUF:Q4_K_M",
       "recipe":"llamacpp","reranking":true}'

curl -X POST http://127.0.0.1:9100/v1/load -H "Authorization: Bearer ${LEMONADE_API_KEY}" \
  -d '{"model_name":"user.bge-reranker-v2-m3-q4_k_m",
       "llamacpp_backend":"rocm","ctx_size":512}'
```

The `hal0_rerank_slot_wiring` memory's port-8086 + non-8081-collision concern **does not apply** under Lemonade — lemond owns the backend port pool and reports per-model `backend_url` via `/v1/health.all_models_loaded[]`. Inference goes via `POST /v1/rerank` (also `/v1/reranking`, see `repo: docs/api/llamacpp.md`).

---

## 4. HF cache resolution workaround

Two plausible causes for the spike's `.gguf`-vs-snapshot-dir bug (resolver source not yet line-traced):

1. **Server-models registry bug** — bundled entry's `checkpoint` doesn't pin a canonical `.gguf` filename, resolver picks the snapshot directory.
2. **HF cache layout mismatch** — if pre-populating from `/mnt/ai-models/`, symlink structure deviating from HF's `refs/main` → `snapshots/<sha>/<file>.gguf` triggers a directory fallback.

### hal0 workaround

- **Never use bundled `server_models.json` entries for embed/rerank.** Always re-register under `user.*` (§2, §3) — puts hal0 in control of the checkpoint coord.
- **Air-gapped / pre-staged caches:** mirror HF snapshot layout exactly:
  ```
  ~/.cache/lemonade/models/models--<owner>--<repo>/
    blobs/<sha256-hex>
    refs/main                       # text file with snapshot sha
    snapshots/<sha>/<file>.gguf    # symlink → ../../blobs/<sha256-hex>
  ```
- **Verify post-load:** check `/v1/health.all_models_loaded[].backend_url` is non-empty. If `/v1/load` returned 200 but the backend didn't actually load weights, the next inference call triggers nuclear evict-all (ADR-0007).
- **File upstream** once we have a clean repro: bundled entries should pin a specific `.gguf` filename when the repo's quantization qualifier is ambiguous.

---

## 5. `/v1/stats` shim → hal0 Prometheus map

Lemonade's bundled llama-server (b9253) returns 501 on `/metrics`. `/v1/stats` is the workaround. **Important caveat:** `/v1/stats` is "performance statistics from the last request" — it is per-inference, not a live process gauge. hal0's metrics layer must sample-and-aggregate, not point-scrape.

### `/v1/stats` response schema

Fields: `time_to_first_token` (float s), `tokens_per_second` (float), `input_tokens` (int), `output_tokens` (int), `decode_token_times` (float[] s), `prompt_tokens` (int).

### Mapping to hal0's Prometheus names

| hal0 metric                         | v0.2 source                                          |
|-------------------------------------|------------------------------------------------------|
| `llamacpp:prompt_tokens_total`      | accumulate `/v1/stats.prompt_tokens` at hal0 layer   |
| `llamacpp:tokens_predicted_total`   | accumulate `/v1/stats.output_tokens` at hal0 layer   |
| `llamacpp:prompt_tokens_seconds`    | derive `input_tokens / time_to_first_token` (approx) |
| `llamacpp:predicted_tokens_seconds` | `/v1/stats.tokens_per_second` direct                 |
| `llamacpp:kv_cache_usage_ratio`     | **`/v1/slots`** — `n_prompt_tokens / n_ctx`          |
| `llamacpp:requests_processing`      | `/v1/slots` count where `is_processing == true`      |
| `llamacpp:requests_deferred`        | hal0's own dispatch queue depth (not exposed)        |

### `/v1/slots` confirmation

`repo: docs/api/llamacpp.md` confirms Lemonade exposes `GET /v1/slots` as a pass-through to llama.cpp's `/slots`. **PR #124's `n_prompt_tokens / n_ctx` approach works unchanged.** Slot save/restore/erase also pass-through at `POST /v1/slots/{id}?action={save,restore,erase}`. Only LLM backends expose `/slots`; embed/rerank/whisper backends don't — KV-cache N/A there.

### Polling cadence

- `/v1/stats` is cheap (in-process JSON). Poll on hal0's 5s metrics tick.
- `/v1/slots` is forwarded to the backend over HTTP; non-trivial under load. Same 5s cadence.
- **Reset semantics:** `/v1/stats` overwrites on every new request. Two requests between polls = first one lost. For "avg TTFT over 60s" widgets, aggregate from the SSE inference stream itself — do not rely on `/v1/stats` polling. Keep the v0.1.x in-request capture path.

---

## 6. WebSocket protocols

### Discovery

`GET /v1/health` returns `websocket_port` (int, OS-assigned by default; configurable via `--websocket-port` or `config.json`). Both WS paths multiplex on that single port.

### `/logs/stream` taxonomy

`ws://127.0.0.1:<ws_port>/logs/stream`

Client → server: `{"type":"logs.subscribe","after_seq":null|<int>}` (null = full backlog, capped 5000).

Server → client:
- `{"type":"logs.snapshot","entries":[{seq,timestamp,severity,tag,line}, ...]}` — one-shot on subscribe.
- `{"type":"logs.entry","entry":{seq,timestamp,severity,tag,line}}` — live.
- `{"type":"error","error":{message,type}}` — protocol error.

Severity: `Trace|Debug|Info|Warning|Error|Fatal`. Tags: `Server`, `Router`, `ModelManager`, `WrappedServer`.

**Key gap:** no `model.loaded`/`model.load_failed` event. UI must parse `(Router)`-tagged lines OR poll `/v1/health.all_models_loaded[].last_use`. The nuclear-evict-all line `"Load failed with non-file-not-found error, evicting all models and retrying..."` is the specific Error-severity entry to pattern-match for ADR-0007 §6's evict-storm warning.

### `/realtime` taxonomy (OpenAI-compatible)

`ws://127.0.0.1:<websocket_port>/realtime?model=Whisper-Tiny`

**Audio format:** PCM16, 16 kHz, mono, base64-encoded chunks (~85 ms recommended chunk size).

**Client→server:** `session.update` (model, VAD `turn_detection`; null disables VAD), `input_audio_buffer.{append,commit,clear}`.

**Server→client:** `session.{created,updated}`, `input_audio_buffer.{speech_started,speech_stopped,committed,cleared}`, `conversation.item.input_audio_transcription.{delta,completed}`, `error`.

VAD defaults: `threshold=0.01`, `silence_duration_ms=800`, `prefix_padding_ms=250`. `turn_detection: null` = manual commit-driven.

### Auth posture

`LEMONADE_API_KEY` is enforced on every HTTP endpoint via `Authorization: Bearer KEY` (confirmed for `/v1/health`, `/v1/models`, `/internal/*`). **WebSocket auth is undocumented** (`repo: docs/embeddable/runtime.md` is silent).

hal0 v0.2 stance:
1. lemond binds `127.0.0.1:9100` in the systemd unit. WS port is OS-assigned; **verify it also binds loopback via `ss -ltnp` post-install**, otherwise force `--websocket-host 127.0.0.1`.
2. LAN-loopback-only is acceptable for v0.2 given (1). Treat WS as same trust boundary as HTTP control plane.
3. File upstream: WS auth via `Sec-WebSocket-Protocol: bearer.<KEY>` or `?api_key=`. **Do not expose WS beyond loopback until that lands.**

### Disable broadcast

lemond's UDP-13305 RFC1918 announcement is a tray-app pattern. Set `--no-broadcast` in the systemd unit.

---

## 7. `--llamacpp rocm` enforcement

Default backend pick on Strix Halo is Vulkan, which the spike measured at 3× slower than ROCm. ROCm must be forced per-model.

### Where it lives in the wire shape

`llamacpp_backend` is a **load-time per-request field**, not a server-startup flag and not a registry field. Source: `repo: docs/api/lemonade.md` (POST /v1/load — `llamacpp_backend: vulkan|rocm|metal|cpu`).

### hal0 enforcement strategy

Preferred: **always include `llamacpp_backend: "rocm"` in every hal0-initiated `/v1/load` for llamacpp recipes.** hal0 owns its own preferences; no server-side state. `LemonadeProvider.load_slot()` sets it unconditionally for any slot configured `accelerator: igpu`.

Rejected alternatives:
- `save_options: true` once per model — introduces hidden persisted state in `recipe_options.json`; user-deletes-file regression.
- `/internal/set {"llamacpp_backend": "rocm"}` — endpoint is loopback-only and explicitly unstable.

Slot-config mapping in `slots/*.toml`:

```toml
[defaults]
accelerator = "igpu"      # → llamacpp_backend = "rocm"
ctx_size    = 8192
extra_args  = ""          # → llamacpp_args (STRING, not list)
```

CPU slots → `llamacpp_backend: "cpu"`. No "auto"; hal0 never lets Lemonade pick. **No Vulkan fallback:** spike measured Lemonade-Vulkan 3× slower than hal0's stock Vulkan (different build). On ROCm load failure, surface to user — do not retry Vulkan.

---

## 8. Open questions for architect + UI

For **architect.md**:

1. **`LoadOptions` adapter ownership** — `SlotConfig`→`LoadOptions` lives in `LemonadeProvider`, not `LemonadeClient`. Confirm interface.
2. **Idle-unload TTL** — `/v1/load` is declarative; Lemonade has its own per-type LRU (max_loaded_models default 1). Keep hal0-layer TTL, or delegate? Affects "slot age" widget.
3. **install.sh model-bootstrap sequencing** — pull-then-load, or pull-only-let-first-request-trigger-load? Affects first-boot latency. Add `/v1/pull` with `embedding`/`reranking` flags for non-LLM models.
4. **`user.*` namespace policy** — all hal0-registered models go under `user.*`. Document so manual `server_models.json` edits don't collide.

For **ui.md**:

1. **Loading state machine** — no WS event for model-load-{started,completed,failed}. Combine `/logs/stream` Router lines + `/v1/health.all_models_loaded[].last_use`. Spec it.
2. **Evict-storm banner regex** — lock the pattern-match string from §6.
3. **`/v1/stats` is per-last-request, not gauge** — can't poll for "current throughput". Use SSE in-stream capture or reframe widget as "Last request".
4. **`/realtime` audio chunk size** — 85 ms recommended; mismatch with browser MediaRecorder default (~250 ms) introduces latency.

---

## File index

- `repo: src/cpp/server/server.cpp::handle_load` (~L3068-3183) — `/v1/load`
- `repo: src/cpp/server/recipe_options.cpp` — `get_keys_for_recipe`, `is_empty_option`
- `repo: src/cpp/cli/lemonade_client.cpp::load_model` — CLI body shape
- `repo: src/cpp/include/lemon/model_types.h::get_model_type_from_labels` — type assignment
- `repo: src/cpp/Extra-Models-Dir-Spec.md` — extra_models_dir scan
- `repo: docs/api/{lemonade,llamacpp,openai}.md` — public API, `/v1/slots`, `/realtime`
- `repo: docs/embeddable/runtime.md` — `LEMONADE_API_KEY` auth

Next consumer: PR-3 (`LemonadeProvider`) — extend `LemonadeClient.load()` per §1, wire ROCm-by-default per §7, add embed/rerank pull steps per §2/§3 to install.sh.
