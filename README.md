<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./ui/public/brand/logo-halo-dark.svg">
  <img src="./ui/public/brand/logo-halo-light.svg" alt="hal0" width="220">
</picture>

### Open-source home AI inference platform

[hal0.dev](https://hal0.dev) · [Install](https://hal0.dev/docs/install/) · [Docs](https://hal0.dev/docs/) · [Roadmap](https://hal0.dev/roadmap)

</div>

---

hal0 turns a Linux box — ideally a Ryzen AI Max+ 395 with 128 GB of
unified memory — into a polished, OpenAI-compatible inference appliance.
A unified runtime, hardware-aware slots, prewired chat UI, signed
self-update. One command installs the lot.

It is not another llama-server wrapper. hal0 ships **AMD's Lemonade
Server as the unified inference runtime** behind a thin hal0 capability
layer. Every workload — chat, embed, rerank, STT, TTS, image gen —
runs as a real, named slot in `capabilities.toml`; the API surface
covers the full OpenAI-compatible matrix; the dashboard is for
operating the box, not chatting with it.

```sh
curl -fsSL https://hal0.dev/install.sh | bash
```

> **Status:** **v0.3.0-alpha.1** (2026-05-24) — the auth-removal +
> dashboard-v3 cut. `hal0-api` now binds `0.0.0.0:8080` open with no
> built-in auth, no Bearer-token store, no Caddy, no TLS termination
> ([ADR-0012](./docs/internal/adr/0012-remove-auth-and-caddy.md)
> supersedes ADR-0001). Front hal0 with an upstream reverse proxy if
> it's reachable off-LAN — see [`docs/operate/auth.mdx`](./docs/operate/auth.mdx)
> for Traefik / nginx / Cloudflare Tunnel recipes. The React dashboard
> v3 lands wired against live data for slots, chat, models, journal,
> and agents; remaining mock-fallback panels are tracked under the
> `dashboard-v3` label. First run still lands the v0.2 **bundle picker**
> (`hal0-Lite` / `Default` / `Pro` / `Max` + `LMX-Omni-52B-Halo`) —
> `capabilities.toml` ships empty by design, no silent default.
> Expect rough edges: APIs may still shift, KV% for GPU slots reads
> `—` (see [ADR-0008 §Costs](./docs/internal/adr/0008-lemonade-adoption.md)),
> and we don't promise upgrade compatibility across `0.3.x` tags.
> See [`CHANGELOG.md`](./CHANGELOG.md) for the full v0.3.0-alpha.1
> change list and [`PLAN.md`](./PLAN.md) §1 for the path to v1.0.

## Why hal0

**Strix Halo native, but not Strix-Halo-only.** The probe is UMA-aware
on Strix Halo and falls back to portable parsers (`/proc/cpuinfo`,
`/proc/meminfo`, `lspci`) on every other host. The `platform` field on
`/api/hardware` resolves to one of `strix-halo`, `wsl2`, `lxc`,
`proxmox-kvm`, `kvm`, `bare-metal-{nvidia,amd,intel}-gpu`, or
`bare-metal-cpu-only`, and the dashboard only labels memory as
"unified" when it actually is. The slot-fit warnings size against the
real unified pool, not a BAR carve-out. The XDNA NPU is a first-class
device — opt-in even at Pro/Max tier, packing three model roles into
one process via the FLM trio (see below). 128 GB Ryzen AI Max+ 395 is
the reference deployment — not a hopeful port.

**One inference runtime, six modalities.** Lemonade Server's per-type
LRU policy lets chat, embed, rerank, STT, TTS, and image generation
share one process pool. Concurrent chat + embed + image on Strix Halo
measured at 1.10s wall-clock under spike #2 with zero evictions; chat
+ embed alone sustains the same ~258 tok/s baseline the v0.1 toolbox
stack hit, with `--parallel 1 --threads N` mandatory in the daemon
config to keep the Vulkan dispatch from oversubscribing CPU cores
(memory: `hal0_lemonade_threads_deadlock`).

**NPU multi-role via the FLM trio.** On Strix Halo with FastFlowLM
installed, a single `flm serve` process hosts chat + transcription +
embedding concurrently on the one AMDXDNA hardware context — ~2 GB
NPU memory, gemma3:1b at 40 tok/s + Whisper-V3-Turbo + Embedding-Gemma
all coresident. hal0 exposes this as three slots (`agent`, `stt-npu`,
`embed-npu`) that all back the same FLM child via direct port dispatch.
See [ADR-0009](./docs/internal/adr/0009-flm-trio-npu-packing.md) for
why.

**Dispatcher with single-flight + OmniRouter tool-calling.** Registry-aware
routing across local slots *and* external upstreams (OpenRouter,
Anthropic, OpenAI, custom). v0.2 adds a client-side OmniRouter loop:
8 OpenAI tool-calling tools (image generation/editing, TTS,
transcription, vision, embed, rerank, route_to_chat) dispatched
through hal0 from any chat slot whose model carries the `tool-calling`
label. The set is filtered per request — a tool only appears in the
prompt if its target slot is enabled and its model carries the
required labels.

**Reliability bar.** Atomic env file writes. Schema-validated TOML.
Structured error envelopes (`{"error":{"code":"slot.not_ready",...}}`).
Cosign-verified self-update with one-flag rollback. Per-type LRU
concurrency with active-inference protection — a serving slot cannot
be evicted out from under a streaming request.

## What's in the box

- **OpenAI-compatible `/v1/*` API** — chat, completions, embeddings,
  rerank, audio transcriptions, audio speech, image generations,
  image edits, models. Drop-in for any OpenAI SDK; point your client
  at `http://localhost:8080/v1` and go.
- **Slots** — each named target in `capabilities.toml` carries a
  Lemonade-vocab `type` (`llm | embedding | reranking | transcription
  | tts | image`), a `device` (`gpu-rocm | gpu-vulkan | cpu | npu`), a
  `model`, plus `enabled` and optional `default`. Six seeded slots
  (`primary`, `embed`, `rerank`, `stt`, `tts`, `img`) plus three NPU
  slots (`agent`, `stt-npu`, `embed-npu`) when FastFlowLM is installed.
  User-added slots via `hal0 slot add NAME --type TYPE --model MODEL`.
- **Lemonade as the unified runtime** — one `lemond` daemon,
  loopback-only on `127.0.0.1:13305` (the canonical proxy target;
  `hal0-api` reverse-proxies `/v1/*` to it). Supervised by
  `hal0-lemonade.service`. Cache + config at `/var/lib/hal0/lemonade/`.
  The hal0 capability layer is the only user-facing inference surface;
  Lemonade is treated as an internal runtime, never exposed off-box.
- **Bundle picker on first run** — `capabilities.toml` ships empty by
  design. The dashboard's first load surfaces four hardware-anchored
  tiers (`hal0-Lite` ≥16 GB / `Default` ≥32 GB / `Pro` ≥64 GB / `Max`
  ≥100 GB) plus the vendor-blessed `LMX-Omni-52B-Halo` kit. Tiers
  that don't fit the detected unified RAM grey out with a tooltip.
  See [ADR-0010](./docs/internal/adr/0010-bundle-picker-no-default-stack.md)
  for the no-silent-default rationale.
- **Hardware-aware probe** — detects GPU / NPU / unified memory,
  writes `/etc/hal0/hardware.json`, surfaces VRAM/RAM fit warnings
  inline in the slot form and the bundle picker.
- **Dispatcher** — registry-aware routing, cold-cache prefetch,
  upstream fallback (OpenRouter, Anthropic, OpenAI, custom OpenAI-shaped
  endpoints). Mix local + remote per-model in one config.
- **Dashboard** — React 18 + TypeScript + Vite + Tailwind 4 UI (v3,
  landed via PR #235) for slot/model management, hardware-aware
  configuration, live logs, and system health. SSE-backed status + log
  tail. Lemonade `/logs/stream` folded into the Journal panel.
  Settings → Lemonade admin panel surfaces the daemon's
  `/internal/config` for inspection. Dark by default.
- **OpenWebUI prewired** — chat at `:3001`, zero config. The installer
  writes `openwebui.env` pointing at the local hal0 API.
- **OmniRouter (8 tools)** — `generate_image`, `edit_image`,
  `text_to_speech`, `transcribe_audio`, `analyze_image` (vision),
  `embed_text`, `rerank_documents`, `route_to_chat`. Dispatched
  client-side from chat slots; dynamically filtered per request.
- **Image generation, day one** — `POST /v1/images/generations` via
  `sd-cpp` (Lemonade-bundled). Bundle manifests pre-pick SDXL Turbo /
  Flux-2-Klein-9B as fits the tier.
- **First-run wizard + bundle picker** — bundle pick (or "Skip —
  configure manually") → models download in background.
- **Atomic self-update with rollback** — `hal0 update --channel
  stable|nightly`. Cosign-verified tarballs swap a
  `/usr/lib/hal0/current` symlink; `--rollback` reverts.
- **One-line install** — `curl -fsSL https://hal0.dev/install.sh | bash`
  (`--models-dir=PATH` or `HAL0_MODELS_DIR=PATH` redirects model pulls
  off `/var/lib/hal0/models`). The bootstrap fetches the release
  manifest, sha256-verifies the tarball, cosign-verifies the signature
  against the workflow OIDC identity, then hands off to
  [`installer/install.sh`](./installer/install.sh). v0.1.x installs
  are detected and refused with explicit backup/wipe instructions;
  see [`CHANGELOG.md`](./CHANGELOG.md) for the v0.2 + v0.3 upgrade
  notes and the `hal0 registry import` recovery path.
- **One-line Proxmox VE install** — on a Proxmox host, `bash -c "$(curl
  -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)"`
  creates an unprivileged Debian 13 LXC and runs the standard bootstrap
  inside it. `--advanced` opens whiptail prompts; every parameter has
  an env-var override (`CTID`, `RAM_MB`, `STORAGE`, …). Hardware-agnostic
  — Strix Halo passthrough still requires the privileged-LXC recipe.
  See [`scripts/proxmox-ve/README.md`](./scripts/proxmox-ve/README.md).

### Bundled agents (v0.3)

hal0 ships **two MCP servers** and **one bundled agent app**. The MCP
servers (`/mcp/admin` for slot / model / capability / config / hardware
/ log admin and `/mcp/memory` for Cognee-backed long-term memory) are
mounted as FastMCP Streamable-HTTP apps at `/mcp/admin` and
`/mcp/memory`, reachable by any MCP-speaking client — Claude Code,
future RAG services, external scripts. The bundled agent for v0.3 is
**Hermes-Agent** (service shape, installed via the hal0-owned
`hal0-hermes` wrapper around upstream `hermes`). Install via the
first-run wizard or `hal0 agent install hermes`. Capital-D destructive
MCP calls (`model_pull`, `slot_delete`, `config_write`, etc.) gate
through a header bell + inbox modal in the dashboard, with CLI parity
via `hal0 agent approvals {list,approve,deny}`. See
[docs/api/mcp.md](./docs/api/mcp.md) and
[docs/api/agents.md](./docs/api/agents.md).

Clients identify themselves to hal0 via the `X-hal0-Agent` request
header, sourced from the `HAL0_AGENT_ID` env var in the wrapper
script. The CLI, the Hermes provisioner, and the bundled agent
templates all set this header today.

> **🚧 Coming in v0.3.0-alpha.2** — *the FastAPI side still reads
> legacy `Authorization: Bearer` and falls back to `"anonymous"`;
> server-side `X-hal0-Agent` adoption + the `MCPAuthMiddleware →
> Identity` rename land as post-alpha.1 follow-ups, tracked under the
> auth-removal close-out.*

> **🚧 Coming in v0.3.0-alpha.2** — *Per-agent private memory
> namespacing is gated on issue #317: `/api/memory/add` currently
> forces `dataset` to `shared` regardless of the incoming
> `private:*` scope.*

The `pi-coder` agent path remains in the repository for v0.3.x
reactivation after the Lemonade UX polish but is not promoted in the
v0.3 dashboard or first-run wizard.

## Backends

hal0 routes every inference workload through Lemonade Server. The
table below lists the Lemonade recipe each capability uses, plus the
hardware device it targets.

| Capability    | Lemonade recipe        | Device                  | Notes                                                       |
|---------------|------------------------|-------------------------|-------------------------------------------------------------|
| chat + embed + rerank | `llamacpp`     | Vulkan / ROCm / CPU     | `--parallel 1 --threads N` mandatory in `lemond` config     |
| chat + STT + embed (NPU) | `flm:npu`   | AMD XDNA (opt-in)       | FLM trio: one process, `--asr 1 --embed 1`, ~2 GB NPU mem  |
| transcription | `whisper.cpp`          | Vulkan / CPU            | Replaces v0.1 Moonshine for the CPU/GPU path                |
| TTS           | `kokoro:cpu`           | CPU                     | `[CPU]` chip + tooltip in dashboard; GPU TTS is a v0.3 stream |
| image         | `sd-cpp`               | ROCm / Vulkan           | SD Turbo / Flux-2-Klein-9B selectable per bundle tier       |

The NPU path is opt-in: a FastFlowLM `.deb` install (manual on Linux —
the Lemonade auto-installer is Windows-only) unlocks the three FLM
slots. With FLM present, the bundle picker's Pro and Max tiers
surface the trio as a toggle; the trio only auto-enables when a
bundled agent is being installed in the same first-run flow.

The legacy hal0 toolbox containers (`hal0-toolbox-vulkan` / `rocm` /
`flm` / `moonshine` / `kokoro` / `comfyui`) and the `hal0-slot@.service`
template that supervised them were retired in v0.2; v0.3 keeps the
single `lemond` daemon as the inference floor. See
[ADR-0008](./docs/internal/adr/0008-lemonade-adoption.md) for the
migration rationale.

## Hardware

Linux + systemd is the only hard requirement
([`installer/install.sh:86`](./installer/install.sh)). macOS and Windows
are not in scope for v1.

| Tier            | Hardware                                                                  | Status |
|-----------------|---------------------------------------------------------------------------|--------|
| **First-class** | AMD Ryzen AI Max+ 395 ("Strix Halo") with iGPU + XDNA NPU + 128 GB unified | Reference deployment. All published perf numbers come from this box. |
| **First-class** | AMD Ryzen AI Max 385 / 390 with 64 GB unified                              | Same path; small + mid tiers fit, 70B Q4 with shorter context. |
| **Supported**   | NVIDIA RTX 30/40/50 (10–32 GB)                                            | CUDA-backed `llamacpp` via Lemonade. Same slot lifecycle, dedicated VRAM instead of UMA. |
| **Supported**   | AMD Radeon RX 7000 / discrete (16–24 GB)                                  | ROCm via Lemonade's `llamacpp` recipe; Vulkan fallback. |
| **Fallback**    | CPU-only x86_64                                                            | Lemonade's CPU path. Usable for tiny models / smoke tests, not the headline experience. |

## Project layout

```
hal0/
├── src/hal0/         # Python package (FastAPI API + capability layer + Lemonade client + CLI)
│   ├── agents/      # Hermes bootstrap + agent install / approvals / identity
│   ├── lemonade/     # HTTP client + catalog_sync + metrics_shim + log_proxy
│   ├── mcp/          # bundled MCP servers (hal0-admin, hal0-memory)
│   ├── memory/       # Cognee adapter for hal0-memory
│   └── omni_router/  # client-side tool-calling loop + tool definitions
├── ui/               # React 18 + TypeScript + Vite + Tailwind 4 dashboard (v3)
├── installer/        # install.sh (writes /var/lib/hal0/lemonade/config.json + hal0-lemonade.service)
├── tests/            # pytest suite (α unit, β integration, γ release-gate)
├── docs/             # user docs (mirror of hal0.dev/docs); dev docs under docs/internal/
└── PLAN.md           # roadmap (current cut: v0.3; path to v1.0)
```

Models live under `/var/lib/hal0/models/<recipe>/<capability>/` — the
canonical app-visible tree Lemonade's `extra_models_dir` points at.
Per-leaf symlinks redirect to `/mnt/ai-models/` when a separate
storage volume is in use. See PLAN.md §6.1 for the layout.

## Quick start (development)

```sh
# backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
hal0 serve --reload

# frontend
cd ui
npm install
npm run dev
```

The API lives at `http://127.0.0.1:8080`, the dashboard at the Vite dev
server URL (usually `http://127.0.0.1:5173`).

Run `hal0 doctor` any time to re-check pre-flight (systemd / python /
disk / ports). `hal0 model pull <ref>` streams models from Hugging Face
into the registry under the `user.*` namespace, and `hal0 uninstall
[--keep-data]` tears down a running install (thin wrapper over
`installer/uninstall.sh`).

### Auth posture

Per [ADR-0012](./docs/internal/adr/0012-remove-auth-and-caddy.md) (supersedes
ADR-0001), hal0 ships with **no built-in auth and no bundled TLS**.
`hal0-api` binds `0.0.0.0:8080` and treats every request as
authenticated by virtue of network reachability — the right posture
for a homelab appliance on a trusted LAN.

If hal0 is reachable from anything you don't physically control,
front it with an upstream reverse proxy that owns auth + TLS — Traefik,
nginx, Cloudflare Tunnel, or your own preferred edge. See
[`docs/operate/auth.mdx`](./docs/operate/auth.mdx) for example configs
of each.

### Proxmox integration (optional)

If hal0 runs inside a Proxmox LXC, the container only sees its own
cgroup slice of memory — other tenants, ZFS ARC, and the host kernel
draw from the same physical DIMMs as GPU GTT but are invisible from
inside. To surface that, drop a read-only `PVEAuditor` API token into
the dashboard's Settings → "Proxmox integration" panel. Once saved,
the unified-memory bar swaps to the physical host's DIMM total and
adds a muted "Proxmox host" segment for other-tenant + kernel
pressure. Token is sensitive and stored 0600 at
`/etc/hal0/proxmox.json`; the API never echoes it back. Bare-metal and
VM installs leave the panel off and the dashboard stays quiet.

## Roadmap

No dates — items are direction. The closer to the left, the closer to
running on your box. Full version at [hal0.dev/roadmap](https://hal0.dev/roadmap).

### Shipped (v0.3.0-alpha.1, 2026-05-24)

- **Auth + Caddy removed** — `hal0-api` binds `0.0.0.0:8080` open;
  no Bearer-token store, no first-run claim OTP, no bundled TLS.
  ~6,000 lines deleted across backend, frontend, tests, installer,
  packaging. Front with an upstream reverse proxy (Traefik / nginx /
  Cloudflare Tunnel) for non-LAN deployments — see
  [ADR-0012](./docs/internal/adr/0012-remove-auth-and-caddy.md) and
  [`docs/operate/auth.mdx`](./docs/operate/auth.mdx).
- **Dashboard v3** — React 18 + TypeScript + Vite + Tailwind 4
  (PR #235). `/chat` and `/dashboard` split out; slots overview
  mirrors snapshot/memmap/throughput sidebars onto `/slots`.
- **Hermes-Agent first-run bootstrap** — bundled service-shape agent
  with env probe, model auto-wiring, MCP-memory connection, and
  identity-card publishing to the dedicated `agents` Cognee dataset
  ([ADR-0011](./docs/internal/adr/0011-agent-identity-cards.md)).
  Install via `hal0 agent install hermes`.
- **Carried forward from v0.2:** Lemonade unified runtime, FLM trio
  NPU packing, OmniRouter client-side tool-calling, first-run bundle
  picker, per-type LRU concurrency, Lemonade admin panel, Journal
  panel, metrics shim, `hal0 registry import`, MCP admin + Cognee
  memory MCP.

### In flight / soon (v0.3.0-alpha.2 and v0.3.x)

- **Dashboard v3 mock-fallback close-out** — remaining panels still
  pull from the seed `HAL0_DATA.*` blob (issues #213–#340).
- **Identity rename + server-side `X-hal0-Agent` read** —
  `MCPAuthMiddleware → Identity` rename and the FastAPI-side switch
  away from legacy `Authorization: Bearer`. Today the client wiring
  ships; the server still falls back to `"anonymous"`.
- **Per-agent private memory** — `/api/memory/add` currently forces
  `dataset = "shared"` regardless of incoming `private:*` (issue
  #317).
- **GPU-accelerated TTS** — kokoro-vulkan or successor; closes the
  `[CPU]` chip on the voice slot card.
- **KV% for GPU slots** — Lemonade upstream or hal0-built
  llama-server swap-in; v0.3.0-alpha.1 still ships `—`.
- **Advanced memory + MCP client side** — Cognee graph extraction
  + Memify pipeline ([ADR-0014](./docs/internal/adr/0014-cognee-graph-extraction-model-gate.md)),
  per-agent allow-listed external MCP clients
  ([ADR-0013](./docs/internal/adr/0013-mcp-client-allow-list.md)),
  federated memory across local + remote sources.
- **Benchmarks & presets UI** — in-dashboard tok/s + latency runs,
  plus curated loadout presets you can flash onto a fresh install.
- **AUR PKGBUILD & Ubuntu PPA** — native distro packages on top of
  the install script; pacman and apt as first-class install paths.
- `hal0.local` mDNS auto-discovery polish.
- Light mode toggle.

### Exploring (v1.x +)

- **Multi-host federation** — a slot mesh across LAN boxes — primary
  on the Strix Halo, embed on the workstation, all behind one `/v1/*`
  surface
- **Fine-tune & LoRA hot-swap** — attach and rotate LoRAs against a
  warm base model without unloading the underlying weights
- **Per-model rate limits & budgets** — cost-style accounting for
  local inference — cap a chatty agent without taking the whole box down
- **Voice mode end-to-end** — Lemonade's `/realtime` WebSocket +
  agent loop stitched into a hands-free streaming conversation
- **ChatOps adapters** — Slack and Matrix bridges as extensions —
  talk to hal0 from the rooms you already live in

## License

Apache 2.0. See [`LICENSE`](./LICENSE).

## Contributing

The contribution model is still being decided
([`PLAN.md`](./PLAN.md) §16). File issues for discussion; PRs aren't
being accepted from outside contributors yet. See
[`CONTRIBUTING.md`](./CONTRIBUTING.md) for the test tiers and the
eventual flow.
