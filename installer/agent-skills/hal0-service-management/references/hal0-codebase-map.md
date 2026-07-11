# hal0 codebase map

Where things live in the hal0 source tree. On an installed host the package
lives under `/usr/lib/hal0/current/src/hal0/`; in a dev checkout it is
`src/hal0/`. Paths below are relative to `src/hal0/`.

hal0 is a Python/FastAPI control plane. There is **no Go gateway**, **no
Lemonade / lemond daemon**, and **no `:13305` upstream** — those were retired.
Every local model runs as a **podman container, one per slot**. `hal0-api`
runs **as root** (the hardened/non-root mode was removed, ADR-0023).

## Top-level subpackages (`src/hal0/`)

| Package | Role |
|---------|------|
| `api/` | FastAPI app factory (`api/__init__.py` → `create_app`, module-level `app` for `uvicorn hal0.api:app`) + all HTTP routes. |
| `dispatcher/` | Registry-aware request router. Decides which upstream serves each OpenAI-compatible request; does **not** start/stop slots. |
| `slots/` | Slot lifecycle: `SlotManager` (load/unload/swap/restart/create/delete) + the `SlotState` state machine. |
| `providers/` | Inference backends. `ContainerProvider` is the sole slot-lifecycle backend (podman). |
| `capabilities/` | Operator-facing overlay grouping slots into capability children (embed/voice/img). |
| `slot_config/` | `SlotConfigStore` — reconciles `capabilities.toml` ↔ per-slot `slots/<name>.toml` atomically (compute `ChangeSet`, then `commit`). |
| `profiles/` | `ProfileCatalog` — runtime profile (image + flags + runtime family) lookup and mutation over `profiles.toml`. |
| `config/` | FHS path resolver (`paths.py`), config loader (`loader.py`), schema (`schema.py`), env (`env.py`), `migrations/`. |
| `registry/` | Atomic TOML model catalog ("what models exist on this host"); `ModelRegistry` is the entry point. |
| `upstreams/` | `UpstreamRegistry` of routing targets: `kind="slot"` (local container) or `kind="remote"` (OpenRouter/Anthropic/etc). |
| `memory/` | Memory subsystem. `MemoryProvider` ABC + `HindsightProvider`/`PgVectorProvider` (Hindsight-backed; cognee removed, ADR-0023). |
| `normalize/` | Virtual model-name resolution (`hal0/agent`, `hal0/utility`, `hal0/<slot>`) + thinking-flag normalization. |
| `omni_router/` | Optional server-side tool-calling loop (`OmniRouter.run_loop`) invoked when a chat request opts in. |
| `agents/` | Bundled agents (hermes + pi-coder), agent manager, MCP client, personas. |
| `mcp/` | MCP servers exposed by hal0: `admin.py` (hal0-admin) and `memory.py` (hal0-memory). |
| `cli/` | `hal0` CLI (`cli/main.py`) — slot/model/config/agent/setup/update subcommands. |
| `capabilities/`, `comfyui/`, `hardware/`, `install/`, `stacks/`, `bundles/`, `release/`, `updater/`, `journal/`, `events/`, `activity/`, `board/`, `dashboard/`, `openwebui/`, `model_meta/`, `templates/` | Supporting subsystems (image-gen, hardware probe, installer orchestration, stacks, release channel, self-updater, audit/events, kanban board, dashboard layout, chat templates). |

## API layer (`api/`)

- `api/__init__.py` — FastAPI app factory; mounts every router and constructs the `MemoryProvider`.
- `api/routes/v1.py` — the OpenAI-compatible `/v1/` surface (the file the old map called `routes/v1.py`). Key functions:
  - `chat_completions()` (L642) — `/v1/chat/completions` entry point.
  - `_rewrite_chat_slot_alias()` (L188) — rewrites `hal0/<alias>` → concrete model/slot.
  - `_ensure_backend_for_model()` (L379) — warms the backing slot via `SlotManager`.
  - `_dispatch_and_forward()` (L438) — hands off to the `Dispatcher` and forwards the upstream response.
  - `_dispatch_via_npu_trio()` (L755) — NPU FLM-trio dispatch path.
  - `list_models()` (L479) — `/v1/models`. Plus embeddings, rerank, STT, TTS, image-gen handlers.
