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

> **v1.1.0 is the GA release.** It is what the `stable` channel has been
> waiting for since 0.9.8 shipped on 2026-07-13: the 1.0 line ran rc.1
> through rc.12 on `preview`, each candidate validated on a real-hardware
> fleet. From v1.0.0 the project follows semver proper. If you are upgrading
> from 0.9.8, read [Upgrading from 0.9.8](#upgrading-from-098) first — that
> hop has three specific gotchas. Boxes already on a late rc get an ordinary
> `hal0 update` with nothing special to do.

> **⚡ The installer is the whole setup.** There is no separate first-run
> wizard afterwards. `install.sh` asks where models should live, optionally
> takes a HuggingFace token, seeds every capability slot, downloads the hal0
> brain steward model, and starts the API. When it finishes, open the
> dashboard and assign models to slots.

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

## Install

### The one-liner

```sh
curl -fsSL https://hal0.dev/install.sh | sudo bash
```

That fetches `installer/bootstrap.sh`, which needs `jq` and `cosign` on the
box. It authenticates the channel manifest before parsing it, sha256- and
Sigstore-bundle-verifies the release tarball, then hands off to
[`installer/install.sh`](./installer/install.sh). Piped through `bash` it
never prompts. Re-running it is safe: every provisioning step is idempotent
and existing slot configs are never overwritten.

Pick a channel with `HAL0_CHANNEL` (`stable` is the default; `preview` and
`nightly` are the other two):

```sh
curl -fsSL https://hal0.dev/install.sh | sudo HAL0_CHANNEL=preview bash
```

### What the installer actually does

1. **Pre-flight** — systemd, Python 3.12–3.14, x86_64, a container runtime
   (podman preferred, auto-installed), GPU/NPU device visibility, disk space,
   free ports.
2. **Code + venv** — a versioned tree under `/usr/lib/hal0/` with a `current`
   symlink and a shared venv.
3. **Config** — `/etc/hal0/` with `hal0.toml`, `profiles.toml`, the ten
   curated slot seeds, and `api.env`.
4. **Hardware probe** — writes `/etc/hal0/hardware.json` and prints the
   detected backends.
5. **Brain steward model** — pulls the curated `lfm2.5-2.6b` (LiquidAI
   LFM2.5-2.6B-Q8_0, ~2.87 GB, 131k context) and binds it to the `brain`
   slot, so the dashboard's steward chat and its tool calls work out of the
   box. On an interactive terminal only, it then *offers* a larger agent
   model (15–31 GB, size printed first, default **No**).
6. **ComfyUI share** — creates the bind-mount directories the `img` slot
   needs and seeds custom nodes.
7. **Service start** — enables and starts `hal0-api` + `hal0.target`, the
   Hindsight memory engine, OpenWebUI on `:3001`, and the bundled Hermes
   agent.

Full step list and the complete env-var table:
[`installer/README.md`](./installer/README.md).

### Common install knobs

| Variable / flag | Effect |
|---|---|
| `--models-dir=PATH` / `HAL0_MODELS_DIR=PATH` | Put model pulls somewhere other than `/var/lib/hal0/models` |
| `HAL0_PORT=9090` | Bind the API somewhere other than `:8080` |
| `HAL0_CHANNEL=preview` | Install from a channel other than `stable` |
| `HAL0_NONINTERACTIVE=1` | Force flag/env defaults; never prompt, even on a TTY |
| `HAL0_SKIP_SETUP=1` | Skip first-run seeding **and** both model steps |
| `HAL0_SKIP_BRAIN_MODEL=1` | Keep the seeding, skip the brain pull (`brain` stays model-less) |
| `HAL0_BRAIN_MODEL=<id>` | Force a specific curated brain quant |
| `HAL0_PULL_AGENT_MODEL=1` / `=0` | Opt in to the big agent pull unattended / force-skip it on a TTY |
| `HAL0_PY_AUTOINSTALL=1` | Let the installer install a compatible `python3.12` |
| `HAL0_SKIP_HINDSIGHT=1`, `HAL0_SKIP_OPENWEBUI=1`, `HAL0_SKIP_HERMES=1`, `HAL0_SKIP_COMFYUI=1` | Skip individual companion services |
| `--no-start` | Provision everything, leave services stopped |

### Proxmox VE, one line

On a Proxmox host:

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve/hal0.sh)"
```

Creates an unprivileged Debian 13 LXC and runs the standard bootstrap inside
it. `--advanced` opens whiptail prompts; every parameter has an env-var
override (`CTID`, `RAM_MB`, `STORAGE`, …). Hardware-agnostic — Strix Halo
passthrough still needs the privileged-LXC recipe. See
[`scripts/proxmox-ve/README.md`](./scripts/proxmox-ve/README.md).

### Uninstall

```sh
hal0 uninstall                 # prompts before deleting config + model data
hal0 uninstall --keep-data     # keep /etc/hal0 and /var/lib/hal0
```

## First ten minutes

The dashboard is at `http://<box>:8080`, the API at `http://<box>:8080/v1`.
Assign models from the dashboard, or from the shell:

