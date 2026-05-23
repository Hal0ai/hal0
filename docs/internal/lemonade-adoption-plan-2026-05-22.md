# hal0 v0.2 — Lemonade Adoption Plan (post-grill source of truth)

**Status:** Finalised after grill session 2026-05-22.
**Supersedes:** the pre-grill draft of this file + `docs/internal/lemonade-migration-plan.md`, ADR-0006, ADR-0007.
**Backs:** ADR-0006 (re-issued with v0.2-locked content), and possibly ADR-NNN for "NPU FLM trio".

This is the working contract for v0.2. Every decision below has been pressure-tested in a `/grill-with-docs` session and reconciled against `CONTEXT.md`. Open questions from the pre-grill draft are resolved unless explicitly flagged.

---

## 1. Locked decisions (do not relitigate)

1. **Path 4 — Lemonade for all iGPU + NPU modalities.** FLM/NPU via Lemonade's `flm:npu` recipe (with manual deb install). hal0 toolbox containers retired for v0.2.
2. **`kokoro:cpu`** accepted for v0.2 TTS. UI surfaces `[CPU]` chip + tooltip on the voice slot card.
3. **ONE `lemond` service per host**, port `13305` loopback-only. Cache dir at `/var/lib/hal0/lemonade/`.
4. **`llamacpp.args = "--parallel 1 --threads N"` MANDATORY** in lemond config (N = (cores − 2) / 4, min 2). Without this, two concurrent child llama-servers deadlock from CPU oversubscription (see `hal0_lemonade_threads_deadlock` memory).
5. **Model dir layout:** `/var/lib/hal0/models/<recipe>/<capability>/` (canonical, app-visible). `/mnt/ai-models/` reorganised to mirror this layout; per-leaf symlinks from canonical → mount.
6. **No default model stack at install.** `capabilities.toml` ships empty. First-run = bundle picker (4 hardware-anchored tiers + LMX vendor bundle).
7. **OmniRouter is client-side**, hal0 owns it. v0.2 ships 8 tools (5 upstream + 3 hal0-custom). Dynamic filtering: LLM only sees tools whose target slot is enabled.
8. **NPU = FLM trio** — `flm.args = "--asr 1 --embed 1"` packs chat + transcription + embedding into one FLM process. Three slots (`agent`, `stt-npu`, `embed-npu`) all back the same FLM child via direct port dispatch.
9. **v0.1.x → v0.2 = clean break.** install.sh detects v0.1.x state, prints backup/wipe instructions, exits non-zero.
10. **Slots are extensible.** Seeded catalog (6 slots: primary/embed/rerank/stt/tts/img, plus optional agent on FLM install) + user can add named slots via dashboard. Each slot has `type` (mirrors Lemonade vocab) + `device` + `model` + `enabled` + optional `default`.

---

## 2. Lemonade architecture — source tree + endpoint reference

### 2.1 Source tree (memorise; refer when modifying)

```
src/cpp/
├── CPackRPM.cmake              # RPM packaging
├── DOCKER_GUIDE.md             # Container build guide
├── Extra-Models-Dir-Spec.md    # Auto-discovery rules for --extra-models-dir
├── Multi-Model-Spec.md         # Per-type LRU + eviction + NPU exclusivity
├── postinst / postinst-full    # Debian post-install scripts
│
├── resources/                  # Self-contained config + data
│   ├── backend_versions.json
│   ├── server_models.json      # Lemonade's built-in model registry
│   └── static/                 # Server landing page
│
├── installer/                  # Windows WiX MSI installer
│
├── server/                     # The lemond C++ server
│   ├── main.cpp / server.cpp / router.cpp / model_manager.cpp
│   ├── cli_parser.cpp / recipe_options.cpp / wrapped_server.cpp
│   ├── streaming_proxy.cpp / system_info.cpp
│   ├── backends/
│   │   ├── llamacpp_server.cpp     # CPU/GPU LLM, embed, rerank
│   │   ├── fastflowlm_server.cpp   # NPU LLM via flm
│   │   ├── ryzenaiserver.cpp       # NPU hybrid (Windows-only)
│   │   ├── sd_server.cpp           # Stable Diffusion
│   │   └── whisper_server.cpp      # CPU/NPU whisper
│   └── utils/                  # http_client, json_utils, process_manager, path_utils
│
├── include/lemon/              # Public headers (mirror of server/)
└── tray/                       # System tray app (Windows/macOS/Linux)
```

