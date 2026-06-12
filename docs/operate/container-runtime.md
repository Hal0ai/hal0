---
title: Container runtime
description: hal0's inference runtime is per-slot podman containers supervised by hal0-slot@<name>.service units, fronted by hal0-api on :8080.
sidebar:
  order: 6
---

> This file is the **repo-native canonical** copy. The Starlight-rendered
> version at <https://hal0.dev/docs/operate/container-runtime/> is mirrored
> from here by the docs-sync workflow. Edit this file; do not hand-edit the
> `.mdx` mirror in `Hal0ai/hal0-web`.

hal0 runs every inference slot as its **own podman container**,
supervised by a per-slot systemd unit (`hal0-slot@<name>.service`).
There is no shared inference daemon: chat, embeddings, rerank, STT,
TTS, the NPU trio, and image generation each get a dedicated process
with its own image, flags, port, and lifecycle. `hal0-api` on `:8080`
is the control plane — it owns the slot state machines, dispatches
OpenAI-compatible `/v1/*` requests to the right slot port, and serves
the dashboard.

This page is the operator's reference: where the moving parts live,
how slots map to containers, and the day-2 operations you'll actually
run.

## Where everything lives

| Component                | Location                                                  |
|--------------------------|-----------------------------------------------------------|
| Control plane            | `hal0-api.service` (`0.0.0.0:8080`)                       |
| Per-slot units           | `hal0-slot@<name>.service` (one podman container each)    |
| Slot definitions         | `/etc/hal0/slots/<name>.toml`                             |
| Backend profiles         | `/etc/hal0/profiles.toml`                                 |
| Capability selection     | `/etc/hal0/capabilities.toml`                             |
| Slot runtime state       | `/var/lib/hal0/slots/<name>/state.json`                   |
| GPU arbiter state        | `/var/lib/hal0/gpu_arbiter.json`                          |
| Model catalog            | `/var/lib/hal0/registry/registry.toml`                    |
| Models on disk           | `/mnt/ai-models` (or `[models].pull_root` in `hal0.toml`) |
| Logs                     | journald (`hal0-api`, `hal0-slot@<name>`)                 |

Slot containers bind **loopback ports** (assigned from the
`[slots]` port range in `/etc/hal0/hal0.toml`, default 8081–8099, plus
fixed seeds like `img` on 8188). External access goes through
`hal0-api`, which aggregates every ready slot behind one OpenAI-style
surface on `:8080`.

## Slots, profiles, and the catalog

A **slot** (`/etc/hal0/slots/<name>.toml`) is a configuration row:
a name, a port, a device class (`gpu-vulkan` / `gpu-rocm` / `cpu` /
`npu` / `img`), `runtime = "container"`, a **profile** reference, and a
`[model]` table (default model + `context_size`).

A **profile** (`/etc/hal0/profiles.toml`) is a bench-tuned backend
template: container image + flag bundle + an `mtp` switch. The seed
catalog ships:

- `moe-rocmfp4` / `dense-mtp-rocmfp4` — ROCm FP4 images for Strix Halo
  (the FP4-capable `llama-server` fork is baked into the image; MTP
  expands to the full `--spec-type draft-mtp` bundle at resolve time).
- `vulkan-std` — fallback for non-FP4 GGUFs.
- `flm-npu` — FastFlowLM on the XDNA NPU.
- `kokoro-cpu` — CPU TTS.
- `comfyui` — image generation (exclusive GPU, see arbiter below).

Loading a slot resolves slot → profile → (image, flags), starts the
`hal0-slot@<name>` unit, and polls the container's `/health` until
READY. Swapping a model on a container slot is a **container restart**
with the new model mounted — state passes through
`/var/lib/hal0/slots/<name>/state.json`.

The **model catalog is `registry.toml`** —
`/var/lib/hal0/registry/registry.toml` is the only place HuggingFace
coordinates, SHA-256 digests, and curated filenames live. Edit it via
`hal0 model`/the dashboard rather than hand-splicing TOML (a malformed
file triggers a destructive auto-rescan). There is no secondary
runtime catalog to regenerate or sync.

## NPU slot (FastFlowLM trio)