```sh
hal0 model pull <hf-repo>/<file.gguf>   # resumable, disk-preflighted
hal0 slot load agent --model <id>       # bind a model and start the container
hal0 status                             # system + slot summary
hal0 chat --brain                       # terminal REPL against the steward
```

Then point any OpenAI-compatible client at the box:

```sh
curl http://<box>:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"<id>","messages":[{"role":"user","content":"hello"}]}'
```

`hal0 model pull` streams from Hugging Face into `registry.toml` under the
`user.*` namespace — a disk-space preflight fails fast before a multi-GB
pull, and an interrupted pull resumes via HTTP `Range` instead of restarting
from zero.

## What's new in 1.0

### The whole platform is agent-reachable

The admin MCP catalog went from 92 to **180 tools**: services lifecycle,
ComfyUI, updater/doctor/health, hardware and request telemetry, the slots and
models long-tail, bench, activity, approvals, runner images and NPU
load/unload. The memory surface went from 5 tools to **26**, at feature parity
with Hindsight 0.8.4 — including `memory_reflect` (LLM-backed synthesis),
`memory_curate`/`memory_history` (the non-destructive "this is wrong"
correction path), mental models, directives and async-operation polling.

### Slots start when you say so

New `autoload` setting: binding a model no longer implies a boot start. New
eviction `priority` (0–100) replaces the inert `lru = true` opt-in, so
memory-pressure eviction actually works on a stock box — pressure and
pre-load eviction unload the lowest-priority slot first, LRU as the tie-break
within a tier, and `pinned` still exempts a slot entirely.

### Voice is a device-keyed switch

Moonshine is back as the CPU STT engine in its own toolbox image. `cpu` runs
Moonshine, `npu` runs whisper-v3:turbo, and a GPU device resolves to *no* STT
engine instead of silently picking up a llama chat profile.

### Image Gen and Slots got their lifecycle right

State-typed engine indicators, a Stop that drives the GPU arbiter back to
inference mode, dropdown-driven runner image / binary selection (picking an
image repopulates the binary dropdown with what that image actually ships),
and a `GET /api/slots` that no longer multiplies `podman inspect` fan-out on
wide boxes.

### Documentation moved into this repo

`docs/` is the source of truth and publishes to hal0.dev through a mirror
workflow, with a restored, v1.0-reconciled `getting-started/` section.

### Stability work that landed in the rc line

- **Hermes bootstrap MCP wiring actually works.** The seed TOML never
  declared the builtin `[mcp.servers.*]` blocks, so the allow-list silently
  skipped wiring both servers, and the post-wire probe double-appended `/mcp`
  and 404ed. Bootstrap now also injects `HAL0_MCP_TOKEN` (0600) and renders
  `Authorization: Bearer` into the Hermes MCP client config when the box has
  auth on, refreshing it on `--repair` after a key rotation.
- **ComfyUI `img` slot reliability.** Bind-mount dirs are created before
  spawn (a missing tree crash-looped podman with exit 125), readiness waits
  on ComfyUI's real `GET /system_stats` probe instead of 404-polling a
  llama-style `/health` for 180 s, and the fail-watcher no longer strikes a
  READY img slot to ERROR seconds after it comes up.
- **`GET /api/slots` latency cut sharply on wide boxes** — no `podman
  inspect` for stopped slots, TTL-cached `podman image inspect`, and a
  single-flight 2 s snapshot that any slot mutation invalidates immediately.
