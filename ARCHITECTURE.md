# hal0 architecture

This document covers hal0's internal architecture. For the user-facing
shape (install, ports, filesystem layout), see [`docs/install.md`](./docs/install.md).
For scope and roadmap, see [`PLAN.md`](./PLAN.md).

## Process model

hal0 is a single FastAPI process (`hal0-api.service`) that orchestrates
N systemd-managed inference containers (`hal0-slot@<name>.service`).
OpenWebUI runs as its own systemd unit (`hal0-openwebui.service`).

```
                   ┌─────────────────────────┐
   user/clients ─▶ │  hal0-api  (:8080)      │ ◀─ OpenWebUI (:3001)
                   │  FastAPI + dispatcher   │
                   └────────────┬────────────┘
                                │ systemctl + HTTP probes
                ┌───────────────┼───────────────┐
                ▼               ▼               ▼
        hal0-slot@primary  hal0-slot@embed   hal0-slot@stt   ...
        (llama.cpp)        (llama.cpp)       (whispercpp via lemond)
```

Each slot is independent: its own port (8081+), its own model, its own
lifecycle. The API process only owns slot **lifecycle** (load / unload /
restart) and **routing** (dispatcher → slot → response). It never holds
a model in its own memory.

## Module layout

```
src/hal0/
├── api/             # FastAPI app + routers + middleware
│   ├── routes/      # one APIRouter per concern (capabilities,
│   │                #   backends, images, events, agents lifecycle,
│   │                #   approvals, mcp, memory, …)
│   ├── agents/      # v0.3 agent surface — personas, chat-proxy,
│   │                #   restart, skills catalog, memory stats
│   ├── plugins/     # v0.3 dashboard plugin host (manifest proxy +
│   │                #   shadow-DOM SDK shim for upstream Hermes plugins)
│   ├── mcp_mount.py # mounts hal0-admin + hal0-memory MCP servers
│   └── middleware/  # error envelope, request id, log scrub
├── agents/          # bundled-agent provisioner + driver
│   ├── hermes_provision.py    # 12-phase Hermes bootstrap
│   ├── hermes/                # hal0-bundled Hermes plugins
│   │   └── plugins/memory_cognee/  # hal0-cognee MemoryProvider
│   ├── personas.py            # persona TOML store + hot-reload nudge
│   ├── manager.py             # single-pick install / uninstall
│   └── mcp_client.py          # MCP client allow-list (ADR-0013)
├── cli/agent_shim.py# /usr/local/bin/hal0-agent for hal0-agent@.service
├── slots/           # slot lifecycle (state machine, unit rendering)
├── dispatcher/      # routing, single-flight, decision logging
├── providers/       # backend abstraction (llama_server, flm, comfyui;
│                    #   slot lifecycle dispatches 100% through lemonade)
├── lemonade/        # idle driver + metrics shim + log bridge
├── capabilities/    # UX overlay grouping flat slots into capability
│                    #   cards (catalog + config + orchestrator);
│                    #   persists selections in capabilities.toml and
│                    #   reconciles slot TOMLs on every apply
├── registry/        # model registry (atomic TOML, mtime cache, GGUF
│                    #   magic-byte detect, HF-cache repo-name fallback)
├── hardware/        # probe + stats (GPU, NPU, RAM, disk)
├── upstreams/       # external LLM providers + composite hal0 upstream
├── config/          # pydantic schemas, TOML loader, migrations
├── events/          # in-process pub/sub for SSE streams
├── journal/         # lemond log ring + unified /api/journal feed
├── memory/          # CogneeWrapper + MemoryRecord
├── mcp/             # hal0-admin + hal0-memory FastMCP servers
├── omni_router/     # client-side OpenAI tool-calling loop
├── updater/         # self-update (cosign-verified, atomic swap)
├── installer/       # first-run wizard backend, hardware probe writer
├── voice/           # REMOVED in #620 — lemond serves STT/TTS natively
├── openwebui/       # companion service env file writer
└── cli/             # `hal0` Typer CLI (incl. `capabilities migrate`)
```

ADR-0012 removed `auth/` + `api/auth/` + `api/middleware/auth.py` —
hal0-api binds `0.0.0.0:8080` open; LAN trust + an upstream reverse
proxy own authentication.

The capabilities layer is a **thin overlay** on the flat slot layer,
not a replacement. Slot configs under `/etc/hal0/slots/*.toml` remain
authoritative; `capabilities.toml` records which capability picks
should be projected back onto those slot files. `hal0 capabilities
migrate` cleans up persisted selections whose (backend, model) pair
is no longer valid — primarily for FLM model-tag namespace drift.

## Key boundaries

- **Slot lifecycle is pure systemd.** The slot manager talks to
  systemctl + filesystem (env files, unit overrides) + journald. It
  doesn't import HTTP client code, doesn't know about models other than
  via the registry, and doesn't make assumptions about backends beyond
  the provider ABC.
- **Dispatcher is HTTP-only.** It does not start/stop slots. It reads
  slot status from the slot manager and routes requests. If a slot is
  offline, it returns a structured error; restarting is a separate API
  call.
