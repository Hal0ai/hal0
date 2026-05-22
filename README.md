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
Slots, dispatcher, hardware probe, prewired chat UI, signed self-update.
One command installs the lot.

It is not another llama-server wrapper. Every workload is a real
systemd-managed slot with a typed lifecycle, the API surface covers
chat *and* embed *and* rerank *and* STT *and* TTS *and* image gen, and
the dashboard is for operating the box — not for chatting with it.

```sh
curl -fsSL https://hal0.dev/install.sh | bash
```

> **Status:** **v0.1.0-alpha**, shipping. The cosign-keyless-OIDC
> release pipeline is wired end-to-end (signed tarball + `.crt` +
> manifest, self-verified before publish); the install command actually
> installs. Expect rough edges: APIs may shift, slot lifecycle bugs are
> still being shaken out, and we don't promise upgrade compatibility
> across `0.1.x` alpha tags. See [`PLAN.md`](./PLAN.md) §1 for what
> ships now and the path to v1.0.

## Why hal0

**Strix Halo native.** The probe is UMA-aware. The slot-fit warnings
size against the real unified pool, not a BAR carve-out. The XDNA NPU
has a first-class provider (FLM) that's only surfaced in the picker
when the hardware *and* the toolbox image are both present. 128 GB
Ryzen AI Max+ 395 is the reference deployment — not a hopeful port.

**Concurrent chat + embed + voice + image.** Five built-in slot
classes (`primary`, `embed`, `stt`, `tts`, `img`), each a real
systemd-managed process on its own port, run at the same time. On
Strix Halo, primary + embed concurrent measures **~258 tok/s** with
**<200 ms** dispatch — both slots hot, iGPU at ~9 GB GTT.

**Dispatcher with single-flight + structured decisions.**
Registry-aware routing across local slots *and* external upstreams
(OpenRouter, Anthropic, OpenAI, custom). Cold-cache prefetch with
request coalescing — a thundering herd of identical prefetches
becomes one HTTP call. Every routing decision logged as a structured
breadcrumb.

**Reliability bar.** Atomic env file writes. Schema-validated TOML.
Structured error envelopes (`{"error":{"code":"slot.not_ready",...}}`).
Adaptive cold-boot health probes that demand a real `/v1/chat/completions`
round-trip, not just a populated `/v1/models` list. Cosign-verified
self-update with one-flag rollback. The things you'd otherwise script
around `llama-server` by hand.

## What's in the box

- **OpenAI-compatible `/v1/*` API** — chat, completions, embeddings,
  rerank, audio transcriptions, audio speech, image generations,
  models. Drop-in for any OpenAI SDK; point your client at
  `http://localhost:8080/v1` and go.
- **Slots** — each inference workload runs in its own systemd-managed
  container with a known port, a typed lifecycle
  (`offline → pulling → starting → warming → ready → serving ↔ idle
  → unloading`), and a real health probe. State is persisted to
  `state.json` and streamed over SSE so the dashboard reflects reality,
  not `systemctl is-active` snapshots.
- **Capability slots overlay** — thin UX layer grouping flat slots
  into operator-facing cards (Embed / Voice / Image + NPU backend
  rollup). Selections persist in `/etc/hal0/capabilities.toml`;
  `CapabilityOrchestrator.apply()` reconciles selections against
  `slots/*.toml` on every call — no drift between what you picked and
  what's running.
- **Hardware-aware probe** — detects GPU / NPU / unified memory,
  writes `/etc/hal0/hardware.json`, surfaces VRAM/RAM fit warnings
  inline in the slot form. Picks the right backend automatically;
  you don't pin one by hand.
- **Dispatcher** — registry-aware routing, cold-cache prefetch,
  upstream fallback (OpenRouter, Anthropic, OpenAI, custom OpenAI-shaped
  endpoints). Mix local + remote per-model in one config.
- **Dashboard** — Vue 3 + Tailwind 4 UI for slot/model management,
  hardware-aware configuration, live logs, and system health. SSE-backed
  status + log tail. Dark by default.
- **OpenWebUI prewired** — chat at `:3001`, zero config. The installer
  writes `openwebui.env` pointing at the local hal0 API.
