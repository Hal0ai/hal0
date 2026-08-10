<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./ui/public/brand/logo-halo-dark.svg">
  <img src="./ui/public/brand/logo-halo-light.svg" alt="hal0" width="220">
</picture>

### Open-source home AI inference platform

**Stop running models from a chat tab.**

[hal0.dev](https://hal0.dev) · [Install](https://hal0.dev/docs/getting-started/install/) · [Docs](https://hal0.dev/docs/) · [Benchmarks](https://hal0.dev/benchmarks) · [Roadmap](https://hal0.dev/#roadmap) · [Discord](https://discord.gg/7M4y6dcUyq)

</div>

---

hal0 turns a Linux box — ideally a Ryzen AI Max+ 395 with 128 GB of unified
memory — into a private, OpenAI-compatible inference appliance. Chat,
completions, embeddings, reranking, transcription, speech and image
generation all answer on one `:8080/v1` surface, on hardware you own, with
no per-token bill.

Every inference workload runs as its own **podman container** under a
`hal0-slot@<name>.service` unit. `hal0-api` on `:8080` is the sole control
plane — it owns slot state machines, dispatches `/v1/*` requests to the right
slot port, and serves the dashboard. No shared inference daemon; no extra
process to babysit.

```sh
curl -fsSL https://hal0.dev/install.sh | sudo bash
```

> **⚡ The installer is the whole setup.** There is no separate first-run
> wizard afterwards. `install.sh` asks where models should live, optionally
> takes a HuggingFace token, seeds every capability slot, downloads the hal0
> brain steward model, and starts the API. When it finishes, open the
> dashboard and assign models to slots.

> **Status: release candidate — `v1.0.0-rc.5`.** v1.0.0 stable is the target
> of the current cut. rc.5 is the validation candidate: it closes every
> defect the rc.4 fresh-install sweep found on real hardware. See
> [`CHANGELOG.md`](./CHANGELOG.md).

## Screenshots

<div align="center">

<table>
<tr>
<td width="50%"><a href="https://hal0.dev/docs/"><img src="https://hal0.dev/screenshots/dashboard-overview.png" alt="hal0 dashboard" width="100%"></a><br><sub><b>Dashboard</b> — unified-memory hero, at-a-glance health, and live slot rows.</sub></td>
<td width="50%"><a href="https://hal0.dev/docs/guides/manage-slots/"><img src="https://hal0.dev/screenshots/slots-inference.png" alt="Slots" width="100%"></a><br><sub><b>Slots</b> — the inference engine: per-slot models, devices, and live telemetry.</sub></td>
</tr>
</table>

<table>
<tr>
<td width="33%"><a href="https://hal0.dev/docs/guides/pull-and-register-models/"><img src="https://hal0.dev/screenshots/models-registry.png" alt="Model registry" width="100%"></a><br><sub><b>Model registry</b> — catalogue with quant chips and pull status.</sub></td>
<td width="33%"><a href="https://hal0.dev/docs/guides/generate-images/"><img src="https://hal0.dev/screenshots/image-gen-comfyui.png" alt="Image generation" width="100%"></a><br><sub><b>Image generation</b> — ComfyUI queue and GPU gauges.</sub></td>
<td width="33%"><a href="https://hal0.dev/docs/guides/enable-memory/"><img src="https://hal0.dev/screenshots/memory-graph.png" alt="Memory graph" width="100%"></a><br><sub><b>Memory</b> — Hindsight facts and graph extraction.</sub></td>
</tr>
<tr>
<td width="33%"><a href="https://hal0.dev/docs/concepts/architecture/"><img src="https://hal0.dev/screenshots/services-page.png" alt="Services page" width="100%"></a><br><sub><b>Services</b> — Open WebUI, ComfyUI, Hermes, Hindsight, n8n with mDNS.</sub></td>
<td width="33%"><a href="https://hal0.dev/docs/concepts/slots/"><img src="https://hal0.dev/screenshots/stacks-tab.png" alt="Stacks" width="100%"></a><br><sub><b>Stacks</b> — declarative slot + profile + model bundles, applied atomically.</sub></td>
<td width="33%"><a href="https://hal0.dev/docs/guides/configure/"><img src="https://hal0.dev/screenshots/settings-page.png" alt="Settings" width="100%"></a><br><sub><b>Settings</b> — full <code>hal0.toml</code> parity with an Advanced section.</sub></td>
</tr>
</table>

<sub>More in the <a href="https://hal0.dev/docs/">docs</a>.</sub>

</div>

## Why hal0

**Point your editor at your own box.** Aim any OpenAI-compatible client at
`http://<box>:8080/v1` — Claude Code, Cline, Continue, an SDK, a cron job.
A dedicated `coder` slot can serve your editor while the `agent` slot serves
chat, each on its own container, model and flag set.

**Retrieval grounded on your data.** Embeddings and reranking are
first-class slots, not an afterthought: `/v1/embeddings` and `/v1/rerank`
run on the same box as chat, and the bundled Hindsight memory engine turns
conversations into recallable facts behind an MCP server.

**Speech and images on the same control plane.** Transcription
(`/v1/audio/transcriptions`), speech (`/v1/audio/speech`) and image
generation (`/v1/images/generations`, `/v1/images/edits`) are the same API,
the same auth posture, the same dashboard — no second stack to operate.

**Not another llama-server wrapper.** Every workload is a real
systemd-managed slot with a typed lifecycle, persisted state
(`/var/lib/hal0/slots/<name>/state.json`), SSE status streaming, per-slot
journald logs and a GPU arbiter that keeps image generation from stealing
the iGPU out from under a streaming completion.

**Strix Halo native, but not Strix-Halo-only.** The probe is UMA-aware on
Strix Halo and falls back to portable parsers (`/proc/cpuinfo`,
`/proc/meminfo`, `lspci`) everywhere else. `platform` on `/api/hardware`
resolves to one of `strix-halo`, `wsl2`, `lxc`, `proxmox-kvm`, `kvm`,
`bare-metal-{nvidia,amd,intel}-gpu` or `bare-metal-cpu-only`, and the
dashboard only labels memory "unified" when it actually is. Slot-fit
warnings size against the real unified pool, not a BAR carve-out.

**NPU multi-role via the FLM trio.** On Strix Halo with FastFlowLM
installed, a single FLM process inside the `hal0-toolbox-flm` container
hosts chat + transcription + embedding concurrently on one AMDXDNA hardware
context — ~2 GB NPU memory, gemma3:1b at ~38.6 tok/s + Whisper-V3-Turbo +
Embedding-Gemma all coresident. hal0 exposes this as one seeded `flm` slot
(chat-first) whose `[npu]` table — or the dashboard's NPU drawer — toggles
ASR and embed on the running `flm serve`. The host-side FLM `.deb` is
installed for device-sanity probes only; inference runs in the container.
See [docs/concepts/strix-halo.mdx](./docs/concepts/strix-halo.mdx).

**An agent that lives on the box.** The hal0-brain steward drives the
platform from the dashboard's top bar, and Hermes/Pi install as real
services prewired to the local API and MCP servers.

**Reliability bar.** Atomic env-file writes. Schema-validated TOML.
Structured error envelopes (`{"error":{"code":"slot.not_ready",...}}`).
Cosign-verified self-update with one-flag rollback. Per-type LRU concurrency
with active-inference protection — a serving slot cannot be evicted out from
under a streaming request. Privileged operations run through an allow-listed
root seam, never a free-form shell.

## Benchmarks

Numbers below are from the public leaderboard at
[hal0.dev/benchmarks](https://hal0.dev/benchmarks) — 26 models measured end
to end on the reference box (AMD Ryzen AI Max+ 395, Radeon 8060S iGPU,
128 GB LPDDR5X, ROCm 7.2.4), `rocm` lane, decode and prefill throughput plus
TTFT, speculative accept rate, memory, watts and temperature per run.

| Model | Params | Quant | Decode (tok/s) | Prefill (tok/s) |
|---|---|---|---|---|
| `qwen3.5-0.8b` | 0.8B | q4 | **169.8** | **6,248** |
| `chadrock3-6-35b-uncensored` | 35B MoE | q4 | 102.1 | 890 |
| `chadrock-35b-ace-saber` | 35B MoE | f16 | 100.5 | 903 |
| `qwen3.6-35b-a3b-crown` | 35B MoE | f16 | 84.4 | 873 |
| `qwopus3-5-4b-coder` | 4B | q4 | 85.0 | 889 |
| `qwen3-4b` | 4B | q4 | 61.9 | 1,849 |
| `gemma-4-26b-a4b` | 26B MoE | q4 | 40.9 | 1,336 |

Concurrency and non-LLM lanes, same box:

| Workload | Measurement |
|---|---|
| Chat + embed, concurrent | ~258 tok/s aggregate |
| NPU chat (FLM trio, gemma3:1b) | ~38.6 tok/s in ~2 GB NPU memory |
| TTS — Kokoro (CPU) | ~0.18 real-time factor |
| TTS — Qwen3-TTS (ROCm) | ~0.48 real-time factor (~2.1× realtime) |

Benchmarking is a first-class subsystem, not a spreadsheet: `hal0 bench
plan|run|results|eval|bundle|upload` drives `llama-bench`/`llama-server`
under a record schema with a dedup identity key, an in-dashboard Benchmarks
page (roster / runs / evals / run-queue), and regression journaling. Results
never leave the box on their own — `hal0 bench bundle` writes a
content-addressed, host-redacted archive you can attach anywhere, and
`hal0 bench upload` publishes one only when you type it. See
[docs/reference/model-roster-benchmark.mdx](./docs/reference/model-roster-benchmark.mdx).

## What's in the box

- **OpenAI-compatible `/v1/*` API** — chat, completions, embeddings,
  rerank, audio transcriptions, audio speech, image generations, image
  edits, models. Drop-in for any OpenAI SDK; point your client at
  `http://localhost:8080/v1` and go.
- **Slots** — each named target carries a `type`
  (`llm | embedding | reranking | transcription | tts | image`), a `device`
  (`gpu-rocm | gpu-vulkan | gpu-cuda | cpu | npu | img`), a `model`, plus
  `enabled` and an optional `default`. Ten curated slots (`agent`, `brain`,
  `coder`, `embed`, `flm`, `img`, `qwen3tts`, `rerank`, `tts`, `utility`)
  are seeded into `/etc/hal0/slots/<name>.toml` on install and each gates on
  its own runtime validation at load time — the `flm` NPU slot simply stays
  grey without FastFlowLM hardware. Add your own with
  `hal0 slot create NAME --type TYPE --model MODEL`. A deliberate
  `hal0 slot delete` is tombstoned, so re-running the installer never
  resurrects it.
- **Model owns its tune** — since v1.0 the *model*, not the slot, owns
  `context_size`, `extra_args`, `chat_template`, `profile` and MTP. A slot's
  `[model].context_size` is a ceiling; a model with no stamped tune inherits
  its profile's template as a floor.
- **Container runtime** — every slot runs as its own podman container under
  `hal0-slot@<name>.service`, bound to a loopback port (8081–8099 + fixed
  seeds). `hal0-api` on `:8080` is the only control plane. See
  [docs/concepts/architecture.mdx](./docs/concepts/architecture.mdx).
- **Provisioning lives in the installer** — `install.sh` is the single
  user-facing entry point: model storage, optional HuggingFace token (on a
  terminal only), curated slot seeds, the `brain` steward pull, an optional
  agent-model offer, and service start. `HAL0_SKIP_SETUP=1` skips first-run
  seeding, `HAL0_SKIP_BRAIN_MODEL=1` skips the brain pull, and
  `HAL0_NONINTERACTIVE=1` suppresses both prompts. See
  [`installer/README.md`](./installer/README.md) for the full step list and
  env-var table.
- **Hardware-aware probe** — detects GPU / NPU / unified memory, writes
  `/etc/hal0/hardware.json`, and surfaces VRAM/RAM fit warnings inline in
  the slot form and during install.
- **Dispatcher** — registry-aware routing, single-flight, cold-cache
  prefetch, and upstream fallback (OpenRouter, Anthropic, OpenAI, Google AI
  Studio, Ollama, custom OpenAI-shaped endpoints). Mix local + remote
  per-model in one config.
- **Dashboard** — React 18 + Vite UI for slot/model management,
  hardware-aware configuration, live logs, system health and a built-in chat
  page (popout window + reasoning toggle). SSE-backed status and log tail; a
  Journal panel streams per-slot container logs from journald. Live
  telemetry — Power & Thermal (GPU clock/temp/power) and per-slot throughput
  — is on by default. The slots page splits into Inference | Image Gen tabs;
  the Image-Gen tab operates the ComfyUI container (live GTT/RAM gauges,
  queue depth, model inventory) with a gated inference ⇄ generation iGPU
  switchover behind a blast-radius confirm.
- **AI Capabilities page** — one settings surface for TTS, STT, embeddings,
  reranking, image generation and the NPU, with per-panel probe-failure
  surfacing instead of a silent grey Save.
- **Stacks** — declarative, runtime-switchable model/slot layouts. A
  `StackConfig` is planned into a change set, applied atomically (with
  rollback) and converged against the live slot set; drift is detected by
  content hash. Stacks export/import via a checksummed `.hal0stack.json`
  envelope, and a live config can be snapshotted back into a Stack.
- **OmniRouter (8 tools)** — `generate_image`, `edit_image`,
  `text_to_speech`, `transcribe_audio`, `analyze_image` (vision),
  `embed_text`, `rerank_documents`, `route_to_chat`. Dispatched client-side
  from any chat slot whose model carries the `tool-calling` label, and
  filtered per request — a tool only appears in the prompt if its target
  slot is enabled and its model carries the required labels.
- **Companion-service management** — `/api/services` is the declarative
  source of truth for OpenWebUI, Hermes, Hindsight, ComfyUI and n8n:
  audit-logged systemd lifecycle actions (start/stop/restart/enable/
  disable), mDNS `.local` advertisement, and a dedicated Services page.
  OpenWebUI is prewired at `:3001` with zero config.
- **Image generation, day one** — `POST /v1/images/generations` via ComfyUI
  in the `img` slot container (ROCm). The installer seeds the `img` slot and
  its ComfyUI model share.
- **Atomic self-update with rollback** — `hal0 update --channel
  stable|preview|nightly`. Cosign-verified tarballs swap a
  `/usr/lib/hal0/current` symlink; `--rollback` reverts. Staging happens in
  a root-only directory with the authenticated digest pinned to the very
  file object the archive is extracted from.
- **One-line install** — `curl -fsSL https://hal0.dev/install.sh | sudo bash`.
  The only model it downloads is the `brain` steward; every other slot lands
  model-less for you to fill. Piped through `bash` it never prompts.
  (`--models-dir=PATH` or `HAL0_MODELS_DIR=PATH` redirects model pulls off
  `/var/lib/hal0/models`.) The bootstrap requires `jq` and `cosign`,
  authenticates the exact channel manifest before strict schema/channel
  parsing, then sha256- and Sigstore-bundle-verifies the tarball before
  handing off to [`installer/install.sh`](./installer/install.sh). Re-running
  it is safe: every provisioning step is idempotent and existing slot
  configs are never overwritten.
- **One-line Proxmox VE install** — on a Proxmox host, `bash -c "$(curl
  -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)"`
  creates an unprivileged Debian 13 LXC and runs the standard bootstrap
  inside it. `--advanced` opens whiptail prompts; every parameter has an
  env-var override (`CTID`, `RAM_MB`, `STORAGE`, …). Hardware-agnostic —
  Strix Halo passthrough still needs the privileged-LXC recipe. See
  [`scripts/proxmox-ve/README.md`](./scripts/proxmox-ve/README.md).

### Agents, MCP, and the brain steward

hal0 ships **two MCP servers** and **two bundled agents**. The MCP servers
(`/mcp/admin` for slot / model / capability / config / hardware / log admin,
`/mcp/memory` for Hindsight-backed long-term memory) are reachable by any
MCP-speaking client — Claude Code, RAG services, your own scripts. Both
bundled agents can be installed simultaneously: `pi-coder` (CLI shape, from
the `Hal0ai/pi-mono` fork via `@earendil-works/pi-coding-agent` on npm) and
`Hermes-Agent` (service shape, via the hal0-owned `hermes` wrapper —
`hal0-hermes` remains a back-compat symlink — connecting to `hal0-api` at
`HAL0_INFERENCE_BASE=http://127.0.0.1:8080`). Install either with
`hal0 agent install <name>`. Destructive MCP calls (`model_pull`,
`slot_delete`, `config_write`, …) gate through a header bell + inbox modal
in the dashboard, with CLI parity via
`hal0 agent approvals {list,approve,deny}`.

The **hal0-brain steward** is a third, built-in persona wired to the
dashboard's top-bar agent chat — no install step. It drives the platform
through the **180-tool `hal0-admin` catalog** (models, slots, stacks,
profiles, settings, upstreams, benchmarks) under a **per-persona tool
policy**: `tools_allowed` hides tools, `require_approval` tightens them, and
a `POLICY_NO_LOOSEN` floor keeps destructive and secret-bearing tools
(`*_delete`, `config_write`, `provider_credential_write`) always gated — no
persona edit can disarm them. Gated calls **pause the turn** for inline
approve/deny. A `[brain_chat]` config gives operators a hard kill switch, a
read-only mode, loop-budget knobs and a slot override (run the steward on
any slot, e.g. `hal0/npu`) from **Settings → Agents / Brain**. The shipped
brain models are native tool-callers, so the steward executes its own calls
on the brain slot out of the box; binding a larger model to the `agent` slot
is the optional upgrade path via the `[brain_chat]` `tool_model` fallback.

See [docs/concepts/agents.mdx](./docs/concepts/agents.mdx),
[docs/reference/mcp-tools.mdx](./docs/reference/mcp-tools.mdx) and
[docs/guides/run-agents.mdx](./docs/guides/run-agents.mdx).

## Backends

Each capability runs in its own container, supervised by
`hal0-slot@<name>.service`. The profile in `/etc/hal0/profiles.toml` pins
the flag bundle; the image is slot-owned.

| Capability               | Profile / image                  | Device              | Notes                                                        |
|--------------------------|----------------------------------|---------------------|--------------------------------------------------------------|
| chat + embed + rerank    | `rocm` / `rocm-dnse` / `rocm-moe` / `vulkan` / `cuda` (experimental) | ROCm / Vulkan / CUDA / CPU | ROCm FP4 fork baked into the image; MTP via `--spec-type draft-mtp` on the `rocm-dnse`/`rocm-moe` profiles |
| chat + STT + embed (NPU) | `flm` (`hal0-toolbox-flm`)  | AMD XDNA (opt-in)   | FLM trio: one container, `[npu] asr/embed` toggles, ~2 GB NPU mem |
| transcription            | `stt` (`hal0-toolbox-moonshine`) / FLM trio above | CPU / AMD XDNA | `voice.stt` switches Moonshine (CPU) ⇄ FLM's whisper-v3:turbo (NPU) without reconfiguring the slot; no GPU STT engine |
| TTS                      | `tts` (`hal0-toolbox-kokoro`) / `tts-qwen3` (`hal0-toolbox-qwen3tts`) | CPU / ROCm | `voice.tts` switches Kokoro (CPU) ⇄ Qwen3-TTS (GPU) without reconfiguring the slot |
| image                    | `comfyui` (`docker.io/kyuz0/amd-strix-halo-comfyui`) | ROCm | Exclusive GPU via arbiter; SD Turbo / Flux-2-Klein-9B        |

Every seeded profile requests `-fa auto`, so Flash Attention is probed at
startup and falls back cleanly when it cannot be scheduled on its layer's
device. A model with a measured win from forcing it can set `-fa on` in its
own `defaults.extra_args`.

The NPU path is opt-in: the installer places a FastFlowLM `.deb` on the host
(SHA-pinned per distro — Ubuntu 24.04/25.10/26.04 and Debian 13) for
device-sanity probes (`flm validate`); inference runs inside the
`hal0-toolbox-flm` container. With FLM present the installer seeds the `flm`
slot, and the NPU trio activates once `flm validate` passes.

For the container-runtime operator reference — service layout, slot TOML
fields, profiles, GPU arbiter and day-2 commands — see
[docs/concepts/architecture.mdx](./docs/concepts/architecture.mdx) and
[docs/guides/manage-slots.mdx](./docs/guides/manage-slots.mdx).

## Hardware

Linux + systemd is the only hard requirement
([`installer/lib/preflight.sh`](./installer/lib/preflight.sh),
`preflight_systemd`). macOS and Windows are not in scope for v1.

| Tier            | Hardware                                                                  | Status |
|-----------------|---------------------------------------------------------------------------|--------|
| **First-class** | AMD Ryzen AI Max+ 395 ("Strix Halo") with iGPU + XDNA NPU + 128 GB unified | Reference deployment. All published perf numbers come from this box. |
| **First-class** | AMD Ryzen AI Max 385 / 390 with 64 GB unified                              | Same path; small + mid tiers fit, 70B Q4 with shorter context. |
| **Experimental** | NVIDIA RTX 30/40/50 (10–32 GB)                                           | Dedicated `cuda` seed profile — upstream `ghcr.io/ggml-org/llama.cpp:server-cuda` via CDI (`nvidia-container-toolkit`), with multi-GPU `gpu_index` pinning on the `gpu-cuda` device. Auto-falls back to the `vulkan` profile when CDI isn't present. `/api/backends` doesn't yet auto-advertise `gpu-cuda`. |
| **Supported**   | AMD Radeon RX 7000 / discrete (16–24 GB)                                  | ROCm or Vulkan container profiles; same `hal0-slot@<name>` lifecycle. |
| **Fallback**    | CPU-only x86_64                                                            | `cpu-llm` profile — the Vulkan toolbox image run CPU-only (no GPU passed to the container). Usable for tiny models / smoke tests, not the headline experience. |

## Setup and day-2 operation

After the one-liner finishes, the dashboard is at `http://<box>:8080` and
the API at `http://<box>:8080/v1`. Assign models from the dashboard, or:

```sh
hal0 model pull <hf-repo>/<file.gguf>   # resumable, disk-preflighted
hal0 slot load agent --model <id>       # bind a model and start the container
hal0 status                             # system + slot summary
hal0 chat --brain                       # terminal REPL against the steward
```

`hal0 model pull` streams from Hugging Face into `registry.toml` under the
`user.*` namespace — a disk-space preflight fails fast before a multi-GB
pull, and an interrupted pull resumes via HTTP `Range` instead of restarting
from zero.

Diagnostics:

```sh
hal0 doctor all              # full pre-flight against the live host
hal0 doctor ports            # port claims + netavark DNAT rule audit
hal0 doctor ports --fix      # prune stale DNAT rules that black-hole a port
hal0 system-info             # host / GPU / NPU / runtime evidence (read-only)
```

Services and logs:

```sh
systemctl status hal0-api
systemctl list-units 'hal0-slot@*'
journalctl -fu hal0-api
journalctl -fu 'hal0-slot@*'
systemctl restart hal0-slot@agent    # restart a wedged slot container
```

`hal0 uninstall [--keep-data]` tears down a running install (a thin wrapper
over `installer/uninstall.sh`).

### Auth posture

hal0 ships built-in login, sessions and a deny-by-default route classifier,
with three tiers — `anon` → `client` → `admin`. There is no per-user account
system: a browser session is always admin-equivalent, and programmatic
callers use `Authorization: Bearer <key>` (or `?api_key=` for WebSocket
upgrades) against an admin or client key.

**Auth ships off by default.** A fresh v1.0 install is trusted-LAN-open —
the right posture for a homelab appliance — until an operator turns
enforcement on:

```sh
hal0 auth rotate admin     # mint an admin key into /etc/hal0/api.env
hal0 auth require on       # enforce; takes effect on the next request
hal0 auth status           # show the current posture
```

hal0 does **not** terminate TLS or manage a user directory. If it is
reachable from anything you don't physically control, front it with a
reverse proxy that owns TLS — Traefik, nginx, Cloudflare Tunnel. Note that
the optional OpenWebUI companion is a **second listener** on `:3001` that
this auth model does not cover. See
[`docs/operate/auth.mdx`](./docs/operate/auth.mdx) and
[`docs/concepts/security.mdx`](./docs/concepts/security.mdx).

### Proxmox integration (optional)

If hal0 runs inside a Proxmox LXC, the container only sees its own cgroup
slice of memory — other tenants, ZFS ARC and the host kernel draw from the
same physical DIMMs as GPU GTT but are invisible from inside. To surface
that, drop a read-only `PVEAuditor` API token into the dashboard's
Settings → "Proxmox integration" panel. The unified-memory bar then swaps to
the physical host's DIMM total and adds a muted "Proxmox host" segment for
other-tenant + kernel pressure. The token is sensitive, stored 0600 at
`/etc/hal0/proxmox.json`, and never echoed back by the API. Bare-metal and
VM installs leave the panel off.

## Project layout

```
hal0/
├── src/hal0/         # Python package (FastAPI API + capability layer + ContainerProvider + CLI)
│   ├── providers/    # ContainerProvider, FLMProvider, ComfyUI, etc.
│   ├── slots/        # slot manager, state machine, GpuArbiter
│   ├── bench/        # benchmark planner, runner, store, publish
│   └── omni_router/  # client-side tool-calling loop + tool definitions
├── ui/               # React 18 + TypeScript + Vite + Tailwind 4 dashboard
├── installer/        # install.sh (writes /etc/hal0/, systemd units, hal0-api.service)
│   ├── etc-hal0/     # curated slot seeds + profiles.toml
│   └── systemd/      # hal0-agent@ template units
├── tests/            # pytest suite (α unit, β integration, γ release-gate)
│   └── release-validation/   # versioned RC validation kit (lanes, regressions, boxes)
├── docs/             # user docs (mirrored to hal0.dev/docs) + docs/adr/
```

The model catalog lives at `/var/lib/hal0/registry/registry.toml` — the
single source of truth for HuggingFace coordinates, SHA-256 digests and
curated filenames. Per-leaf symlinks under the models directory redirect to
a separate storage volume when one is in use.

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

## Roadmap

No dates — items are direction. The closer to the left, the closer to
running on your box. Full version at
[hal0.dev/#roadmap](https://hal0.dev/#roadmap).

### Shipped (v1.0)

- **Per-slot podman containers** — every inference workload in its own
  `hal0-slot@<name>.service`; `ContainerProvider` + `profiles.toml` replaced
  the single-daemon model
- **hal0-brain steward** — top-bar agent chat driving the 180-tool
  `hal0-admin` catalog under a per-persona tool policy, pausing turns on
  gated tools for inline approve/deny
- **Model-owned tuning** — the model, not the slot, owns context size, MTP
  and launch flags; profiles supply a floor, never an override
- **Stacks** — declarative model/slot layouts applied atomically with
  rollback, drift detection and a portable export envelope
- **In-dashboard benchmarks** — the `hal0.bench` engine, `/api/benchmarks`,
  a Benchmarks page (roster / runs / evals / run-queue), shareable bundles
  and leaderboard publishing
- **Upstream model controls** — external providers (OpenRouter, Anthropic,
  OpenAI, Google AI Studio, Ollama, custom) with reactive CRUD, per-upstream
  model filters and an `enabled` kill switch across CLI, MCP and dashboard
- **Companion services, one surface** — Open WebUI, ComfyUI, Hermes,
  Hindsight and n8n from a dashboard Services page with mDNS discovery
- **FLM trio NPU packing** — chat + ASR + embed coresident on one AMDXDNA
  hardware context, toggled from the slot TOML's `[npu]` table
- **GpuArbiter exclusive image mode** — ComfyUI gets the iGPU to itself; LLM
  GPU slots pause and resume automatically
- **OmniRouter client-side tool-calling** — 8 tools, dynamic per-request
  filtering, `route_to_chat` cross-slot delegation
- **GPU generalization** — experimental CUDA and per-slot `gpu_index`
  pinning for multi-GPU hosts alongside the ROCm/Vulkan defaults
- **Built-in auth** — three tiers, deny-by-default route classification, key
  rotation and enforcement toggling from CLI or dashboard
- **Privileged-seam hardening** — the root wrapper allow-lists the content
  of every unit, drop-in and quadlet it writes; update staging is root-only
  with the verified digest pinned to the extraction handle
- **Installer-owned first-run provisioning** — `install.sh` is the single
  entry point: curated slot seeds, brain steward pull, service start
- **`registry.toml` as sole model catalog**, slot-state Prometheus +
  EventBus journal, `hal0 mcp` CLI with two-way MCP, cosign-keyless
  self-update with rollback, bundled agents (pi-coder / Hermes-Agent)

### Soon

- **Federated memory** — recall across local + remote sources behind one
  memory surface (the MCP-client side shipped in v0.9)
- **Loadout presets** — curated model/slot presets you can flash onto a
  fresh install
- **AUR PKGBUILD & Ubuntu PPA** — native distro packages on top of the
  install script; pacman and apt as first-class install paths
- Light mode toggle

### Exploring (v1.x +)

- **Multi-host federation** — a slot mesh across LAN boxes — primary on the
  Strix Halo, embed on the workstation, all behind one `/v1/*` surface
- **Fine-tune & LoRA hot-swap** — attach and rotate LoRAs against a warm
  base model without unloading the underlying weights
- **Per-model rate limits & budgets** — cost-style accounting for local
  inference; cap a chatty agent without taking the whole box down
- **ChatOps adapters** — Slack and Matrix bridges as extensions

## License

Apache 2.0. See [`LICENSE`](./LICENSE).

## Contributing

The contribution model is still being decided. File issues for discussion;
PRs aren't being accepted from outside contributors yet. See
[`CONTRIBUTING.md`](./CONTRIBUTING.md) for the test tiers and the eventual
flow. For questions and general chat, join the
[hal0 Discord](https://discord.gg/7M4y6dcUyq).

This project adheres to the [Contributor Covenant](./CODE_OF_CONDUCT.md)
code of conduct. To report a security vulnerability, see
[`SECURITY.md`](./SECURITY.md).
