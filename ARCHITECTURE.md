# hal0 architecture & reference

This is hal0's single authoritative internal document. It covers the
architecture, the project glossary, the bundled-agent subsystem, and how
work lands in this repo. For the user-facing shape (install, ports,
filesystem layout) see [`docs/getting-started/install.mdx`](./docs/getting-started/install.mdx).
For scope and roadmap see [`PLAN.md`](./PLAN.md). Published operator docs
live under `docs/` (Starlight `.mdx`).

> **One authoritative doc.** This file replaces the former split across
> `ARCHITECTURE.md`, `CONTEXT.md`, and `AGENTS.md`. Every standing decision
> below is explained inline: hal0 keeps no separate ADR tree, so nothing
> here cites a decision record by number — the rationale lives next to the
> statement it justifies.

## hal0 in one paragraph

hal0 is an open-source home AI inference platform: a single FastAPI
service (`hal0-api`) that orchestrates chat / embed / voice / image
models, each served by its own per-slot container runtime
(`src/hal0/providers/container.py` — one podman container per slot),
plus a bundled-agent surface where a third-party agent runtime runs as a
sibling systemd unit with hal0 wired in as its local AI provider.

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
        hal0-slot@chat   hal0-slot@npu    hal0-slot@img   ...
        (llama-server    (FLM, NPU trio:  (ComfyUI)
         container)       chat+asr+embed)
```

Each slot is independent: its own port, its own model, its own
lifecycle. Every slot runs as a **podman container** under its
`hal0-slot@<name>.service` systemd unit (the lemonade `lemond` daemon
that fronted all slots in v0.2 was removed in the container-switchover
epic, #687). The API process only owns slot **lifecycle** (load /
unload / restart) and **routing** (dispatcher → slot → response). It
never holds a model in its own memory.

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
│   ├── hermes_provision.py    # 15-phase Hermes bootstrap
│   ├── hermes/driver.py       # hal0-bundled Hermes driver (plugin sources
│   │                          #   live under installer/agents/hermes/plugins/)
│   ├── personas.py            # persona TOML store + hot-reload nudge
│   ├── manager.py             # single-pick install / uninstall
│   └── mcp_client.py          # MCP client allow-list (default-deny, server + tool axes)
├── cli/agent_shim.py# /usr/local/bin/hal0-agent for hal0-agent@.service
├── slots/           # slot lifecycle (state machine, unit rendering,
│                    #   GpuArbiter, prometheus metrics)
├── dispatcher/      # routing, single-flight, NPU-trio, decision logging
├── providers/       # backend abstraction (container, llama_server, flm,
│                    #   kokoro, comfyui); slot lifecycle dispatches 100%
│                    #   through ContainerProvider (one podman container
│                    #   per slot under hal0-slot@<name>.service)
├── capabilities/    # UX overlay grouping flat slots into capability
│                    #   cards (catalog + config + orchestrator);
│                    #   selections persist in capabilities.toml,
│                    #   reconciliation delegates to slot_config/
├── slot_config/     # SlotConfigStore (#697): capabilities.toml +
│                    #   slots/*.toml as one reconciled truth —
│                    #   compute-only apply() → ChangeSet, atomic
│                    #   commit()/revert(); single slot-TOML write path
├── registry/        # model registry (atomic TOML, mtime cache, GGUF
│                    #   magic-byte detect, HF-cache repo-name fallback)
├── hardware/        # probe + stats (GPU, NPU, RAM, disk)
├── upstreams/       # external LLM providers + composite hal0 upstream
├── config/          # pydantic schemas, TOML loader, migrations
├── events/          # in-process pub/sub for SSE streams
├── journal/         # shared time helper; /api/journal is the unified
│                    #   EventBus feed, per-slot logs read from journald
├── memory/          # Hindsight engine client/provider + MemoryRecord
├── mcp/             # hal0-admin + hal0-memory FastMCP servers
├── omni_router/     # client-side OpenAI tool-calling loop
├── updater/         # self-update (cosign-verified, atomic swap)
├── installer/       # first-run wizard backend, hardware probe writer
├── voice/           # emptied in #620 (in-process Moonshine/Kokoro
│                    #   providers deleted); STT runs in the npu FLM
│                    #   container, TTS in the tts container
├── openwebui/       # companion service env file writer
└── cli/             # `hal0` Typer CLI (incl. `capabilities migrate`)
```

The dedicated auth packages (`auth/`, `api/auth/`,
`api/middleware/auth.py`) were removed: hal0-api binds `0.0.0.0:8080`
open, and LAN trust plus an upstream reverse proxy own authentication.

The capabilities layer remains a **thin overlay** on the flat slot
layer as a UX surface, not a replacement. Slot configs under
`/etc/hal0/slots/*.toml` remain authoritative; `capabilities.toml`
records which capability picks should be projected back onto those
slot files. Since #697 the projection itself is no longer an in-place
rewrite inside the orchestrator: `hal0.slot_config.SlotConfigStore` is
the deep module that owns both files as one reconciled truth. Its
`apply(selection)` is compute-only and returns a
`ChangeSet{before, after}`; `commit(cs)` writes both files atomically
(rolling back to `before` on partial failure) and `revert(cs)` restores
the prior state. The testable invariant: after `commit` disk equals
`cs.after`, after `revert` disk equals `cs.before`, and a failed
mid-apply leaves disk at `before` — the two files can never be left
half-reconciled. `write_slot_toml()` in the same module is the single
byte-level write path every `slots/*.toml` writer routes through.
`hal0 capabilities migrate` still cleans up persisted selections whose
(backend, model) pair is no longer valid — primarily for FLM model-tag
namespace drift.

## Key boundaries

- **Slot lifecycle is pure systemd + podman.** `SlotManager` talks to
  systemctl + the filesystem (state.json, unit files) + journald, and
  dispatches every state-changing call through `ContainerProvider`,
  which renders and `systemctl restart`s a self-contained
  `hal0-slot@<name>.service` unit whose `ExecStart` is one
  `podman run … <image> --model <path> --port <n> <flags>`. It doesn't
  import the dispatcher, doesn't know about models other than via the
  registry, and doesn't make assumptions about backends beyond the
  provider ABC.
- **Dispatcher is HTTP-only.** It does not start/stop slots. It reads
  slot status from the slot manager and routes requests. If a slot is
  offline, it returns a structured error; restarting is a separate API
  call.