- **Image generation, day one** — `POST /v1/images/generations`
  served by a bundled ComfyUI provider on ROCm. Curated SDXL Turbo /
  SD 1.5 / Flux Schnell with license badges; the `img` slot runs
  inside the same lifecycle as everything else.
- **First-run wizard** — 8 linear steps from password through hardware,
  primary model, capabilities, optional HF token, license aggregation,
  parallel pulls, done.
- **Atomic self-update with rollback** — `hal0 update --channel
  stable|nightly`. Cosign-verified tarballs swap a
  `/usr/lib/hal0/current` symlink; `--rollback` reverts. Slot units
  survive API restarts.
- **One-line install** — `curl -fsSL https://hal0.dev/install.sh | bash`
  (`--models-dir=PATH` or `HAL0_MODELS_DIR=PATH` redirects HuggingFace
  pulls off `/var/lib/hal0/models`). The bootstrap fetches the release
  manifest, sha256-verifies the tarball, cosign-verifies the signature
  against the workflow OIDC identity, then hands off to
  [`installer/install.sh`](./installer/install.sh).

## Backends

| Backend     | Hardware                | Use case                           |
|-------------|-------------------------|------------------------------------|
| llama.cpp   | Vulkan (default) / ROCm | chat, embed, rerank, vision        |
| FLM         | AMD XDNA NPU (opt-in)   | chat + embed (ASR multiplex available via `defaults.load_asr`; Moonshine is the default STT) |
| Moonshine   | CPU                     | STT (`/v1/audio/transcriptions`)   |
| Kokoro      | CPU / Vulkan            | TTS (`/v1/audio/speech`)           |
| ComfyUI     | ROCm                    | image gen (`/v1/images/generations`) |

The NPU/FLM column is populated at runtime from `flm list -j` inside
the FLM toolbox image — hal0 doesn't pretend a GGUF runs on the NPU,
and the dashboard's model picker narrows the backend dropdown to the
backends a given model can actually serve. (`hal0 capabilities migrate`
cleans up persisted selections that pre-date this check.)

## Hardware

Linux + systemd is the only hard requirement
([`installer/install.sh:86`](./installer/install.sh)). macOS and Windows
are not in scope for v1.

| Tier            | Hardware                                                                  | Status |
|-----------------|---------------------------------------------------------------------------|--------|
| **First-class** | AMD Ryzen AI Max+ 395 ("Strix Halo") with iGPU + XDNA NPU + 128 GB unified | Reference deployment. All published perf numbers come from this box. |
| **First-class** | AMD Ryzen AI Max 385 / 390 with 64 GB unified                              | Same path; small + mid tiers fit, 70B Q4 with shorter context. |
| **Supported**   | NVIDIA RTX 30/40/50 (10–32 GB)                                            | CUDA-backed llama.cpp. Same slot lifecycle, dedicated VRAM instead of UMA. |
| **Supported**   | AMD Radeon RX 7000 / discrete (16–24 GB)                                  | Vulkan today; ROCm toolbox image on the build list. |
| **Fallback**    | CPU-only x86_64 / Vulkan-CPU                                              | CI runs Qwen 0.5B here. Usable for tiny models / smoke tests, not the headline experience. |

## Project layout

```
hal0/
├── src/hal0/         # Python package (FastAPI API + slot manager + CLI)
├── ui/               # Vue 3 + Tailwind 4 dashboard
├── installer/        # install.sh (systemd unit templates live in packaging/systemd/)
├── tests/            # pytest suite (α unit, β integration, γ release-gate)
├── docs/             # user docs (mirror of hal0.dev/docs); dev docs under docs/internal/
└── PLAN.md           # roadmap (current cut: v0.1.0-alpha; path to v1.0)
```

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
docker / disk / ports). `hal0 model pull <ref>` streams models from
Hugging Face into the registry, and `hal0 uninstall [--keep-data]`
tears down a running install (thin wrapper over
`installer/uninstall.sh`).

### Auth posture

Per [ADR-0001](./docs/internal/adr/0001-collapse-edge-auth-into-fastapi.md), all
auth lives in FastAPI. As of v0.1.0-alpha, a fresh install **starts locked** —
the dashboard, `/v1/*`, and every admin route reject anonymous requests
with `401 auth.required`. Only the first-run wizard claim paths
(`/api/install/*` and `POST /api/auth/password`) stay reachable, and
only while the installer's `.first-run.lock` file is present on disk.

