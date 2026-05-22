# Lemonade Repo Deep-Dive — 2026-05-22

**Repo state read:** `lemonade-sdk/lemonade@7af26f75` (HEAD of `main`, 2026-05-21).
**Scope:** dev internals, full API surface, embeddable build, omni recipe, WS protocol — all that hal0's v0.2 LemonadeProvider and v0.2.1 UI rework will sit on top of.
**Pairs with:** `lemonade-spike-findings-2026-05-22.md`, `lemonade-migration-plan.md`, ADR-0006, ADR-0007.

---

## Executive summary (200 words)

The spike findings stand, but the repo reveals two big surprises and three structural wins.

**What hal0 inherits for free, more than expected:**
1. Lemonade *already ships* an embeddable build target (`cmake --build --target embeddable`) that emits a portable `lemond + lemonade + resources/` tarball with byte-identical CLI semantics to the deb. ADR-0006 decision #14 — "publish our own `ghcr.io/hal0ai/hal0-lemond` wrapper container" — can be re-evaluated; the embeddable tarball already does 90% of the bundling. A thin systemd-unit wrapper on host may beat full containerization.
2. A first-party browser UI (`src/web-app/`) is already maintained as a hard invariant to be Debian-packageable from system npm modules only. Tracks the lemond release version 1:1. hal0 v0.2.1 can either reuse `/app` directly or build against the documented WS taxonomy.
3. The OmniRouter pattern (`collection.omni` recipe) is conceptually identical to hal0's capability slots — only it's expressed inside Lemonade's own model registry rather than as an external overlay.

**What's fragile to depend on:**
1. `/internal/*` endpoints are explicitly first-party-only, loopback-restricted, "may change without notice." Slot eviction / config writes from hal0 must avoid these despite their convenience.
2. The "nuclear evict-all" policy is *documented intent*, not a bug.

**Biggest surprise vs public docs:** `/v1/load` request schema is bare — `model_name` is the *only* mandatory field. The spike curl that returned `"type must be string, but is null"` was almost certainly hitting `request_json["model_name"]` against an absent or null key — a nlohmann unconditional access. The "type must be string" message is nlohmann's, not Lemonade's. Mystery solved.

---

## 1. `/v1/load` actual request schema (ground truth)

**Source of truth:** `src/cpp/server/server.cpp::handle_load()` lines 3068-3183 + `src/cpp/server/recipe_options.cpp::RecipeOptions(recipe, options)` lines 132-141.

### Required field

```cpp
model_name = request_json["model_name"];  // unconditional access — null/missing → nlohmann throws
```

`model_name` is the only required field. The exception thrown if it is missing or null is the same `"type must be string, but is null"` that the spike saw. **Diagnosis: the spike body had `model_name` either absent, JSON-null, or non-string.** Standard `{"model_name": "Qwen3-0.6B-GGUF"}` works.

### Optional fields — all consumed via `request_json.value(key, default)` or via `RecipeOptions` constructor

| Field | Type | Recipes it applies to | Notes |
|---|---|---|---|
| `save_options` | bool | all | If true, persists per-model options to `recipe_options.json`; default `false` |
| `ctx_size` | int | `llamacpp`, `flm`, `ryzenai-llm` | Context window |
| `llamacpp_backend` | string | `llamacpp` | `vulkan`, `rocm`, `metal`, `cpu` |
| `llamacpp_args` | string | `llamacpp` | Custom args to llama-server (reserved-args list applies) |
| `llamacpp_device` | string | `llamacpp` | Comma-separated device list (e.g. `Vulkan0`) |
| `whispercpp_backend` | string | `whispercpp` | `npu` / `cpu` / `vulkan` |
| `whispercpp_args` | string | `whispercpp` | Custom args |
| `sd-cpp_backend` | string | `sd-cpp` | (note the hyphen in the key) |
| `sdcpp_args` | string | `sd-cpp` | |
| `steps`, `cfg_scale`, `width`, `height`, `sampling_method`, `flow_shift` | numeric | `sd-cpp` | Image gen params |
| `flm_args` | string | `flm` | |
| `vllm_backend`, `vllm_args` | string | `vllm` | |
| `merge_args` | bool | all | Default `true`; if `false`, per-model `*_args` replace global instead of merging |

