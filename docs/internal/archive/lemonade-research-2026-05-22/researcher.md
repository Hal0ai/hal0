# Lemonade upstream survey — 2026-05-22

Read-only catalogue of Lemonade integration hooks for hal0 v0.2 (Path 4: Lemonade for all iGPU modalities, FLM/NPU toolbox retained). Source: `lemonade-sdk/lemonade@main` as of 2026-05-22. Pairs with `../lemonade-spike-findings-2026-05-22.md` and `../lemonade-repo-deep-dive-2026-05-22.md`; this doc covers what they didn't — full doc-tree, integration patterns, UI primitives, upstream multi-tenant threads. No design decisions.

---

## 1. Embeddable build (`docs/embeddable/`)

Officially-supported build target producing a standalone tarball for app vendors to bundle their own `lemond`.

**Process model:** single-process, single-binary; long-lived subprocess of the embedding app. No daemonization helper — embedder supervises (systemd/runit/launchd). Linux single-instance protection via file-lock (`dev/getting-started.md`); two lemonds on the same host fight for the lock.

**Startup contract** ([source][embed-runtime]):
```
LEMONADE_API_KEY=KEY lemond ./ --port PORT
```
- `./` = working dir; lemond writes `config.json` + `recipe_options.json` here on first boot.
- `--port` = HTTP listen port (live-tunable via `/internal/set port`).
- `LEMONADE_API_KEY` env var = bearer token. All requests need `Authorization: Bearer KEY`; missing/wrong → 401.

**Tarball layout** (`cmake --build --target embeddable`):
```
lemond                           # HTTP server
lemonade                         # CLI client (optional — strip before shipping)
LICENSE
resources/server_models.json     # registry — controls model visibility
resources/backend_versions.json  # pinned llama.cpp / whisper.cpp / FLM / sd-cpp versions
resources/defaults.json          # seeds config.json on first boot; safe to delete after
resources/web-app/               # only if -DBUILD_WEB_APP=ON
```
At runtime `./bin/{llamacpp,whispercpp,flm,sd-cpp,kokoro}/{rocm,vulkan,cpu,npu}/…` lazy-downloaded via `lemonade backends install RECIPE:DEVICE` or `POST /v1/install`. ([source][embed-readme], [embed-backends])

**Vendor playbook:** `lemond ./` first-boot → `lemonade config set …` → `lemonade backends install …` → edit `server_models.json` + `backend_versions.json` → strip CLI + `defaults.json` before shipping.

**hal0 substitution surface** (informational): the install.sh path building `ghcr.io/hal0ai/hal0-lemond` could shrink to "tarball + systemd unit + `LEMONADE_API_KEY`," sidestepping the `apparmor_parser` LXC issue.

**Caveats:** per-platform tarball (no multi-arch); `BUILD_WEB_APP=ON` adds Node toolchain; `backend_versions.json` = only backend-pinning lever.

[embed-readme]: https://github.com/lemonade-sdk/lemonade/blob/main/docs/embeddable/README.md
[embed-runtime]: https://github.com/lemonade-sdk/lemonade/blob/main/docs/embeddable/runtime.md
[embed-backends]: https://github.com/lemonade-sdk/lemonade/blob/main/docs/embeddable/backends.md

---

## 2. Dev docs index (`docs/dev/`)

| File | Purpose | Relevance |
|---|---|---|
| `README.md` | Index of the other 7 dev docs. | low |
| `getting-started.md` | Build + test prereqs, **canonical `/internal/*` list**, dev workflow. | **high** — install.sh + LemonadeProvider |
| `app.md` | Tauri desktop app architecture; "thin client for a separately-running lemond." | low (no Tauri) |
| `contribute.md` | Maintainer directory + PR workflow. No plugin/extension story. | low |
| `lemonade-omni.md` | `collection.omni` registration + OmniRouter tool-calling pattern. | **high** — §4 |
| `philosophy.md` | 8 principles. Notable: "Foundation, Not Destination" (lemond expects to be embedded), "Backend Interchangeability," "Speed to Bleeding Edge." | med |
| `web-ui.md` | Browser React build; Debian-packageable invariant; webpack, `USE_SYSTEM_NODEJS_MODULES`. | **high** — §5 |
| `self-hosted-runners.md` | CI runner labels include `stx-halo` (first-class Strix Halo CI). | low |

([source][docs-dev])

