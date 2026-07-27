# Strix Halo Benchmark Harness (hal0)

GPU inference benchmarking for hal0 / Strix Halo, sweeping **both runtimes — ROCm and
Vulkan** — with the official `llama-bench`, emitting structured JSON for Hal0 tracking.

Ported from
[`kyuz0/amd-strix-halo-toolboxes/benchmark`](https://github.com/kyuz0/amd-strix-halo-toolboxes/tree/main/benchmark),
adapted to drive the container images already in root's podman via `podman` (not `toolbox`).

## Layout & privilege model (D hardened-perms)

This harness is **root-owned and root-executed**. The unprivileged `hal0` agent never runs
it directly — it goes through the `hal0-benchctl` sudo seam, exactly like `hal0-slotctl`.

| Path | Owner | Purpose |
|------|-------|---------|
| `/usr/lib/hal0/bench/` | `root:root` | this harness (not agent-writable, off NFS) |
| `/usr/lib/hal0/bin/hal0-benchctl` | `root:root 0755` | the seam (validates args, execs harness) |
| `/etc/sudoers.d/hal0-benchctl` | `root:root 0440` | the grant |
| `/var/lib/hal0/benchmarks/` | `hal0:hal0` | results (`runs/`, `logs/`, `index.json`, `SUMMARY.md`) |

## Usage

Agents/operators use the seam:

```bash
S="sudo -n /usr/lib/hal0/bin/hal0-benchctl"
$S run --exclusive                 # full curated sweep, clean GPU
$S run-model <rel.gguf>            # one model, both backends
$S sweep <rel.gguf> <backend> -ub 512,1024,2048   # tuning (whitelisted flags)
$S aggregate                       # rebuild index.json + SUMMARY.md
$S list
```

Direct (root shell, e.g. operator on hal0) — the engine under the seam:

```bash
/usr/lib/hal0/bench/run_benchmarks.sh --help
/usr/lib/hal0/bench/run_benchmarks.sh --all-models --contexts all --exclusive
/usr/lib/hal0/bench/generate_results_json.py /var/lib/hal0/benchmarks
```

`run_benchmarks.sh` also accepts `--force` (run on a busy GPU; contended numbers) and
`--dry-run`; the seam deliberately does **not** expose `--force`.

## GPU device passthrough (issue #1303)

`config.sh` hardcodes **no** device node. It calls `hal0.bench.devices`, which reuses the
same helpers the production slot containers use (`hal0.providers._gpu`) plus the
`hal0 probe` snapshot in `hardware.json`, so bench and slot containers derive their GPU
nodes and render/video GIDs from one source of truth:

| Tier | podman flags |
|------|--------------|
| AMD | `--device=/dev/kfd` + every real `/dev/dri` char device + the real render/video GIDs |
| NVIDIA | `--device=nvidia.com/gpu=all` (CDI — no paths, no `--group-add`) |
| CPU | none — a CPU-tier run never requires a DRI node |

Inspect what a run will get (no GPU touched, no writes):

```bash
hal0 bench devices              # human summary
hal0 bench devices --format json
```

Overrides, for unusual passthrough layouts and recovery (each is validated: allowed
device-node shape + must be a character device, otherwise the sweep aborts **before** any
container starts, naming the paths it checked):

| Variable | Meaning |
|----------|---------|
| `HAL0_BENCH_GPU_DEVICES` | full node list, comma/colon separated |
| `HAL0_BENCH_KFD_DEVICE` / `_CARD_DEVICE` / `_RENDER_DEVICE` | individual nodes |
| `HAL0_BENCH_GPU_GROUPS` | numeric GIDs for `--group-add` |
| `HAL0_BENCH_TIER` | pin `amd` / `nvidia` / `cpu` |
| `HAL0_BENCH_KFD_PATH` / `HAL0_BENCH_DRI_DIR` | relocate the discovery roots |
| `HAL0_BENCH_PYTHON` | interpreter used to run the resolver |

## ⚠️ GPU contention

One iGPU, shared with the live inference slots. The harness refuses to run while a GPU slot
is active; `--exclusive` stops/restarts them for clean numbers (briefly offlines production).
`hal0-slot@npu` is GPU-free and ignored.

## Tuning / extending (operator, edits config.sh)

- Add a backend: entry in `BACKENDS` (`image|bench_bin|ubatch|env`) + `BACKEND_ORDER`.
- Add a context: entry in `CTX_CONFIGS` (`args|reps`, `%UB%` = per-backend ubatch).
- Curated default model set: `DEFAULT_MODELS`. Common flags: `COMMON_BENCH_ARGS`.

## Profile-matrix (seed-profile re-tune)

Two companions script the flag re-tune matrix from the profile-consolidation
handoff (2026-07-04):

- **`profile-matrix.sh`** — Tier A: the llama-bench cells (batch/ubatch grids
  per profile class, symmetric KV-quant at depth on both backends, threads
  sanity) as `hal0-benchctl sweep` calls. Runs as the unprivileged user;
  `--dry-run` prints the seam commands; `--cell` selects a subset.
- **`server_ab.py`** — Tier B: the server-only levers llama-bench can't see —
  MTP draft depth (`--spec-draft-n-max`), `--cache-reuse` on a shared-prefix
  trace, poll, and the embed/rerank endpoints. Talks to hal0-api + the slot
  port as the hal0 user (no sudo), always restores the slot's original
  `extra_args`, and writes JSON to `/var/lib/hal0/benchmarks/server-ab/`.
  Supersedes the ad-hoc `/root/bench_mtp.py`.

## Scope & roadmap

Now: the kyuz0 **toolbox sweep** (raw `llama-bench` across backends) + a tuning `sweep` verb
+ the profile-matrix pair above (Tier A seam cells, Tier B server-level A/Bs incl.
MTP/draft-speculative). Deferred: RPC bench (needs ≥2 nodes), pi-bench (coding-agent eval).
Upstream end-state: a `hal0 bench` CLI + `/api/benchmarks` route reading `index.json`,
landed in the hal0 git repo.