- **The Slots page no longer stalls on activity backfill** — the SSE stream
  takes a `limit` and the client coalesces frame bursts into one render per
  50 ms.
- **Brain tool calls no longer 500** with `Unknown (built-in) filter 'min'`.
  A corrected `hal0-brain-sft.jinja` ships bundled and the curated catalogue
  stamps it into the model's `defaults.chat_template` at pull time.
- **A drifted hermes venv re-converges on upgrade** instead of staying broken.

### Quality-of-life

- `hal0 doctor all` — one read-only pass over every surface with a rolled-up
  verdict and an exit code you can branch on.
- `hal0 doctor bundle` — a redacted support bundle in one command.
- `hal0 doctor ports --fix` — prune stale netavark DNAT rules that black-hole
  a port; it is the drill-down the `Slot ports` row used to lack.
- `POST /api/profiles/generate` (`profile_generate` over MCP) drafts a profile
  from a registered model or a HuggingFace repo, with an optional `use_llm`
  pass that degrades to heuristics when inference is down.
- Slot drawer: Profile moved under the model select (it rides the model
  choice); Runner Image is a catalog dropdown with a "Custom image ref…"
  escape hatch.
- The `/mcp/memory` mount is now CLIENT-tier (was ADMIN), so memory-only
  agents no longer need the platform-admin key.

Breaking changes, migrations and known issues for this release are in
[`CHANGELOG.md`](./CHANGELOG.md).

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
Cosign-verified self-update with one-flag rollback. Priority-ordered
concurrency with active-inference protection — a serving slot cannot be
evicted out from under a streaming request. Privileged operations run
through an allow-listed root seam, never a free-form shell.

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
  (`gpu-rocm | gpu-vulkan | gpu-cuda | cpu | npu`), a `model`, plus
  `autoload`, an eviction `priority` and an optional `default`. Ten curated
  slots (`agent`, `brain`, `coder`, `embed`, `flm`, `img`, `qwen3tts`,
  `rerank`, `tts`, `utility`) are seeded into `/etc/hal0/slots/<name>.toml`
  on install and each gates on its own runtime validation at load time — the
  `flm` NPU slot simply stays grey without FastFlowLM hardware. Add your own
  with `hal0 slot create NAME --type TYPE --model MODEL`. A deliberate
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

### Agents, MCP, and the brain steward

hal0 ships **two MCP servers** and **two bundled agents**. The MCP servers
(`/mcp/admin` for slot / model / capability / config / hardware / log admin,
`/mcp/memory` for Hindsight-backed long-term memory) are reachable by any
MCP-speaking client — Claude Code, RAG services, your own scripts.
`/mcp/admin` is ADMIN-tier; `/mcp/memory` is CLIENT-tier as of 1.0, so a
memory-only agent does not need the platform-admin key. Both bundled agents
can be installed simultaneously: `pi-coder` (CLI shape, from the
`Hal0ai/pi-mono` fork via `@earendil-works/pi-coding-agent` on npm) and
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
any slot, e.g. `hal0/npu`) from **Settings → Agents / Brain**. The seeded
brain model is a native tool-caller, so the steward executes its own calls on
the `brain` slot out of the box. Binding a larger model to the `agent` slot is
the optional upgrade path: `[brain_chat] tool_model` (default `hal0/agent`)
runs tool rounds there while chat stays on the brain slot, so a runner that
cannot emit parseable tool calls reroutes instead of failing the turn. Set it
to `off`, `none` or `disabled` to route tool turns nowhere.

See [docs/concepts/agents.mdx](./docs/concepts/agents.mdx),
[docs/reference/mcp-tools.mdx](./docs/reference/mcp-tools.mdx) and
[docs/guides/run-agents.mdx](./docs/guides/run-agents.mdx).

## Backends

Each capability runs in its own container, supervised by
`hal0-slot@<name>.service`. Two things compose to launch a slot: the
**runner image** (slot-owned — `image` plus `binary`) supplies the engine,
and the **profile** in the catalog supplies the bench-tuned flag bundle.
Profiles are device-agnostic tune templates; they no longer carry an image.

