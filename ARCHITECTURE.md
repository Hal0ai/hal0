# hal0 architecture

This document covers hal0's internal architecture. For the user-facing
shape (install, ports, filesystem layout), see
[`docs/install.md`](./docs/install.md). For scope and roadmap, see
[`PLAN.md`](./PLAN.md). For the v0.3 release notes, see
[`CHANGELOG.md`](./CHANGELOG.md).

## Process model

As of v0.3, hal0 runs as **three** long-lived systemd units on the
host:

- `hal0-api.service` — the FastAPI app (Uvicorn). Owns slot lifecycle,
  capability dispatch, the OmniRouter loop, MCP server mounts, and the
  React dashboard build. Binds `0.0.0.0:8080` open (no built-in auth
  since [ADR-0012](./docs/internal/adr/0012-remove-auth-and-caddy.md);
  see "Auth posture" in the README).
- `hal0-lemonade.service` — one `lemond` daemon. The unified inference
  runtime ([ADR-0008](./docs/internal/adr/0008-lemonade-adoption.md)).
  Binds `127.0.0.1:13305` loopback-only — that's the canonical proxy
  target `hal0-api` points at. `lemond` also opens a separate
  `127.0.0.1:9000` listener for its own OpenAI-compatible surface, but
  hal0 never proxies through that port; everything goes via 13305.
- `hal0-openwebui.service` — OpenWebUI on `:3001`, prewired against
  the local hal0 API. Optional but installed by default.

```
                   ┌─────────────────────────┐
   user/clients ─▶ │  hal0-api  (:8080)      │ ◀─ OpenWebUI (:3001)
                   │  FastAPI + dispatcher   │       hal0-openwebui.service
                   │  + /mcp/admin           │
                   │  + /mcp/memory          │
                   │  + /v1/* proxy          │
                   └────────────┬────────────┘
                                │ HTTP (127.0.0.1:13305)
                                ▼
                   ┌─────────────────────────┐
                   │  lemond  (:13305)       │
                   │  hal0-lemonade.service  │
                   │  llamacpp / flm / sd-cpp│
                   │  whisper.cpp / kokoro   │
                   └─────────────────────────┘
```

Slots are no longer one-systemd-unit-per-slot. A slot is a config row
under `/etc/hal0/slots/<name>.toml` (mirrored into
`/etc/hal0/capabilities.toml`) that names ONE Lemonade-registered
model + a `type` + a `device`. `SlotManager.start(slot)` is a
`POST /v1/load` against `lemond`; the lifecycle state machine
(`offline → starting → warming → ready ↔ serving / idle → unloading`)
still holds, but each state is derived from `/v1/health` polling +
Lemonade `/logs/stream` events rather than `systemctl is-active` on a
slot-specific unit.

The FLM trio is the one exception worth calling out: on Strix Halo
with FastFlowLM installed, three NPU slots (`agent`, `stt-npu`,
`embed-npu`) all back the same `flm serve` child. Lemonade tracks the
chat role only; hal0's capability dispatcher reads
`/v1/health.loaded[].backend_url` for the FLM model and routes
transcription + embedding requests directly to that child's port. See
[ADR-0009](./docs/internal/adr/0009-flm-trio-npu-packing.md).

## Module layout

```
src/hal0/
├── api/             # FastAPI app + routers + middleware
│   ├── routes/      # ~27 APIRouters: slots, models, capabilities, agents,
│   │                #   approvals, journal, events, hardware, health,
│   │                #   bundles, lemonade_admin, lemonade_logs,
│   │                #   lemonade_proxy (/v1/* reverse-proxy), mcp,
│   │                #   memory, images, openwebui, installer, ...
│   ├── middleware/  # error envelope, request id, CORS, identity
│   ├── mcp_mount.py # mounts /mcp/admin + /mcp/memory (FastMCP
│   │                #   Streamable-HTTP apps)
│   └── deps.py      # FastAPI dependency providers
├── lemonade/        # HTTP client (DEFAULT_BASE_URL = 127.0.0.1:13305),
│                    #   catalog_sync, metrics_shim, log_proxy
├── slots/           # slot lifecycle (state machine, status, registry-
│                    #   driven config)
├── capabilities/    # UX overlay grouping flat slots into capability
│                    #   cards (catalog + config + orchestrator);
│                    #   persists selections in capabilities.toml and
│                    #   reconciles slot TOMLs on every apply
├── dispatcher/      # routing, single-flight, decision logging
├── omni_router/     # client-side OpenAI tool-calling loop + tool defs
├── agents/          # bundled-agent install (Hermes-Agent),
│                    #   hermes_provision orchestrator, identity cards,
│                    #   approvals queue
├── mcp/             # bundled MCP servers (hal0-admin, hal0-memory)
├── memory/          # Cognee adapter (SQLite + LanceDB + Kuzu)
├── registry/        # model registry (atomic TOML, mtime cache, GGUF
│                    #   magic-byte detect, HF-cache repo-name fallback)
├── hardware/        # probe + stats (GPU, NPU, RAM, disk)
├── upstreams/       # external LLM providers (OpenRouter, Anthropic, ...)
├── config/          # pydantic schemas, TOML loader, migrations
├── events/          # in-process pub/sub for SSE streams
├── journal/         # journal-event store powering the dashboard Journal
├── bundles/         # first-run bundle picker (hal0-Lite/Default/Pro/Max
│                    #   + LMX-Omni-52B-Halo)
├── providers/       # legacy provider ABC + non-slot consumers (image-gen,
│                    #   hardware probe, catalog). LemonadeProvider is the
│                    #   only dispatch-path provider in v0.3.
├── updater/         # self-update (cosign-verified, atomic swap)
├── installer/       # first-run wizard backend, hardware probe writer
├── voice/           # legacy Moonshine + Kokoro provider glue (still used
│                    #   by non-Lemonade voice paths)
├── openwebui/       # companion service env file writer
└── cli/             # `hal0` Typer CLI (incl. agent / approvals / registry
│                    #   subapps + `capabilities migrate`)
```

