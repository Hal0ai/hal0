# ADR 0023 — Bump Lemonade's llama.cpp Vulkan pin (b9253 → b9484) for the qwen3next perf path

- **Status:** Accepted (implemented 2026-06-03)
- **Supersedes/relates:** [[0022-backend-selection-display-control]] (per-slot backend honoring)

## Context

Lemonade 10.6.0 bundles a **frozen llama-server build `b9253`** (compiled 2026-05-20,
ggml 0.12.0). Our primary coding model is `qwen3-coder-next` — architecture
`qwen3next`, a linear/SSM-attention hybrid MoE. The **optimized Vulkan kernels for
`qwen3next` landed in llama.cpp *after* `b9253`**, so the bundled binary runs an
unoptimized fallback path.

A controlled ROCm-vs-Vulkan benchmark (4 cells, median of 3) established:

| model | backend | gen tok/s | note |
|---|---|---|---|
| coder-next (MoE) | rocm | 8.71 | |
| coder-next (MoE) | vulkan | 8.90 | |
| qwen3.6-27b (dense) | rocm | 2.05 | |
| qwen3.6-27b (dense) | vulkan | 2.04 | |

Findings:
- **Backends are a tie** on gen tok/s — so the default backend is *not* the lever, and
  ADR-0022's job (honor + truthfully display the per-slot choice) was the right fix.
- Measured **8.9 tok/s vs ~45 tok/s** community baseline on the same model + hardware.
  Tuning every flag (`-fa on`, `--threads 28`, `--batch-size 256`, ctx 8192) moved
  nothing → **not config**.
- **Not storage / not NFS.** `/mnt/ai-models` is a *local* Gen4 NVMe (`devpool`, Sabrent
  Rocket 4 Plus), 1.2 GB/s direct read. During inference the model is GPU-resident:
  **16 MB disk read across 768 generated tokens**. Storage affects *load* time
  (CPU param-fit bound, ~minutes), never tok/s.
- Root cause is therefore **build vintage**, full stop.

## Decision

Use Lemonade's **own backend-version pin** to pull a fresh official build — the
lemonade-native, persistent path. (A from-source build + `patchelf $ORIGIN` swap into
`bin/llamacpp/vulkan/` was the first attempt; it FAILS because lemond *restores the
binary that matches the pin* on every model load — see Consequences. Pinning is the
correct fix.)

1. **Bump the pin** in `/opt/lemonade/resources/backend_versions.json`:
   `llamacpp.vulkan: "b9253" → "b9484"` (latest `ggml-org/llama.cpp`, 2026-06-02 — commit
   `63e66fd`, which has the post-b9253 qwen3next Vulkan kernels).
2. Restart lemond, then `POST /api/v1/install {"recipe":"llamacpp","backend":"vulkan"}` —
   lemond downloads `llama-b9484-bin-ubuntu-vulkan-x64.tar.gz` from the official release
   and installs it to `bin/llamacpp/vulkan/`.
3. **Pin RADV** (≫ AMDVLK on gfx1151): systemd drop-in
   `/etc/systemd/system/hal0-lemonade.service.d/20-vulkan-radv.conf` →
   `Environment=AMD_VULKAN_ICD=RADV`.
4. `llamacpp.args = "--parallel 1 -fa on --threads 8"` in `config.json`. `ctx_size`
   stays `65536` (product decision — full context retained).

## Result

`qwen3-coder-next` Vulkan: **8.9 → ~35 tok/s (3.9×)** (commit `63e66fd`, benchmarked
standalone). **Persistent** — the binary stays `b9484` across model loads because it now
matches the pin (verified). Loads + generates through the live lemond path. `ctx_size=65536`
kept; a single MoE still occupies ~48 GiB GTT (KV cache) — accounted for in the dashboard
memory map (now anchored to the 80 GiB GTT cap).

## Reversibility

Restore the pin: set `llamacpp.vulkan` back to `b9253` in `backend_versions.json`
(backup at `backend_versions.json.pre-b9484-bak`), restart lemond, re-install. The
original binaries are also at `vulkan.b9253-bak` / `vulkan.b9253-prev`.

## Consequences / follow-ups

- **Persistent across loads** (pin matches binary) — but the pin lives in
  `/opt/lemonade/resources/backend_versions.json`, which a lemonade **reinstall/upgrade**
  resets. hal0's installer should set this pin post-lemonade-install (#438). Bump the pin
  as ggml-org advances and re-trigger install.
- **35 → ~45 tok/s headroom:** b9484 is the *generic* official Vulkan build. kyuz0's
  gfx1151-tuned RADV build (`ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv`) reportedly
  hits ~45. To use it without leaving the lemonade-native path, point
  `llamacpp.vulkan_bin` at a bins dir holding that binary (instead of `"builtin"`).
- **ROCm path deferred:** fresh gfx1151 ROCm builds live in `lemonade-sdk/llamacpp-rocm`
  (`rocm-nightly` pin, latest b1287) but (a) gfx1151 ROCm needs kernel **≥6.18.4** (pve on
  6.17.13) and (b) `rocm-nightly` has a 64 GB allocation cap (TheRock #4645). Backends tie
  + slots are vulkan, so Vulkan is the right near-term path.
- **`--threads 8`** is concurrency-safety (avoids the multi llama-server oversubscription
  deadlock under `max_loaded_models=4`); a single-model load could push threads higher.
- **Model load latency** (~minutes for a 47 GB MoE) is CPU param-fit bound, not disk;
  separate optimization (`--no-mmap`, keep-resident) tracked separately.