Files we'll cross-reference most: `router.cpp` (eviction), `model_manager.cpp` (registry shape), `backends/fastflowlm_server.cpp` (FLM lifecycle), `backends/llamacpp_server.cpp` (where `--threads` default is set — currently absent), `Multi-Model-Spec.md` (per-type LRU contract).

### 2.2 Endpoints hal0 calls

**Public `/api/v1/*`** (stable surface; third-party safe):

| Method | Path | hal0 usage |
|---|---|---|
| GET | `/api/v1/health` | Slot HUD polling (1–5 s), capability rollup, `loaded[]` for FLM-trio backend_url discovery |
| GET | `/api/v1/models[?show_all=true]` | Catalog (`show_all=true` includes `collection.omni` bundles) |
| POST | `/api/v1/pull` | Register a model — user-imports get `user.*` prefix + `labels` for type classification |
| POST | `/api/v1/delete` | Remove a `user.*` model |
| POST | `/api/v1/load` | Load with explicit `llamacpp_backend: "rocm"` override per slot's `device` |
| POST | `/api/v1/unload` | Unload by name or all |
| POST | `/api/v1/chat/completions` | Chat dispatch (primary, agent, coder) |
| POST | `/api/v1/embeddings` | Direct embed dispatch + OmniRouter `embed_text` tool |
| POST | `/api/v1/rerank` | Direct rerank dispatch + OmniRouter `rerank_documents` tool |
| POST | `/api/v1/audio/transcriptions` | STT dispatch + OmniRouter `transcribe_audio`; for NPU stt-npu slot, dispatched directly to FLM child port |
| POST | `/api/v1/audio/speech` | TTS dispatch + OmniRouter `text_to_speech` |
| POST | `/api/v1/images/generations` | Image gen + OmniRouter `generate_image` |
| POST | `/api/v1/images/edits` | Image edit + OmniRouter `edit_image` |
| GET | `/api/v1/stats` | Per-last-request stats (TTFT, tok/s, prompt_tokens) — KV% NOT included for GPU slots (see §12.1) |
| WS | `/logs/stream` | Server log stream → hal0 journal panel |
| WS | `/realtime` | OpenAI-compat realtime audio (future v0.3) |

**Internal `/internal/*`** (loopback only, 403 from non-localhost; hal0-only):

| Method | Path | hal0 usage |
|---|---|---|
| POST | `/internal/shutdown` | Clean unload + exit → `ExecStop` in systemd unit |
| GET | `/internal/config` | Full runtime config snapshot → admin UI |
| POST | `/internal/set` | Atomic config setter |
| POST | `/internal/cleanup-cache` | Weekly cron for HF cache hygiene |

**`/internal/set` keys we'll use:**
- Immediate effect: `port`, `host`, `log_level`, `global_timeout`, `no_broadcast`, `extra_models_dir`
- Deferred (next load): `max_loaded_models`, `ctx_size`, `llamacpp_backend`, `llamacpp_args`, `sdcpp_backend`, `whispercpp_backend`, `steps`, `cfg_scale`, `width`, `height`, `flm_args`

---

## 3. Service topology

```
                       hal0-api (port 8081)               ← user-facing
                                │
                                ▼
                       hal0 capability layer              ← dispatcher
                                │
                ┌───────────────┼───────────────┐
                ▼                                ▼
       lemond (port 13305, loopback)    FLM trio child (port assigned by lemond, e.g. 14002)
       /var/lib/hal0/lemonade/                  ↑
                │                                │
       ┌────────┼────────┐                       └─── hal0 routes asr/embed-on-NPU
       ▼        ▼        ▼                            DIRECTLY to this port
  llama-server  sd-server  whisper-server
  child procs   child proc child proc
  (Lemonade-managed, ports 14000+)
```

**Decisions:**
- **One lemond per host.** Spawned by `hal0-lemonade.service` (new systemd unit).
- **Port 13305** loopback-only. hal0-api on 8081 fronts external access.
- **Cache dir** = `/var/lib/hal0/lemonade/` — holds `config.json`, `user_models.json`, runtime state.
- **`bin/` inside cache dir** = symlinks to `/opt/lemonade/bin/` (backend binaries shared, easy version bump).
- **WebSocket port** = auto-allocated; `/v1/health.websocket_port` reports the current value.

**`hal0-lemonade.service`:**

```ini
[Unit]
Description=hal0 Lemonade backend (lemond)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/lemonade/lemond /var/lib/hal0/lemonade
ExecStop=/usr/bin/curl -s -X POST http://127.0.0.1:13305/internal/shutdown
Restart=on-failure
RestartSec=5s
User=hal0
Group=hal0
LimitMEMLOCK=infinity        # required for FLM/NPU
CPUQuota=80%                  # leaves 20% for hal0-api + system

[Install]
WantedBy=multi-user.target
```