- `api/routes/` — one router per concern: `slots.py`, `models.py`, `capabilities.py`, `profiles.py`, `providers.py`, `config.py`, `backends.py`, `stacks.py`, `memory.py`/`memory_admin.py`, `health.py`/`services_health.py`, `hardware.py`, `npu.py`, `updater.py`, `agents.py`, `installer.py`, `logs.py`, `journal.py`, `power.py`, `secrets.py`, `settings.py`, `events.py`, etc.
- `api/agents/` — agent-facing endpoints (chat proxy, budget, personas, restart, memory stats).
- `api/middleware/` — request-id, log scrubbing, error-code mapping.
- `api/mcp_mount.py` — mounts the MCP servers under the API.

There is **no** `api/routes/lemonade_proxy.py`.

## Dispatcher (`dispatcher/`)

- `dispatcher/router.py` — the heart. `Dispatcher.dispatch()` resolves a request through: (1) registry-exact binding → (2) warm-cache passthrough → (3) cold-cache prefetch (single-flight coalesced) → (4) capability/path routing (`resolve_by_capability`). The legacy `proxy.py` shim was **absorbed into `router.py`** — its slot heuristics now live in `resolve_by_capability`. There is **no** `dispatcher/forward.py` (only a `tests/dispatcher/test_forward.py` test remains).
- `dispatcher/single_flight.py` — `SingleFlightGroup` for coalescing duplicate prefetches.
- `dispatcher/npu_trio.py`, `_npu_common.py`, `npu_swap_status.py` — NPU FLM-trio routing helpers.
- `dispatcher/memory_dispatcher.py` — routing for memory requests.
- Decision logging: every routing decision emits one structured journald line with `SYSLOG_IDENTIFIER=hal0-dispatch`.

## Slots (`slots/`)

- `slots/manager.py` — `SlotManager`: every state-changing call (`load`/`unload`/`swap`/`status`/`restart`/`create`/`delete`/`update_config`) dispatches through `ContainerProvider`. Returns `Slot` snapshots, never dicts. Does **not** import from `dispatcher`.
- `slots/state.py` — `SlotState` enum + state machine: `offline → pulling → starting → warming → ready ⇄ idle ⇄ serving → unloading → offline` (+ `error`). Persisted atomically to `/var/lib/hal0/slots/<name>/state.json`, streamable via SSE.
- `slots/arbiter.py`, `capacity.py`, `argv.py`, `flag_merge.py`, `metrics.py`, `ttft_samples.py` — admission/capacity arbitration, argv assembly, flag merging, metrics.

## Providers (`providers/`)

- `providers/container.py` — `ContainerProvider`, the **sole** slot-lifecycle backend. Each slot → systemd template unit `hal0-slot@<name>.service` running `podman run … <image> --model <path> --port <n> <flags>`. Port is loopback-published; dispatcher reaches it via a `kind="remote"` upstream entry. Mounts `/mnt/ai-models` at the identical path (registry GGUFs are symlinks to absolute `/mnt/ai-models/...` targets).
- `providers/base.py` — `Provider` ABC (stateless) + `ContainerSpec` (frozen).
- `providers/llama_server.py` — llama.cpp argv/env derivation (Vulkan default, ROCm opt-in) consumed by container profiles.
- `providers/flm.py` — AMD NPU via host FLM (Strix Halo only).
- `providers/comfyui.py`, `kokoro.py`, `qwen3tts.py` — image-gen / TTS helpers (driven by `api/routes/v1.py`, not self-managed slots).
- `providers/_gpu.py` — render/video GID resolution for container `/dev/dri` access.

## Capabilities & slot config

- `capabilities/orchestrator.py` — `CapabilityOrchestrator` bridges capability children (embed.embed, embed.rerank, voice.stt, voice.tts, img.img) 1:1 to real slots; `apply()` flips slots load/swap/unload. NPU-trio modalities drive the FLM anchor instead of spawning standalone processes.
- `slot_config/__init__.py` — `SlotConfigStore`: `apply()` is compute-only (returns a `ChangeSet`), `commit()` writes atomically. Prevents `capabilities.toml` ↔ `slots/<name>.toml` drift.

## Agents (`agents/`)

