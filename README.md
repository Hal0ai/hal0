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
Hardware-aware slots, prewired chat UI, signed self-update. One command
installs the lot.

Every inference workload runs as its own **podman container** under a
`hal0-slot@<name>.service` unit. `hal0-api` on `:8080` is the sole
control plane — it owns slot state machines, dispatches OpenAI-compatible
`/v1/*` requests to the right slot port, and serves the dashboard. No
shared inference daemon; no extra process to babysit.

```sh
curl -fsSL https://hal0.dev/install.sh | bash
```

> **Status:** **v0.5.0-alpha.1** — container-runtime era. Each slot
> (`chat`, `embed`, `rerank`, `stt`, `tts`, `img`, NPU trio) runs as
> a dedicated podman container (`hal0-slot@<name>.service`). Slot
> definitions live in `/etc/hal0/slots/<name>.toml`; backend profiles
> in `/etc/hal0/profiles.toml`; the model catalog is
> `registry.toml` — the single source of truth for every HuggingFace
> coordinate and SHA-256 digest. First run lands a **bundle picker**
> (`hal0-Lite` / `Default` / `Pro` / `Max` + `LMX-Omni-52B-Halo`) —
> `capabilities.toml` ships empty by design, no silent default. See
> [`PLAN.md`](./PLAN.md) §1 for what ships now and the path to v1.0.

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

**One control plane, six modalities, dedicated containers.** Each
slot runs in isolation — chat, embed, rerank, STT, TTS, and image
generation each get their own podman container with its own image,
flags, port, and lifecycle. Concurrent workloads don't share a process
pool; they share only the GPU through the arbiter. Slot state is
persisted to `/var/lib/hal0/slots/<name>/state.json` and streamed via
SSE. Chat + embed alone sustains ~258 tok/s on Strix Halo at the
reference ROCm FP4 loadout.

**NPU multi-role via the FLM trio.** On Strix Halo with FastFlowLM
installed, a single FLM process inside the `hal0-toolbox-flm` container
hosts chat + transcription + embedding concurrently on the one AMDXDNA
hardware context — ~2 GB NPU memory, gemma3:1b at 40 tok/s +
Whisper-V3-Turbo + Embedding-Gemma all coresident. hal0 exposes this
as three slots (`agent`, `stt-npu`, `embed-npu`); the `npu.toml`
`[npu]` table toggles ASR and embed. The host-side FLM `.deb` is
installed for device-sanity probes only — inference runs in the
container. See [ADR-0009](./docs/internal/adr/0009-flm-trio-npu-packing.md)
for why.

**Dispatcher with single-flight + OmniRouter tool-calling.** Registry-aware
routing across local slots *and* external upstreams (OpenRouter,
Anthropic, OpenAI, custom). The client-side OmniRouter loop provides
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
  `type` (`llm | embedding | reranking | transcription | tts | image`),
  a `device` (`gpu-rocm | gpu-vulkan | cpu | npu | img`), a `model`,
  plus `enabled` and optional `default`. Six seeded slots (`chat`,
  `embed`, `rerank`, `stt`, `tts`, `img`) plus three NPU slots
  (`npu`, `stt-npu`, `embed-npu`) when FastFlowLM is installed.
  User-added slots via `hal0 slot create NAME --type TYPE --model MODEL`.
  Slots refer to a **profile** in `/etc/hal0/profiles.toml` that pins
  the container image + flag bundle for that backend.
- **Container runtime** — each slot runs as its own podman container
  under `hal0-slot@<name>.service`. `hal0-api` on `:8080` is the
  control plane; slot containers bind loopback ports (8081–8099 + fixed
  seeds). No shared inference daemon. See
  [docs/operate/container-runtime.md](./docs/operate/container-runtime.md).
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
- **Dashboard** — React 18 + Vite UI for slot/model management,
  hardware-aware configuration, live logs, system health, and a
  built-in chat page (with popout window + reasoning toggle).
  SSE-backed status + log tail. Journal panel streams per-slot
  container logs via journald. Dark by default.
  The slots page splits into Inference | Image Gen tabs: the Image-Gen
  tab operates the ComfyUI container (live GTT/RAM gauges, queue
  depth, model inventory) with a gated inference ⇄ generation iGPU
  switchover behind a blast-radius confirm.
- **OpenWebUI prewired** — chat at `:3001`, zero config. The installer
  writes `openwebui.env` pointing at the local hal0 API.
- **OmniRouter (8 tools)** — `generate_image`, `edit_image`,
  `text_to_speech`, `transcribe_audio`, `analyze_image` (vision),
  `embed_text`, `rerank_documents`, `route_to_chat`. Dispatched
  client-side from chat slots; dynamically filtered per request.