**Mandatory `/var/lib/hal0/lemonade/config.json` baseline (written by install.sh):**

```jsonc
{
  "config_version": 1,
  "host": "127.0.0.1",
  "port": 13305,
  "ctx_size": 4096,
  "max_loaded_models": 4,                        // per-type budget
  "extra_models_dir": "/var/lib/hal0/models",
  "global_timeout": 900,
  "no_broadcast": true,
  "log_level": "info",
  "rocm_channel": "stable",                      // pin, NOT nightly
  "llamacpp": {
    "args": "--parallel 1 --threads 8",           // MANDATORY — computed: (cores−2) / 4, min 2
    "backend": "rocm",
    "prefer_system": false
  },
  "flm":         { "args": "--asr 1 --embed 1" }, // trio mode
  "kokoro":      { "cpu_bin": "builtin" },
  "whispercpp":  { "backend": "vulkan" },
  "sdcpp":       { "backend": "rocm", "steps": 20, "cfg_scale": 7.0, "width": 512, "height": 512 }
}
```

---

## 4. Slot model

### 4.1 Identity, type, group

A slot has:
- **Bare-name identity** (e.g., `primary`, `embed`, `agent`). Unique across the whole `capabilities.toml`.
- **`type`** — Lemonade vocab verbatim: `llm | embedding | reranking | transcription | tts | image`. Determines per-type LRU budget + OmniRouter tool routing.
- **`device`** — `gpu-rocm | gpu-vulkan | cpu | npu`. Maps to Lemonade's recipe:backend pair (per CONTEXT.md `device` entry).
- **`model`** — Lemonade-registered model name (no prefix = registered, `user.*` = user-pulled).
- **`enabled`** — boolean.
- **`default`** — optional boolean. Exactly one `default = true` per type allowed.
- **`group`** — string used for dashboard rollup (`chat | embed | voice | img | custom`).

`capabilities.toml` shape stays at `selections.<group>.<slot_name>` for back-compat; the bare slot name is canonical in code.

### 4.2 Seeded slots

| Slot | type | group | Default in fresh install |
|---|---|---|---|
| `primary` | `llm` | chat | seeded, model empty until bundle picker or manual |
| `embed` | `embedding` | embed | seeded, empty |
| `rerank` | `reranking` | embed | seeded, empty |
| `stt` | `transcription` | voice | seeded, empty |
| `tts` | `tts` | voice | seeded, kokoro:cpu only in v0.2 |
| `img` | `image` | img | seeded, empty |
| `agent` | `llm` | chat | **only added when a bundled agent installs** (Phase 8 side-effect) |
| `stt-npu` | `transcription` | voice | added when FLM `.deb` is installed; opt-in enabled at Pro+ tier |
| `embed-npu` | `embedding` | embed | added when FLM `.deb` is installed; opt-in enabled at Pro+ tier |

### 4.3 User-added slots

`hal0 slot add NAME --type TYPE --model MODEL [--device DEVICE] [--group GROUP]`:
- Kebab-case `NAME`, must not collide with seeded names.
- `TYPE` must be one of the six Lemonade types.
- `MODEL` comes from registry (`hal0-blessed`) or HF coords (pulled to `user.*`).
- `GROUP` defaults to "custom" if not specified.

`hal0 slot remove NAME` is no-side-effect on the underlying model (model stays in registry / Lemonade catalog).

### 4.4 Routing (which slot serves a request?)

1. **Type match.** A request of type T resolves to the slot with `type = T` AND `default = true`.
2. **Label filter overlay** (OmniRouter only). If the tool requires a label (e.g., `analyze_image` needs `vision`), the default's model must have it; otherwise fall through to the first enabled slot of type T whose model has the label. Return "no compatible model" if none match.
3. **Fall-through.** If the `default = true` slot is `enabled = false`, fall through to the first enabled slot of T in TOML declaration order. Dashboard warns.
4. **Hard validation.** Two slots of the same type with `default = true` = refuse to save / refuse to load.

---

## 5. NPU + FLM trio

### 5.1 Why a trio

Strix Halo NPU = 1 AMDXDNA hardware context per host. **One** `flm serve` process at a time. But that one process accepts `--asr 1 --embed 1` flags that pack chat + ASR + embed into the same process, sharing the NPU's 8 columns.

Verified 2026-05-22: gemma3:1b + Whisper-V3-Turbo + Embedding-Gemma-300M loaded together, ~2 GB NPU memory, chat at 40 tok/s, embed at 768-dim, all concurrent.