| Capability | Runner (binary) | Device | Profile |
|---|---|---|---|
| chat | `rocmfpx`, `vulkanfpx` (`llama-server`) | `gpu-rocm`, `gpu-vulkan` | `chat`, `chat-long-context`, `dense`, `moe`, `thinking`, `coding`, `brain` |
| chat (NVIDIA, experimental) | `cuda` (`llama-server`) | `gpu-cuda` | `chat` |
| chat (fallback) | `cpu` (`llama-server`) | `cpu` | `cpu-chat` |
| embeddings / rerank | `rocmfpx`, `vulkanfpx` (`llama-server`) | `gpu-rocm`, `gpu-vulkan` | `embedding`, `reranking` |
| chat + STT + embed (NPU) | `flm` | `npu` | `flm` |
| transcription | `moonshine` / the FLM trio above | `cpu` / `npu` | `moonshine` / `flm` |
| TTS | `kokoro` / `qwen3tts` | `cpu` / `gpu-rocm` | `kokoro` / `qwen3-tts` |
| image | `comfyui` | `gpu-rocm` | `comfyui` |

A device also has a *default* profile it resolves to when a slot names none —
`gpu-rocm`, `gpu-vulkan` and `gpu-cuda` resolve to `chat`, `cpu` to
`cpu-chat`, `npu` to `flm`. The seeded slots override that where it matters:
`agent` ships on `chadrock-moe`, `coder` on `coding`, `brain` on `brain`,
`utility` on `chat`.

The seeded profile catalog is virtual — it lives in code
(`SEED_PROFILES`) and is overlaid on every load, so a re-tuned seed reaches
every install on upgrade instead of being frozen by a stale on-disk copy.
`/etc/hal0/profiles.toml` is for *your* profiles: `chat`,
`chat-long-context`, `dense`, `moe`, `thinking`, `coding`, `brain`,
`embedding`, `reranking`, `cpu-chat`, `flm`, `kokoro`, `moonshine`,
`qwen3-tts` and `comfyui` are immutable seeds you clone under a new name to
customize.

`voice.stt` switches Moonshine (CPU) ⇄ FLM's whisper-v3:turbo (NPU) and
`voice.tts` switches Kokoro (CPU) ⇄ Qwen3-TTS (GPU) without reconfiguring
the slot. There is no GPU STT engine — a GPU device resolves to none rather
than silently borrowing a chat profile.

Every seeded chat profile requests `-fa auto`, so Flash Attention is probed
at startup and falls back cleanly when it cannot be scheduled on its layer's
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
| **Experimental** | NVIDIA RTX 30/40/50 (10–32 GB)                                           | Dedicated `cuda` runner — upstream `ghcr.io/ggml-org/llama.cpp:server-cuda` via CDI (`nvidia-container-toolkit`), with multi-GPU `gpu_index` pinning on the `gpu-cuda` device. Auto-falls back to the Vulkan runner when CDI isn't present. `/api/backends` doesn't yet auto-advertise `gpu-cuda`. |
| **Supported**   | AMD Radeon RX 7000 / discrete (16–24 GB)                                  | ROCm or Vulkan runner images; same `hal0-slot@<name>` lifecycle. |
| **Fallback**    | CPU-only x86_64                                                            | `cpu` runner + `cpu-chat` profile — the Vulkan toolbox image run CPU-only (no GPU passed to the container). Usable for tiny models / smoke tests, not the headline experience. |

## Day-2 operation

```sh
hal0 status                          # system + slot summary
hal0 slot list                       # every slot, state, port, bound model
hal0 slot load agent --model <id>    # bind + start
hal0 slot unload agent               # stop without unbinding
hal0 slot restart agent
hal0 model list                      # the registry
hal0 update --check                  # is there anything newer?
```

Services and logs:

```sh
systemctl status hal0-api
systemctl list-units 'hal0-slot@*'
journalctl -fu hal0-api
journalctl -fu 'hal0-slot@*'
systemctl restart hal0-slot@agent    # restart a wedged slot container
```

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

## Troubleshooting

### Start here: `hal0 doctor`

`hal0 doctor` with no subcommand re-runs the installer's own pre-flight
battery against the live host (systemd, Python, container runtime, GPU/NPU
device visibility, disk, ports) and exits with the script's own status code,
so it composes: `hal0 doctor && hal0 status`.