[docs-dev]: https://github.com/lemonade-sdk/lemonade/tree/main/docs/dev

---

## 3. API surface

### 3.1 Public REST endpoints

Path prefixes `/api/v0`, `/api/v1`, `/v0`, `/v1` aliased; `/live` unversioned. ([source][api-lemonade])

**Lemonade-specific (lifecycle/config):**

| Method | Path | Purpose |
|---|---|---|
| GET | `/live` | Cheap liveness; HEAD ok; no auth |
| GET | `/v1/health` | Loaded models, `websocket_port`, `max_models{}` per type, `ready` |
| GET | `/v1/stats` | Last-inference perf (§3.3) |
| GET | `/v1/system-info` | OS/CPU/RAM, `devices{cpu,gpu,npu}`, `recipes{}` install state |
| POST | `/v1/pull` | Install model. `stream:true` → SSE; accepts `embedding:true`/`reranking:true` for type labels |
| POST | `/v1/load` | §3.4 |
| POST | `/v1/unload` | `model_name` or omit for unload-all |
| POST | `/v1/delete` | Remove from storage; auto-unloads |
| POST | `/v1/install` | Install backend (`recipe`+`backend`); `stream:true` |
| POST | `/v1/uninstall` | Uninstall backend; auto-unloads dependents |
| GET | `/v1/downloads` | List server download jobs |
| POST | `/v1/downloads/control` | `{id, action: pause\|cancel\|remove}` |
| GET | `/v1/pull/variants?checkpoint=<hf-repo>` | Enumerate GGUF variants |