### 5.2 hal0's wrapping

Lemonade only knows about ONE FLM model (the chat one). hal0's capability dispatcher reads `/v1/health.loaded[].backend_url` for the FLM model and:

- **Chat (agent) requests** → Lemonade's `/v1/chat/completions` (Lemonade routes to FLM child port)
- **STT requests (`stt-npu` slot enabled)** → bypass Lemonade, dispatch directly to `<FLM child port>/v1/audio/transcriptions`
- **Embed requests (`embed-npu` slot enabled)** → bypass Lemonade, dispatch directly to `<FLM child port>/v1/embeddings`

### 5.3 Hard constraints

- Only one `device = "npu", type = "llm"` slot can be `enabled = true` at a time. Switching NPU chat = swapping the FLM trio's chat model (slow but supported).
- `route_to_chat` between two NPU `llm` slots is blocked — would require mid-conversation FLM swap.
- `stt-npu` and `embed-npu` are "coresident with the NPU chat" — enabling either when no NPU chat is configured prompts the user to add one.
- Total NPU memory budget: trio loaded ≈ 2 GB; bigger NPU LLMs (e.g., qwen3.5-9b-FLM at 9 GB) replace the chat slot's model only.

### 5.4 Future-feature flag

FLM may expose additional model roles (e.g., reranking on NPU) via more flags. The trio architecture extends naturally — `flm_args` gets a new flag, hal0 adds a fourth coresident slot. We may also be able to push past 3 simultaneous roles via different flag combinations once FLM upstream supports it.

---

## 6. Model management

### 6.1 Canonical layout

**App-visible path** (single source of truth — Lemonade config `extra_models_dir` points here):

```
/var/lib/hal0/models/
├── llamacpp/
│   ├── chat/      qwen3.5-9b-q4_k_xl.gguf, qwen3.6-27b-mtp-q4_k_xl.gguf, ...
│   ├── embed/     nomic-embed-text-v1.5-q8_0.gguf
│   └── rerank/    bge-reranker-v2-m3-q4_k_m.gguf
├── flm/
│   ├── chat/      gemma3-1b/ (NPU2 model dir)
│   └── embed/     embed-gemma-300m/
├── whispercpp/
│   ├── stt/       whisper-tiny.bin, whisper-base.bin
│   └── moonshine/ moonshine-base-en/
├── kokoro/
│   └── tts/       kokoro-v1.bin, voices/
├── sd-cpp/
│   └── img/       sd-turbo.safetensors, flux-2-klein-9b.safetensors
└── collections/
    └── omni/      hal0-lite.json, hal0-default.json, hal0-pro.json, hal0-max.json, LMX-Omni-52B-Halo.json
```

**Disk-backed path**: `/mnt/ai-models/<recipe>/<capability>/...` (per-leaf symlinks to the canonical path). `/mnt/ai-models/huggingface/` stays untouched — Lemonade reads HF cache directly.

### 6.2 Namespace policy

| Namespace | Lemonade source | hal0 usage |
|---|---|---|
| Registered (no prefix) | `resources/server_models.json` | hal0-curated. Generated from `registry.toml` by `hal0 registry sync`. Requires `lemond` restart. |
| `user.*` | `user_models.json` | All on-demand pulls (HF coords or local file imports). Written via `POST /v1/pull`. No restart. |
| `extra.*` | `--extra-models-dir` auto-discovery | UNUSED. `extra_models_dir` points at the canonical models tree for compatibility; entries are all already registered. |

Dashboard model picker shows two badges: `blessed` (registered) | `pulled` (user.*). No third tier.

### 6.3 Registry sync flow

- `hal0 registry add KEY ...` → edits `registry.toml`
- `hal0 registry sync` → regenerates `/var/lib/hal0/lemonade/resources/server_models.json` from `registry.toml` AND restarts `lemond`
- `hal0 model pull HF_REPO:VARIANT` → `POST /v1/pull` with `user.*` prefix (no restart)
- `hal0 model import /path/to/file.gguf` → copy + symlink into models tree → `POST /v1/pull` (no restart)

Sync runs explicitly. Background drift detector (cheap hourly mtime check) surfaces a dashboard banner when `registry.toml` is newer than `server_models.json`.

---

## 7. OmniRouter

### 7.1 Mental model

Client-side OpenAI tool-calling loop, owned by hal0. The LLM in a chat slot is given a tool catalog; it emits `tool_calls`; hal0 dispatches each to the appropriate endpoint and folds the result back into the conversation. **Not server-side routing** — the upstream docs are clear: "You bring the LLM loop. Lemonade brings the local tools."