```sh
hal0 doctor                       # re-run installer pre-flight
hal0 doctor --plain               # ASCII-only output (for logs / CI)
hal0 doctor --ports "8080 3001"   # override the port-collision check list
```

`hal0 doctor all` is the one to reach for when something is wrong but you
don't know where. It is strictly read-only and rolls every surface up into
one verdict:

```sh
hal0 doctor all           # human table
hal0 doctor all --json    # same rows as JSON
```

It composes the `doctor verify` report card (API, runners, DNS, capability
slots, memory, OpenWebUI, Hermes) with auth posture, secret-file modes,
model-store integrity, pending migrations, bound slot ports, the
`hal0.target` boot anchor, the MCP mount probes and the privileged sudo
seams. **Exit codes: `0` clean, `1` an actionable failure, `2` critical**
(API unreachable, or zero healthy runners). Every failing row names the
follow-up command.

Those follow-ups, and what each one is for:

| Command | What it audits | Repair flag |
|---|---|---|
| `hal0 doctor verify` | Post-setup report card: live health seams, computed URLs, doc links. `--json` for machines. Exits 2 only on a critical. | — |
| `hal0 doctor perms` | Ownership: Hermes runtime state, the editable checkout's group-share, the canonical path-ownership table. | `--fix` (needs root), `--force`/`-f` to skip the prompt |
| `hal0 doctor models` | Registry paths vs disk, store/roots agreement, unregistered files in the store, FLM store ownership. | `--fix` (needs root), `--force`/`-f` |
| `hal0 doctor ports` | Which port each slot bound, collisions, corrupt netavark DNAT rules. | `--fix` prunes stale DNAT rules |
| `hal0 doctor profiles` | Dangling slot→profile references and un-pulled runner images. | — (read-only, `--json`) |
| `hal0 doctor migrations` | A pending model-layout migration. | — (read-only, `--json`) |
| `hal0 doctor toolbox-pull` | Every image pinned in `manifest.json` is anonymously pullable from ghcr.io. | — (`--json`, `--manifest PATH`) |
| `hal0 doctor logs` | `hal0-api`'s own journal. | `--unit`, `--follow`/`-f`, `--lines`/`-n`, `--level`, `--since` |
| `hal0 doctor bundle` | Writes a redacted support bundle (system / config / diagnostics / logs). | `--out DIR`, `--no-rocm-smi`, `--include-logs auto\|yes\|no` |

`--json` on `perms` and `models` implies audit-only — `--fix` is ignored
under it. Both emit stable `Diagnosis` JSON (`HAL0-PERMS-*`,
`HAL0-MODEL-*`) so you can gate a script on a specific finding.

Two more read-only evidence commands:

```sh
hal0 system-info    # host / GPU / NPU / runtime evidence
hal0 ports          # port claims across the box
```

### Install troubleshooting

**`✗ pre-flight failed: port 8080 is already in use.`** Find the squatter
with `lsof -i :8080`, then either stop it or move hal0:

```sh
HAL0_PORT=9090 sudo bash installer/install.sh
```

After changing the port, update `/etc/hal0/api.env` and
`/etc/hal0/openwebui.env` to match, then
`systemctl restart hal0-api hal0-openwebui`.

**`✗ pre-flight failed: less than 20GB free in /var/lib`** — free space or
redirect model pulls to a bigger disk:

```sh
sudo bash installer/install.sh --models-dir=/mnt/large-disk/hal0-models
```

The installer records this under `[models].pull_root` in
`/etc/hal0/hal0.toml`, so subsequent `hal0 model pull` calls honor it too.

**Python too old.** hal0 needs 3.12–3.14. Let the installer provision one:
`HAL0_PY_AUTOINSTALL=1`, or point it at an interpreter you already have with
`HAL0_PYTHON=/usr/bin/python3.12`.

**No container runtime.** `install.sh` sets `HAL0_CONTAINER_REQUIRED=1` and
auto-installs podman via the detected package manager — if that is not
possible it hard-fails with the exact one-liner for your distro. A box that
finishes without a runtime would have every slot dead, which is why this one
is fatal during install and only a warning under `hal0 doctor`.

