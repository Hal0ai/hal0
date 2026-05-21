# hal0 — Plan

A polished, reliable, open-source home AI inference platform. Forked from
the existing haloai project; stripped to a tight core (slot + model
management, OpenAI-compatible API, polished dashboard, prewired chat UI,
one-line install) and re-architected around the things that make hal0
different from "a wrapper around llama-server": hardware-aware slots,
clean lifecycle, and a real reliability bar.

**Status (2026-05-21):** shipping as **v0.1.0-alpha** — Strix Halo +
AMD GPU + NVIDIA GPU Linux home installs, OpenAI-compatible inference,
bundled OpenWebUI chat. Everything in §1 "v0.1.0-alpha ships" is in
the box; v1.0 is the eventual stability/perf bar (see §1 "Path to v1.0").

---

## 1. Scope

### v0.1.0-alpha ships

- **Core inference platform**
  - OpenAI-compatible API (`/v1/chat/completions`, `/v1/embeddings`,
    `/v1/rerankings`, `/v1/audio/transcriptions`, `/v1/audio/speech`,
    `/v1/images/generations`, `/v1/models`)
  - Slot lifecycle (load / unload / restart / swap / spawn / terminate)
    on `hal0-slot@.service` template units
  - Model registry, downloads, assignment to slots
  - Dispatcher: registry-aware routing with cold-cache prefetch and
    upstream fallback
  - Provider abstraction with **five** providers in v0.1.0-alpha:
    - `llama.cpp` (Vulkan default, ROCm opt-in) — chat / embed / rerank / vision
    - `flm` (AMD NPU, optional) — chat / embed / ASR multiplex
    - `moonshine` (STT) — CPU (upstream `useful-moonshine-onnx` wheel
      ships ONNX-runtime CPU EP only), OpenAI-compatible
    - `kokoro` (TTS) — CPU/Vulkan
    - `comfyui` (ROCm) — image gen, OpenAI-compatible `/v1/images/generations`
      (shipped ahead of schedule via Team K, 2026-05-15 — `1a8a480`, `76b7f8b`)
  - External-LLM upstreams (OpenRouter, Anthropic, OpenAI, custom
    OpenAI-compatible)
  - **Capability slots overlay** (shipped 2026-05-19 — `78d749b`,
    `d6f34e1`) — UX layer over the flat slot layer. Dashboard renders
    Embed / Voice / Image cards + an NPU-backend rollup; user
    selections persist in `/etc/hal0/capabilities.toml` and
    `CapabilityOrchestrator.apply()` reconciles `slots/*.toml` against
    the selection on every call (drift fix `39adaf7`). FLM-aware
    catalog (`b90a569`) groups models first, narrows the backend
    dropdown to backends a model can actually serve, and ships
    `hal0 capabilities migrate` for stale persisted selections.