**OpenAI-compat (hal0 already proxies):** `POST /v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, `/v1/responses`, `/v1/audio/transcriptions`, `/v1/audio/speech`, `/v1/images/{generations,edits,variations,upscale}`, `GET /v1/models`, `/v1/models/{id}`. ([source][api-openai])

**Ollama-compat:** `/api/{chat,generate,tags,show,delete,pull,embed,embeddings,ps,version}`. `/api/{create,copy,push}` → 501. ([source][api-ollama])

**Anthropic-compat:** `POST /v1/messages` (streaming + `?beta=true`). ([source][api-anthropic])

**llama.cpp passthrough:** `POST /v1/reranking`, `GET /v1/slots`, `POST /v1/slots/{id}?action=save|restore|erase`, `POST /v1/tokenize`. Slot save/restore needs `--slot-save-path`. ([source][api-llamacpp])

**Internal (loopback-only, unstable):** `POST /internal/{shutdown,set,cleanup-cache}`, `GET /internal/config`. Covered in deep-dive §7; use `lemonade config set` subprocess instead.

### 3.2 WebSocket endpoints

Single port (published in `/v1/health.websocket_port`; OS-assigned, or `--websocket-port`). Two paths multiplexed.

**`/logs/stream`** — log streaming. Already detailed in memory `hal0_lemonade_ws_protocol`; confirmed accurate. Quick summary:
- C→S `{type:"logs.subscribe", after_seq: null|<int>}` (replay or full backlog ≤5000)
- S→C `logs.snapshot` (one-shot), `logs.entry` (live), `error`
- Severity: `Trace|Debug|Info|Warning|Error|Fatal`; tags: `Server`, `Router`, …
- **No model-load WS event** — load progress arrives as Router-tagged lines; parse, or poll `/v1/health.all_models_loaded[].last_use`.

**`/realtime?model=Whisper-Tiny`** — OpenAI-compat audio transcription.
- C→S: `session.update`, `input_audio_buffer.{append,commit,clear}` (base64 PCM16 16kHz mono)
- S→C: `session.{created,updated}`, `input_audio_buffer.{speech_started,speech_stopped,committed,cleared}`, `conversation.item.input_audio_transcription.{delta,completed}`, `error`
- VAD via `session.turn_detection = {threshold, silence_duration_ms, prefix_padding_ms}` or `null` to disable.

### 3.3 `/v1/stats` vs `/metrics` (resolved)

lemond does **not** expose Prometheus `/metrics` itself; the spike's 501 came from the per-WrappedServer `backend_url` (upstream llama.cpp built without `LLAMA_SERVER_METRICS`). **Canonical Lemonade metric source = `GET /v1/stats`**, JSON, last-request-scoped (no built-in historical):

```json
{
  "time_to_first_token": <float>,
  "tokens_per_second": <float>,
  "input_tokens": <int>,
  "output_tokens": <int>,
  "prompt_tokens": <int>,
  "decode_token_times": [<float>, ...]
}
```

### 3.4 `/v1/load` canonical body shape

Resolved in deep-dive §1 from source. Key fact: **`model_name` is the ONLY required field. All others optional via `request_json.value(key, default)`. Empty-string, `"auto"`, `-1` are "use default" sentinels — NEVER send JSON `null`** (trips nlohmann unconditional accessor; was the spike's "type must be string, but is null" red herring).

Documented body fields ([`docs/api/lemonade.md`][api-lemonade]): `model_name` (required), `ctx_size`, `llamacpp_backend`, `llamacpp_args`, `whispercpp_backend`, `whispercpp_args`, `steps`, `cfg_scale`, `width`, `height`, `save_options` (default `false`), `merge_args` (default `true`). Source-only: `sd-cpp_backend`, `sdcpp_args`, `sampling_method`, `flow_shift`, `flm_args`, `vllm_backend`, `vllm_args`, `llamacpp_device`.

`/v1/load` is **declarative** — no-op if already loaded with matching options; reloads only if they differ. hal0's reload-on-change driver does not need to track load state.

### 3.5 Reserved args (managed by lemond, forbidden in `*_args`)

Documented `llamacpp_args`: `-m, --port, --ctx-size, -ngl, --jinja, --mmproj, --embeddings, --reranking`. `whispercpp_args`: `-m, --model, --port`. Spike captured the larger live-build superset including `--device, --gpu-layers, --n-gpu-layers, -dev, --mmproj-*, --no-mmproj-*, -mm, -mmu, --embedding, --rerank, --no-jinja`. **Spike list is authoritative for the bundled b9253 build; validate from the larger set.**

[api-lemonade]: https://github.com/lemonade-sdk/lemonade/blob/main/docs/api/lemonade.md
[api-openai]: https://github.com/lemonade-sdk/lemonade/blob/main/docs/api/openai.md
[api-ollama]: https://github.com/lemonade-sdk/lemonade/blob/main/docs/api/ollama.md
[api-anthropic]: https://github.com/lemonade-sdk/lemonade/blob/main/docs/api/anthropic.md
[api-llamacpp]: https://github.com/lemonade-sdk/lemonade/blob/main/docs/api/llamacpp.md

---

## 4. Omni recipe pattern

**IS:** a registration type in `server_models.json` with `recipe: "collection.omni"` + `components: [...]`. `POST /v1/load` against an omni `model_name` iterates components, downloads missing, loads each with its own `recipe_options.json` entry. ([source][omni-doc], `server.cpp:3132-3137`)

**IS NOT:** a server-side multi-modal router. "OmniRouter" is an **OpenAI tool-calling JSON contract** the host agent consumes; the agent picks `/v1/…` endpoints per tool call. Memory `hal0_lemonade_omni_pattern` confirmed accurate. ([omni-doc])

Canonical tool catalogue:

| Tool | Endpoint | Required label |
|---|---|---|
| `generate_image` | `POST /v1/images/generations` | `image` |
| `edit_image` | `POST /v1/images/edits` | `edit` |
| `text_to_speech` | `POST /v1/audio/speech` | `tts` |
| `transcribe_audio` | `POST /v1/audio/transcriptions` | `transcription` |
| `analyze_image` | `POST /v1/chat/completions` | LLM + `vision` |

Omni models hidden from default `/v1/models`; visible with `?show_all=true`.

**LMX-Omni-52B-Halo composition** (vendor-blessed Strix Halo bundle, "Halo" = Strix Halo, not coincidence): LLM `Qwen3.6-35B` (spike bench: 53→46 tok/s), image `Flux-2-Klein-9B`, ASR `Whisper-Large-v3-Turbo`, TTS `kokoro-v1`. hal0 inherits a curated reference bundle by adopting Lemonade.

**Interop:** `capabilities.toml` (which backend serves which capability) and `collection.omni` (a kit of registered model_names loadable in one call) live at different layers — not in conflict with the decision-locked stance. A future `hal0 capabilities export-as-omni` one-way bridge is cheap to add.

[omni-doc]: https://github.com/lemonade-sdk/lemonade/blob/main/docs/dev/lemonade-omni.md

---

## 5. UI primitives

Two UIs share React source:
1. **Tauri desktop** — `src/app/` — React 19 + TS + Rust shim. Out of scope.
2. **Browser web-app** — `src/web-app/` — webpack bundle of the same renderer, Tauri imports stubbed. Served by lemond at `GET /app`. ([source][dev-web-ui])

**Shipped components (55+ files in `src/app/src/renderer/`):**
- Chat: `ChatWindow.tsx`, `MarkdownMessage.tsx` (KaTeX, Debian-system overlay)
- Model mgmt: `ModelManager.tsx`, `AddModelPanel.tsx`, `DownloadManager.tsx`, `ModelOptionsModal.tsx`, `MarketplacePanel.tsx`
- Logs: `LogsWindow.tsx` (already wired to `/logs/stream`)
- Chrome: `TitleBar.tsx`, `StatusBar.tsx`, `Tabs.tsx`+`TabsContext.tsx`, `SettingsPanel.tsx`, `ResizablePanel.tsx`+`ResizableDivider.tsx`
- Atoms: `AboutModal.tsx`, `ConfirmDialog.tsx`, `Toast.tsx`, `AudioButton.tsx`, `NumericSetting.tsx`

Webpack + TS `transpileOnly`. `lemond` injects `window.api`; components branch on `window.api?.isWebApp`. `@tauri-apps/*` aliased to no-op `tauri-stub.js`.

**Debian-packageable invariant:** *"the native Debian package for `lemond` must build using only npm modules that ship in Debian's repositories. This split is an invariant."* `USE_SYSTEM_NODEJS_MODULES=true` resolves from `/usr/share/nodejs`, `/usr/lib/nodejs`, `/usr/share/javascript`. Narrow vetted dep set — positive for hal0 packaging.

**Adoptability matrix (UI agent owns the call):**
- **As-is:** `LogsWindow` + `/logs/stream` client; `DownloadManager` (matches `/v1/downloads*`); `MarkdownMessage` (KaTeX renderer).
- **With rewrite:** `ModelManager` / `AddModelPanel` — Lemonade is per-model, hal0 is per-capability.
- **Skip:** `TitleBar`/`Tabs`/`StatusBar` (Lemonade-branded Tauri chrome), `MarketplacePanel` (Lemonade marketplace, not hal0).
- **v0.2.1 hook:** `/logs/stream` with `after_seq` replaces dashboard polling; `/realtime` WS is the ASR transport if hal0 ever grows browser voice input.

License Apache 2.0 — compatible with hal0 (memory `hal0_license_apache2`).

[dev-web-ui]: https://github.com/lemonade-sdk/lemonade/blob/main/docs/dev/web-ui.md

---

## 6. Concurrency model (per upstream source)

Covered in deep-dive §5; appending what new sources surfaced.

**LRU per type slot.** `--max-loaded-models N` (default 1) applies independently per llm/embedding/reranking/transcription/image/tts. `-1` = unlimited. `/v1/health.max_models` reports per-type slot count. ([Multi-Model-Spec])

**Eviction granularity:** same-type only, EXCEPT NPU loads which evict any NPU model regardless of type (NPU exclusivity is the only cross-type rule).

**Nuclear evict-all — EXACT trigger (`router.cpp` verbatim):**
```cpp
bool is_file_not_found = (error_message.find("not found") != std::string::npos ||
                         error_message.find("does not exist") != std::string::npos ||
                         error_message.find("No such file") != std::string::npos);
...
if (is_file_not_found) {
    LOG(ERROR, "Router") << "File not found error, NOT evicting other models" << std::endl;
    throw std::runtime_error(error_message);
}
// Nuclear option: evict all models and retry
LOG(WARNING, "Router") << "Load failed with non-file-not-found error, evicting all models and retrying..." << std::endl;
evict_all_servers();
```
**So:** ANY load failure whose error message lacks the literal substrings `"not found"`, `"does not exist"`, `"No such file"` evicts EVERY WrappedServer of EVERY type. OOM, backend incompat, corrupted weights, HF-cache dir-not-file (spike scenario), unknown llama-server flag — all trigger evict-all. **Substring match, not error-class check** — pure stdlib string search. Brittle by design ("policy, not bug").

**Serialized loading.** One WrappedServer loads at a time; concurrent `/v1/load` calls queue. Eviction decision happens when the queued load *starts*, not on enqueue. Busy-protection: a WrappedServer servicing an inference request is non-evictable until done (EVICTION_TIMEOUT=5s).

**Multi-process topology NOT supported.** Linux uses exclusive-file-lock single-instance protection; Windows uses system-wide mutex (`dev/getting-started.md`). A second `lemond` against the same working dir blocks on the lock. Embeddable doc assumes one lemond per embedding app. **Per-tenant isolation requires per-tenant lemond binaries with distinct working dirs, `--port`, `--websocket-port`, cache dirs.**

**Upstream issue threads:**
- #1705 "Model loading und unloading policy" (open) — proposes a less-nuclear policy.
- #1630 "important / pinned models" (open) — proposes pinning against eviction.
- #1836 "Incomplete HTTP responses when concurrent chat requests…max_loaded_models=1" — serialization extends into the inference path under tight budgets.
- #1804 "OOM crash when loading multiple resident models exceeds available memory" — no upstream OOM guard; user must size `max_loaded_models` to fit RAM.

Search for `multi-tenant` → zero issues. Upstream has not engaged with that use case.

[Multi-Model-Spec]: https://github.com/lemonade-sdk/lemonade/blob/main/src/cpp/Multi-Model-Spec.md

---

## 7. Open questions for architect / API / UI

**Sys/AI architect:**
1. **Bundle path: embeddable tarball vs `.deb` vs custom container.** Embeddable + systemd unit may have materially smaller surface than `ghcr.io/hal0ai/hal0-lemond`; sidesteps the `apparmor_parser` LXC issue. → [`docs/embeddable/README.md`][embed-readme]
2. **Per-tenant isolation needs per-tenant lemonds.** Upstream has zero multi-tenant story (§6). → `src/cpp/Multi-Model-Spec.md`, `dev/getting-started.md`
3. **Custom backend binary swap.** `lemonade config set llamacpp.vulkan_bin /path` replaces bundled llama-server — the lever for spike's "b9253 slower than hal0 b1274" finding. → [`docs/embeddable/backends.md`][embed-backends]
4. **`POST /v1/install` for backend management over HTTP** (not just `lemonade backends install` CLI). install.sh can drive over REST. → `docs/api/lemonade.md`

**API architect:**
5. **`/v1/pull` with `embedding: true` / `reranking: true`** is the documented path to register GGUFs with correct type labels. Spike's "rerank/embed loaded as LLM" came from skipping this and using `extra_models_dir`. → `docs/api/lemonade.md` + `src/cpp/include/lemon/model_types.h::get_model_type_from_labels`
6. **`/v1/pull/variants` for HF-coords resolution** — could replace hal0's manual registry-curation for adding a new HF model. → `docs/api/lemonade.md`
7. **Slot save/restore for context warm-starts.** `/v1/slots/{id}?action=save|restore` — requires `lemond --slot-save-path`. → `docs/api/llamacpp.md`
8. **Free Anthropic + Ollama compat surfaces.** `/v1/messages` + `/api/chat`. → `docs/api/{anthropic,ollama}.md`
9. **Avoid `/internal/*`. Drive runtime tuneables via `lemonade config set` CLI subprocess** — same endpoint underneath but Lemonade owns the compat contract. → `dev/getting-started.md`

**UI/UX:**
10. **`/logs/stream` with `after_seq`** for the live event surface (reconnect-safe, dedup by seq). No model-load-state WS event — parse Router-tagged lines OR poll `/v1/health.all_models_loaded[].last_use`. → `docs/api/lemonade.md` + `src/cpp/server/websocket_server.cpp`
11. **Adoptable React components.** `LogsWindow`, `DownloadManager`, `MarkdownMessage` — Apache 2.0, Debian-packageable dep set. → `src/app/src/renderer/`
12. **`/realtime?model=Whisper-Tiny`** for browser voice input. OpenAI-compat WS, VAD configurable, PCM16 16kHz mono. → `docs/api/openai.md`
13. **`?show_all=true` on `/v1/models`** to surface omni models (hidden by default). → `docs/api/openai.md`
14. **UDP beacon on port 13305** broadcasts to RFC1918 for client auto-discovery. `--no-broadcast` to disable. Consider for hal0 discovery — or suppress to avoid LAN cross-talk between hal0 + lemond beacons. → `dev/getting-started.md`

---

*End survey. No design decisions taken; everything above is fact-with-source. Architect/API/UI agents own next steps.*