The `npu` slot runs FLM in the `hal0-toolbox-flm` image. One FLM
process can serve up to three modalities at once — LLM chat plus ASR
(Whisper) and embeddings — toggled per slot:

```toml
# /etc/hal0/slots/npu.toml
[npu]
asr = false     # add --asr 1 (Whisper-V3-Turbo on the NPU)
embed = false   # add --embed 1 (embedding endpoint on the NPU)
```

The NPU is **single-tenant**: one FLM process per NPU, and a model (or
trio-toggle) swap is a container restart, not a hot reload. The ASR and
embed models inside the trio are fixed by FLM — the request `model`
field is ignored for those two surfaces. The host-side `flm` package
(installed by the installer on AMDXDNA hosts) is only used for device
sanity probes (`flm validate`); inference runs in the container.

## Image mode and the GPU arbiter

The `img` slot (ComfyUI) needs the iGPU to itself. When image mode
turns on, the **GPU arbiter** stops every LLM GPU slot, records what it
stopped in `/var/lib/hal0/gpu_arbiter.json`, and hands the GPU to the
ComfyUI container. While image mode is active:

- LLM requests that need a GPU slot get **503** from the dispatcher
  (`gpu.image_mode`) — clients should retry after image mode ends.
- CPU and NPU slots are unaffected.

When image mode goes idle, the arbiter restores the slots it stopped.
If the dashboard shows GPU slots stuck OFFLINE after an image session,
check the arbiter file and the `img` slot state first.

## Operating it

```sh
# What's running
systemctl status hal0-api
systemctl list-units 'hal0-slot@*'
curl -s http://127.0.0.1:8080/api/slots | python3 -m json.tool

# Logs
journalctl -fu hal0-api
journalctl -fu 'hal0-slot@*'        # every slot container
journalctl -fu hal0-slot@chat      # one slot

# Restart a slot (recovery hammer for a wedged container)
systemctl restart hal0-slot@chat

# Readiness: hal0-api view + the container's own health endpoint
curl -s http://127.0.0.1:8080/api/slots/chat
curl -s http://127.0.0.1:8081/health     # slot port from the slot TOML / state.json

# Swap a model on a slot (drives an unload → restart → load cycle)
hal0 slot load chat --model <model-id>

# Round-trip a chat request through the control plane
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "chat", "messages": [{"role": "user", "content": "hi"}]}'
```

Notes:

- **First load pulls the image.** A slot's first start can block on a
  multi-GB container image pull; watch `journalctl -fu
  hal0-slot@<name>` rather than assuming a hang.
- **Slot edits are picked up on next load.** Editing
  `/etc/hal0/slots/<name>.toml` or `profiles.toml` does not restart a
  running container — `systemctl restart hal0-slot@<name>` (or an
  unload/load from the dashboard) applies it.
- `hal0 doctor` wraps API health, FLM host-probe state (NPU hosts),
  and config sanity into one check; run it first when something is
  off.

## Troubleshooting quick hits

- **Slot stuck LOADING** — image pull in progress, or the model file
  named in the slot TOML isn't on disk / in `registry.toml`. Check the
  slot journal, then `hal0 model list`.
- **`model.not_found` on swap** — the requested id isn't in the
  catalog; check `GET /api/models` before blaming the slot.
- **GPU slots all OFFLINE** — image mode probably owns the GPU; check
  `/var/lib/hal0/gpu_arbiter.json` and the `img` slot.
- **NPU slot dead, GPU fine** — run `flm validate` on the host; if the
  XRT runtime or NPU firmware is unhappy the container can't claim the
  device either.
- **state.json disagrees with the TOML** — state is written by
  hal0-api on lifecycle transitions; a hand-edited TOML doesn't
  back-propagate. Reload the slot to reconcile.

## See also

- [Dashboard v3 tour](/docs/dashboard/v3/) — visual inspection of the
  slot panels.
- [Configuration](/docs/operate/config/) — TOML layout for
  `/etc/hal0/` and the slot files.
- [Logs](/docs/operate/logs/) — journalctl recipes and the SSE log tail.
- [Slots](/docs/slots/what-is-a-slot/) — the slot lifecycle state
  machine.
- The container-runtime design doc in `docs/internal/` — layering
  rationale for slots ↔ profiles ↔ images.