- **Providers are stateless.** Each provider (`ContainerProvider`,
  `LlamaServerProvider`, `FLMProvider`, `KokoroProvider`,
  `ComfyUIProvider`) is a class with `build_env()`, `start_cmd()`,
  `health()`, `infer()` (the container path also adds
  `load_sync`/`unload_sync`/`status`/`container_spec`). They don't hold
  connection state, don't share globals, and one instance is shared
  process-wide. One provider per backend type.

  **Dispatch model (container runtime, #652/#687):** `SlotManager`
  routes every slot's lifecycle 100% through `ContainerProvider` — one
  podman container per slot. GPU/llama-server slots render via the
  flag-bundle path; `_spec_provider_for` hands NPU (FLM), TTS (Kokoro),
  and image (ComfyUI) slots to their own provider, which builds a
  `ContainerSpec` rendered into the same unit shape. The profile
  (`/etc/hal0/profiles.toml`, seeded from `config/schema.SEED_PROFILES`)
  supplies the container image + bench-tuned flags; the slot TOML
  supplies model path, `context_size`, and port.

  Request dispatch (separate from lifecycle) flows through hal0-api's
  `/v1` surface: the `Dispatcher` (registry binding → container-remote
  preemption → warm-cache passthrough → prior heuristics), the
  `NpuTrioRouter` (static-port STT/embed forwarding to the npu
  container), and the `GpuArbiter` (exclusive llm⇄img GPU groups). The
  composite `hal0` upstream exists only to aggregate `/v1/models`; it is
  never a forward target.

  `FLMProvider` additionally probes `flm list -j` inside the toolbox image
  to advertise its own model-tag namespace (`share/flm/model_list.json`) —
  it does **not** run arbitrary GGUFs from the registry.

  **STT/TTS run in containers, not in-process (#620).** The removed local
  `MoonshineProvider` / in-process `Kokoro` implementation (the
  `hal0.voice` package that ran Moonshine/Kokoro in the API process) was
  deleted in #620 — it had no live importers. `moonshine` and `kokoro`
  **remain valid capability-provider identifiers** in the
  config/capability layer (`SlotConfig.provider`, `capabilities/config.py`,
  `capabilities/catalog.py`, the backend/model classification in
  `api/routes`); the actual inference is served by the FLM NPU container
  (`--asr` role of the npu trio) for STT and the `tts` container
  for TTS, not by an in-process hal0 provider class.
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

Operator-authored configuration stays in TOML; machine-owned runtime
state and model metadata move to SQLite. These are distinct concepts and
must not be merged.

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

## Bundled agents (v0.3)

A bundled agent in v0.3 is a third-party agent runtime running as a
sibling systemd unit with hal0 wired in as its local AI provider. Two
agents ship as first-class bundled agents —
`BUNDLED_AGENTS = ("pi-coder", "hermes")` in
`src/hal0/agents/manager.py`, single-pick at install; `hermes` is the
default integration. The boundary is intentionally narrow: hal0 owns
provisioning, identity, MCP wiring, and the chat-surface proxy. Runtime
is whatever the bundled upstream does natively.

Standing decision — hal0 does **not** build its own agent runtime. It
bundles third-party runtimes and runs them safely as sandboxed sibling
units. (A previous haloai-style first-party runtime was stripped; the
bundled agents are not a revival of it.)

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
        │  hal0-admin        │ Hindsight│  upstream → /v1/*    │
        └────────────────────┘         └──────────────────────┘
```

### Install & lifecycle

```
sudo hal0 agent provision hermes         # one-shot 15-phase bootstrap
sudo systemctl status hal0-agent@hermes  # unit health
hal0 agent personas                      # list personas (TOML store)
hal0 agent personas activate coder       # swap active persona
```

The provisioner is the authoritative install path for a hal0-bundled
agent. It renders `config.yaml`, `hermes.env`, persona TOMLs, the MCP
server entries (`hal0-admin`, `hal0-memory`), the system-prompt addendum,
and the composite `hal0` upstream config — all idempotently.

### Surfaces

* **Provision** — `hal0 agent provision hermes` → the 15-phase
  orchestrator in `src/hal0/agents/hermes_provision.py`
  (preflight → install → env_probe → home_init → install_artifacts →
  persona_seed → config_write → mcp_wire → context_link →
  namespace_register → model_automap → voice_wire →
  gateway_secrets_wire → smoke_tests → self_report). Idempotent +
  checkpointed via `/var/lib/hal0/state/agents/hermes/provision.json`.
* **Service** — `hal0-agent@<id>.service` (template; v0.3 instances:
  `hermes` only). Sandboxed (`NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome=yes`). Type=notify + `WatchdogSec=60`. The agent reaches
  inference over HTTP at `HAL0_INFERENCE_BASE=http://127.0.0.1:8080`
  (hal0-api, which fronts the per-slot inference containers) — a plain
  endpoint hint, not a hard systemd dependency, so the agent survives a
  slot container restart or GPU-cleanup hang.
* **Chat proxy** — `src/hal0/api/agents/chat_proxy.py`. WS upgrades
  gated by Origin allowlist + HMAC session cookie; outbound carries
  the runtime.json embed token in `Authorization: Bearer …`. Browser
  never sees the embed token.
* **Plugin host** — `src/hal0/api/plugins/`. Proxies upstream Hermes
  plugin manifests + serves plugin static assets so the dashboard can
  mount them in shadow-DOM iframes.
* **Personas** — `src/hal0/agents/personas.py` owns the TOML store;
  `src/hal0/api/agents/personas.py` is the REST shim. Hot-reload nudges
  hermes via JSON-RPC; system-prompt scope swaps on the next turn. The
  provisioner seeds three personas: `hermes` (general), `coder`, and
  `hal0-brain` (the dashboard agent-chat steward — own memory namespace,
  targets the `brain` slot via `hal0/brain` by default). The active
  persona is the contents of `active.txt`; switching swaps the
  system-prompt scope on the next turn without restarting the process.
* **Memory** — `hal0-memory` MCP wraps the engine-neutral
  `MemoryProvider` (`src/hal0/memory/provider.py`), whose default engine
  is Hindsight (`src/hal0/memory/hindsight_provider.py`); the cognee
  wrapper was removed (Hindsight is the only memory engine). Per-agent
  private namespace = `private:<agent_id>`.
* **Approvals & audit** — every gated tool invocation (`model_pull`,
  `slot_delete`, `config_write`, `memory_delete` >1) goes through the
  approval inbox at `/api/agent/approvals`; the SidebarAgentBlock's
  approvals bell and the `hal0 agent approvals` CLI share the same
  lifespan-scoped `ApprovalQueue`. Audit rows flow through journald via
  the `hal0.mcp.audit` logger; `GET /api/agents/{name}/activity` reads
  them back for the dashboard Activity tab.
* **Skills catalog** — `GET /api/agents/skills` returns the static
  catalog (`HERMES_TOOL_CATALOG` + `HAL0_MCP_TOOL_CATALOG`) the
  dashboard sidebar renders. Bumps ride the weekly `hermes-sdk-diff`
  drift PRs (see [Upstream pin](#upstream-pin)).
* **Identity** — an agent identity card is published once into the
  `agents` memory namespace during first-run bootstrap and cleaned on
  uninstall. `X-hal0-Agent` is the header the proxy injects on every
  outbound hop.

### Module map

| Module                                         | Owns                                         |
|------------------------------------------------|----------------------------------------------|
| `src/hal0/agents/manager.py`                   | single-pick install / uninstall              |
| `src/hal0/agents/hermes_provision.py`          | 15-phase Hermes bootstrap orchestrator       |
| `src/hal0/agents/personas.py`                  | persona TOML store + hot-reload helper       |
| `src/hal0/agents/mcp_client.py`                | MCP server-axis + tool-axis classifier       |
| `installer/agents/hermes/plugins/hal0-memory/`| hal0-hindsight MemoryProvider plugin (canonical source) |
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

### Standing decisions

These decisions are settled and explained here inline (hal0 keeps no ADR
tree):

* **Agent bundling** — hal0 bundles third-party runtimes
  (`pi-coder`, `hermes`) and runs them as sandboxed sibling units; it
  does not build its own runtime.
* **MCP allow-list** — the MCP client is default-deny on two axes
  (server-axis + tool-axis); a persona's `tools_allowed` opens the gate.
* **Upstream pin** — the bundled Hermes runtime is pinned with periodic
  drift detection rather than tracking upstream `main`.
* **LLM roles** — the canonical chat roles are `agent` (default anchor)
  and `utility`; the older `chat` / `primary` roles are retired.
* **Memory engine** — Hindsight only (Cognee removed).
* **Auth** — hal0-api runs as `hal0` and binds open on the LAN; route
  auth was removed except the chat-proxy HMAC seam. LAN trust plus an
  upstream reverse proxy own authentication.

### Upstream pin

The Hermes-Agent upstream commit hal0 v0.3 is vendored / shimmed
against lives in `pyproject.toml [tool.hal0.upstream-hermes]`. The
weekly `hermes-sdk-diff` GitHub Action opens a drift issue when one of
the tracked files changes between the pin and upstream HEAD. Bump
process: review issue, edit shim adapters if needed, run
`scripts/hermes-sdk-diff.sh --bump <sha>`, δ-harness + γ-suite, open
`chore(hermes): bump upstream pin to <short-sha>` PR. The pin is bumped
only through this human-gated path — never auto-merged.

## Glossary

Project terminology: the canonical names plus short disambiguators. Not a
spec. Update inline as new terms get resolved during design sessions or
PR reviews. Decisions that still matter are explained where the term is
defined.

### agent

Two distinct senses in this repo. Disambiguate by context.

1. **internal dev sense** — a Claude teammate (the multi-agent fan-out
   pattern used when building hal0; see [`CONTRIBUTING.md`](./CONTRIBUTING.md)).
   Never user-facing. About *how we build hal0*, not what hal0 is.
2. **product sense** — a bundled agent app (`pi-coder` or `Hermes-Agent`).
   User-facing. About *what users do with hal0*.

When in doubt, ask which sense applies before writing the word.

### Agents subsystem (stripped first-party runtime)

**Stripped.** Previously a haloai-style first-party agent runtime
(PLAN.md §1 Strip listed it as gone). The product-sense bundled agents
(above) are NOT a revival — they're third-party bundled apps with a
fundamentally different architecture. Do not reintroduce the first-party
runtime.

### bundled agent

A third-party agent application installed alongside hal0, prewired to use
hal0 as its local AI provider and to consume hal0's MCP servers. Supports
`pi-coder` (CLI shape) and `Hermes-Agent` (service shape). Single-pick at
install.

### Hindsight

The memory engine (client at `src/hal0/memory/hindsight_client.py`,
provider at `hindsight_provider.py`). Replaced the earlier Cognee engine
— the Cognee wrapper was removed in c4c77a80. Powers `/mcp/memory` and
the `/api/memory/*` REST surface. (Cognee is dead vocabulary in this
repo; do not reintroduce it.)

### namespace (memory)

Hindsight's per-client scope primitive. hal0's namespace rule: default
`shared` for all clients; per-client `--private` toggle promotes that
client's writes to `private:<client_id>`. Private-mode reads expand to
`[shared, private:<client_id>]` so a caller sees their own scoped items
alongside the shared bucket. Resolution lives in
`src/hal0/memory/namespace.py`, shared by the MCP + REST surfaces.
Multi-user hardening of the rule is a future concern.

### MCP server (hal0-exposed)

hal0 exposes two MCP servers:
- `/mcp/admin` — wraps existing `/api/*` routes (slot/model/hardware/log
  admin). Tool catalog rule: ships iff it maps to an existing route.
- `/mcp/memory` — wraps the Hindsight memory surface.

Both reachable by any MCP-speaking client: bundled agents, Claude Code,
future RAG services. Auth via the API's existing Bearer token.

### memory

Two distinct memory surfaces coexist on a hal0 box. They serve different
scopes — don't displace one with the other.

- **pi-memory-md** — project-scoped markdown files in the repo. Pi-coder's
  native extension, kept in place by the hal0 pi-coder shim. NOT touched
  by hal0's memory MCP.
- **hal0 memory MCP** — cross-session, cross-agent, cross-app. Backed by
  Hindsight. Default namespace `shared`.

### pi-coder

Bundled agent option (CLI shape). Upstream: `earendil-works/pi` (formerly
`badlogic/pi-mono`), hard-forked at `Hal0ai/pi-mono`. Track-latest
upstream (NOT pinned). hal0's shim (`hal0.agents.pi_coder`) wires it up on
install:

- **hal0 theme** — deployed + set as default.
- **Model provider** — the `hal0-provider` extension auto-discovers active
  hal0 slots from `/v1/models` and registers them as pi's default provider
  (`hal0/agent`). No remote-provider config needed out of the box.
- **Memory** — the `hal0-memory` extension talks to hal0-api's
  `/api/memory/*` REST surface directly, with dual private/shared banks
  (see "memory" above). Supersedes routing memory through
  `pi-mcp-adapter`; only `hal0-admin` still rides that generic MCP proxy.
- **Delegation** — best-effort `pi install npm:pi-subagents` for
  scout/planner/worker/reviewer/oracle-style child agents. Subagents
  inherit the hal0 default model unless overridden.

`pi-memory-md` (upstream's own project-scoped markdown memory) is left in
place — different scope from hal0's memory surface.

### Hermes-Agent

Bundled agent option (service shape). User-owned upstream — grows native
hal0-awareness on the Hermes side rather than via a hal0-owned shim. Runs
as `hal0-agent-hermes.service`. Sidebar link-out OWUI-style in dashboard.

### skills

Overloaded THREE ways. Default to sense (3) in hal0 product context.

1. **Claude Code skills** — the markdown + YAML-frontmatter format Claude
   Code itself uses (e.g. `~/.claude/skills/`). Internal tooling for dev
   sessions; not a hal0 product feature.
2. **stripped haloai skills subsystem** — historical, gone (PLAN.md §1
   Strip section). Do not reintroduce.
3. **hal0 platform skills** = MCP tools exposed by the admin MCP server.
   An agent calling `/mcp/admin` sees `slot_list`, `model_swap`, etc. as
   its "skills." This is the sense used in hal0 product copy.

(Possible future stretch: agent-side skills in the `openclaw-skills`
style — `SKILL.md` + YAML frontmatter + self-improving loop. If we ever
ship that, it's a separate noun and gets its own gloss entry.)

### device

Per-slot hardware preference. Field on `SlotConfig` replacing v0.1.x's
overloaded `backend` field (which mixed providers and backends). Enum:
`gpu-rocm` | `gpu-vulkan` | `cpu` | `npu`. Default for new installs:
`gpu-rocm`. `model_meta.device_to_backend(device)` maps it to a
`(recipe, llamacpp)` pair, and the device selects the container profile
(`config/schema.DEVICE_DEFAULT_PROFILES`): `gpu-rocm` → `rocm` (ROCm-FP4
llama-server image), `gpu-vulkan` → `vulkan`, `cpu` → `tts`, `npu` →
`flm` (the FLM NPU image).

Note: spike data showed `gpu-vulkan` is much slower than `gpu-rocm` for
Strix Halo — the user-facing UI should advise `gpu-rocm` as the
recommended default and label `gpu-vulkan` as fallback.

### slot

A named, configured serving target — e.g. `agent`, `embed`, `stt`, `tts`,
`img`, plus the `utility` helper slot and any user-defined ones. NOT a
memory or RAG primitive — slots serve inference, memory lives in
`/mcp/memory`.

**Current runtime (container-per-slot, #652/#687).** A slot is one
`hal0-slot@<name>.service` systemd unit whose `ExecStart` is a single
`podman run` of the slot's container (llama-server, FLM, Kokoro, or
ComfyUI). `SlotManager` dispatches lifecycle (load/unload/swap/status)
through `ContainerProvider` (`src/hal0/providers/container.py`); the
profile (`/etc/hal0/profiles.toml`) supplies the image + bench-tuned
flags, the slot TOML supplies model/port/`context_size`. Slot state
(`ready`/`idle`/`serving`) comes from the state machine in
`hal0.slots.state`, reconciled against `systemctl is-active` + a `/health`
probe on the slot's loopback port. Slot identity persists in
`capabilities.toml`; the runtime layer is the per-slot container.

(Historical: slots once mapped logically onto a single shared `lemond`
(Lemonade) process with no per-slot systemd unit. The Lemonade runtime
was removed in the container-switchover epic, #687; the per-slot template
unit returned, this time wrapping a podman container.)

#### slot inventory

A slot has exactly ONE `type` and ONE loaded model. **Slot identity is a
bare name** (e.g., `agent`, `utility`, `embed`, `rerank`) — unique across
the whole `capabilities.toml`. The `group` is a field on the slot's
selection, used purely for dashboard rollup. `embed` and `rerank` are two
separate slots filed under the `embed` group; same for `stt`/`tts` under
`voice`. The canonical chat roles are `agent` (default anchor) and
`utility` (seeded helper); the pre-existing `chat`/`primary` names are
retired (a lingering operator-custom `chat` slot is reachable by its own
name via generalized `hal0/<slot>` resolution, not an alias). The only
surviving back-compat alias in `SlotManager.SLOT_ALIASES` is
`agent-hermes` → `agent`.

Migration note: the v0.1.x `capabilities.toml` shape
`selections.<group>.<slot>` carries the group implicitly in the TOML
path. The same TOML path is kept for back-compat but the canonical
identity in code is the bare slot name.

The seeded set is `SlotManager.SEEDED_SLOTS = (utility, embed, rerank,
stt, tts, img, vision, agent)`; the NPU shadow slots `(stt-npu,
embed-npu)` (`NPU_SEEDED_SLOTS`) seed only when the FastFlowLM `.deb` is
installed.

| Slot | type | UI group | Default at install |
|---|---|---|---|
| `agent` | `llm` | chat | seeded, empty (default chat anchor — GPU MoE) |
| `utility` | `llm` | chat | seeded, empty (seeded helper role) |
| `embed` | `embedding` | embed | seeded, empty |
| `rerank` | `reranking` | embed | seeded, empty |
| `stt` | `transcription` | voice | seeded, empty |
| `tts` | `tts` | voice | seeded, empty (tts container) |
| `img` | `image` | img | seeded, empty (ComfyUI container) |
| `vision` | `llm` | chat | seeded, empty |

The seeded slots are a **catalog**, not a stack — every selection is empty
(`enabled = false`, `model = ""`) until the user picks. The platform does
not prescribe a model stack at install.

#### group

Pure UI rollup in `capabilities.toml` (`selections.<group>.<slot>`).
Groups bundle related slots into one dashboard panel. Groups: `chat`
(agent, utility, vision, …), `embed` (embed, rerank), `voice` (stt, tts),
`img` (img). Groups do NOT carry types or per-group state — slots do.
Users adding custom slots pick which group to file under.

#### user-defined slots

Beyond the seeded catalog, the user can add named slots via the dashboard
(`hal0 slot add NAME --type TYPE --model MODEL`). The new slot:

- Must have a unique kebab-case `name` (not in the reserved seeded set)
- Must declare a `type` (see "slot type" below) — drives OmniRouter tool
  routing and (for NPU) the single-context exclusivity rule
- Picks a `model` from `registry.toml` OR pulls fresh via `/v1/pull` under
  the `user.*` namespace
- Lives in `capabilities.toml` under whichever group the user picks (or
  `selections.custom.<name>` if none chosen)

Removing a user-defined slot is a no-side-effect operation
(`hal0 slot remove NAME`) — the underlying model stays in the registry.

### FLM trio (NPU coresident slots)

The Strix Halo NPU enforces ONE AMDXDNA hardware context per host — so
only one `flm serve` process can run at a time. **But that one process can
host three model roles simultaneously** via FLM's `--asr 1 --embed 1`
flags: chat + transcription + embedding. Verified empirically 2026-05-22
(gemma3:1b + Whisper-V3-Turbo + Embedding-Gemma-300M loaded in one FLM
process, ~2 GB NPU memory total, chat at 40 tok/s).

hal0 leverages this with the `flm` profile (`flm serve --asr 1 --embed 1`)
running in the `npu` slot's container (`hal0-slot@npu`), which answers
`/v1/chat/completions`, `/v1/audio/transcriptions`, and `/v1/embeddings`
on one static port. Three slot records ride that one container:

| Slot | type | device | Backing |
|---|---|---|---|
| `npu` | `llm` | `npu` | the FLM trio's chat model (the anchor — owns the container) |
| `stt-npu` | `transcription` | `npu` | the same FLM container's `--asr` model |
| `embed-npu` | `embedding` | `npu` | the same FLM container's `--embed` model |

**Routing fan-out.** The `npu` chat slot routes through its registered
upstream like any other slot. The two shadow roles are handled by
`NpuTrioRouter` (`dispatcher/npu_trio.py`): when `v1.py`'s gating check
detects an enabled `device=npu` transcription/embedding slot, it forwards
the request straight to the npu container's static port (read from slot
config — no discovery step) at `/v1/audio/transcriptions` /
`/v1/embeddings`.

**Coresident constraint.** Loading the `npu` chat slot starts the FLM
container; the two shadow roles are then available instantly (no extra
load time). A model swap on any NPU role is a container restart (single
AMDXDNA context — one `flm serve` at a time). `[npu] asr` / `[npu] embed`
TOML toggles (orchestrator-owned) decide which shadow roles the container
serves.

**Default behavior on install.** The `stt-npu` / `embed-npu` shadow slots
seed only when the FastFlowLM `.deb` is detected at install
(`NPU_SEEDED_SLOTS`); the `npu` chat anchor is opt-in at Pro+ bundle
tier. Users without FLM installed get no NPU slots at all.

**Hard constraints (validated in capabilities.toml):**
- Only one `device = "npu", type = "llm"` slot can be `enabled = true` at
  a time. Selecting a different NPU chat means swapping the FLM trio's
  chat model (slow but supported).
- `route_to_chat` between two NPU `llm` slots is blocked — would require
  an FLM-process swap mid-conversation.

**Future-feature flag.** FLM's `--asr` and `--embed` are the documented
v0.9.42 flags. Upstream may expose additional model roles (e.g., reranking
on NPU) via similar flags later. The trio architecture extends naturally —
add a fourth slot when FLM supports a fourth role.

### slot type

The discriminator that determines:
1. **OmniRouter tool routing** — which tools dispatch to slots of this
   type.
2. **Device / GPU constraints** — `npu`-device slots are exclusive at the
   runtime layer (single AMDXDNA context; a model swap is a container
   restart, only one model on the NPU at a time), and `GpuArbiter` flips
   the single iGPU between the `llm` and `img` exclusive groups.

Type vocabulary: `llm`, `embedding`, `reranking`, `transcription`, `tts`,
`image` (`SlotManager._VALID_SLOT_TYPES`). UI labels are a separate
concern (`Chat`/`Embed`/`Rerank`/`STT`/`TTS`/`Image`) rendered by the
dashboard, not stored in config.

### OmniRouter

Client-side OpenAI tool-calling loop, owned by hal0 (it lives in hal0-api,
not the inference container). The LLM in an `llm`-type slot is given a JSON
tool catalog; it emits `tool_calls`; hal0 dispatches each to the
appropriate `/api/v1/*` endpoint and folds the result back into the
conversation.

The tool set is per-bundle (a `collection.omni` manifest names which tools
the LLM sees). It ships these 7 tools:

| Tool | Source | Endpoint | Target slot type | Required model labels |
|---|---|---|---|---|
| `generate_image` | upstream verbatim | `/v1/images/generations` | `image` | `image` |
| `edit_image` | upstream verbatim | `/v1/images/edits` | `image` | `edit` |
| `text_to_speech` | upstream verbatim | `/v1/audio/speech` | `tts` | `tts` |
| `transcribe_audio` | upstream verbatim | `/v1/audio/transcriptions` | `transcription` | `transcription` |
| `analyze_image` | upstream verbatim | `/v1/chat/completions` | `llm` | `vision` |
| `embed_text` | **hal0 custom** | `/v1/embeddings` | `embedding` | `embeddings` |
| `rerank_documents` | **hal0 custom** | `/v1/rerank` | `reranking` | `reranking` |

Deferred: `route_to_chat` (LLM-driven persona swap; needs semantics
decision), `recall_memory` (depends on the Hindsight memory MCP).

Upstream tools are kept in sync via a checksum-pinned copy of
`src/app/src/renderer/utils/toolDefinitions.json` at
`src/hal0/omni_router/tool_definitions.json`. hal0 custom tools live next
to them in the same JSON.

**Dynamic tool filtering (per chat request).** hal0's OmniRouter client
computes the active tool set at chat-start based on (a) which slots are
`enabled = true` with a loadable model, AND (b) for label-gated tools like
`analyze_image`, whether any enabled slot of the required type has a model
with the required label. Only the active subset goes into the LLM prompt.
LLMs without the `tool-calling` label receive no tools at all. Filtering
re-runs at next dispatch when slot configuration changes mid-conversation.

Bundle-level tool whitelists/blacklists are NOT supported (YAGNI until
requested). The set is always derived from slot enablement.

### model namespace

`registry.toml` (under `/var/lib/hal0/registry/`) is the **sole** model
catalog — there is no second loader process to keep in sync (the Lemonade
`server_models.json` / `user_models.json` / `extra.*` split was removed
with the runtime, #687). Every slot's `--model` path is a registry-backed
file under the model store `/mnt/ai-models`.

The registry tags each row with a namespace bucket (`ns`) the dashboard
renders as a badge:

| Bucket | Source | hal0 usage |
|---|---|---|
| `blessed` | files laid out under the curated/blessed recipe tree | hal0-curated models seeded into the registry. |
| `pulled` | on-demand pulls (HF coords or local imports via the dashboard) and upstream-only rows | written through the registry pull path (`registry/pull.py`); no daemon restart needed. |

No third tier.

### default slot (per type)

Exactly one slot per type can carry `default = true` in
`capabilities.toml`. This slot receives:
- All OmniRouter tool dispatches keyed on its type (e.g. `text_to_speech`
  → default `tts` slot)
- All unqualified `/api/v1/<endpoint>` calls that don't specify `model`
- The "Active" badge in the dashboard

Resolution rules:
1. **Type match first.** A request of type T resolves to the slot with
   `type = T` AND `default = true`.
2. **Label filter overlay.** OmniRouter tools may require model labels
   (e.g. `analyze_image` needs LLM + `vision` label). If the default LLM's
   model lacks the required label, fall through to any other enabled LLM
   slot whose model has it. Return "no compatible model" if none match.
3. **Fall-through if default disabled.** If the `default = true` slot is
   `enabled = false`, fall through to the first enabled slot of that type
   (in `capabilities.toml` declaration order). Dashboard surfaces a
   warning.
4. **Hard validation.** Two slots of the same type with `default = true`
   is a config error — refuse to save / refuse to load.

### chat persona (DO NOT use "chat-duo")

A user-facing label for "which chat slot is currently serving the
dashboard chat surface". Implementation = each persona is just an
`llm`-type slot (`agent`, `utility`, etc.). The UI offers a persona
dropdown; the OmniRouter `route_to_chat` tool lets the LLM switch personas
mid-conversation. "Chat-duo" was an early term that implied pairs —
retired before it landed.

### v0.1.x → v0.2 upgrade

**Clean break, no migration script.** The v0.2 `install.sh` detected
v0.1.x state (old `/etc/hal0/slots/*.toml` shape without the v0.2 runtime
config present) and refused to install. It printed:

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

Driver: v0.1.x audience is single-digit alpha users; migration script ROI
is bad. The backup-and-wipe instruction takes 30 seconds; users who want
their configs back later run `hal0 registry import
hal0-v0.1-backup.tar.gz` (single command we ship in v0.2 to restore
`registry.toml` only — slot selections must be redone via bundle picker).

### fresh install

Installs ship **no pre-selected model stack**. capabilities.toml lands
with empty selections (`enabled = false` for every group). First-run
dashboard shows a **bundle picker** — 4 hardware-anchored tiers plus the
vendor-blessed LMX bundle — with a "Skip — configure manually" path to a
blank dashboard.

The bundle picker is the user's first action; the installer never silently
chooses. Driver: hal0 is a platform, not a curated bundle; opinionation is
a per-tier concept, not a default-install concept.

### bundle tiers (first-run picker)

| Tier | Min unified RAM | `chat.primary` | `chat.coder` | Aux | NPU trio |
|---|---|---|---|---|---|
| `hal0-Lite` | 16 GB | qwen3.5-0.8b | — | — | — (not shown) |
| `hal0-Default` | 32 GB | qwen3.5-9b | — | nomic-v1.5, whisper-tiny, kokoro:cpu | — (not shown) |
| `hal0-Pro` | 64 GB | Qwen3.6-27B-MTP | Qwen3-Coder-30B-A3B | + bge-reranker-v2-m3, whisper-base, sd-turbo | shown, **opt-in** |
| `hal0-Max` | 100 GB Strix Halo | Qwen3.6-35B-A3B-MTP | Qwen3-Coder-Next-80B-A3B | + whisper-large-v3-turbo, flux-2-klein-9b | shown, **opt-in** |
| `LMX-Omni-52B-Halo` | 100 GB Strix Halo | Qwen3.6-35B-A3B-MTP | — | Whisper-Large-v3-Turbo, kokoro-v1, Flux-2-Klein-9B | — |

Notes:
- `hal0-Max` was originally proposed as `hal0-Halo` but renamed to avoid
  collision with the vendor-blessed `LMX-Omni-52B-Halo`.
- The LMX bundle is shown under a "Pre-built kits" section below the tier
  picker, not as a tier card.
- `gpt-oss-120b` and other extreme models are intentionally excluded from
  bundle defaults — power users install them manually via `hal0 model
  pull`.
- Bundle definitions live in `installer/manifests/omni/<name>.json`. Each
  carries a `collection.omni` block (the inherited model-kit manifest
  shape) plus a `hal0` block of slot-selection metadata.
- hal0 reads `/proc/meminfo` at install; tiers that don't fit are greyed
  out in the picker with a tooltip explaining why.

### two-tier scope

Access-control pattern for the admin MCP. Routine ops (slot status,
`model_swap`, `hardware_probe`, `memory_add`, etc.) = autonomous.
Capital-D destructives (`model_pull`, `slot_delete`, `config_write`,
`memory_delete` >1 record, etc.) = gated via the dashboard approval inbox.
No per-agent trust toggle (destructives must always be approved).

### agent identity card

A memory item published by a bundled agent during its first-run bootstrap,
recording who-it-is and how-to-reach-it. Lives in the dedicated `agents`
memory namespace (NOT `shared`, NOT `private:*`), tagged `agent-identity`.
Immutable — written once, cleaned on uninstall. Schema v1 pins required
fields in `metadata`: `agent_id`, `display_name`, `namespace`,
`hal0_state.{registered_at, bootstrap_version, hal0_version,
hermes_version}`. The `text` field is a human-readable summary.

### agents namespace

Hindsight memory namespace reserved for agent identity cards. Separate
from `shared` (episodic memory) and `private:*` (per-agent working
memory). Small forever (5-10 cards). Discoverable via
`memory_search({namespace: "agents", tags: ["agent-identity"]})`.
Foundation for multi-agent discovery.

### Hal0Profile

**Removed (R4 H4).** This was a hal0-owned Hermes model-provider plugin at
`$HERMES_HOME/plugins/model-providers/hal0/`. It was deleted because it
hardcoded a stale base URL; `hermes_provision` now actively removes the
old plugin and points Hermes's config at hal0-api directly
(`base_url = {HAL0_API_URL}/v1`, derived from the default chat slot — the
`agent` anchor). Inference request shaping happens server-side in
hal0-api's `/v1` surface, not in a Hermes-side profile.

### Hal0MemoryProvider

A hal0-owned Hermes plugin extending `agent.memory_provider.MemoryProvider`.
Lives at `$HERMES_HOME/plugins/memory/hal0-memory/`. Native memory
injection: implements `system_prompt_block`, `prefetch`, `sync_turn`,
etc. — memory is part of the prompt, not a tool the agent has to remember
to call. Talks to `hal0-memory` MCP at `/mcp/memory` over HTTP. v0.3.

### hermes_provision

The hal0 module at `src/hal0/agents/hermes_provision.py` that orchestrates
the 15-phase Hermes bootstrap (preflight → install → env_probe →
home_init → install_artifacts → persona_seed → config_write → mcp_wire →
context_link → namespace_register → model_automap → voice_wire →
gateway_secrets_wire → smoke_tests → self_report). Idempotent +
checkpointed via `/var/lib/hal0/state/agents/hermes/provision.json`. CLI
verb is `hal0 agent bootstrap hermes`. Renamed from `hermes_bootstrap.py`
to avoid a soft collision with upstream's Windows-UTF8 module of the same
filename.

### HERMES_HOME (v0.3)

Pinned to `/var/lib/hal0/agents/hermes/` for hal0-bundled installs (not
`~/.hermes`). Multi-agent-root-ready: pi-coder and future agents land at
`/var/lib/hal0/agents/<name>/`. Wrapper `/usr/local/bin/hal0-hermes`
sources `/var/lib/hal0/secrets/agents/hermes.env`, exports `HERMES_HOME`,
and execs `/var/lib/hal0/venvs/hermes/bin/hermes`. Raw
`/usr/local/bin/hermes` stays unwrapped so human SSH sessions get normal
behavior.

### v0.3

The milestone opened 2026-05-23. Five interlocking streams: (1)
Hermes-Agent first-run bootstrap, (2) React dashboard v3 wired to live
data, (3) inference-runtime polish (preload+idle, GPU TTS, KV%, `/v1/*`
proxy) — at the time this rode on the Lemonade runtime, since replaced by
the per-slot container runtime (#687), (4) Admin/auth simplification
(FastAPI owns auth, Caddy collapsed to TLS-only or removed), (5) Advanced
memory + MCP client side (memory graph + Memify + federation + per-agent
external MCP allow-list). See PLAN.md §1 v0.3 + §15 Phase 10.

### composer

The browser-side input surface in the v3 dashboard's `<HermesChat>` tab. A
React `<textarea>` + send button — NOT an xterm / PTY. Enter submits the
current text as a `prompt.submit` JSON-RPC frame; Shift+Enter inserts a
newline. Replaces the earlier "xterm-over-PTY" design (MASTER-PLAN §1
pivot #1) — the PTY route was cut after DA-sec-ops flagged it as a LAN-RCE
class problem. Lives in `ui/src/dash/agents/chat/Composer.jsx`.

### transcript

The rolling chat history rendered above the composer. Built by zustand
store `useTranscript` from `message.delta` + `message.complete` events
streamed off `/api/agents/{id}/events`. Tool-call deltas appear as inline
collapsed cards. The store dedupes by `(session_id, message_id)` so the WS
reconnect path (250ms → 4s backoff, owned by `use-hermes-session.js`)
doesn't duplicate frames.

### plugin host

The hal0-api surface (`/api/dashboard/plugins/*` +
`/dashboard-plugins/{name}/*`) that proxies upstream Hermes plugin
manifests + static assets so the v3 dashboard can mount plugin bundles
(the kanban plugin in v0.3) inside an `<AgentView>` tab. Each plugin
renders inside a shadow-DOM iframe with the `__HERMES_PLUGIN_SDK__` global
shimmed from `src/hal0/api/plugins/sdk-shim.js`. hal0 vendors the SDK
shape; the upstream-Hermes pin is human-gated and a drift-detection job
(see `hermes-sdk-diff`) alerts on upstream registry.ts changes that would
break the shim. See PR-7.

### sidecar agent block

The compact agent panel rendered in the v3 dashboard sidebar —
service-status chip, persona picker, memory chip, skills list, approvals
bell, [Open chat] button. Lives at
`ui/src/dash/agents/SidebarAgentBlock.jsx`. Parameterised by `agent_id` so
pi-coder lights up by adding a row. The service chip wires `POST
/api/agents/{id}/restart`; the memory chip reads `GET
/api/agents/{id}/memory/stats`; the skills list reads `GET
/api/agents/skills`.

### persona TOML

A file under `/var/lib/hal0/agents/hermes/personas/{id}.toml` declaring
`[persona]` (`id`, `display_name`, `summary`, `system_prompt`,
`tools_allowed`, `memory_namespace`, `preferred_upstream`,
`preferred_model`) and `[persona.approval]` (`default_policy`,
`auto_approve`, `require_approval`). The filename stem MUST match
`[persona].id` — `load_persona` raises `PersonaError` on a mismatch to
prevent silent renames. Seeded by `hermes_provision`; operator-edited via
`hal0 agent personas` or the dashboard persona editor. The active persona
is the contents of `active.txt` next to the personas dir; switching swaps
the system-prompt scope on the next turn without restart.

### hal0-memory (Hermes plugin)

The hal0-owned `MemoryProvider` plugin at
`installer/agents/hermes/plugins/hal0-memory/` (renamed from
`hal0-cognee`; this is the canonical, shipped source — no mirror
elsewhere), mounted into `$HERMES_HOME/plugins/hal0-memory/` by the
provisioner. Wraps hal0-memory's REST surface
(`/api/memory/{add,search,recall,list,delete}`) so memory injection
happens inside Hermes's prompt pipeline (`system_prompt_block` +
`prefetch`), plus explicit `hal0_memory_{search,recall,add}` tools. Two
banks: `private:<agent_id>` (default, agent_id `hermes`) + `shared`,
selected per write via `X-hal0-Private`; reads are a server-side union of
both. Supersedes `Hal0MemoryProvider` above as the canonical name.

### hermes-sdk-diff

The weekly GitHub Action + local script (`scripts/hermes-sdk-diff.sh`)
that diffs hal0's pinned Hermes upstream commit (`pyproject.toml
[tool.hal0.upstream-hermes]`) against upstream HEAD for the tracked files
(`web/src/plugins/registry.ts`, `web/src/plugins/slots.ts`,
`hermes_cli/web_server.py`, `agent/memory_provider.py`,
`tools/registry.py`, `agent/events.py`). When any tracked file changes,
the workflow opens an issue with the upstream commit range so the bump is
human-gated. The upstream-Hermes pin is bumped only through this
human-gated path — never auto-merged.

### HMAC session cookie

The browser-facing authn seam on hal0-api's chat-proxy surface
(`/api/agents/{id}/{events,submit,session/*}`). Minted on first GET
`/api/agents/{id}/session/handshake`, payload `{session_id, expires_at}`,
signature `HMAC-SHA256(<secret>, base64url(payload))`. Secret lives at
`/var/lib/hal0/agents/secret.bin` (chmod 0600, generated on first use).
`HttpOnly` + `SameSite=Lax` + 8h TTL. Belongs to the chat-proxy
specifically (route auth was removed from the rest of the API; identity on
hal0-api is carried by the `X-hal0-Agent` header, below). See
`src/hal0/api/agents/_auth.py`.

### X-hal0-Agent

The HTTP header hal0-api sets on outbound hops to hermes (and that bundled
agents set on inbound hops to hal0-api). Carries the agent's stable id
(e.g. `hermes`, `pi-coder`). It is the identity claim on hal0-api (NOT
Bearer); per the security baseline the chat-proxy injects it, the browser
never sees or sets it. The hal0-memory MCP resolver derives the per-agent
private namespace from this header.

### composite hal0 upstream

A single `Upstream(name="hal0", kind="slot",
url="http://127.0.0.1:8080/v1", slot_name=None)` registered automatically
in the upstream registry
(`src/hal0/api/__init__.py::_autoregister_slot_upstreams`). It aggregates
every chat-capable slot's model id through one `/v1/models` response (5s
TTL cache) so the dashboard and OpenAI clients see a single model list
rather than one entry per slot. It exists **only** for that aggregation:
`SlotManager` has no `hal0` entry, so the dispatch readiness gate and
SERVING wrap skip it and it is never a forward target — real requests
resolve to the concrete slot that owns the model id. Explicit
`upstreams.toml` entries claiming the name `hal0` win — autoregistration is
skipped when overridden.

### SlotConfigStore

Deep module (`src/hal0/slot_config/`, issue #697) that owns *both*
`capabilities.toml` selections and `slots/*.toml` as one reconciled truth,
ending the drift between them (previously reconciled by an unconditional
rewrite in `capabilities/orchestrator.py:apply()`). Interface:
**`apply(selection) -> ChangeSet` is compute-only** (no disk write); the
store also exposes `commit(cs)` (atomic per-file write with
rollback-to-before on partial failure) and `revert(cs)`. Keeping `apply()`
pure is the whole point — it makes the write an explicit, observable,
reversible step instead of a hidden rewrite. The same module's
`write_slot_toml()` is the single byte-level write path for `slots/*.toml`;
every writer (SlotManager create/update/persist-default, installer
pick-default, model-delete cascade) routes through it. First-boot seed and
v1→v2 schema migrations stay on `save_capabilities_config`, which shares
the store's `capabilities_toml_payload` serializer so shapes can't
diverge. See [ChangeSet](#changeset), candidate 1 of the 2026-06-11
review.

### ChangeSet

The value `SlotConfigStore.apply()` returns: `{before, after}` snapshots
of the on-disk slot config. Makes reconciliation a pure computation
separate from the write — drift is testable as `disk == after` after a
committed apply, `disk == before` after a revert. A failed mid-flight
apply leaves disk at `before`, never a half-reconciled state. See
[SlotConfigStore](#slotconfigstore).

### is_ready_for_dispatch / SlotManager.state (proposed)

The public readiness seam on `SlotManager`, replacing the Dispatcher's
reach into the private `_current_state()` (`dispatcher/router.py:723,791`).
`state(name) -> SlotState` exposes the slot state; `is_ready_for_dispatch(name)
-> bool` owns the ready-set rule (`READY | SERVING | IDLE`) so it is
defined exactly once instead of duplicated across Dispatcher and
SlotManager. The Dispatcher stops knowing the state-cache, the disk
fallback, and the enum. Endorsed by the 2026-06-07 audit (ANSWERS §2.1).
Candidate 2 of the 2026-06-11 review. **Proposed** — the `state()` half is
implemented by in-flight PR #649; `is_ready_for_dispatch()` remains
(#696).

### SlotViewAggregator

Stateless module (`src/hal0/slot_view/`, issue #698 / PR #706) that lifts
the five enrichment concerns previously inline in
`api/routes/slots.py:list_slots()` (state serialization, config-field
enrich, container systemctl/port/image probe + drift detect, per-slot
memory accounting, metric injection) behind **eager `snapshot() ->
list[SlotView]`** (single computation, no per-concern composition seam
until a second caller justifies one). Takes its stores as constructor
dependencies — the real set is wider than the four nominal ones
(model_cache, upstreams, last_used_model, slot_pull_jobs also come off
`app.state`; see the class docstring) — so tests inject fakes instead of
crossing HTTP. The route is a thin adapter. Decision held: no per-concern
methods — one caller today makes a composable enrichment pipeline a
hypothetical seam; add `snapshot(include_metrics=...)` only when the admin
MCP surface actually needs state-without-metrics. Per-concern logic lives
as module-level functions (`serialize_slot`, `config_enrichment`,
`container_enrichment`, `synthesize_upstream_entries`), each unit-tested
with fake stores; helpers shared with `get_slot` / `routes/health.py`
remain in `slots.py` as request-bound adapters. Candidate 3 of the
2026-06-11 review. See [SlotView](#slotview).

### SlotView

The enriched per-slot record `SlotViewAggregator.snapshot()` emits — one
slot's state plus its container enrichment, memory attribution, and
metrics (`SlotMetricsView`), as a typed object (with `to_dict()` for the
API shape) rather than the ad-hoc dict previously assembled inline in the
route. See [SlotViewAggregator](#slotviewaggregator).

### LoadedSlot (proposed)

The typed routing result `SlotManager.resolve_for_request(slot_type, *,
required_labels)` and `SlotManager.loaded_slot(name)` return: `{name,
model_id, system_prompt, device, slot_type, enabled}` — frozen, no
`backend_url` (handlers use the shared dispatch-context URL). Replaces the
omni-router pattern of routing to a bare name and re-iterating
`iter_configs()` to dig the config back out. `route_for_request` itself is
unchanged (the tool filter only consumes its boolean). Locked invariant:
`iter_configs()` never appears in `omni_router/dispatch.py`. The "public
slot introspection" half of the original candidate was dropped — already
solved by [model_meta](#model_meta)'s `labels_of`. Issue #701, grilled
2026-06-12; lands after PR #649 (shared `slots/manager.py` surface).

### PhaseContext (proposed)

The argument every Hermes-bootstrap phase receives once issue #702 lands:
read-only `BootstrapState`, an explicit `repair` flag (deleting the
`_repair_flag` dict sentinel), an injectable `PhaseIO` bundle of the
network/subprocess seams (the test suite's monkeypatch tax, typed), and
`output_of(phase)` — which raises unless the phase declared that
dependency in its `needs` tuple. Deliberately NOT an input→output chained
pipeline: the bootstrap's 15 phases (code comments say 12 — stale) have
exactly four cross-phase reads, so the deepening is declared-and-validated
dependencies over the existing run-all runner, whose FAIL semantics
(failures never halt or skip dependents; fallbacks fire and are now
recorded in `details["fallbacks"]`) are preserved byte-identically. Issue
#702, grilled 2026-06-12.

### GPUMemorySample (proposed)

The typed record `hardware/gpu_view.py:sample()` emits — vendor, both
pools split (`vram_*`/`gtt_*`) AND max-pooled (`used_mb`/`total_mb`),
`is_uma`, raw `gpu_busy`, and `util_is_forced_high`. One home for the live
GPU-memory quirks: stats.py delegates to it, the hardware route stops
re-importing private probe helpers to un-pool the numbers, and the two
decisions locked 2026-06-12 are factual rather than heuristic —
`util_is_forced_high` reads `power_dpm_force_performance_level == "high"`
(the gpu-compute.service pin that makes `gpu_busy_percent` a flat-100
lie), and `is_uma` is the physical carve-out signature (`gtt_total > 0 and
vram_total < 2GB`), replacing the route's `vram > ram*0.5` guess. Probe
stays the one-time detection owner; per-slot capacity attribution and the
ComfyUI proxy stay separate readers. Issue #703, grilled 2026-06-12.

### model_meta

The single home (`src/hal0/model_meta/`, issue #695 / PR #700) for the
model-classification and device→backend logic previously copy-pasted
across `routes/models.py:_classify_type`, `routes/slots.py`,
`capabilities/orchestrator.py` (four `_canonical_*` helpers), the
omni-router heuristic, and the provider layer. **Stateless surface, no
construction:** `classify(model_id) -> slot type` and
`device_to_backend(device) -> (recipe, llamacpp)` are pure;
**`is_resolvable(model_id, registry) -> bool` takes the registry
explicitly** (it needs registry membership + FLM-catalog presence via
`is_installed_flm_id`) so the module stays importable everywhere without a
handle to thread through. Also home to `canonical_device`,
`device_to_legacy_backend` (deliberately separate — they disagree on
unknown input, see `# NOTE(#695)`), and `labels_of` (the ex-"keep the two
in sync" extraction shared by `omni_router/filter.py` and
`slots/manager.py`). Candidate 4 of the 2026-06-11 review.

## Working in this repo

This section is agent-facing: what shape the work takes, where the
contracts live, and how a teammate lands a change without colliding with
another parallel session.

### Issue tracker & triage

- **Issue tracker** — GitHub Issues on `Hal0ai/hal0` via the `gh` CLI.
- **Triage labels** — `needs-triage` / `needs-info` / `ready-for-agent` /
  `ready-for-human` / `wontfix`.
- **Published contracts** — the operator-facing docs under `docs/`
  (Starlight `.mdx`): [`docs/concepts/agents.mdx`](./docs/concepts/agents.mdx)
  (bundled-agent model), [`docs/guides/run-agents.mdx`](./docs/guides/run-agents.mdx)
  (the `hal0 agent` CLI), and [`docs/concepts/memory.mdx`](./docs/concepts/memory.mdx)
  (the Hindsight-backed memory subsystem).

### Shipping: deploy + PR workflow

This repo is worked by **multiple parallel Claude sessions** against one
shared runtime (CT 105, `/opt/hal0`). Follow this so two agents never
collide and nothing reaches CT 105 by hand-guessing.

**1. Isolate every change in a worktree off `main`.** Never edit on a
branch another agent owns, and never stack new work on an unmerged feature
branch unless you intend a stacked PR. Pin to `main` so your diff is
reviewable independently:

```bash
git fetch origin --prune
git worktree add -b <type>/<slug> ~/dev/wt/<slug> origin/main
```

If your changes were authored on top of someone else's branch, re-base
them onto `main` with `git apply --3way` (production regions are usually
disjoint; only test mocks tend to conflict — adapt the assertion to
main's fixtures, don't pull in the other branch's unmerged mock).

**2. Claim before you touch the shared tree.** Local board:
`~/.claude/bin/wip claim "<intent>" <files…>`. For CT 105 itself:
`~/.claude/bin/wip hal0 claim "<intent>" /opt/hal0` — and check
`wip hal0 status` first; if it's not on `main` or has tracked edits,
another session is mid-deploy, so coordinate, don't reset over it.

**3. Verify on the branch before deploying.** `tsc --noEmit` (ui),
targeted `pytest` (not the whole suite — it hangs on this dev box), and the
relevant `playwright … --project=chromium` spec (forced-mock). Build the
UI clean (`rm -rf node_modules/.vite dist && npm run build`) — `ui/dist`
is gitignored, so a stale bundle hides UI changes.

**4. Deploy / preview to CT 105 with `scripts/deploy.sh` — never by
hand.** A bare `git reset` updates source but leaves the served bundle
stale; the script folds in the UI rebuild, the group-share perms
re-assert, the `hal0-api` restart, and a health check. To preview an
**unmerged** branch:

```bash
ssh hal0 'cd /opt/hal0 && sudo bash scripts/deploy.sh --ref origin/<your-branch>'
```

It refuses to reset over another session's uncommitted tracked edits
unless `--force`. After this, CT 105 is **ahead of `main`** until your PR
merges.

**5. PR against `main`; merge base-first.** Open the PR (`gh pr create
--base main`), let CI go green, get approval. Stacked PRs merge their base
first. After merge, reconcile CT 105 back to trunk:
`ssh hal0 'cd /opt/hal0 && sudo bash scripts/deploy.sh --ref origin/main'`,
then clean `[gone]` branches.

**6. Record memory-worthy outcomes** (PR/merge, gotcha, decision) to the
hal0 Hindsight engine via the `hal0-memory` skill — see the standing rules
in `CLAUDE.md`.

## See also

- [`PLAN.md`](./PLAN.md) — v1 scope, modules ported from haloai, milestones
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — tests, PR workflow, anti-scar rules
- [`docs/reference/slot-lifecycle.mdx`](./docs/reference/slot-lifecycle.mdx) — slot lifecycle state machine
- [`docs/concepts/architecture.mdx`](./docs/concepts/architecture.mdx) — control plane + dispatcher routing
- [`docs/getting-started/install.mdx`](./docs/getting-started/install.mdx) — install flow + filesystem layout