- **Image generation, day one** — `POST /v1/images/generations` via
  ComfyUI in the `img` slot container (ROCm). Bundle manifests
  pre-pick SDXL Turbo / Flux-2-Klein-9B as fits the tier.
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
  [`installer/install.sh`](./installer/install.sh).
- **One-line Proxmox VE install** — on a Proxmox host, `bash -c "$(curl
  -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)"`
  creates an unprivileged Debian 13 LXC and runs the standard bootstrap
  inside it. `--advanced` opens whiptail prompts; every parameter has
  an env-var override (`CTID`, `RAM_MB`, `STORAGE`, …). Hardware-agnostic
  — Strix Halo passthrough still requires the privileged-LXC recipe.
  See [`scripts/proxmox-ve/README.md`](./scripts/proxmox-ve/README.md).

### Bundled agents

hal0 ships **two MCP servers** and **one bundled agent app**. The MCP
servers (`/mcp/admin` for slot / model / capability / config / hardware
/ log admin and `/mcp/memory` for Cognee-backed long-term memory) are
reachable by any MCP-speaking client — Claude Code, future RAG
services, external scripts. The bundled agent is single-pick at install:
`pi-coder` (CLI shape, installed from `Hal0ai/pi-mono` fork via
`@earendil-works/pi-coding-agent` on npm) or `Hermes-Agent` (service
shape, installed via the hal0-owned `hal0-hermes` wrapper; connects to
`hal0-api` via `HAL0_INFERENCE_BASE=http://127.0.0.1:8080`). Pick one
via the first-run wizard or `hal0 agent install <name>`; swap
atomically with `--switch`. Capital-D destructive MCP calls
(`model_pull`, `slot_delete`, `config_write`, etc.) gate through a
header bell + inbox modal in the dashboard, with CLI parity via
`hal0 agent approvals {list,approve,deny}`. See
[docs/mcp/overview.md](./docs/mcp/overview.md) and
[docs/agents/overview.md](./docs/agents/overview.md).

## Backends

Each capability runs in its own container, supervised by
`hal0-slot@<name>.service`. The profile in `/etc/hal0/profiles.toml`
pins the container image and flag bundle for each backend.

| Capability               | Profile / image                  | Device              | Notes                                                        |
|--------------------------|----------------------------------|---------------------|--------------------------------------------------------------|
| chat + embed + rerank    | `rocm` / `rocm-mtp` / `vulkan` | ROCm / Vulkan / CPU | ROCm FP4 fork baked into the image; MTP via `--spec-type draft-mtp` |
| chat + STT + embed (NPU) | `flm` (`hal0-toolbox-flm`)  | AMD XDNA (opt-in)   | FLM trio: one container, `[npu] asr/embed` toggles, ~2 GB NPU mem |
| transcription            | whisper.cpp in toolbox image     | Vulkan / CPU        | `stt` slot                                                   |
| TTS                      | `tts` (`hal0-toolbox-kokoro`) | CPU             | `[CPU]` chip + tooltip in dashboard                          |
| image                    | `comfyui` (`hal0-toolbox-comfyui`) | ROCm              | Exclusive GPU via arbiter; SD Turbo / Flux-2-Klein-9B        |

The NPU path is opt-in: the installer places a FastFlowLM `.deb` on
the host for device-sanity probes (`flm validate`); inference runs
inside the `hal0-toolbox-flm` container image. With FLM present, the
bundle picker's Pro and Max tiers surface the trio as a toggle.

For the container-runtime operator reference — service layout, slot
TOML fields, profiles, GPU arbiter, and day-2 commands — see
[docs/operate/container-runtime.md](./docs/operate/container-runtime.md).

## Hardware

Linux + systemd is the only hard requirement
([`installer/install.sh:86`](./installer/install.sh)). macOS and Windows
are not in scope for v1.

| Tier            | Hardware                                                                  | Status |
|-----------------|---------------------------------------------------------------------------|--------|
| **First-class** | AMD Ryzen AI Max+ 395 ("Strix Halo") with iGPU + XDNA NPU + 128 GB unified | Reference deployment. All published perf numbers come from this box. |
| **First-class** | AMD Ryzen AI Max 385 / 390 with 64 GB unified                              | Same path; small + mid tiers fit, 70B Q4 with shorter context. |
| **Supported**   | NVIDIA RTX 30/40/50 (10–32 GB)                                            | CUDA-backed llama-server in the `vulkan` container profile. Same slot lifecycle, dedicated VRAM instead of UMA. |
| **Supported**   | AMD Radeon RX 7000 / discrete (16–24 GB)                                  | ROCm or Vulkan container profiles; same `hal0-slot@<name>` lifecycle. |
| **Fallback**    | CPU-only x86_64                                                            | `vulkan` profile, CPU path. Usable for tiny models / smoke tests, not the headline experience. |

## Project layout