`RecipeOptions(recipe, options)` filters incoming JSON to only the keys returned by `get_keys_for_recipe(recipe)`, so passing extra fields is harmless. Empty-string and `-1` are treated as "use default" via `is_empty_option()`.

### `/v1/load` is declarative

Comment in source: *"Load model with optional per-model settings (declarative: no-op if already loaded with matching options, reload only if options differ)"*. hal0's idle-unload + reload-on-options-change driver does not need to track load state itself; Lemonade no-ops correctly.

### Collection load behavior (omni)

If `info.recipe == "collection.omni"`, handle_load iterates `info.components`, downloads any missing ones, and loads each via `router_->load_model(component, comp_info, comp_info.recipe_options, ...)`. **Per-load options like `ctx_size` or `llamacpp_backend` are NOT forwarded to components.** Each component is loaded with its own persisted `recipe_options.json` entry. Documented in the API ref; verified in `server.cpp:3132-3137`.

### Server bug class to know about

`is_empty_option` treats `""`, `"auto"`, and `-1` (int) as "use default". Pass `null` for any of these fields and you trip the unconditional accessor. Always send omitted-or-typed-value, never explicit nulls.

---

## 2. WebSocket protocol (for v0.2.1 UI)

**Source of truth:** `src/cpp/server/websocket_server.cpp` + `docs/api/lemonade.md` + `docs/api/openai.md`.

### Connection routing

The WS server shares a single port (`websocket_port`, OS-assigned by default; configurable via `--websocket-port` or `config.json`). Discovered via `GET /v1/health` → `websocket_port` field. Two URL paths multiplexed on that port:

- `ws://host:<port>/logs/stream` → log streaming
- `ws://host:<port>/realtime?model=Whisper-Tiny` → realtime audio transcription (OpenAI-compatible)

### `/logs/stream` taxonomy

**Client → server:**
- `{"type":"logs.subscribe","after_seq":null|<int>}` — replay from seq, or full backlog (≤5000 entries retained)

**Server → client:**
- `{"type":"logs.snapshot","entries":[ {seq,timestamp,severity,tag,line}, ... ]}` — initial batch (sent once)
- `{"type":"logs.entry","entry":{seq,timestamp,severity,tag,line}}` — live entries
- `{"type":"error","error":{message,type}}` — protocol error

Severity enum: `Trace | Debug | Info | Warning | Error | Fatal`. Tags are component-level strings (`Server`, `Router`, ...).

### `/realtime` taxonomy (OpenAI-compatible)

Initial `session.created` on connect. Then:

**Client → server:** `session.update`, `input_audio_buffer.append` (base64 PCM16 16kHz mono), `input_audio_buffer.commit`, `input_audio_buffer.clear`.

**Server → client:** `session.created`, `session.updated`, `input_audio_buffer.speech_started`, `input_audio_buffer.speech_stopped`, `input_audio_buffer.committed`, `input_audio_buffer.cleared`, `conversation.item.input_audio_transcription.delta` (interim), `conversation.item.input_audio_transcription.completed` (final), `error`.

VAD configurable via `session.update.session.turn_detection = {threshold, silence_duration_ms, prefix_padding_ms}` or `null` to disable.

### What hal0's v0.2.1 UI should consume

- `/logs/stream` is the *primary* live signal; current hal0 dashboard polling can be replaced with `logs.subscribe + after_seq` for reconnect-safe streaming. Use `seq` for dedup.
- There is **no** WS event for "model load started/completed/failed" — those land on `/logs/stream` as `(Router)` and `(Server)` tagged Info/Error lines. v0.2.1 should either parse tagged log lines OR poll `/v1/health.all_models_loaded[].last_use` to drive a load-progress UI.
- There is no metrics-over-WS channel. Stats are pull-only via `/v1/stats`.