### 7.2 v0.2 tool set (8 tools)

| Tool | Source | Endpoint | Target slot type | Required model labels |
|---|---|---|---|---|
| `generate_image` | upstream | `/v1/images/generations` | `image` | `image` |
| `edit_image` | upstream | `/v1/images/edits` | `image` | `edit` |
| `text_to_speech` | upstream | `/v1/audio/speech` | `tts` | `tts` |
| `transcribe_audio` | upstream | `/v1/audio/transcriptions` | `transcription` | `transcription` |
| `analyze_image` | upstream | `/v1/chat/completions` | `llm` | `vision` |
| `embed_text` | **hal0** | `/v1/embeddings` | `embedding` | `embeddings` |
| `rerank_documents` | **hal0** | `/v1/rerank` | `reranking` | `reranking` |
| `route_to_chat` | **hal0** | (internal) | `llm` (target slot) | `tool-calling` (caller) |

Deferred to v0.3+: `recall_memory` (depends on Cognee MCP maturity in Phase 8).

### 7.3 Dynamic filtering

Per chat request, hal0 computes `active_tools = []` and includes a tool only if:
1. At least one enabled slot of the tool's target type exists, AND
2. (For label-gated tools) at least one of those slots has a model with the required labels.

LLMs without `tool-calling` label receive no tools. The set is recomputed when slot config changes mid-conversation. **No bundle-level whitelist/blacklist knob in v0.2** (YAGNI).

### 7.4 `route_to_chat` semantics

**One-shot delegation, persona stays unchanged.** Matches every other tool-call shape.

```jsonc
{
  "name": "route_to_chat",
  "description": "Delegate a single message to another chat slot (e.g. 'coder' for code questions, 'agent' for tool-using tasks). Use only when a specialised model would clearly do better.",
  "parameters": {
    "type": "object",
    "properties": {
      "target": { "type": "string", "description": "Slot name to delegate to. Must be an enabled chat slot." },
      "prompt": { "type": "string", "description": "Self-contained task description for the target slot." },
      "context": { "type": "string", "description": "Optional: bullet-summary of prior conversation the target should know." }
    },
    "required": ["target", "prompt"]
  },
  "requires_llm_labels": ["tool-calling"]
}
```

**Dispatch handler** (~50 LOC):
1. Validate `target` is an enabled chat slot, not self.
2. Build `[{system: target.system_prompt}, {user: args.prompt + ("\n\nContext:\n" + args.context if any)}]`.
3. Call `target.model` via `/v1/chat/completions`.
4. Return assistant content as the tool_result.

**Guardrails:**
- Nested delegation blocked at depth=1.
- NPU↔NPU delegation blocked (would require FLM swap).
- Target-not-found returns tool_result `{"error": "slot 'X' not enabled"}`; LLM apologises to user.

### 7.5 Upstream tool sync

`src/hal0/omni_router/tool_definitions.json` carries a top-of-file pin comment:

```
// Mirrored from lemonade@<commit-sha>:src/app/src/renderer/utils/toolDefinitions.json
// SHA-256 of upstream file at pin time: <hash>
// Last reviewed: 2026-MM-DD
```

CI script `scripts/check-tool-definitions.sh` fetches upstream, fails on drift. Drift triggers manual review and bump.

---

## 8. First-run UX (bundle picker)

### 8.1 The picker

On first dashboard load (capabilities.toml empty + no prior tier choice), the user sees:

```
┌─────────────────────────────────────────────────────────────────┐
│  Welcome to hal0                                                │
│  Pick a starting configuration. You can customise any slot      │
│  afterwards. Or skip to configure manually.                     │
│                                                                 │
│  ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐              │
│  │ Lite    │ │ Default  │ │ Pro     │ │ Max     │              │
│  │ 16 GB+  │ │ 32 GB+   │ │ 64 GB+  │ │ 100 GB+ │              │
│  └─────────┘ └──────────┘ └─────────┘ └─────────┘              │
│                                                                 │
│  Pre-built kits                                                 │
│  ┌────────────────────────────┐                                 │
│  │ LMX-Omni-52B-Halo (AMD)    │                                 │
│  └────────────────────────────┘                                 │
│                                                                 │
│  [ Skip — configure manually ]                                  │
└─────────────────────────────────────────────────────────────────┘
```

Hardware-anchored tiers; install reads `/proc/meminfo` and greys out tiers that don't fit (tooltip explains).

### 8.2 Bundle definitions