The installer prints a one-time OTP in the post-install summary; the
wizard's "Set a password" step asks for it before minting the owner
credential. Once the password is set the lockfile is deleted and the
claim window closes — from then on every request needs a session
cookie (browser) or Bearer token (programmatic client).

To opt back into the trusted-LAN open posture (single-user dev boxes
only), set `HAL0_AUTH_DISABLED=1` in `/etc/hal0/api.env` and restart
`hal0-api`. The legacy `HAL0_AUTH_ENABLED=0` falsy form is still
honoured for compatibility.

The default install runs Caddy in front for TLS termination (Caddy's
internal CA on `.local` hosts, Let's Encrypt for real DNS-resolvable
hostnames). Use `--no-tls` to skip Caddy and front hal0 with your own
reverse proxy. See [`installer/README.md`](./installer/README.md) for
the full flow.

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

### Shipped (v0.1.0-alpha)

- OpenAI-compatible `/v1/*` API (chat / completions / embeddings /
  rerank / transcriptions / speech / images / models)
- Slot lifecycle state machine — atomic transitions, persisted +
  SSE-streamed
- Five-provider stack: llama.cpp, FLM (NPU), Moonshine, Kokoro, ComfyUI
- Hardware-aware probe with UMA awareness for Strix Halo
- Capability slots overlay + orchestrator drift reconcile
- Dispatcher with single-flight + cold-cache prefetch + upstream fallback
- Bundled OpenWebUI on `:3001`, zero config
- First-run wizard (8 linear steps; conditional HF-token step)
- Image generation via ComfyUI — curated SDXL Turbo / SD 1.5 / Flux Schnell
- FLM NPU provider live with self-contained toolbox image
- Caddy + basic auth + HTTPS, one flag (`--auth=basic`)
- Cosign-keyless self-update with rollback, stable + nightly channels
- Installer overhaul: preflight + hardware cards + `hal0 doctor` +
  ERR-trap recovery hints + live-hello + QR + reachability finish
- `--models-dir=PATH` install flag with auto-scan on first boot
- Per-slot live metrics (`GET /api/slots/metrics`)
- Optional Proxmox host-pressure integration (read-only `PVEAuditor` token)

### Soon (v0.2)

- **Hugging Face model pulls** — `POST /api/models/{id}/pull` is wired
  in the picker + first-run wizard for v1.0; FLM tags need an
  FLM-aware pull path since they don't carry an HF repo + filename
- **Agent management & integration** — first-class agents stored on
  hal0 with conversation orchestration and deterministic tool-use
  loops. The platform for agents to run on, not just chat with
- **Unified memory system** — persistent, federated memory across
  local slots and (optionally) external models. Mem0-style API with
  sources, search, and scoping
- **MCP support — host and server** — hal0 speaks Model Context
  Protocol both directions. Compose tools across local slots and
  external MCP services; discover them from the dashboard
- **Extensions framework** — third-party apps packaged as hal0
  extensions: systemd unit + dashboard tile + healthcheck. BYO app,
  lifecycle-managed
- **Benchmarks & presets UI** — in-dashboard tok/s + latency runs,
  plus curated loadout presets you can flash onto a fresh install
- **AUR PKGBUILD & Ubuntu PPA** — native distro packages on top of
  the install script; pacman and apt as first-class install paths
- `hal0.local` mDNS auto-discovery
- Light mode toggle

### Exploring (v1.x +)

- **Multi-host federation** — a slot mesh across LAN boxes — primary
  on the Strix Halo, embed on the workstation, all behind one `/v1/*`
  surface
- **Fine-tune & LoRA hot-swap** — attach and rotate LoRAs against a
  warm base model without unloading the underlying weights
- **Per-model rate limits & budgets** — cost-style accounting for
  local inference — cap a chatty agent without taking the whole box down
- **Voice mode end-to-end** — Moonshine + agent loop + Kokoro stitched
  into a hands-free streaming conversation, gated through the slot
  lifecycle
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