---

## 3. Type classification + reserved args

**Source:** `src/cpp/include/lemon/model_types.h::get_model_type_from_labels()`.

Type assignment is **label-driven, not field-driven**. There is no `type` field in `server_models.json`. Resolution order:
1. **Chat-indicator labels win.** Any of `vision`, `reasoning`, `tool-calling`, `tools`, `chat-transcription` → `ModelType::LLM`.
2. Else first-match on: `embeddings`/`embedding` → EMBEDDING, `reranking` → RERANKING, `transcription` → TRANSCRIPTION, `image` → IMAGE, `tts` → TTS.
3. Else → `ModelType::LLM` (default).

**Hal0 impact:** for the spike's broken rerank/embed discovery, the cause is `extra_models_dir` GGUFs receive labels `["custom"]` only (Extra-Models-Dir-Spec.md §"Model Properties"), so type defaults to LLM and `--reranking`/`--embedding` flags are never passed. Workaround: don't rely on extra-models-dir for non-LLM modalities — register them via `/v1/pull` with `embedding: true` or `reranking: true` (the API explicitly accepts these, which adds the `embeddings` / `reranking` label).

**Device** is derived from recipe via `get_device_type_from_recipe()`, NOT from labels. Recipe-to-device static map: `llamacpp` → GPU (overridable to CPU for `cpu` backend), `ryzenai-llm`/`flm` → NPU, `whispercpp` → CPU (overridable), `sd-cpp` → CPU (overridable), `kokoro` → CPU (no GPU build exists), `collection.omni` → NONE.

### Reserved args (the canonical list)

From `/v1/load` doc verbatim — args forbidden in `llamacpp_args`:

> `-m, --port, --ctx-size, -ngl, --jinja, --mmproj, --embeddings, --reranking`

Whispercpp_args forbidden: `-m, --model, --port`. The spike captured a larger superset from server logs (`--device, --gpu-layers, --n-gpu-layers, -dev, --mmproj-*, --no-mmproj-*, -mm, -mmu`) — these are *also* managed but not in the doc. Treat the spike's list as authoritative for current build.

---

## 4. `collection.omni` recipe + OmniRouter

**Source:** `docs/dev/lemonade-omni.md` + `src/cpp/include/lemon/model_types.h:9` + `src/app/src/renderer/utils/toolDefinitions.json`.

An **Omni model** is a registered model with `recipe: "collection.omni"` and a `components: [...]` array of other registered model names. It is *not* a multi-modal model file — it is a manifest that bundles several single-modality models together. Loading the collection loads each component using *its own* recipe_options entry.