- **Providers are stateless.** Each provider (`LlamaServerProvider`,
  `FLMProvider`, `ComfyUIProvider`, `LemonadeProvider`) is a class with
  `build_env()`, `start_cmd()`, `health()`, `infer()`. They don't hold
  connection state, don't manage systemd, and don't share globals.
  One provider per backend type.

  **Dispatch model (v0.2, ADR-0008):** SlotManager routes 100% through
  `LemonadeProvider`. The three non-SlotManager callers that bypass this
  are: `api/routes/v1.py` → `ComfyUIProvider.infer()` (image-gen);
  `api/routes/hardware.py` → `FLMProvider.flm_served_models()` (NPU
  footprint probe); `registry/pull.py` → `FLMProvider._probe_flm_catalog()`
  (FLM model-tag resolution).

  `FLMProvider` additionally probes `flm list -j` inside the toolbox image
  to advertise its own model-tag namespace (`share/flm/model_list.json`) —
  it does **not** run arbitrary GGUFs from the registry.

  **STT/TTS dispatch is lemond-only (#620).** The dead local
  `MoonshineProvider` and `KokoroProvider` implementation classes (the
  `hal0.voice` package that ran Moonshine/Kokoro in-process) were deleted
  in #620 — they had no live importers. `moonshine` and `kokoro` **remain
  valid capability-provider identifiers** in the config/capability layer
  (`SlotConfig.provider`, `capabilities/config.py`, `capabilities/catalog.py`,
  the backend/model classification in `api/routes`); the actual STT/TTS
  inference is served by lemond (whispercpp + kokoro recipes) or the
  corresponding toolbox image, not by an in-process hal0 provider class.
- **The registry is the only source of truth for "what models exist."**
  Atomic TOML files under `/var/lib/hal0/registry/`. mtime-cached. Slot
  configs reference model IDs from the registry; if a model is deleted,
  any slot referencing it fails to load with a structured error.

## State

Three categories of state, three filesystem locations:

| Kind      | Location              | Examples                                |
|-----------|-----------------------|------------------------------------------|
| Code      | `/usr/lib/hal0/current/` | Python package, UI dist, unit templates |
| Config    | `/etc/hal0/`          | `hal0.toml`, `slots/*.toml`, `providers.toml`, `hardware.json` |
| Runtime   | `/var/lib/hal0/`      | `models/`, `registry/`, `openwebui/`, `slots/<name>/state.json` |

Code is replaceable (every update writes a new versioned dir + flips a
symlink). Config is preserved across updates. Runtime is preserved
across updates and survives uninstall when `--keep-data` is passed.

### Slot lifecycle state machine

The authoritative enum lives in
[`hal0.slots.state.SlotState`](./src/hal0/slots/state.py); transitions are
enforced by `SlotManager._transition()` and persisted atomically to
`/var/lib/hal0/slots/<name>/state.json`.

```
offline → pulling → starting → warming → ready ←──┐
                                  │      ↑        │
                                  │      ↓        │
                                  └──→  idle ←─ serving
                                         │
                                         ↓
                                     unloading → offline
                                         ↑
                                       error
```

| State        | Meaning                                                              |
|--------------|----------------------------------------------------------------------|
| `offline`    | No systemd unit active.                                              |
| `pulling`    | Model files downloading / verifying; unit not yet started.           |
| `starting`   | `systemctl start` issued; container not yet reachable.               |
| `warming`    | Container reachable; model loading or `/v1/models` populating.       |
| `ready`      | Probe converged AND at least one model advertised — safe to route.   |
| `serving`    | At least one inference request in flight on this slot.               |
| `idle`       | Container up but cannot fulfil requests right now. Two sub-cases:    |
|              | (a) `--model ""` / empty `/v1/models` — process-up-no-model;         |
|              | (b) ready slot quiet for longer than the idle timeout.               |
| `unloading`  | Graceful `systemctl stop` in progress.                               |
| `error`      | Failed; details in `state.json.message` and journald.                |

`SlotManager.status()` runs a bidirectional reconciler against
`systemctl is-active`:

- A `ready`/`serving`/`idle` state with a dead unit → transition to
  `error`.
- An `offline`/`error` state with a live unit → run a one-shot health
  probe and adopt the slot into `ready` or `idle` (issue #30).

Routers MUST treat `idle` distinctly from `ready`: an idle slot has no
model advertised and will 4xx on inference attempts (issue #31).

## v0.3 agents subsystem

A bundled agent in v0.3 is a third-party agent runtime (Hermes-Agent
today, pi-coder in v0.4) running as a sibling systemd unit with hal0
wired in as its local AI provider. The boundary is intentionally
narrow: hal0 owns provisioning, identity, MCP wiring, and the chat-
surface proxy. Runtime is whatever the bundled upstream does natively.

### Process model

```
        ┌────────────────────┐         ┌──────────────────────┐
        │  hal0-api          │  proxy  │  hal0-agent@hermes   │
        │  :8080             │ ──────▶ │  127.0.0.1:9119      │
        │                    │  WS/REST│  (hermes dashboard)  │
        └─────────┬──────────┘         └──────────┬───────────┘
                  │                                │
       MCP /mcp/* │                                │ HTTP / config.yaml
                  ▼                                ▼
        ┌────────────────────┐         ┌──────────────────────┐
        │  hal0-memory       │ ◀────── │  composite "hal0"    │
        │  hal0-admin        │  Cognee │  upstream → /v1/*    │
        └────────────────────┘         └──────────────────────┘
```

### Surfaces

* **Provision** — `hal0 agent provision hermes` → 12-phase orchestrator
  in `src/hal0/agents/hermes_provision.py`. Idempotent + checkpointed
  via `/var/lib/hal0/state/agents/hermes/provision.json`.
* **Service** — `hal0-agent@<id>.service` (template; v0.3 instances:
  `hermes` only). Sandboxed (`NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome=yes`). Type=notify + `WatchdogSec=60`. Soft-link to
  lemonade (`Wants=`, NOT `Requires=`/`BindsTo=`) so the agent survives
  a lemonade GPU-cleanup hang.
* **Chat proxy** — `src/hal0/api/agents/chat_proxy.py`. WS upgrades
  gated by Origin allowlist + HMAC session cookie; outbound carries
  the runtime.json embed token in `Authorization: Bearer …`. Browser
  never sees the embed token.
* **Plugin host** — `src/hal0/api/plugins/`. Proxies upstream Hermes
  plugin manifests + serves plugin static assets so dashboard can
  mount them in shadow-DOM iframes.
* **Personas** — `src/hal0/agents/personas.py` owns the TOML store;
  `src/hal0/api/agents/personas.py` is the REST shim. Hot-reload nudges
  hermes via JSON-RPC; system-prompt scope swaps on the next turn.
* **Memory** — `hal0-memory` MCP wraps `src/hal0/memory/cognee_wrapper.py`.
  Per-agent private namespace = `private:<agent_id>` per ADR-0005 §3.
* **Skills catalog** — `GET /api/agents/skills` returns the static
  catalog (`HERMES_TOOL_CATALOG` + `HAL0_MCP_TOOL_CATALOG`) the
  dashboard sidebar renders. Bumps ride ADR-0018's weekly drift PRs.
* **Identity** — agent identity card published once into the `agents`
  Cognee dataset per ADR-0011. `X-hal0-Agent` is the header the proxy
  injects on every outbound hop.

### Module map

| Module                                         | Owns                                         |
|------------------------------------------------|----------------------------------------------|
| `src/hal0/agents/manager.py`                   | single-pick install / uninstall              |
| `src/hal0/agents/hermes_provision.py`          | 12-phase Hermes bootstrap orchestrator       |
| `src/hal0/agents/personas.py`                  | persona TOML store + hot-reload helper       |
| `src/hal0/agents/mcp_client.py`                | MCP server-axis + tool-axis classifier       |
| `src/hal0/agents/hermes/plugins/memory_cognee/`| hal0-cognee MemoryProvider plugin            |
| `src/hal0/api/agents/personas.py`              | `/api/agents/{id}/personas[/{pid}/activate]` |
| `src/hal0/api/agents/chat_proxy.py`            | WS proxy + session REST shim                 |
| `src/hal0/api/agents/restart.py`               | `POST /api/agents/{id}/restart`              |
| `src/hal0/api/agents/skills.py`                | `GET /api/agents/skills`                     |
| `src/hal0/api/agents/memory_stats.py`          | `GET /api/agents/{id}/memory/stats`          |
| `src/hal0/api/routes/agents.py`                | install / uninstall / activity               |
| `src/hal0/api/routes/approvals.py`             | approval inbox                               |
| `src/hal0/api/plugins/`                        | plugin host (manifest + static assets)       |
| `src/hal0/cli/agent_shim.py`                   | `/usr/local/bin/hal0-agent` (unit ExecStart) |
| `ui/src/dash/agents/`                          | v3 dashboard `<AgentView>` + Composer        |

### Upstream pin

The Hermes-Agent upstream commit hal0 v0.3 is vendored / shimmed
against lives in `pyproject.toml [tool.hal0.upstream-hermes]`. The
weekly `hermes-sdk-diff` GitHub Action (ADR-0018) opens a drift issue
when one of the tracked files changes between the pin and upstream
HEAD. Bump process: review issue, edit shim adapters if needed, run
`scripts/hermes-sdk-diff.sh --bump <sha>`, δ-harness + γ-suite, open
`chore(hermes): bump upstream pin to <short-sha>` PR.

## See also

- [`PLAN.md`](./PLAN.md) — v1 scope, modules ported from haloai, milestones
- [`docs/slots.md`](./docs/slots.md) — slot lifecycle state machine *(TODO)*
- [`docs/dispatcher.md`](./docs/dispatcher.md) — routing algorithm *(TODO)*
- [`docs/install.md`](./docs/install.md) — install flow + filesystem layout *(TODO)*