- **Auth + reverse proxy** (per [ADR-0001](docs/adr/0001-collapse-edge-auth-into-fastapi.md),
  collapsed to a single FastAPI layer in PRs #58 + #59; original dual-layer
  shipped ahead of schedule via Team J, 2026-05-15 — `ba79427`, `f62902c`)
  - All auth lives in FastAPI: Bearer tokens for programmatic clients,
    password + session cookie for browser dashboard. Caddy is a dumb TLS
    terminator + reverse proxy (no edge auth, no path allowlist).
  - **Trust posture:** a fresh install starts **open on the LAN** — no
    password set, dashboard + `/v1/*` reachable without credentials.
    Password auth is **opt-in via the dashboard wizard** (Set up password
    step → `POST /api/auth/password`); once set, writer routes require
    login (reads stay open per the wizard's choice). Programmatic clients
    use Bearer tokens minted under #29 — unchanged.
  - `--no-tls` install flag skips Caddy entirely; FastAPI binds
    `0.0.0.0:8080` for hosts behind an existing reverse proxy
    (Traefik / nginx / Cloudflare Tunnel / etc.).
  - `hal0.local` reachable on the LAN (mDNS via avahi); HTTPS via
    Caddy's internal CA or Let's Encrypt when a public hostname is set.
- **Dashboard UI** (Vue 3 + Pinia + Tailwind 4)
  - 9 views: Dashboard, Slots, Models, Hardware, Logs, Settings,
    Providers, FirstRun, plus a not-found / error shell
  - Dark mode only; mobile-responsive on read-only paths
  - SSE for slot status + log tail
  - Hardware-aware slot config form (VRAM fit warnings inline)
- **Bundled prewired OpenWebUI** at `:3001`
  - Installer pre-configures `OPENAI_API_BASE_URLS=http://127.0.0.1:8080/v1`
  - `WEBUI_AUTH=False` (LAN-only home install)
  - State at `/var/lib/hal0/openwebui/`
- **One-line installer**
  - Sensible defaults, non-interactive (`curl -fsSL hal0.dev/install.sh | bash`)
  - Pre-flight checks, hardware probe, lay down `/etc/hal0/slots/{primary,embed,stt,tts}.toml`
  - Pulls toolbox images in background
  - First-run wizard in dashboard for default-model pick
- **Self-update**
  - `hal0 update` — atomic version swap with rollback
  - Stable + nightly channels
  - Slot units keep running across API restart
- **Reliability (Tier 1 + 2 + 3 from audit)**
  - Atomic env writes, schema-validated TOML, structured error codes
  - Tightened health probes, tuned cold-boot timeouts
  - **Slot lifecycle state machine** (offline → pulling → starting → warming → ready → serving → idle → unloading)
  - **Request coalescing / single-flight** on cold-cache prefetch
  - **Config evolution / migration tooling**
  - Dispatcher decision logging with structured breadcrumbs
- **Tests**
  - Unit tests per module (pytest)
  - Slot integration tests on CI (real `hal0-slot@.service` with Qwen3 0.5B on Vulkan-CPU)
  - Playwright γ tests on every critical UI path

### Path to v1.0

v0.1.0-alpha is the shipping cut. v1.0 isn't a feature milestone — it's
a quality bar:

- **Stability** — alpha → beta when the slot lifecycle state machine
  has been hammered with concurrent load + restart fuzzing without
  hangs; beta → rc when the auth + first-run + uninstall paths have
  no known regressions across two consecutive nightly γ-suite runs.
- **Performance** — published throughput + latency baselines for the
  default loadout on each supported hardware tier (Strix Halo iGPU,
  AMD dGPU, NVIDIA dGPU, CPU). No surprises at v1.0 install.
- **Docs parity** — every documented feature actually works at the
  documented URL; the `hal0.dev/docs/` page-count matches the CLI's
  `--help` coverage.

Tags between now and v1.0: `v0.1.0-alpha.N` → `v0.1.0-beta.N` →
`v0.1.0-rc.N` → `v0.1.0`, then v0.2 deferred features as separate
minor bumps.

### v0.2 (deferred)

- Memory subsystem
- MCP support
- Benchmarks UI + Presets UI
- AUR PKGBUILD + Ubuntu PPA
- Light mode toggle

### Strip (gone for good unless re-justified)

Agents subsystem, training subsystem, clawteam (multi-agent), kanban,
voice gateway (the Hermes voice pipeline; provider slots are kept),
vault, notes, projects, skills, fixlog, reflection, RAG, ChatOps
adapters, all extension-bundled services other than OpenWebUI, vLLM,
Vibevoice, Whisper.cpp, Infinity.

---

## 2. Architecture

### Deployment model

Linux host + systemd. The hal0 API runs as `hal0-api.service`. Each slot
is an instance of the `hal0-slot@.service` template unit, parameterized
by slot name. Each slot's `ExecStart` is `docker run` against a toolbox
image (`hal0-toolbox-vulkan`, `hal0-toolbox-rocm`, or the FLM/Moonshine/
Kokoro images). OpenWebUI is its own systemd unit running its official
container.

```
systemd
├── hal0-api.service           (FastAPI on :8080, host process)
├── hal0-openwebui.service     (docker run open-webui, host :3001)
└── hal0-slot@.service         (template)
    ├── hal0-slot@primary
    ├── hal0-slot@embed
    ├── hal0-slot@stt
    └── hal0-slot@tts
```

### Filesystem layout (FHS-aligned)

```
/usr/lib/hal0/                  # code (versioned)
  current -> /usr/lib/hal0-0.1.0/   (symlink, atomic update target)
  hal0-0.1.0/
    bin/hal0                    # CLI + daemon entry (single binary)
    site-packages/hal0/         # python package
    ui/                         # built Vue dist
    systemd/                    # unit templates
    manifest.json               # toolbox image versions, etc.

/etc/hal0/                      # user-editable config (preserved on update)
  hal0.toml                     # top-level
  slots/
    primary.toml
    embed.toml
    stt.toml
    tts.toml
  providers.toml
  upstreams.toml
  hardware.json                 # written by hal0 probe; user can edit

/var/lib/hal0/                  # mutable state (preserved on update)
  models/                       # local model cache (or symlink to /mnt/...)
  registry/                     # model metadata, atomic TOML
  openwebui/                    # webui.db, uploads, vector_db, cache
  slots/<name>/                 # per-slot working dir
  hal0.previous/                # last installed version (for rollback)

/var/log/hal0/                  # optional (journald is primary)
```

`HAL0_HOME` env var overrides all the above for dev installs.

### Ports

- `8080` — hal0 API (dashboard + `/v1/*` + `/api/*`)
- `3001` — OpenWebUI (separate systemd unit)
- `8081-8099` — slot ports (assigned by `lib/config.next_free_port()`)

All slot ports bind `127.0.0.1` only; only the API and OpenWebUI bind
public interfaces.

### Naming

- Python package: `hal0`
- CLI binary: `hal0` (with subcommands)
- systemd prefix: `hal0-`
- Toolbox image org: `ghcr.io/hal0ai/`

---

## 3. Module port plan

Modules ported from `/opt/haloai/lib/` (post-audit, zero coupling to
bloat — clean ports):

| haloai source | hal0 destination | Notes |
|---|---|---|
| `lib/slots.py` (1082) | `hal0/slots/manager.py` | Refactor for state machine (Tier 3) |
| `lib/dispatcher.py` (617) | `hal0/dispatcher/router.py` | Add single-flight + decision logging |
| `lib/proxy.py` | `hal0/dispatcher/proxy.py` | Keep for now; absorbed into router post-v0.2 |
| `lib/registry.py` | `hal0/registry/` (split: store + watcher) | Atomic TOML, mtime cache |
| `lib/capacity.py` | `hal0/slots/capacity.py` | Capacity snapshot |
| `lib/slot_unit_template.py` | `hal0/slots/unit_template.py` | Rendered for template unit, not per-slot units |
| `lib/providers/base.py` | `hal0/providers/base.py` | Provider ABC |
| `lib/providers/llama_server.py` | `hal0/providers/llama_server.py` | Kept |
| `lib/providers/flm.py` | `hal0/providers/flm.py` | Kept |
| `lib/providers/comfyui.py` | DROP (v0.2 image gen) | — |
| `lib/providers/vllm.py` | DROP (v0.2 perf path) | — |
| `lib/providers/test_providers.py` | `tests/providers/` | Migrate to pytest |
| `lib/hardware.py` | `hal0/hardware/` (split: probe + stats) | Adds `probe` CLI subcommand |
| `lib/upstreams.py` (737) | `hal0/upstreams/` | Adds adaptive cold-boot timeout |
| `lib/integrations.py` | `hal0/upstreams/integrations.py` | Provider catalog |
| `lib/config.py` (420) | `hal0/config/` (split: schema + loader) | Pydantic models, validation at load |
| `lib/env_manager.py` | `hal0/config/env.py` | Atomic env file writes |
| `lib/features.py` | `hal0/config/features.py` | Feature flag store |
| `lib/updater.py` (569) | `hal0/updater/` | Self-update logic |
| `lib/healthcheck.py` (409) | `hal0/health/` | System health endpoint |
| `lib/benchmark.py` (503) | DROP (v0.2 Benchmarks UI) | — |
| `lib/paths.py` | `hal0/config/paths.py` | FHS-aware path resolution + `HAL0_HOME` |

New modules (not in haloai):

- `hal0/installer/` — first-run + dashboard wizard backend, hardware probe writer
- `hal0/cli/` — `hal0` CLI entry (Click or Typer)
- `hal0/openwebui/` — companion service config writer
- `hal0/voice/` — Moonshine + Kokoro provider integration (the inference
  pieces of `lib/voice/`, not the Hermes voice gateway)

---

## 4. API decomposition

`api.py` (3697 lines) → `hal0/api/` with one APIRouter per module:

```
hal0/api/
  __init__.py            # FastAPI app factory
  deps.py                # Depends() helpers (slot manager, registry, etc.)
  routes/
    v1.py                # /v1/* (OpenAI-compat: chat, embeddings, rerank, audio)
    slots.py             # /api/slots/*
    models.py            # /api/models/*
    hardware.py          # /api/hardware, /api/stats/*
    logs.py              # /api/logs/*
    settings.py          # /api/settings/*
    health.py            # /api/health/*, /api/metrics, /api/status, /api/features
    providers.py         # /api/providers/*, /api/upstreams/*
    config.py            # /api/config/urls
    updater.py           # /api/updates/*
    installer.py         # /api/install/* (first-run wizard endpoints)
  middleware/
    error_codes.py       # structured error envelope
    request_id.py        # X-Request-ID
    cors.py
```

Routes dropped entirely: `/api/training/*`, `/api/agents/*`, `/api/kanban/*`,
`/api/rag/*`, `/api/voice/*` (the Hermes voice gateway — providers stay),
`/api/projects/*`, `/api/skills/*`, `/api/fixlog/*`, `/api/extensions/*`,
`/api/credentials/*` (defer to v0.2 with auth), `/api/auth/*` (defer),
`/api/mcp/*` (defer to v0.2), `/api/npu/pull` (folded into `/api/models`),
`/api/benchmark/*` (defer), `/api/presets/*` (defer).

---

## 5. Reliability work (Tier 1 + 2 + 3)

Concrete items, with file:line references against the haloai source as
the starting point.

### Tier 1 — bug fixes / hardening (audit-identified)

- `lib/upstreams.py:500-520` — cold-boot health probe 2s timeout. Replace
  with adaptive policy: probe interval `(0.5s, 1s, 2s, 5s, 10s)`
  exponential backoff with jitter, total grace `180s` per slot, exposed
  via `hardware.json` per-slot override
- `lib/slots.py:551-622` — non-atomic env writes. Use
  `tempfile.NamedTemporaryFile(delete=False, dir=...)` + `os.replace()`.
  Failure leaves prior env intact
- `lib/slots.py:899-920` — FLM/vLLM health probe accepts empty
  `/v1/models`. Require non-empty plus a `/v1/chat/completions` with
  `max_tokens=1` against a sentinel message before reporting `ready`
- `lib/slots.py:59-69`, `lib/dispatcher.py:115-120, 291`,
  `lib/capacity.py:85-110` — silent exception swallows. Replace with:
  log at WARN with structured fields, return typed error to caller,
  never `return {}` on parse failure
- New: pydantic-validated TOML schema at load time. Module
  `hal0/config/schema.py` defines `SlotConfig`, `ModelConfig`,
  `ProvidersConfig`, `UpstreamsConfig`. `lib/config.load_*()` returns
  validated models. Typos in `[slot] backend = vukan` raise at startup
  with the field path
- Structured error envelope on every API response:
  ```json
  {"error": {"code": "slot.not_ready", "message": "...", "details": {...}}}
  ```
  Error code namespace: `slot.*`, `model.*`, `dispatch.*`, `config.*`, `system.*`

### Tier 2 — polish

- `lib/dispatcher.py:217-237` — cold-cache prefetch timeout. Replace
  hardcoded 4s with `dispatcher.prefetch_timeout_s` config (default 8s)
  plus per-upstream parallel cap (default 4)
- `lib/slots.py:240-346` — negative tps math. Clamp at 0, log WARN when
  histogram counter resets detected
- `lib/slots.py:316-346` — `_drm_mem()` parser. Handle multi-line
  fdinfo, return `Optional[float]` instead of 0.0 on parse failure;
  call sites distinguish "unknown" from "zero"
- Dispatcher decision logging — every routing decision emits one
  structured log line: `{request_id, model, resolution_path, upstream,
  cache_state, latency_ms}`. Goes to journald with `SYSLOG_IDENTIFIER=hal0-dispatch`

### Tier 3 — architecture refactors

- **Slot lifecycle state machine.** `hal0/slots/state.py` defines:
  ```
  offline → pulling → starting → warming → ready
                                         ↓
                                       serving ↔ idle → unloading → offline
                                         ↓
                                       error
  ```
  State transitions are atomic, persisted to `/var/lib/hal0/slots/<name>/state.json`,
  and streamable via SSE. Dashboard surfaces real transitions, not just
  systemd snapshots
- **Request coalescing / single-flight.** `hal0/dispatcher/single_flight.py` —
  in-flight map keyed by `(upstream, operation)`. Concurrent identical
  prefetches share one HTTP call. Result propagated to all waiters. On
  error, all waiters get the same error (no retry storm)
- **Config migration tooling.** `hal0 config migrate` walks `/etc/hal0/`
  applying versioned transforms. Schema version stored in `hal0.toml`'s
  `[meta] schema_version = N`. Each migration is a function in
  `hal0/config/migrations/v<N>_to_v<N+1>.py`. Tested with golden inputs

---

## 6. UI work

### v0.1.0-alpha views (Vue 3, Tailwind 4)

1. **`Dashboard.vue`** — system health rail, slot summary cards, "your hardware can run these models" tease, link to FirstRun if no models installed
2. **`Slots.vue`** — list, per-slot card with status (state machine!), inline log tail, load/unload/restart/swap actions, "create slot" → modal with hardware-aware form
3. **`Models.vue`** — registry, downloads with progress, slot assignment, deletion
4. **`Hardware.vue`** — GPU/NPU/RAM/disk detect, current allocation across slots, "your NPU is idle" hints, `hal0 probe` re-run
5. **`Logs.vue`** — API + per-slot logs, SSE tail, filters (level, slot, time range)
6. **`Settings.vue`** — config editor (`/etc/hal0/hal0.toml`), update channel, telemetry toggle, dangerous-actions section
7. **`Providers.vue`** — external upstreams (OpenRouter etc.), API key entry, test button
8. **`FirstRun.vue`** — wizard: model picker (Qwen3 4B / Llama 3.2 3B / Phi-3 Mini / custom HF URL), license accept, "start chatting" deep link to OpenWebUI

Plus shell: `Sidebar.vue`, `TopBar.vue`, `CommandPalette.vue`,
`ToastContainer.vue`, `Modal.vue`, `RestartBanner.vue`, `StatusRail.vue`.

### Polish bar

- Dark mode default, no light toggle in v1
- Mobile-responsive read paths; write paths show "use a larger screen"
- SSE-based realtime (no polling indicators); status changes appear within 1s
- Hardware-aware slot form — VRAM fit, RAM fit, "this will swap to disk" warnings
- Every async action emits a toast (success + failure paths)
- Empty states deep-link to creation flows
- Loading skeletons on every initial data fetch

### Strip

Delete entirely (source + folder):
- `views/Chat.vue`, `views/Benchmarks.vue`, `views/Memories.vue`,
  `views/Fixlog.vue`, `views/Presets.vue`, `views/rag/*`, `views/Projects.vue`,
  `views/Training.vue`, `views/training/*`, `views/Agents.vue`,
  `views/AgentSpecs.vue`, `views/Teams.vue`, `views/TeamDetail.vue`,
  `views/HALOnotes.vue`, `views/Rag.vue`, `views/Notebooks.vue`,
  `views/Tasks.vue`, `views/Extensions.vue`, `views/Metrics.vue`
- `components/clawteam/*`, `components/rag/*`

---

## 7. Installer + first-run wizard

### `install.sh` flow

1. Pre-flight: Linux, systemd present, root or sudo, ≥20GB free in `/var/lib`, ports 8080 + 3001 free, docker installed and current user in docker group (or sudo). Each check fails with a fix-it message
2. Download `hal0-vX.Y.Z-linux-x86_64.tar.gz` + `.sig` from `hal0.dev/releases/latest.json` (channel = stable | nightly)
3. Verify signature (cosign keyless against the release OIDC identity)
4. Lay down `/usr/lib/hal0-X.Y.Z/`, atomic-swap `/usr/lib/hal0/current` symlink
5. If first install: write `/etc/hal0/` defaults; if upgrade: skip
6. Hardware probe → `/etc/hal0/hardware.json` + default slot configs derived from detected NPU/GPU
7. Install + enable systemd units (`hal0-api`, `hal0-openwebui`, `hal0-slot@.service` template)
8. Pull toolbox images in background (`docker pull` for `hal0-toolbox-vulkan` etc. + `open-webui` container)
9. Start `hal0-api` + `hal0-openwebui`. **Do not** auto-start slots — that happens after model pick
10. Print URLs and "next: open the dashboard"

`install.sh` is non-interactive. All overrides via env:
`HAL0_CHANNEL`, `HAL0_AUTO_PULL`, `HAL0_INSTALL_DIR`, `HAL0_PORT`,
`HAL0_OPENWEBUI_PORT`.

### Installer overhaul (shipped 2026-05-15)

The installer got a UX pass that lands in v1:

- ASCII banner + step counter + sodium-amber spinner with last-line
  tail (`86befb1` — `lib/ui.sh` with banner / step / spinner / box)
- Preflight extracted to `lib/preflight.sh` with contextual ERR-trap
  recovery hints (`c392859`); disk check walks up to the deepest
  existing ancestor before failing (`a34293d`)
- Hardware cards rendered inline from `format_cards()`, primary slot
  pre-populated from `recommend_primary_slot()` (`c865547`, `13a0764`)
- `hal0 doctor` subcommand for re-runnable pre-flight after install
  (`c16422b`)
- Post-auth self-test that round-trips through Caddy (`f59bbf1`)
- "Wow finish": live hello, QR code, reachability summary (`f10c99d`)

### First-run wizard (dashboard route)

Triggers when `/var/lib/hal0/models/` is empty. The prototype shipped
in Phase 4 was replaced with a **linear 5-step wizard** in `d715611`:

1. **Welcome** — what hal0 does + privacy posture
2. **Hardware** — render the probed `hardware.json` cards inline
3. **Models** — curated list (Qwen3 4B / Llama 3.2 3B / Phi-3 Mini /
   custom HF URL) with size + VRAM + license per row
4. **Capabilities** — assign picked models to capability cards (Embed /
   Voice / Image / NPU rollup) — projects into `slots/*.toml` via the
   orchestrator
5. **HF token** — conditional; only shown when any selected model is
   gated. Token writes into the registry's HF credential store.

A final "Done" panel deep-links to OpenWebUI at `:3001`.

---

## 8. OpenWebUI integration

### Bundling shape

OpenWebUI runs as `hal0-openwebui.service`, a systemd unit invoking
`docker run` against `ghcr.io/open-webui/open-webui:main` (pinned per
hal0 release). State dir mounted from `/var/lib/hal0/openwebui/`.

### Prewired

Installer writes `/etc/hal0/openwebui.env`:

```
OPENAI_API_BASE_URLS=http://127.0.0.1:8080/v1
WEBUI_AUTH=False
WEBUI_NAME=hal0
ENABLE_OPENAI_API=True
ENABLE_OLLAMA_API=False
DATA_DIR=/app/backend/data        # mounted to /var/lib/hal0/openwebui
DEFAULT_LOCALE=en
```

Dashboard sidebar has a single "Chat" item with
`href="http://<host>:3001"` and `target="_blank"`. Read host from
`/api/config/urls` so it's correct after install.

---

## 9. Update mechanism

```
hal0 update [--channel=stable|nightly] [--check] [--rollback]
```

- Check: GET `hal0.dev/releases/latest.json?channel=stable` returns
  `{version, url, sig_url, manifest_url, min_data_version}`
- If `version > current`:
  - Download tarball + sig to `/var/lib/hal0/cache/`
  - Verify cosign signature against `hal0ai/hal0` GitHub OIDC identity
  - Extract to `/usr/lib/hal0-<new>/`
  - Run any pending config migrations (`hal0 config migrate` if `schema_version` advanced)
  - Atomic-swap `/usr/lib/hal0/current` symlink
  - `systemctl restart hal0-api` (slots untouched unless `--restart-slots`)
  - Old version retained at `/usr/lib/hal0-<old>/` for rollback
- Rollback: swap symlink back, restart API
- Dashboard polls `/api/updates/check` on a 24h interval, shows
  `RestartBanner.vue` when an update is available

Channels:
- `stable` — tagged releases, signed, default
- `nightly` — every `main` push (signed by CI OIDC), opt-in via channel switch in Settings or `hal0 update --channel=nightly`

---

## 10. Test strategy

### Unit (pytest)

- Every ported module gets unit tests with mocked systemd / docker / HTTP
- Aim: 70%+ line coverage on `hal0/slots/`, `hal0/dispatcher/`, `hal0/config/`
- Run on every PR

### Integration (β, CI)

- GitHub Actions Linux runner pulls a tiny model (Qwen3 0.5B GGUF) cached
  across runs
- Builds `hal0-toolbox-vulkan` image (CPU-only Vulkan baseline)
- Starts `hal0-api` + `hal0-slot@ci-test` in a netns; runs through:
  - load → verify ready
  - `/v1/chat/completions` round-trip
  - swap model → verify
  - unload → verify
  - state machine transitions visible via SSE
- ~10 min per run; required for PR merge
- ROCm + NPU paths covered by a separate release-gate `make release-test`
  run on the `hal0-test` LXC, not CI

### E2E (γ, Playwright)

Critical paths, each a separate spec:

1. **FirstRun wizard** — empty state → pick model → see slot ready → click "open chat" → OpenWebUI loads → models populated
2. **Slot lifecycle** — create slot via form → load → see state transitions in card → restart → unload → delete
3. **Model management** — download model → assign to slot → see in OpenWebUI picker → delete model → verify slot unassigned
4. **Settings** — change a config value → restart banner appears → restart → value persists
5. **Logs** — open logs page → filter to slot → tail shows new lines on slot activity
6. **Hardware page** — probe re-runs on click → hardware.json updates → slot form fit warnings reflect new state
7. **Update flow** — mock the `/api/updates/check` response → see banner → trigger update → see rollback option

Runs on PR with browser cached, ~8 min total.

### Harness (δ, local end-to-end smoke)

Beyond α/β/γ, `tests/harness/` drives every public surface a contributor
or operator touches on a real host — installer, every CLI subcommand,
slot lifecycle, uninstall — and emits one structured JSON row per
scenario. A fail flags one specific surface, not the whole pipeline.

- `bash scripts/harness.sh` — non-mutating defaults (skips prod install + TLS path)
- `HAL0_HARNESS_PROD=1 bash scripts/harness.sh` — also exercises sudo `/opt/hal0` install
- `HAL0_HARNESS_TLS=1 HAL0_HARNESS_PROD=1 bash scripts/harness.sh` — adds the
  TLS-default install row (installs Caddy + renders the Caddyfile per ADR-0001)
- `python3 scripts/harness-report.py tests/harness/reports/harness.json` — pretty-printer

Status vocabulary, scenario layout, JSON schema, and the "how to add a
row" template live in `tests/harness/README.md`. Findings get catalogued
inline at `tests/harness/FINDINGS.md` with file:line cites so a fix can
land directly.

The δ-tier has been driven on both hosts:

- **hal0-dev** (10.0.1.141, CUDA dev VM) — baseline run 2026-05-15:
  24 pass / 2 fail / 10 skip / 5 deferred across 41 rows.
- **hal0-test** LXC (10.0.1.230, Strix Halo iGPU + NPU) — run
  2026-05-16: 33 / 1 / 5 / 2 across 41 rows. Same run drove three
  additional probes against the live prod install: **62 distinct
  API route × method tuples** (9/9 auth-error contract probes pass),
  **Caddy edge audit** (3 handle blocks, public-paths bypass gap
  flagged), and **real inference** (phi3-mini on Vulkan: TTFT 59 ms,
  ~85 tok/s sustained).

Harness is **not** required for PR merge — it's a contributor-side smoke
loop on real hardware, not a CI gate. The `hal0-test` LXC matrix
(`make release-test`) remains the release-gate γ for NPU + ROCm +
Vulkan combinations CI can't cover.

---

## 11. Dev environment + migration plan

### Three boxes, three jobs

- **hal0ai** (10.0.1.141, this VM, RTX 4080): code lives here. IDE, fish shell, local toolchain. Backend logic + UI dev + installer scripting. Vulkan / CUDA dev for non-NPU paths
- **hal0-test** (new Proxmox LXC, 10.0.1.221 or similar, Strix Halo passthrough): **installer QA target**. Wiped between release candidates. Every RC runs through `wipe → install → smoke test`. Also exercises NPU + ROCm paths that CI can't
- **haloai** (10.0.1.220, existing): untouched until v1 cuts over. Continues running haloai + Hermes + your daily slots

### Cutover (post-v1.0)

When hal0 v1 is shipped + tested:

1. Back up the haloai LXC's `/opt/haloai/openwebui/webui.db` and any user-relevant slot configs
2. Run `hal0` migration tool (one-shot script): translates haloai TOMLs to hal0 schema, copies openwebui state, points slot model paths at the existing `/mnt/dock-models` / `/mnt/ai-models` shares
3. Stop all `haloai-*.service` and `hermes-*.service` units; disable
4. Run `install.sh` on the haloai LXC
5. Restore migrated data into `/etc/hal0/` and `/var/lib/hal0/`
6. Start hal0; verify
7. After a week of stability: `rm -rf /opt/haloai /root/.hermes-next`

The haloai LXC becomes the prod hal0 box. `hal0-test` remains the QA LXC.

---

## 12. Toolbox images

Rename + republish. Current source: `ghcr.io/hal0ai/amd-strix-halo-toolboxes:*-server`
(kyuz0-derived, pending PRs #86/#87 upstream).

v1 images:
- `ghcr.io/hal0ai/hal0-toolbox-vulkan:v1`
- `ghcr.io/hal0ai/hal0-toolbox-rocm:v1`
- `ghcr.io/hal0ai/hal0-toolbox-flm:v1` (NPU)
- `ghcr.io/hal0ai/hal0-toolbox-moonshine:v1`
- `ghcr.io/hal0ai/hal0-toolbox-kokoro:v1`
- `ghcr.io/hal0ai/hal0-toolbox-comfyui:v1` (image gen, ROCm — added via
  Team K)

Each tagged + signed (cosign). hal0 release manifest references specific
image digests, so an update pulls the exact images known-good for that
hal0 version. Old images retained for rollback.

**Manifest digest status (2026-05-15):** `manifest.json` now pins real
sha256 digests for vulkan, rocm, moonshine, kokoro, and comfyui
(`3449b2c chore(manifest): pin comfyui digest + refresh others`). The
`flm` digest stays `null` until that toolbox publishes successfully;
the runtime falls back to pulling by tag with a warning in that case.

**NPU live (2026-05-19/20):** the FLM toolbox image now self-contains
the XRT staging tree — `9c8f3e7` preserves `LD_LIBRARY_PATH` so
`libxrt_coreutil.so.2` resolves inside the container, and `c998106`
drops the prior host bind-mount entirely. The `FLMProvider` invokes
`flm list -j` against the image to enumerate its own model-tag
namespace (`b90a569`), so the dashboard NPU rollup advertises only
models FLM can actually serve.

**NPU dashboard pull (2026-05-21):** the dashboard can now pull FLM
tags end-to-end (PR #89). Two fixes shipped together: the catalog
probe + the new pull path both bind-mount `HAL0_FLM_MODELS_DIR` to
`/var/lib/hal0/.config/flm/models` (the toolbox image's non-root
`hal0` HOME, not `/root`), so `flm list` and `flm pull` see and
persist into the host-managed model cache. `POST /api/models/{id}/pull`
detects FLM tags via `is_flm_tag()` and routes them through
`run_flm_pull()`, which shells out to `flm pull <tag>`, parses the
`Downloading: …%` progress lines, and writes an HF-shaped registry
entry on completion. `pullable=True` now propagates to FLM rows in
the capability catalog. Verified live with `gemma3:1b` (~18 s for
1.26 GB). FLM-aware pull was the last v0.2 follow-up still listed
on the public roadmap; closed.

**Moonshine rebuild (2026-05-20):** Republished `hal0-toolbox-moonshine:v1`
at digest `sha256:a5bbb78b…` after fixing `moonshine_server.py` to pass
both `models_dir` and `model_name` to `MoonshineOnnxModel` (commit
`61c62c2`). The prior `:v1` could never load local `.ort` weights — it
treated `--model_path` as an HF identifier and 404'd against a stale HF
layout. Anyone who pulled `:v1` before this date should `docker pull`
again. `manifest.json` still pins the older sha; refresh on next release.

When kyuz0 PRs land, mirror back to upstream and re-converge.

---

## 13. CLI surface

```
hal0 status                          # system + slot summary (JSON | table)
hal0 probe                           # re-run hardware detection
hal0 slot list
hal0 slot load <name> [--model M]
hal0 slot unload <name>
hal0 slot restart <name>
hal0 slot swap <name> --model M
hal0 slot logs <name> [--follow]
hal0 model list
hal0 model pull <ref>                # HF ref or curated alias
hal0 model rm <ref>
hal0 model assign <ref> --slot S
hal0 update [--channel] [--check] [--rollback]
hal0 config show
hal0 config edit                     # $EDITOR
hal0 config migrate
hal0 config validate
hal0 uninstall [--keep-data] [--force] [--dev]
```

Implementation: Typer (typed click). Each command hits the local API on
`127.0.0.1:8080` so the CLI is a thin client. Daemon mode is `hal0
serve` (used by the systemd unit).

---

## 14. Telemetry

**Off by default**, opt-in via Settings page or `HAL0_TELEMETRY=1` env.
Anonymous: hardware class (GPU vendor, VRAM bucket), hal0 version, OS,
slot count, daily ping. No model names, no IPs, no error contents, no
config contents.

Endpoint: `https://telemetry.hal0.dev/v1/ping` (deferred until hal0.dev
exists). Code path lives in `hal0/telemetry/` from day one; the toggle
defaults to off, but the plumbing is real so v0.2 enabling it isn't a
refactor.

---

## 15. Phased milestones

Working assumption: 1 person full-time + Claude as pair. Adjust if not.

**Phase 0 — scaffold (week 1)**
- New `hal0ai:/home/halo/dev/hal0/` repo, `git init`
- pyproject.toml, package layout, FastAPI app factory, empty routers
- Vue 3 + Tailwind 4 scaffold; sidebar, dark theme, empty 9 view stubs
- CI skeleton (GitHub Actions, ruff + pytest)
- README, ARCHITECTURE.md, CONTRIBUTING.md drafted

**Phase 1 — port core (weeks 2-3)** — ✅ done 2026-05-15
- Port slot manager → `hal0/slots/manager.py` ✅
- Port dispatcher → `hal0/dispatcher/router.py` ✅
- Port providers (llama_server, flm, moonshine, kokoro) ✅
- Port registry, hardware probe, upstreams, config (with pydantic schema) ✅
- `hal0-slot@.service` template unit, env writer (atomic) ✅
- Unit tests for each module ✅ (326 passing, 2 integration tests gated on installed systemd template)
- API routers wired up; `/v1/*` and `/api/slots/*` working end-to-end ✅ (`Dispatcher.forward()` now backed by a shared httpx client with streaming + non-streaming + binary paths; `/v1/{models,chat/completions,completions,embeddings,rerankings,audio/*}` all route through dispatch→forward; lifespan creates the singleton, response hop-by-hop headers filtered)
- All three reliability tiers in (TIER1/TIER2/TIER3 markers across the ported code, lifecycle state machine + single-flight + migration framework + adaptive backoff)
- Cross-agent reconciliation: `HardwareInfo` canonicalised in `config/schema.py` (multi-GPU list, MiB integers, richer GPUInfo with compute/vulkan_capable + drm_path + nested NPUInfo); slot port range stays at PLAN §2's 8081-8099

**Phase 2 — installer + LXC QA (week 4)** — ✅ done 2026-05-15
- Provision `hal0-test` LXC with Strix Halo passthrough ✅
- Write `install.sh` (signed-release path) + uninstall ✅ (including UX overhaul: ASCII banner + step counter + spinner + preflight + hardware cards + `hal0 doctor`; `--dev` mode + warning re: systemd visibility; uninstall.sh `--dev` parity + hal0-caddy unit removal)
- Hardware probe → default slot configs ✅
- Toolbox image build + publish (`hal0-toolbox-vulkan` first) ✅ (vulkan/rocm/moonshine/kokoro/comfyui digests pinned; **flm in progress** — Team I CI run 25951155295)
- Run install on hal0-test, fix everything ✅ (harness `make harness` drives every public surface; 9 findings catalogued in `tests/harness/FINDINGS.md`, all in-scope ones fixed)

**Phase 3 — reliability Tier 1 + 2 (week 5)** — ✅ done 2026-05-15
- Atomic env writes, schema validation, structured errors ✅
- Tightened health probes, adaptive cold-boot ✅
- Dispatcher decision logging, prefetch tuning ✅
- All audit-identified bugs closed ✅

**Phase 4 — UI polish (weeks 6-7)** — ✅ done 2026-05-15
- All 9 views built out, dark mode, hardware-aware slot form ✅
- SSE plumbing for slot status + log tail ✅ (Team B verified real `EventSource` per slot — not polling — overlays the 5s `/api/status` poll)
- FirstRun wizard end-to-end with model download ✅ (Models.vue `pullProgress` bug fixed in PR #7; γ-3 spec rewritten for Wave-3 per-id pull URL in PR #12; prototype replaced with linear 5-step wizard in `d715611` — welcome / hardware / models / capabilities / HF-token)
- Empty states, loading skeletons, toasts everywhere ✅
- Hal0-web brand language: sodium amber + JBM/Geist applied

**Phase 5 — Tier 3 reliability + OpenWebUI prewire (week 8)** — ✅ done 2026-05-15
- Slot lifecycle state machine ✅ (9-state machine; PR #11 wired the last 3 — PULLING / SERVING / IDLE — closing the gap Team B's research surfaced)
- Push-driven systemd failure detector ✅ (PR #8 — per-slot watcher flips to ERROR within ~1s of unit death vs prior 180s grace)
- Request coalescing / single-flight ✅
- Config migration tooling ✅
- OpenWebUI systemd unit + env prewire ✅ (PR #4 — CI smoke test boots the real container against stub upstream)
- Auth token hot-reload ✅ (PR #9 — mint a token via CLI, next API request honors it without `systemctl restart`)
- `hal0 update` mechanism + signed releases pipeline ✅ (PR #3 — release.yml drafted, cosign verify path proven locally; HAL0_UPDATE_SKIP_COSIGN gated to pre-release builds in PR #6; `releases.hal0.dev` live via CF Pages Function middleware in `hal0ai/hal0-web`#2)

**Phase 6 — γ testing + release prep (week 9)** — ✅ mostly done 2026-05-15
- Playwright suites for the 7 critical paths ✅ (7/7 green: firstrun, hardware, logs, models, settings, slot-lifecycle, update)
- Release notes draft — pending
- `make release-test` on hal0-test (full NPU + ROCm + Vulkan matrix) — ⏳ blocked behind FLM toolbox build (Team I) + ghcr.io image visibility (task #25)
- Bug bash — superseded by the harness (`tests/harness/`); 9 findings closed-or-deferred

**Phase 7 — v1.0 cut (week 10)** — ⏳ in progress
- Tag v1.0.0 — pending blockers below
- Cut signed release artifacts — pipeline ready, awaiting first real tag-push to validate cosign keyless OIDC end-to-end
- Migrate haloai LXC → hal0 (cutover) — script ready (PR #22, `scripts/migrate-haloai.py` + 14-model curated allow-list); cutover script tested with synthetic fixtures, not yet against live haloai data
- Public launch — pending §16 decisions (launch story, contribution model)

**Total: ~10 weeks of focused work.** Phases 1–6 closed in ~3 weeks of compressed sprint work + a multi-agent sweep on 2026-05-15.

---

## 16. Deferred decisions (track explicitly)

These were intentionally not settled during the grilling and need their
own decisions before relevant milestones:

- ~~**Repo home**~~ — RESOLVED 2026-05-15: GitHub org is `Hal0ai` (capital H) for both `Hal0ai/hal0` and `Hal0ai/hal0-web` (marketing/docs)
- ~~**License**~~ — RESOLVED 2026-05-15: Apache 2.0 (LICENSE file at repo root)
- ~~**`hal0.dev` web property** scope~~ — RESOLVED 2026-05-15: marketing site + Starlight docs in `Hal0ai/hal0-web` (Astro+Starlight). Apex `hal0.dev` stays on Vercel for now; `releases.hal0.dev` subdomain serves the updater manifest from CF Pages (Function middleware in `Hal0ai/hal0-web#2`)
- ~~**`releases.hal0.dev` host**~~ — RESOLVED 2026-05-15: CF Pages from `Hal0ai/hal0-web` master; subdomain via host-conditional rewrite in `functions/_middleware.ts`. `Updater.check()` verified end-to-end against the live URL
- **Hermes shutdown timing** on the haloai LXC. Decide at cutover (phase 7)
- **Public launch story** — blog post? HN? home AI subreddit? Decide before phase 7
- **Contribution model** — accepting external PRs from day one? GH issues open? Decide before phase 7
- **`hal0.dev` apex migration off Vercel** — optional; tracker for if/when the marketing site moves to CF Pages alongside `releases.hal0.dev`

---

## 17. Risks + mitigations

| Risk | Likelihood | Mitigation / Status |
|---|---|---|
| ~~Slot lifecycle state machine refactor balloons in scope~~ | RESOLVED | All 9 states wired (PR #11); fail-watcher push-driven (PR #8) |
| ~~Cosign release pipeline + signed-artifact verification has nasty edges~~ | RESOLVED | Verify-roundtrip prototype proven locally; `release.yml` drafted; cosign 3.x compat handled (`--new-bundle-format=false`). First real tag-push still pending to validate keyless OIDC end-to-end |
| ~~Toolbox images on `ghcr.io/hal0ai/` blocked by org provisioning~~ | RESOLVED | `Hal0ai` GitHub org exists (decided 2026-05-15) |
| **GHCR image visibility — `ghcr.io/hal0ai/*` returns `unauthorized` on pull** | **High** | Task #25 — launch blocker; user must flip org-package visibility to public OR document `docker login` requirement in installer/README.md. Harness finding #8 |
| ~~FLM (XDNA2) toolbox build flakiness~~ | RESOLVED | Self-contained toolbox image lands the XRT staging tree (`c998106`) + preserves `LD_LIBRARY_PATH` (`9c8f3e7`); first end-to-end NPU model load through the slot API landed in `9f3bdae`. `manifest.json` digest pin still pending (task #15). |
| OpenWebUI internal API changes break prewire env | Low | Pin OpenWebUI container version per hal0 release; CI smoke test in PR #4 boots the real container + asserts `/api/models` round-trips |
| Strix Halo NPU driver flakiness on `hal0-test` LXC delays integration | Medium | Build CI path on Vulkan-CPU first; NPU is release-gate only, not per-commit |
| Self-update mechanism corrupts an install | Low–Medium | Atomic symlink swap + retained previous version + `hal0 update --rollback` command. Test rollback as part of release-gate. `HAL0_UPDATE_SKIP_COSIGN` gated to pre-release builds (PR #6) so v1.0.0 mandates signature verification |
| Scope creep ("just add memory while we're in there") | High | This document is the scope. Anything else is v0.2. Push back hard |

---

## 18. Definition of done — v1.0

- [ ] Fresh LXC: `curl -fsSL hal0.dev/install.sh | bash` → install completes in <5 min — *unmeasured against a real wipe*
- [x] Dashboard at `:8080`, OpenWebUI at `:3001`, both reachable
- [x] FirstRun wizard downloads a model, assigns to slot, slot reports ready — *UI flow unblocked (PR #7 + #12); pending live model-pull measurement*
- [ ] Chat works end-to-end (OpenWebUI → hal0 → llama.cpp slot → response) — *stub-proxy CI green (PR #4); real run gated on toolbox image pull (task #25)*
- [x] All 9 views render, dark mode, hardware-aware slot form works
- [x] SSE log tail works without manual refresh
- [ ] `hal0 update --channel=nightly` upgrades, `--rollback` reverts — *manifest hosting live; release.yml drafted; not exercised against a real RC tag yet*
- [ ] `systemctl restart hal0-api` doesn't kick running slots — *code path supports it; unmeasured live*
- [x] All Tier 1, 2, 3 reliability items closed
- [x] CI green (unit + slot integration), Playwright γ green on 7 paths
- [ ] Release-gate `make release-test` passes on hal0-test (NPU + ROCm + Vulkan) — *blocked behind FLM toolbox build (task #15) + ghcr.io visibility (task #25)*
- [ ] README, install docs, slot docs, model docs written — *README + installer/README + docs/release-manifest + docs/migration shipped; need final pre-launch pass*
- [x] haloai LXC migration script tested on synthetic fixtures — *14-model curated allow-list, 19 hermetic tests pass (PR #22). Live `make harness` style dry-run on real haloai data still pending*

### Outstanding launch blockers (tracked, by owner)

| # | Blocker | Owner | Note |
|---|---|---|---|
| 25 | `ghcr.io/hal0ai/*` toolbox pulls `unauthorized` | **user** | Flip org-package visibility OR document login in installer/README.md |
| 15 | FLM toolbox digest pin | **Team I** | CI run 25951155295 in flight (Rust + ffmpeg + XRT staging tree) |
| 26 | CI continue-on-error mask on FLM matrix | **Team I** | Drop once #15 stable |
| — | Real v1.0.0-rc1 tag-push + cosign keyless OIDC end-to-end | **user/release-ritual** | First-tag-push validation pending |
| — | Fresh-LXC install timing measurement | **user** | One wipe → install → stopwatch run on hal0-test |
| — | Decisions: launch story, contribution model, Hermes shutdown timing | **user** | See §16 |