```
hal0/
├── src/hal0/         # Python package (FastAPI API + capability layer + ContainerProvider + CLI)
│   ├── providers/    # ContainerProvider, FLMProvider, ComfyUI, etc.
│   ├── slots/        # slot manager, state machine, GpuArbiter
│   └── omni_router/  # client-side tool-calling loop + tool definitions
├── ui/               # React 18 + TypeScript + Vite + Tailwind 4 dashboard (v3)
├── ui-vue.bak/       # v0.2.1 Vue 3 dashboard preserved verbatim for reference
├── installer/        # install.sh (writes /etc/hal0/, systemd units, hal0-api.service)
│   ├── etc-hal0/     # seed slot TOMLs + profiles.toml
│   └── systemd/      # hal0-agent@ template units
├── tests/            # pytest suite (α unit, β integration, γ release-gate)
├── docs/             # user docs (mirror of hal0.dev/docs); dev docs under docs/internal/
└── PLAN.md           # roadmap + history
```

The model catalog lives at `/var/lib/hal0/registry/registry.toml` —
the single source of truth for HuggingFace coordinates, SHA-256
digests, and curated filenames. Per-leaf symlinks under the models
directory redirect to `/mnt/ai-models/` when a separate storage
volume is in use.

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
disk / ports / NPU probe). `hal0 model pull <ref>` streams models from
Hugging Face into `registry.toml` under the `user.*` namespace, and
`hal0 uninstall [--keep-data]` tears down a running install (thin
wrapper over `installer/uninstall.sh`).

Useful day-2 commands:

```sh
# service status
systemctl status hal0-api
systemctl list-units 'hal0-slot@*'

# logs
journalctl -fu hal0-api
journalctl -fu 'hal0-slot@*'

# restart a wedged slot container
systemctl restart hal0-slot@chat
```

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

### Shipped (v0.3 / container runtime)

- **Per-slot podman containers** — every inference workload runs in
  its own `hal0-slot@<name>.service` container; `ContainerProvider` +
  `profiles.toml` replace the old single-daemon model
  (epic #652, extraction #687, PRs #688–#726)
- **GpuArbiter exclusive image mode** — ComfyUI gets the iGPU to
  itself; LLM GPU slots pause and resume automatically
- **NPU trio via `hal0-toolbox-flm` container** — FLM trio now runs
  containerised; host FLM `.deb` is probe-only
- **`registry.toml` as sole model catalog** — no secondary runtime
  catalog to sync; `hal0 model` commands and the dashboard are the
  only safe edit paths
- **Slot-state Prometheus + EventBus journal** — per-slot observability
  via journald (`hal0-slot@<name>`) and the dashboard Journal panel
- **FLM trio NPU packing** — chat + ASR + embed coresident on one
  AMDXDNA hardware context; toggle via `[npu]` in the slot TOML
- **OmniRouter client-side tool-calling** — 8 tools, dynamic
  per-request filtering, `route_to_chat` cross-slot delegation
- **First-run bundle picker** — `hal0-Lite` / `Default` / `Pro` /
  `Max` + `LMX-Omni-52B-Halo`, hardware-anchored, no silent default
- **`hal0 registry import`** — one-shot v0.1.x → v0.3 registry
  recovery from a backup tarball
- Carried forward: OpenAI-compatible `/v1/*`, portable hardware
  probe, capability slots + orchestrator, dispatcher with
  single-flight + cold-cache prefetch, bundled OpenWebUI on `:3001`,
  cosign-keyless self-update with rollback, bundled agents
  (pi-coder / Hermes-Agent) + MCP admin + Cognee memory MCP

### Soon

- **GPU-accelerated TTS** — kokoro-vulkan or successor; closes the
  `[CPU]` chip on the voice slot card
- **Advanced memory** — Cognee graph + Memify pipeline enabled, MCP
  client side of hal0 (agents reach external MCP servers), federated
  memory across local + remote sources
- **Benchmarks & presets UI** — in-dashboard tok/s + latency runs,
  plus curated loadout presets you can flash onto a fresh install
- **AUR PKGBUILD & Ubuntu PPA** — native distro packages on top of
  the install script; pacman and apt as first-class install paths
- `hal0.local` mDNS auto-discovery polish
- Light mode toggle

### Exploring (v1.x +)

- **Multi-host federation** — a slot mesh across LAN boxes — primary
  on the Strix Halo, embed on the workstation, all behind one `/v1/*`
  surface
- **Fine-tune & LoRA hot-swap** — attach and rotate LoRAs against a
  warm base model without unloading the underlying weights
- **Per-model rate limits & budgets** — cost-style accounting for
  local inference — cap a chatty agent without taking the whole box down
- **Voice mode end-to-end** — agent loop stitched into a hands-free
  streaming conversation
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

This project adheres to the [Contributor Covenant](./CODE_OF_CONDUCT.md)
code of conduct. To report a security vulnerability, see
[`SECURITY.md`](./SECURITY.md).
