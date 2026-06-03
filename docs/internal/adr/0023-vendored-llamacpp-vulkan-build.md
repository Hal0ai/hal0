# ADR 0023 — Vendor a fresh llama.cpp Vulkan build for the qwen3next perf path

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

Vendor a fresh llama.cpp build for the lemonade **Vulkan** path:

1. **Build on the LXC** (CT 105, Debian 13) for glibc/mesa compatibility — *not* on the
   Arch hal0-dev VM (newer glibc → `GLIBC_2.xx not found`). Recipe:
   ```
   git clone --depth 1 https://github.com/ggml-org/llama.cpp /root/llama.cpp-build
   cmake -B build -DGGML_VULKAN=ON -DLLAMA_CURL=ON -DCMAKE_BUILD_TYPE=Release
   cmake --build build -j$(nproc) --target llama-server
   ```
   (built commit `63e66fd`, ggml 0.13.1, GNU 13.3.0)
2. **Install relocatable** into `/var/lib/hal0/lemonade/bin/llamacpp/vulkan/`: copy
   `llama-server` + all `*.so*`, then `patchelf --set-rpath '$ORIGIN'` on every ELF so
   transitive ggml libs resolve to the install dir (not the build tree). Verified via
   `ldd` (all `$ORIGIN`, no build-dir refs, no "not found"). Owned `hal0:hal0`.
3. **Pin RADV** (≫ AMDVLK on gfx1151): systemd drop-in
   `/etc/systemd/system/hal0-lemonade.service.d/20-vulkan-radv.conf` →
   `Environment=AMD_VULKAN_ICD=RADV`.
4. `llamacpp.args = "--parallel 1 -fa on --threads 8"` in `config.json`. `ctx_size`
   stays `65536` (product decision — full context retained).

## Result

`qwen3-coder-next` Vulkan: **8.9 → ~35 tok/s (3.9×)**, verified standalone *and* through
the live lemond load path. `ctx_size=65536` kept; a single MoE still occupies ~48 GiB
GTT (KV cache) — accounted for in the dashboard memory map (now anchored to the 80 GiB
GTT cap).

## Reversibility

Original binary preserved at `vulkan.b9253-bak` and `vulkan.b9253-prev`. Revert:
```
cd /var/lib/hal0/lemonade/bin/llamacpp && rm -rf vulkan && mv vulkan.b9253-prev vulkan
rm /etc/systemd/system/hal0-lemonade.service.d/20-vulkan-radv.conf
# restore config.json llamacpp.args to "--parallel 1"
systemctl daemon-reload && systemctl restart hal0-lemonade
```

## Consequences / follow-ups

- **A lemonade bundle upgrade will clobber the swapped binary.** This swap is a runtime
  override, not yet persistent. Follow-up: re-apply via installer step or an
  `ExecStartPre` hook keyed on lemonade version (tracked separately).
- **The ROCm binary is still `b9253`-vintage** (lacks `rocWMMA-FA`). Since backends tie
  and our slots are `gpu-vulkan`, this is deferred; a `-DGGML_HIP_ROCWMMA_FATTN=ON` ROCm
  build is the future path for high-ctx ROCm slots.
- **`--threads 8`** is a concurrency-safety choice (avoids the documented multi
  llama-server oversubscription deadlock under `max_loaded_models=4`); a single-model
  load could push threads higher toward the ~45 tok/s ceiling. Tuning follow-up.
- **Model load latency** (~minutes for a 47 GB MoE) is CPU param-fit bound, not disk;
  separate optimization (e.g. `--no-mmap`, keep-resident policy) tracked separately.
