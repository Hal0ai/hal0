---
title: Lemonade runtime
description: The AMD-blessed Lemonade Server is hal0's unified inference runtime — one daemon, all modalities, supervised by hal0-lemonade.service on 127.0.0.1:13305.
sidebar:
  order: 6
---

> This file is the **repo-native canonical** copy. The Starlight-rendered
> version at <https://hal0.dev/docs/operate/lemonade/> is mirrored from
> here by the docs-sync workflow. Edit this file; do not hand-edit the
> `.mdx` mirror in `Hal0ai/hal0-web`.

As of **v0.2** (the Lemonade migration, ADR-0008), hal0 ships a single
runtime daemon: **AMD's Lemonade Server**. One process supervises every
modality hal0 exposes — chat, embeddings, rerank, STT, TTS, image
generation — across every backend the host hardware supports (Vulkan
llama.cpp, ROCm, CUDA, FastFlowLM on XDNA NPUs, sd.cpp, whisper.cpp).
The six per-modality toolbox container images from v0.1 are gone.

This page is the operator's reference: what Lemonade is, how hal0 wires
it up, where its state lives, how slots map onto its model registry,
and the known caveats you'll hit in production.

## What Lemonade is

[Lemonade Server](https://github.com/lemonade-sdk/lemonade) is AMD's
vendor-blessed local-inference daemon, purpose-built for Strix Halo
(Ryzen AI Max+ 395, Radeon 8060S iGPU, XDNA NPU, unified memory). It
exposes an OpenAI-compatible HTTP surface (`/v1/chat/completions`,
`/v1/embeddings`, `/v1/audio/transcriptions`, `/v1/images/generations`,
etc.) plus a small control plane (`/v1/load`, `/v1/unload`,
`/v1/health`, `/v1/stats`, `/v1/models`, `/v1/pull`) for managing the
in-memory model set.

hal0 uses Lemonade as the only thing that talks to the GPU/NPU. Slots
are now thin configuration rows that point at a registered Lemonade
`model_name`; the dispatcher resolves a request to a slot, the slot
resolves to a `model_name`, and Lemonade does the work.

## Where it lives

| Component                 | Location                                                       |
|---------------------------|----------------------------------------------------------------|
| Systemd unit              | `hal0-lemonade.service`                                        |
| Process name              | `lemond`                                                       |
| Listen address            | `127.0.0.1:13305` (loopback only)                              |
| Cache dir                 | `/var/lib/hal0/lemonade/`                                      |
| Daemon config             | `/var/lib/hal0/lemonade/config.json`                           |
| Registered models         | `/var/lib/hal0/lemonade/resources/server_models.json`          |
| User-pulled models        | `/var/lib/hal0/lemonade/user_models.json`                      |
| HuggingFace cache         | `${HAL0_VAR_DIR}/.cache/huggingface/` (chowned `hal0:hal0`)    |
| Models on disk            | `/var/lib/hal0/models/` (or `$HAL0_MODELS_DIR` if set)         |
| Pinned versions           | `manifest.json` at the repo root                               |

Lemonade binds **loopback only**. External access goes through hal0's
own FastAPI server on `:8080`, which reverse-proxies the un-routed
`/v1/*` surface to `127.0.0.1:13305` (see below).

## The `/v1/*` proxy

hal0-api at `:8080` serves two `/v1/*` tiers:

1. **Curated, dispatcher-backed routes.** Chat, completions, embeddings,
   rerank, audio, images, models. These aggregate across every
   registered slot so OpenAI clients see one unified catalogue. The
   dispatcher picks the slot that owns the model and forwards to
   Lemonade with the slot's tuned arguments. Implemented in
   `src/hal0/api/routes/v1.py`.

2. **Lemonade control plane (catch-all).** Anything *not* matched by
   the curated routes falls through to a reverse-proxy at
   `/v1/{path:path}` and is forwarded verbatim to Lemonade. That covers
   `/v1/health`, `/v1/stats`, `/v1/load`, `/v1/unload`,
   `/v1/system-info`, `/v1/params`, and anything else Lemonade adds in
   a future release without hal0 needing a code change. Implemented in
   `src/hal0/api/routes/lemonade_proxy.py`; mounted last so FastAPI's
   first-match-wins routing leaves curated handlers intact. Shipped in
   PR #248 (closes #212), in v0.3.0-alpha.1.

The dispatcher itself also falls through to the Lemonade proxy on
`NoRouteFound` (PR #277): if a request shape is OpenAI-shaped but no
slot claims it, hal0 hands it to Lemonade directly rather than 404'ing.

> Lemonade also opens a second HTTP port on `127.0.0.1:9000` for its
> own embedded OpenAI surface. hal0 does not use it. All hal0 traffic
> targets `:13305`; `:9000` is incidental and safe to ignore.

## Slots ↔ Lemonade models

A slot is a row in hal0's config (`/etc/hal0/slots/<name>.toml`) that
binds a slot **name** (e.g. `primary`, `embed`, `stt`) to a registered
Lemonade `model_name`. Loading the slot is `POST /v1/load` to Lemonade
with the slot's tuned `args`; unloading is `POST /v1/unload`. The
slot's lifecycle state machine (OFFLINE → LOADING → READY) is driven
by polling `/v1/health` and `/v1/stats` for that model_name.

Two registries feed Lemonade's `/v1/models`:

- **`server_models.json`** — generated by `hal0 registry sync` from
  `/var/lib/hal0/registry/registry.toml` (the canonical hal0 registry,
  the only place HuggingFace coordinates + SHA256 + curated filenames
  live). Edit `registry.toml` directly to add or pin a model, then
  `hal0 registry sync`. Going through the CLI's `register` subcommand
  loses the HF coordinates by design — use the TOML.
- **`user_models.json`** — written by `POST /v1/pull` (the dashboard's
  "add model" flow). Lemonade scans `extra_models_dir` for compatible
  files; the result is appended here so they survive restart.

Slots created via `hal0 slot create --type <X>` derive the Lemonade
device flag from the slot type (PR #282). Slot creation also accepts
the Lemonade-shape model name directly and auto-picks a free port (PR
#281, with the import fix in PR #320).

## The 8-model cap

Lemonade's `config.json` ships with `max_loaded_models: 8` (set by
`installer/install.sh:977`, raised from upstream's default of 4 per
PR #283). When loading a 9th model, Lemonade evicts the
least-recently-used model **of the same type** (chat-vs-embed-vs-stt
have separate LRU pools per ADR-0008 §3 — this supersedes the
nuclear-evict-all behavior described in the obsolete ADR-0007). The
slot whose model got evicted drifts to **OFFLINE** (not ERROR), and
the next request that needs it re-loads it on demand. The OFFLINE-on-
eviction behavior is PR #276.

If you need a larger working set, raise the cap in
`/var/lib/hal0/lemonade/config.json` and restart `hal0-lemonade`. The
practical ceiling is unified-memory pressure, not the integer in the
config.

## Daemon config

`/var/lib/hal0/lemonade/config.json` is the persisted daemon config.
Lemonade reads it on start; hal0's `/internal/set` writes mutate it
atomically. Fields hal0 cares about:

| Key                  | What it does                                                                       |
|----------------------|------------------------------------------------------------------------------------|
| `port`               | Bind port. Stays `13305` unless you have a conflict.                               |
| `host`               | Bind address. Stays `127.0.0.1` — external access is hal0-api's job.               |
| `max_loaded_models`  | LRU cap (see above). Default 8.                                                    |
| `extra_models_dir`   | Where Lemonade looks for compatible model files. Set to `$HAL0_MODELS_DIR`.        |
| `llamacpp.args`      | Extra argv for every llama.cpp child. Default `--parallel 1 --threads N` (N = (cores − 2) / 4, min 2). |
| `llamacpp.backend`   | `vulkan` / `rocm` / `cuda` / `cpu`. The installer probes hardware and writes this. |
| `flm.args`           | Extra argv for FastFlowLM NPU children. Default `--asr 1 --embed 1`.               |

The `--parallel 1 --threads N` line is **load-bearing**. Lemonade's
upstream default omits `--threads`, which on a 12-core LXC lets two
concurrent llama-server children oversubscribe the CPU and starve
Vulkan dispatch — observable as a ~30-second deadlock the first time
two slots load at once. Set the `llamacpp.args` line before the first
multi-slot load.

## Operating it

```sh
# Daemon status + recent logs
systemctl status hal0-lemonade
journalctl -u hal0-lemonade -f

# Health (curated through hal0-api at :8080)
curl http://127.0.0.1:8080/v1/health

# Currently loaded models + per-model stats
curl http://127.0.0.1:8080/v1/stats

# Restart (the recovery hammer for most ops issues)
systemctl restart hal0-lemonade

# Round-trip a chat request against the primary slot
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "primary", "messages": [{"role": "user", "content": "hi"}]}'
```

`hal0 doctor` wraps daemon reachability, the FLM `.deb` install state
(NPU hosts), and HF-cache sanity into one check; run it first when
something is off.

## Known caveats (v0.3.0-alpha.1)

These are real, in production, deferred to follow-up issues. None of
them block normal operation — but if you trip one, the symptom is
recognizable.

### 1. llama-vulkan does not emit a KV-cache metric

The bundled `/opt/llama-vulkan/llama-server` is built without the
`llamacpp:kv_cache_usage_ratio` Prometheus metric. The scrape silently
returns nothing for that gauge and the dashboard's KV% chip stays `—`
on Vulkan slots. PR #124 added a workaround that derives KV% locally
from `n_prompt_tokens / n_ctx` polled from `/slots`. Fully fixed when
the toolbox tracks an llama.cpp release that emits the gauge natively;
in the meantime, the derived value is what shows up on the dashboard.

### 2. Whisper bundle ships a broken `RUNPATH`

Lemonade ≤ 10.6 packages `whispercpp/vulkan` with `RUNPATH=/home/runner`
(a CI builder leftover). `whisper-server` exits 127 on load, which in
older Lemonade tripped the nuclear-evict-all path and wiped every other
slot. hal0 ships `/usr/local/sbin/hal0-patchelf-whisper` and runs it
via an `ExecStartPre=` drop-in on `hal0-lemonade.service` to re-apply
`patchelf --set-rpath '$ORIGIN'` on every start. If you swap in a
hand-built Lemonade tree, the drop-in becomes a no-op as long as the
binary has a sane `RUNPATH`; no action required.

### 3. Unload can deadlock in GPU cleanup

Lemonade 10.6.0 can deadlock inside `ProcessManager` GPU cleanup after
a model unload. Symptoms: the port stays open, `/v1/health` returns
500 / times out, `systemctl show hal0-lemonade` reports `NRestarts=0`
(the watchdog hasn't tripped because the process is still alive). This
is distinct from the nuclear-evict-all behavior in (2). Recovery:

```sh
systemctl restart hal0-lemonade
```

The slot manager observes the restart, drifts every loaded slot to
OFFLINE, and re-loads on next request. Tracking issue
[lemonade-sdk/lemonade#TBD](https://github.com/lemonade-sdk/lemonade).

## See also

- [Dashboard v3 tour](/docs/dashboard/v3/) — visual inspection of every
  panel that scrapes the Lemonade surface.
- [Configuration](/docs/operate/config/) — TOML layout for
  `/etc/hal0/` and the slot files.
- [Logs](/docs/operate/logs/) — journalctl recipes and the SSE log tail.
- [Slots](/docs/slots/what-is-a-slot/) — the slot lifecycle state
  machine and how it talks to Lemonade.
- ADR-0008 in `docs/internal/adr/` — the canonical decision record for
  the v0.2 Lemonade migration and the eviction model.