| Bundle | Target RAM | `chat.primary` | `chat.coder` | Aux | NPU trio |
|---|---|---|---|---|---|
| **hal0-Lite** | ≥16 GB | qwen3.5-0.8b (1.0 GB) | — | — | — (not shown) |
| **hal0-Default** | ≥32 GB | qwen3.5-9b (6.9 GB) | — | nomic-v1.5, whisper-tiny, kokoro:cpu | — (not shown) |
| **hal0-Pro** | ≥64 GB | Qwen3.6-27B-MTP (18.8 GB) | Qwen3-Coder-30B-A3B (18.6 GB, LRU) | + bge-reranker, whisper-base, sd-turbo | shown, **opt-in** |
| **hal0-Max** | ≥100 GB Strix Halo | Qwen3.6-35B-A3B-MTP (23.8 GB) | Qwen3-Coder-Next-80B-A3B (48 GB, LRU) | + whisper-large-v3-turbo, flux-2-klein-9b | shown, **opt-in** |
| **LMX-Omni-52B-Halo** *(AMD-curated)* | ≥100 GB Strix Halo | Qwen3.6-35B-A3B-MTP | — | Whisper-Large-v3-Turbo, kokoro-v1, Flux-2-Klein-9B | — |

Bundle manifests live in `/var/lib/hal0/models/collections/omni/`. Each is a `collection.omni` Lemonade manifest plus hal0-specific slot-selection metadata.

Selecting a bundle:
- Triggers model downloads in background (progress toast).
- Writes selections into `capabilities.toml`.
- Marks `default = true` per type on the seeded slot.
- For Pro and Max: ensures FLM trio slots are defined-but-disabled (user enables manually).
- Skip → blank dashboard with empty seeded slot cards (each shows "Configure" button).

### 8.3 Model size truncation

`hal0-Pro` and `hal0-Max` include LRU-evictable secondary models (coder, image). With per-type LRU budget = 2, both LLMs (primary + coder) can be co-resident but the older gets evicted when memory pressure forces it. Image model is in its own type budget so doesn't compete with LLMs.

### 8.4 Excluded from default tiers

`gpt-oss-120b` (62.8 GB) and other extreme models are NOT in any default bundle. Power users install manually via `hal0 model pull` or dashboard "Add model" form.

---

## 9. v0.1.x → v0.2 upgrade

**Clean break, no migration script.** `install.sh` detects v0.1.x state and refuses to install:

```
hal0 v0.1.x detected. v0.2 is a breaking change — slot architecture, model layout,
and runtime have all changed. The installer will not overwrite a v0.1.x state.

To preserve your configuration:
  sudo tar czf hal0-v0.1-backup-$(date +%F).tar.gz /etc/hal0 /var/lib/hal0/registry

To wipe v0.1.x and start fresh:
  sudo systemctl stop 'hal0-slot@*' hal0-api
  sudo systemctl disable 'hal0-slot@*' hal0-api
  sudo rm -rf /etc/hal0 /var/lib/hal0 /opt/hal0
  # then re-run this installer

Or read the v0.2 migration notes: https://hal0.dev/docs/v0.2-upgrade
```

Detection criterion: `/etc/hal0/slots/*.toml` exists AND `/var/lib/hal0/lemonade/config.json` is absent.

Recovery path for registry only: v0.2 ships `hal0 registry import hal0-v0.1-backup.tar.gz` (single command, restores `registry.toml`). Slot selections must be redone via the bundle picker — alpha social contract.

---

## 10. Slot architecture migration (code reshuffle)

### 10.1 v0.1.x → v0.2 code map

**Dies in v0.2:**
- `hal0-slot@.service` systemd template
- Per-toolbox `Dockerfile`s
- `/etc/hal0/slots/*.toml` (per-slot configs; replaced by `capabilities.toml`)
- `src/hal0/slots/provider.py` (Provider ABC; Lemonade IS the only provider now)
- Most of `src/hal0/slots/lifecycle.py` (no per-slot systemd; Lemonade owns process lifecycle)

**Stays in v0.2:**
- `src/hal0/slots/state.py` — slot lifecycle state machine (states + transitions). Side effects change; states don't.
- Per-slot identity + capability vocabulary
- `src/hal0/slots/ttft_samples.py` — now scrapes `/v1/stats` (Lemonade) + FLM native fields
- hal0 audit/journal log