The capabilities layer is a **thin overlay** on the flat slot layer,
not a replacement. Slot configs under `/etc/hal0/slots/*.toml` remain
authoritative; `capabilities.toml` records which capability picks
should be projected back onto those slot files. `hal0 capabilities
migrate` cleans up persisted selections whose (backend, model) pair
is no longer valid — primarily for FLM model-tag namespace drift.

## Key boundaries

- **Slot lifecycle talks to Lemonade.** `SlotManager.start(slot)` is a
  `POST /v1/load` against `lemond`; `.stop(slot)` is `POST /v1/unload`;
  `.status(slot)` reads `/v1/health.loaded[]`. The slot manager
  doesn't render systemd units, doesn't import providers, and doesn't
  parse model files — it asks `lemond` and propagates the answer.
- **Dispatcher is HTTP-only.** It does not start/stop slots. It reads
  slot status from the slot manager and routes requests. If a slot is
  offline, it returns a structured error; loading is a separate API
  call.
- **One inference daemon.** `LemonadeProvider` is the only dispatch-path
  provider in v0.3. Legacy `Provider` subclasses
  (`MoonshineProvider`, `KokoroProvider`, `ComfyUIProvider`,
  `FLMProvider`) remain in the tree to back non-slot paths (image-gen
  driver, hardware-probe helpers, FLM model-tag namespace probe via
  `flm list -j`) but no longer manage processes or own ports.
- **The registry is the only source of truth for "what models exist."**
  Atomic TOML files under `/var/lib/hal0/registry/`. mtime-cached.
  `hal0 registry sync` projects the registry into Lemonade's
  `server_models.json`; user pulls via `POST /v1/pull` land under the
  `user.*` namespace and don't require a `lemond` restart.

## State

Three categories of state, three filesystem locations:

| Kind      | Location                 | Examples                                                          |
|-----------|--------------------------|-------------------------------------------------------------------|
| Code      | `/usr/lib/hal0/current/` | Python package, UI dist, unit templates                           |
| Config    | `/etc/hal0/`             | `hal0.toml`, `slots/*.toml`, `capabilities.toml`, `hardware.json` |
| Runtime   | `/var/lib/hal0/`         | `lemonade/`, `models/`, `registry/`, `openwebui/`, `state/`, `agents/` |

Code is replaceable (every update writes a new versioned dir + flips a
symlink). Config is preserved across updates. Runtime is preserved
across updates and survives uninstall when `--keep-data` is passed.

## Slot lifecycle state machine

The authoritative enum lives in
[`hal0.slots.state.SlotState`](./src/hal0/slots/state.py); transitions
are enforced by `SlotManager._transition()` and persisted atomically
to `/var/lib/hal0/slots/<name>/state.json`.

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
| `offline`    | No model loaded into `lemond` for this slot.                         |
| `pulling`    | Model files downloading / verifying; load not yet issued.            |
| `starting`   | `POST /v1/load` issued; Lemonade hasn't acknowledged the load yet.   |
| `warming`    | Load acknowledged; child reachable but not yet returning `/v1/models`.|
| `ready`      | Probe converged AND at least one model advertised — safe to route.   |
| `serving`    | At least one inference request in flight on this slot.               |
| `idle`       | Loaded but quiet for longer than the idle timeout (eviction-eligible).|
| `unloading`  | Graceful `POST /v1/unload` in progress.                              |
| `error`      | Failed; details in `state.json.message` and `lemond` logs.           |

`SlotManager.status()` runs a bidirectional reconciler against
`/v1/health.loaded[]`:

- A `ready`/`serving`/`idle` state with the model missing from
  `loaded[]` → transition to `error` (or back to `offline` if the
  unload was deliberate).
- An `offline`/`error` state with the model present in `loaded[]` →
  adopt the slot into `ready` or `idle`.

Routers MUST treat `idle` distinctly from `ready`: an idle slot is
eviction-eligible and may be nuclear-evicted by Lemonade's per-type
LRU under load.

## See also

- [`PLAN.md`](./PLAN.md) — v0.3 scope + path to v1.0
- [`CHANGELOG.md`](./CHANGELOG.md) — release-level change history
- [`docs/operate/auth.mdx`](./docs/operate/auth.mdx) — reverse-proxy
  recipes for hal0 boxes reachable off-LAN
- [`docs/internal/adr/0008-lemonade-adoption.md`](./docs/internal/adr/0008-lemonade-adoption.md)
  — why one daemon, why 13305
- [`docs/internal/adr/0012-remove-auth-and-caddy.md`](./docs/internal/adr/0012-remove-auth-and-caddy.md)
  — why no built-in auth or TLS in v0.3