Tool surface (canonical, used by Lemonade's desktop app + the doc):

| Tool | Endpoint | Required model label |
|---|---|---|
| `generate_image` | `POST /v1/images/generations` | `image` |
| `edit_image` | `POST /v1/images/edits` | `edit` |
| `text_to_speech` | `POST /v1/audio/speech` | `tts` |
| `transcribe_audio` | `POST /v1/audio/transcriptions` | `transcription` |
| `analyze_image` | `POST /v1/chat/completions` | LLM with `vision` |

The "router" is just OpenAI tool-calling JSON shipped to the agent; Lemonade does not own the agent loop. Omni models are hidden from default `/v1/models` and only appear with `?show_all=true`.

**hal0 mapping:**
- hal0's `capabilities.toml` (capability → backend slot rollup) and `collection.omni` (collection of model_names) overlap conceptually but target different layers. capabilities.toml expresses "which backend serves which capability"; omni expresses "this group of registered models is a single user-facing kit."
- For v0.2: keep `capabilities.toml` as the higher-level *UX rollup*; treat collection.omni as a sub-feature hal0 can expose to power-users (`hal0 capabilities export-as-omni`) or pass-through unchanged.
- The omni "Halo" SKU (`LMX-Omni-52B-Halo` = Qwen3.6-35B + Flux-2-Klein-9B + Whisper-Large-v3-Turbo + kokoro-v1) is *named for Strix Halo*. hal0 inherits a curated, vendor-blessed Strix Halo bundle by adopting Lemonade.

---

## 5. Process supervision + nuclear evict-all

**Source:** `src/cpp/Multi-Model-Spec.md` + `src/cpp/server/router.cpp`.

- LRU per *type* slot. `--max-loaded-models N` (default 1) applies to each of llm/embedding/reranking/transcription/image/tts independently. `-1` = unlimited.
- Eviction granularity: only models of the *same type* are evicted to make room. Exception: an NPU load evicts any existing NPU model regardless of type (NPU exclusivity).
- **"Nuclear" evict-all policy is policy, not bug.** Multi-Model-Spec §"Error Handling": *"If a WrappedServer load fails (with exceptions noted below), all WrappedServers of every type are evicted, and the load is re-attempted. This 'nuclear' policy simplifies implementation while remaining effective in practice."* **Exception:** file-not-found errors are exempt. This validates ADR-0007's mitigation strategy verbatim.
- **Serialized loading.** Only one WrappedServer loads at a time. Concurrent `/v1/load` queues indefinitely. Hal0 needs a hard timeout at its layer.
- **Busy-protection.** A WrappedServer fulfilling an inference request cannot be evicted until it finishes (EVICTION_TIMEOUT=5s; `router.h:18`).
- **Auto-load protection.** An inference request to an unloaded model triggers auto-load; the inference completes before the WrappedServer becomes eligible for eviction.

---

## 6. Build system + embeddable tarball

**Source:** `docs/embeddable/`, `docs/dev/getting-started.md`, `CMakeLists.txt` (68 KB, not fully read).

- Single CMake target `embeddable` produces a per-platform archive: `build/lemonade-embeddable-<VERSION>-{ubuntu|windows|macos}-{x64|arm64}.{tar.gz|zip}`.
- Archive contents: `lemond`, `lemonade`, `LICENSE`, `resources/server_models.json`, `resources/backend_versions.json`, `resources/defaults.json`. Optionally `resources/web-app/` if `-DBUILD_WEB_APP=ON`.
- Runtime layout when `lemond ./` is invoked from the archive root:
  - `./config.json` — auto-generated from `resources/defaults.json` on first launch; **safe to delete `defaults.json` after**.
  - `./recipe_options.json` — per-model overrides.
  - `./bin/{llamacpp,ryzenai-server,flm,sdpp,whispercpp}/{rocm,vulkan,cpu,npu}/...` — backend binaries (downloaded lazily, or pre-staged at packaging time via `lemonade backends install BACKEND:DEVICE`).
  - `./models/models--<owner>--<repo>/` — HF-standard layout.
  - `./extra_models/` — bring-your-own GGUFs (extra.* namespace).
- Auth: `LEMONADE_API_KEY=KEY lemond ./ --port PORT` enables bearer-token auth. Missing/wrong key → 401. This is the canonical embedding pattern — hal0 should adopt it.

**ADR-0006 decision #14 challenge:** the spike findings + the embeddable tarball + the existing `.deb` package together mean hal0 has *three* viable bundle paths:
1. Custom `ghcr.io/hal0ai/hal0-lemond` container (original ADR-0006 plan)
2. Apt-install Lemonade's official `.deb` + a hal0 systemd unit
3. Bundle the `embeddable` tarball + hal0 systemd unit, fully self-contained

Option 3 has the smallest surface and avoids the `apparmor_parser` LXC docker-build issue (memory `hal0_docker_build_lxc_apparmor`). Worth raising in next grilling pass.

---

## 7. Internal endpoints (DO NOT depend on)

**Source:** `docs/dev/getting-started.md` + `docs/embeddable/runtime.md`.

Endpoints under `/internal/*` are:
- Loopback-restricted (`127.0.0.1`/`::1`); non-localhost → 403.
- Documented as *"for first-party Lemonade software only (CLI, tray app, desktop app). They are not part of the public API, may change without notice, and must not be relied upon by third-party integrations."*
- `POST /internal/set` (server-level + deferred keys — see embeddable/runtime.md §"POST /internal/set" for full list including `port, host, log_level, global_timeout, no_broadcast, extra_models_dir, max_loaded_models, ctx_size, llamacpp_backend, llamacpp_args, sdcpp_backend, whispercpp_backend, whispercpp_args, vllm_backend, vllm_args, steps, cfg_scale, width, height, flm_args`)
- `GET /internal/config` — full runtime config snapshot.
- `POST /internal/shutdown` — unload all + shut down.
- `POST /internal/cleanup-cache` — orphan HF cache cleanup.

**Hal0 stance:** these are tempting but unstable. Stay on the public surface. If a runtime tuneable is only available via `/internal/set`, drive it through the `lemonade config set` CLI subprocess instead — same endpoint underneath but Lemonade owns the compat contract.

---

## 8. Test strategy

**Source:** `docs/dev/getting-started.md#testing` + `test/` directory.

Python test suite under `test/` drives the `lemonade` CLI binary (NOT the HTTP surface directly):

| File | Coverage |
|---|---|
| `server_cli2.py` | CLI verbs: version, status, list, export, backends, pull, import, load, unload, run, launch, delete |
| `server_endpoints.py` | HTTP: health, models, pull, load, unload, system-info, stats |
| `server_llm.py` | Inference: chat, embeddings, reranking — parametrized by `--wrapped-server` and `--backend` |
| `server_whisper.py` | ASR |
| `server_sd.py` | Image gen (~2-3 min/img on CPU) |

**Hal0 applicability:** `server_endpoints.py` is directly relevant as a contract test — hal0's LemonadeClient regression suite should mirror its assertions (response shapes for /v1/health, /v1/load, /v1/unload, /v1/models, /v1/stats, /v1/system-info). Worth pulling into hal0 verbatim or by reference.

---

## 9. Things that surprised me vs the spike

1. **`lemonade backends install` writes to `./bin/` of the lemond CWD**, not a global location. The "`/opt/hal0/flm-ubuntu` doesn't exist" finding from the spike is consistent — that path was a guess. The real path on the LXC is `~/.cache/lemonade/bin/flm/...` (default lemond cache).
2. **`vllm` is a documented recipe with full /internal/set keys.** Spike treated vLLM-ROCm as out of scope; the API support already exists. Cost of enabling it later is small.
3. **`kokoro` has its own recipe slot** (recipe `kokoro`, device CPU only, no GPU variant ever — spike confirmed but the source makes it explicit: `get_device_type_from_recipe` hardcodes CPU). GPU-kokoro loss is *permanent in Lemonade's design*, not a transient state.
4. **`/live` is unversioned and outside `/api/v0/`, `/api/v1/`, `/v0/`, `/v1/` prefixes.** Use this for hal0 healthcheck, not `/v1/health` — `/live` does zero work; `/v1/health` enumerates loaded models.
5. **UDP beacon on port 13305 broadcasts hostname to RFC1918 networks** for client discovery. Disable with `--no-broadcast` if hal0 doesn't want unsolicited LAN announcements (currently a leaky tray-app pattern in a server context).
6. **`max_models` is reported per-type by `/v1/health`** — `{"transcription":1,"embedding":1,"image":1,"llm":1,"reranking":1,"tts":1}`. hal0's dashboard already wants per-type slot counts; this surfaces them directly.

---

## 10. File index — which Lemonade file covers which subsystem

| Subsystem | File(s) | Why care |
|---|---|---|
| /v1/load handler | `src/cpp/server/server.cpp:3068-3183` | Source of truth for required/optional fields |
| Recipe option parsing | `src/cpp/server/recipe_options.cpp` | Per-recipe key whitelist, defaults, CLI flag mapping |
| Eviction policy | `src/cpp/Multi-Model-Spec.md` + `src/cpp/server/router.cpp` | LRU + nuclear-evict-all rationale |
| Type/device classification | `src/cpp/include/lemon/model_types.h` | Label → type, recipe → device |
| Extra-models-dir scan | `src/cpp/Extra-Models-Dir-Spec.md` + `src/cpp/server/model_manager.cpp` | extra.* namespace, label-restrictions |
| WebSocket protocol | `src/cpp/server/websocket_server.cpp` + `docs/api/lemonade.md` + `docs/api/openai.md` | logs.* + realtime audio taxonomy |
| Collection.omni | `src/cpp/include/lemon/model_types.h:9-13` + `src/cpp/server/model_manager.cpp` (`validate_collection_request`) + `docs/dev/lemonade-omni.md` | Omni recipe shape + tool catalog |
| Backend version pinning | `src/cpp/resources/backend_versions.json` | What versions of llama.cpp / whisper.cpp / FLM ship per release |
| Server config defaults | `src/cpp/resources/defaults.json` | First-launch config.json content |
| Embeddable build | `docs/embeddable/` + `CMakeLists.txt` (target `embeddable`) | Tarball assembly + auth pattern |
| First-party UI | `src/app/` (Tauri desktop) + `src/web-app/` (browser, Debian-packageable) | Shared React renderer; web-app is what `/app` serves |
| Web-UI Debian invariant | `docs/dev/web-ui.md` | Why hal0 can't just `npm install` arbitrary deps into the web-app build |
| API specs | `docs/api/{lemonade,openai,anthropic,ollama,llamacpp}.md` | Full request/response shapes for all surfaces |
| Internal endpoints | `docs/dev/getting-started.md#internal-endpoints` + `docs/embeddable/runtime.md` | Tempting but explicitly unstable |
| Integration patterns | `docs/integrations/{claude-code,open-webui,continue,...}.md` | What "free integrations" hal0 inherits |

---

## 11. Recommendations to feed back into ADR-0006 / migration plan

1. **Reconsider decision #14 (custom hal0-lemond container).** The `embeddable` tarball + a hal0 systemd unit may replace it with significantly less moving parts. Re-cost vs the `apparmor_parser`-on-LXC pain.
2. **Adopt `LEMONADE_API_KEY` from day one.** It's the canonical lockout pattern and hal0's threat model includes "other LAN apps reaching the gateway directly."
3. **Switch hal0 healthcheck from `/v1/health` to `/live`.** Cheaper, doesn't risk perturbing eviction timestamps.
4. **For v0.2.1 UI:** subscribe to `/logs/stream` with `after_seq` for the live event surface; supplement with `/v1/health` polling for `all_models_loaded[].last_use`. There is no dedicated model-state-change WS event; do not wait for one.
5. **Reserved-args list:** the spike's superset (`--device --gpu-layers --n-gpu-layers --jinja --mmproj* --no-mmproj* -dev -mm -mmu`) is current truth; the API doc's shorter list is the public-promised subset. Hal0's CLI/config validator should reject from the larger list.
6. **Type classification:** for embed/rerank models in `extra_models_dir`, the only reliable path is `/v1/pull` registration with `embedding:true`/`reranking:true`. Document this in the install.sh model-bootstrap path.
7. **Collection.omni as v0.2.1+ feature:** `hal0 capabilities export-as-omni` would let users hand off hal0-curated bundles into Lemonade's native omni picker — a one-way bridge that costs little.

---

*End deep-dive. Memories written: `hal0_lemonade_internals`, `hal0_lemonade_v1_load_schema`, `hal0_lemonade_ws_protocol`, `hal0_lemonade_omni_pattern`. MEMORY.md index updated.*