**GPU/NPU devices not visible inside an LXC.** Pre-flight prints the exact
Proxmox `dev0`/gid fix. CPU-only is a valid install and never blocks.

**The hermes step failed on a fresh box.** First-boot installs can lose the
dpkg lock race to `unattended-upgrades` (#1584). The step degrades
gracefully — re-run it afterwards:

```sh
hal0 agent install hermes
```

**`hal0 doctor: AMDXDNA NPU detected but FastFlowLM not installed`.** The
host-side sanity probe wants the FLM `.deb`. If you installed on a non-NPU
host and later added the hardware, re-run `installer/install.sh` to pick up
the prerequisite. If `flm validate` fails because `libxrt-npu2` is
unavailable from your apt sources, the NPU **container** slot still works —
it bundles its own XRT runtime.

**A slot won't load.**

```sh
systemctl status hal0-slot@<name>
journalctl -u hal0-slot@<name> -n 60
hal0 doctor profiles          # dangling profile ref? un-pulled image?
```

Usual causes: the runner image hasn't been pulled yet (the first load blocks
on a multi-GB pull — watch the journal), the model named in
`/etc/hal0/slots/<name>.toml` isn't in the registry (`hal0 model list`), or
the GPU is held by image mode (the dispatcher returns 503 while the `img`
slot owns the GPU — stop image mode or wait for idle-restore).

**Something bound the port but nothing answers on it.** That is usually a
stale netavark DNAT rule left by a container that died badly:

```sh
hal0 doctor ports          # audit
hal0 doctor ports --fix    # prune the stale rules
```

### Update troubleshooting

```sh
hal0 update                       # check + apply if newer
hal0 update --check               # check only, apply nothing
hal0 update -y                    # skip the confirmation prompt
hal0 update --channel preview     # persist a channel, then check
hal0 update --target 1.0.0        # require the manifest to match exactly
hal0 update --rollback            # restore the previous tree
hal0 update --restart-slots       # bounce only slots still on the old launch command
```

The CLI is a thin client over `/api/updates/*` — the swap happens in the
daemon, so the same code path runs whether you update from the shell or the
dashboard. After a successful apply the daemon try-restarts `hal0-api`
itself.