- `agents/hermes_provision.py` — Hermes bootstrap state machine (15 deterministic phases, checkpointed to `/var/lib/hal0/state/agents/hermes/provision.json`). This is the provisioner.
- `agents/hermes_refresh.py` — re-render/refresh of an already-provisioned Hermes.
- `agents/hermes_templates/` — Jinja2 templates rendered into the Hermes home: `HERMES.md.j2`, `AGENTS.md.j2`, `SOUL.md.j2`, `STATE.md.j2`, `MCP-CLIENTS.md.j2`. (There is **no** `config.yaml.j2` — Hermes owns its own `config.yaml`; hal0 applies it via `hermes config set`.)
- `agents/hermes/` — the bundled Hermes driver. The hal0-memory `MemoryProvider` plugin itself lives at `installer/agents/hermes/plugins/hal0-memory/` (canonical, shipped source — copied verbatim into `$HERMES_HOME/plugins/hal0-memory/` at provision time).
- `agents/pi_coder.py`, `agents/manager.py`, `agents/persona.py`, `agents/personas.py`, `agents/mcp_client.py` — pi-coder agent, agent manager, persona definitions, MCP client.

## Memory (`memory/`)

- `memory/provider.py` — `MemoryProvider` ABC + record types.
- `memory/hindsight_provider.py` + `hindsight_client.py` — Hindsight backend (the default).
- `memory/pgvector_provider.py` — pgvector backend.
- `memory/namespace.py`, `migrate.py`, `extraction_env.py` — namespacing, migration, extraction-slot env. (cognee removed per ADR-0023.)
- Surface: `/mcp/memory` (MCP) + `/api/memory/*` (REST), wired in `api/routes/memory.py`.

## Runtime paths

| Path | Role |
|------|------|
| `/usr/lib/hal0/current/` | Installed code (symlink to versioned dir). Dev checkout uses the repo root instead. |
| `HAL0_HOME` | Env override for all roots (dev installs / tests). When set, roots become `$HAL0_HOME/{usr-lib,etc,var-lib,var-log}`. |
| `/etc/hal0/` | User-editable config (preserved on update). |
| `/etc/hal0/capabilities.toml` | Operator capability selections (edit via hal0-admin / dashboard, not by hand). |
| `/etc/hal0/slots/<name>.toml` | Per-slot config. |
| `/etc/hal0/profiles.toml` | Runtime profiles (image + flags). |
| `/etc/hal0/upstreams.toml` | Remote/slot upstream targets. |
| `/var/lib/hal0/` | Mutable runtime state (preserved on update). |
| `/var/lib/hal0/slots/<name>/state.json` | Persisted slot state. |
| `/var/lib/hal0/registry/` | Atomic TOML model catalog. |
| `/var/lib/hal0/state/agents/hermes/provision.json` | Hermes provisioning checkpoints. |
| `/var/log/hal0/` | Optional file logs (journald is primary). |
| `/mnt/ai-models/` | GGUF model store, mounted read-only into every slot container at the identical path. |

## Services / systemd units

- `hal0-api.service` — the FastAPI control plane on `:8080` (runs as **root**).
- `hal0-slot@<name>.service` — one podman container per slot (`ExecStart = podman run …`, `ExecStop = podman stop`).
- MCP servers (`hal0-admin`, `hal0-memory`) are mounted under the API, not separate daemons.

## hal0-api chat request flow

```
POST /v1/chat/completions (:8080)
  → chat_completions()            api/routes/v1.py:642
  → _rewrite_chat_slot_alias()    v1.py:188   (hal0/<alias> → model/slot)
  → [OmniRouter.run_loop if the request opts into server-side tools]
  → _ensure_backend_for_model()   v1.py:379   (SlotManager warms the slot)
  → _dispatch_and_forward()       v1.py:438
      → Dispatcher.dispatch()     dispatcher/router.py
          → registry-exact | warm passthrough | cold prefetch | resolve_by_capability
      → forward to the slot's loopback container upstream (kind="remote")
  → [_dispatch_via_npu_trio() v1.py:755 for NPU FLM-trio requests]
```

## Verifying this map

This doc is hand-maintained. When in doubt, re-derive it from the tree:

```
git ls-files src/hal0/            # current subpackages and modules
sed -n '1,30p' src/hal0/<mod>.py  # each module's docstring states its role
```