**New in v0.2:**
- `src/hal0/lemonade/` — Lemonade HTTP client (PR-2 #137 already shipped; extended in PR-3)
- `src/hal0/lemonade/catalog_sync.py` — `registry.toml` → `server_models.json` generator
- `src/hal0/lemonade/metrics_shim.py` — `/v1/stats` + FLM native → Prometheus
- `src/hal0/lemonade/log_proxy.py` — `/logs/stream` → hal0 journal panel
- `src/hal0/omni_router/` — OmniRouter client + tool definitions
- `src/hal0/slots/manager.py` — drastically simplified: maps capability dispatch → Lemonade call

### 10.2 `BUILTIN_SLOTS` rewrite

Current: `BUILTIN_SLOTS = ("primary", "embed", "stt", "tts", "img")` — missing `rerank`, no `agent`/`stt-npu`/`embed-npu`.

v0.2: rename to `SEEDED_SLOTS`, expand:

```python
SEEDED_SLOTS = ("primary", "embed", "rerank", "stt", "tts", "img")
NPU_SEEDED_SLOTS = ("agent", "stt-npu", "embed-npu")  # only added when FLM .deb present
```

Plus runtime mutability for user-added slots.

---

## 11. Implementation sequence

19 PRs across 6 phases. Replaces the pre-grill plan.

**Phase 1 — foundation (no user-visible change):**
- **PR-2** ✅ shipped (`#137`): Lemonade client skeleton.
- **PR-3** [next]: extend client — fix `llamacpp_args` typing bug, add typed `/v1/pull`, `/v1/load`, `/v1/unload`, `/v1/health`, `/v1/stats`, all four `/internal/*` endpoints.

**Phase 2 — install + registry:**
- **PR-4**: install.sh adds Lemonade PPA + libxrt-npu2 + ffmpeg6 + boost1.83 + fftw3 + FLM .deb v0.9.42.
- **PR-5**: install.sh provisions `/opt/lemonade/` + writes `config.json` with computed `--threads N` + `flm.args = "--asr 1 --embed 1"` + writes `hal0-lemonade.service`.
- **PR-6**: `hal0 registry sync` — registry.toml → server_models.json generator.
- **PR-7**: model layout migration script — reorganises `/mnt/ai-models/{local,flm-ubuntu,moonshine_voice,voices,comfyui}` into `<recipe>/<capability>/` layout + symlink farm at `/var/lib/hal0/models/`.

**Phase 3 — slot layer rewrite:**
- **PR-8**: capability layer dispatches via Lemonade. `chat.primary`, `embed`, `rerank`, `stt`, `tts`, `img` wired.
- **PR-9**: retire toolbox containers + per-slot systemd units.
- **PR-10**: simplify `src/hal0/slots/` — delete provider.py, rewrite manager.py around `SEEDED_SLOTS` + user-added slots + default-per-type + label-filter routing.

**Phase 4 — UI + metrics:**
- **PR-11**: dashboard reads `/v1/health` for slot state. Surfaces NPU exclusivity, FLM trio coresident marker, nuclear-evict banner via `/logs/stream`.
- **PR-12**: metrics shim — `/v1/stats` for llamacpp slots, FLM native fields for NPU slots. KV% shown as "—" for GPU slots (see §12.1).
- **PR-13**: `Settings → Lemonade` admin panel — uses `/internal/config` + `/internal/set`.
- **PR-14**: journal panel — folds Lemonade `/logs/stream` into Logs tab.
- **PR-15**: `[CPU]` chip + tooltip on voice slot card; kokoro:cpu disclosure.

**Phase 5 — OmniRouter + bundles:**
- **PR-16**: `src/hal0/omni_router/` — OmniRouter client + tool definitions + dynamic filtering + `route_to_chat` dispatcher.
- **PR-17**: bundle picker UI + bundle manifests (`hal0-lite/default/pro/max.json` + `LMX-Omni-52B-Halo.json`).
- **PR-18**: dashboard chat surface — Voice + Image controls that route through OmniRouter; persona dropdown; chat-coder slot UI.

**Phase 6 — NPU + close-out:**
- **PR-19**: FLM trio dispatch — capability layer routes stt-npu/embed-npu directly to FLM child port discovered from `/v1/health`.
- **PR-20**: NPU exclusivity validation + "swap incoming" UX.
- **PR-21**: v0.1.x detection clause in install.sh + `hal0 registry import` recovery command.
- **PR-22**: ADR-0006 rewritten with this plan; docs/README/PLAN/CONTENT_BRIEF synced; v0.2 release notes.

---

## 12. Operational config + known caveats

### 12.1 KV% metric for GPU slots — accepted missing in v0.2

The Lemonade-bundled llama-server (b9253 Vulkan, b1274 ROCm) returns `null` for `n_past` / `n_prompt_tokens` / `prompt` in `/slots` responses, even during active inference. PR #124's KV%-from-`/slots` strategy fails.

**v0.2 acceptance:**
- Dashboard shows `—` for KV% on llamacpp slots.
- FLM/NPU slots get KV% native from `kv_token_occupancy_rate_percentage` in `/v1/chat/completions` responses.
- TTFT + tok/s + prompt_tokens from `/v1/stats` are unaffected and continue to work.

**v0.2.x patch path:** if upstream takes >6 weeks to populate the fields, hal0 builds its own llama-server and swaps via `lemonade config set llamacpp.{rocm_bin,vulkan_bin} /opt/hal0/bin/...`.

### 12.2 Port assignment

- `lemond`: `13305` (Lemonade default), loopback only.
- `hal0-api`: `8081` (unchanged), user-facing.
- Multi-tenant homelab override: `lemond` port configurable via `config.json`.

### 12.3 Backend version pinning

- `rocm_channel: "stable"` in `config.json`. Pin, NOT nightly.
- Lemonade build version pinned in install.sh (tarball URL with specific version).
- FLM .deb pinned: `fastflowlm_0.9.42_ubuntu24.04_amd64.deb` (bump per hal0 release).
- llama-server binaries managed by Lemonade backend install; we don't pin them per recipe in v0.2 (see §12.1 caveat).

### 12.4 Registry sync cadence

- Explicit: `hal0 registry sync` runs on user invocation, on `hal0 self-update`, on `hal0 registry add`.
- Drift detector: hourly cron compares mtimes; surfaces dashboard banner on drift.
- No silent auto-sync (restarts must be user-visible).

### 12.5 Upstream toolDefinitions sync

Pinned-with-checksum at `src/hal0/omni_router/tool_definitions.json`. CI fail-on-drift. Manual review on bumps.

### 12.6 Concurrency contract (Lemonade per-type LRU)

Per `Multi-Model-Spec.md` + spike #2 empirical:
- 6 types: `llm, embedding, reranking, transcription, tts, image`. (Spec says 5; runtime reports 6. Treat as 6.)
- Per-type LRU eviction when budget filled. Default budget = 1, hal0 sets 4 globally.
- NPU exclusivity: 1 AMDXDNA HW context per host; bypasses `max_loaded_models`. Trio mode (`--asr 1 --embed 1`) packs 3 model roles into the one context.
- Nuclear evict-all escape valve: fires only when a `/v1/load` errors AND the error does NOT substring-match "not found"/"does not exist"/"No such file".
- Active inference protection: a wrapped server cannot be evicted while it has an in-flight request.
- Loads are serialised globally.

---

## 13. References

**Upstream Lemonade docs:**
- `docs/dev/lemonade-omni.md` — OmniRouter spec, bundle naming, tool definitions
- `docs/embeddable/README.md` — customisation surfaces
- `src/cpp/Multi-Model-Spec.md` — per-type LRU, NPU exclusivity, nuclear-evict trigger
- `src/cpp/Extra-Models-Dir-Spec.md` — auto-discovery rules
- `examples/lemonade_tools.py` — minimal OmniRouter client (~150 LOC Python)
- `src/app/src/renderer/utils/toolDefinitions.json` — canonical tool schemas (mirrored with checksum)
- `lemonade-server.ai/flm_npu_linux.html` — FLM Linux install procedure

**hal0 spike + research artifacts:**
- `docs/internal/lemonade-spike-2-findings-2026-05-22.md` — Phase A/B/C results + diagnose chain
- `docs/internal/lemonade-research-2026-05-22/{researcher,architect,api,ui}.md` — 4-agent design pass
- `docs/internal/lemonade-repo-deep-dive-2026-05-22.md` — upstream source-file map

**Memories (cross-linked into CONTEXT.md):**
- `hal0_lemonade_threads_deadlock` — `--threads N` requirement
- `hal0_lemonade_flm_npu_install` — Linux FLM install procedure
- `hal0_lemonade_internals` — subsystem map
- `hal0_lemonade_v1_load_schema` — `/v1/load` body shape
- `hal0_lemonade_ws_protocol` — `/logs/stream` + `/realtime` taxonomies
- `hal0_lemonade_omni_pattern` — collection.omni manifest behaviour
- `hal0_capability_slots_system` — capability rollup architecture (PRESERVED)
- `slot_architecture` — current slot lifecycle (SIMPLIFIED)

**Glossary:** see `CONTEXT.md` for canonical terminology (slot, slot type, group, default slot, OmniRouter, FLM trio, bundle tiers, model namespace, fresh install, v0.1.x → v0.2 upgrade).