**`Error: prepare failed: cosign is not installed`** — you are on 0.9.8,
whose updater verifies with `cosign verify-blob` and aborts without it. Its
`HAL0_UPDATE_SKIP_COSIGN` escape hatch is ignored on `stable`, so there is
no bypass. Re-run the install one-liner (it installs cosign for you), or
install cosign by hand and retry — see
[Upgrading from 0.9.8](#upgrading-from-098).

**`hal0 update` exits 1 but the update worked.** The 0.9.8 client polls job
status through the API it just restarted and treats the mid-restart
connection refusal as fatal (#1540, fixed in the 1.0 client — which by
definition is not the one driving a 0.9.8 upgrade). Check the real outcome
before retrying anything:

```sh
hal0 --version
curl -s http://127.0.0.1:8080/api/health
```

**`release manifest fetch returned HTTP 302`** — `HAL0_RELEASES_URL` is
pointed at a GitHub release-asset URL. 0.9.8's updater does not follow
redirects. Leave it at the default; `releases.hal0.dev` serves manifests
directly.

**"Nothing to apply" when you know there is something.** Two known shapes:

- You are on `1.0.0-rc.1`, whose venv predates prerelease-aware version
  comparison and ranks `1.0.0rc1` above `1.0.0` (#1663/#1640). Bypass the
  gate: `hal0 update --target 1.0.0`.
- You came from 0.9.8 via `hal0 update` rather than the one-liner, so the
  profile-catalog reset is still outstanding (#1585). Nothing is lost — it
  applies on the next update run by 1.0 code.

**Something is wedged after an update.** Roll the tree back, then look:

```sh
hal0 update --rollback
hal0 doctor all
```

Rollback restores the previous tree from `/var/lib/hal0/hal0.previous`.
Config and state are **not** rewound — schema migrations are forward-only.
Treat rollback as a way to get serving again quickly, not as an undo.

**Slots still running the pre-update launch command.** By design, an update
never bounces a slot on its own. Converge them when you're ready:

```sh
hal0 update --restart-slots
```

### Filing a bug

```sh
hal0 doctor bundle
```

Writes `./hal0-doctor-bundle-<host>-<ts>/` — system, config, diagnostics and
logs in one directory, with every config dump redacted (SECRET / TOKEN /
PASSWORD / API_KEY / PRIVATE_KEY / ENCRYPTION_KEY / SALT-named keys). It is
read-only and never uploads anything: `tar czf` it and attach it yourself.
Exit `1` means some probe commands failed — see `commands.tsv` inside.

## Upgrading from 0.9.8

`hal0 update` supports any `1.0.0-rc` and **0.9.8**, the version every
stable-channel box has been pinned at since 2026-07-13.

**The one-liner is the supported 0.9.8 path.** It is an upgrade in place, not
a reinstall: `/etc/hal0` and `/var/lib/hal0` carry across, your slot TOMLs
stay where they are, and the installer adds what 1.0 needs rather than
replacing what you had.

```sh
curl -fsSL https://hal0.dev/install.sh | sudo bash
```

It resolves the dependencies 0.9.8 never installed — **cosign included** —
and that is the whole reason to prefer it. Its migrations also run under the
*new* code, so the box lands on `meta.schema_version = 2` in the same pass.

If you would rather drive the updater yourself, install cosign first:

```sh
curl -fsSL -o /usr/local/bin/cosign \
  https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64
chmod +x /usr/local/bin/cosign
hal0 update -y
```

Three things to know before you start, all specific to being on 0.9.8:

- **The CLI may exit 1 on a successful update** — the old client losing the
  API across the restart it triggered. Check `hal0 --version` before you
  retry anything.
- **Your channel does not change.** 0.9.8 persisted `stable` and the
  installer will not overwrite a channel already on disk, so
  `HAL0_CHANNEL=preview` on the upgrade command is ignored. Stable is what
  you want at 1.0; set preview afterwards with
  `hal0 update --channel preview` if you are deliberately tracking it.
- **Do not point `HAL0_RELEASES_URL` at a GitHub release-asset URL** — 0.9.8
  does not follow the `302`.

Two migrations are deliberately left to you, because both rewrite state in
ways that want a human deciding when. The **slot-flag fold**
(`hal0 slot migrate-flags`) moves per-slot tunes onto their bound model; it
is a dry run until `--apply`, and `--apply` refuses while `hal0-api` or any
`hal0-slot@*` unit is active unless you add `--stop-services`. Back up
`hal0.db` and your slot dirs first, and expect it to refuse the whole run —
rather than write half of it — when slots share a model with divergent
tunes. **Slot id-keying** (`hal0 slot migrate-id-keying`) is optional and
reversible; the runtime reads either layout, so run it in a downtime window.

Everything else — schema migrations, the one-shot `enabled` sweep at first
v1.0 boot, the config-load repairs — runs itself. Full breaking-change,
migration and known-issue detail is in [`CHANGELOG.md`](./CHANGELOG.md).

## Project layout

```
hal0/
├── src/hal0/         # Python package (FastAPI API + capability layer + ContainerProvider + CLI)
│   ├── providers/    # ContainerProvider, FLMProvider, ComfyUI, etc.
│   ├── slots/        # slot manager, state machine, GpuArbiter
│   ├── runners/      # RUNNER_IMAGES — the runner-image registry
│   ├── bench/        # benchmark planner, runner, store, publish
│   └── omni_router/  # client-side tool-calling loop + tool definitions
├── ui/               # React 18 + TypeScript + Vite + Tailwind 4 dashboard
├── installer/        # install.sh (writes /etc/hal0/, systemd units, hal0-api.service)
│   ├── etc-hal0/     # curated slot seeds + operator profiles.toml
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
  `hal0-slot@<name>.service`; `ContainerProvider` + the runner-image registry
  replaced the single-daemon model
- **hal0-brain steward** — top-bar agent chat driving the 180-tool
  `hal0-admin` catalog under a per-persona tool policy, pausing turns on
  gated tools for inline approve/deny
- **Model-owned tuning** — the model, not the slot, owns context size, MTP
  and launch flags; profiles supply a floor, never an override
- **Slot autoload + eviction priority** — binding a model no longer implies
  a boot start, and pressure eviction orders by `priority` instead of an
  opt-in nobody set
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
