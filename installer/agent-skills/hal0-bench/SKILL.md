---
name: hal0-bench
category: homelab-ops
description: Running LLM inference benchmarks on hal0 / Strix Halo. Use when asked to benchmark a model, measure tokens/sec (prompt-processing or generation), compare the ROCm vs Vulkan backends, sweep context lengths, or refresh benchmark data. Encodes the structural fact that benchmarking is a rootful GPU op the unprivileged agent reaches ONLY through the hal0-benchctl sudo seam, and that the single iGPU is shared with the live inference slots.
---

# hal0 benchmarking

Benchmarks llama.cpp inference on this box across **both runtimes (ROCm and Vulkan)**
using the official `llama-bench`, and records structured results for hal0 tracking.

You (the `hal0` agent user) are unprivileged. Benchmark containers are **rootful** and
need `/dev/kfd` + root's podman image store. Your entry point is the **`hal0 bench`
CLI** (and the Benchmarks dashboard) — never call `podman`/`systemctl` directly. Under
the hood, `hal0 bench run` composes the full podman/llama-bench argv in Python and hands
it to the **`hal0-benchctl` sudo seam**, which re-validates it structurally and execs it
— the same hardened-seam pattern as `hal0-slotctl`. You never invoke the seam yourself.

```bash
hal0 bench <verb> [flags]
```

- **Harness** (yours to read, not the seam): `src/hal0/bench/` — `harness.py` composes
  the podman/llama-bench argv, `planner.py` decides what's stale, `runner.py` drives a
  run session, `store.py` is the result store.
- **Results** (yours to read): the v2 store under `/var/lib/hal0-bench/` —
  `records.jsonl` (source of truth, append-only) + `bench.db` (derived SQLite index) —
  and the API (`/api/benchmarks/...`, including `/api/benchmarks/regressions`).
- **Backends (lanes):** `rocm` and `vulkan_radv` share the rocmfpx runner image
  (`ghcr.io/hal0ai/...`, `/opt/rocmfpx/bin/llama-bench`); `cpu` runs the lean
  Vulkan/CPU toolbox image — the matrix is `hal0.bench.harness.lane_specs()`, not a
  shell config file.

## When to use this skill

- "Benchmark <model>" / "ROCm vs Vulkan speed?" / "measure tg or pp t/s".
- Refresh the dataset after a new model/quant/image (usually via a suite, not one-off).
- As the measurement engine for the [`hal0-tune`](../hal0-tune/SKILL.md) skill.

## Commands (the v2 CLI)

```bash
hal0 bench plan    --suite <id|path>            # what's stale and why (no GPU, no writes)
hal0 bench run     --suite <id|path> [--budget-min N] [--dry-run] [--scheduled]
hal0 bench status  [--json]                     # recent records / session state
hal0 bench worker  [--poll SECONDS]             # drain the dashboard run queue
hal0 bench results [--model ID] [--since ISO] [--limit N] [--json]
hal0 bench history [--cell KEY] [--model ID] [--limit N] [--json]
hal0 bench reindex                              # rebuild bench.db from records.jsonl
hal0 bench devices [--format text|env|json|flags]  # GPU device nodes a run will use
hal0 bench publish [--check] [--site-ts PATH]   # regenerate roster.json
hal0 bench eval    --models A,B [--task ID ...] [--dry-run] [--force]  # agentic eval
```

`--suite` accepts a suite id (looked up under `/etc/hal0/bench/suites/`, e.g. `roster`,
`smoke`, `lane-matrix`) or a path to a `.toml`. Everything above is a plain argparse
passthrough — run `hal0 bench <verb> --help` for the exact flags on this build.

There is no `hal0 bench sweep`/`run-model`/`aggregate`/`list` any more — a one-off model
run is just a suite whose `[selector].include` names one model (the dashboard's "+"
button on a roster row builds exactly that), and results are queried live from the store,
never rebuilt into a file.

## The GPU rule (most important)

There is **one iGPU**, shared with the live inference slots (`hal0-slot@agent`,
`@nano`, …). A suite marked `exclusive = true` stops the active GPU slots before
running and restarts them on exit — this briefly takes production inference offline, so
only run one when nothing is mid-request, and confirm recovery afterwards
(`hal0 slot status`). A non-exclusive suite (e.g. `smoke`) may run over live traffic; its
records are stamped `outcome="skipped-contended"` instead of `"ok"` and never feed
regression detection.

- `hal0-slot@npu` does not use the GPU and is ignored by exclusivity.
- `hal0 bench run --scheduled` additionally applies the politeness window
  (`/etc/hal0/bench/window.toml`) — the weekly timer's mode, not a manual run's.

## Reading results

- `hal0 bench results --json` / `hal0 bench history --cell <key> --json` — query the
  store directly; no aggregation step, no generated file to go stale.
- The Benchmarks dashboard surfaces roster comparisons, per-run outcomes, trigger/config
  provenance, and regressions.
- `/api/benchmarks/regressions` — cells whose latest value is a statistically
  significant drop from their trailing baseline.
- Each record's `Outcome` is one of `ok, failed, skipped-contended, oom, hang` — only
  `ok` is a measured/publishable value; don't report the others as real numbers.

**Sanity gate:** a real GPU run shows `lane`=`rocm`/`vulkan_radv` and a GPU device in the
run's `host` block naming the Radeon 8060S. CPU-low t/s with no GPU device means the run
landed on the `cpu` lane or was contended — don't report it as a GPU number.

## How this fits hal0 (D hardened-perms)

`hal0-benchctl` is the entire privileged surface, and it is now a dumb validate-and-exec
shim: `exec [--timeout-s N] -- podman run ...` re-validates every element of an
already-composed argv (model path, device flags, image, entrypoint, the llama-bench flag
whitelist) and execs it — no shell, no arbitrary args, no matrix knowledge of its own.
`telemetry start|end <run_id> [tier]` samples GPU counters during a run. Grant:
`/etc/sudoers.d/hal0-benchctl`. See `references/seam.md` for the seam internals. To
extend the backend matrix, an operator edits `hal0.bench.harness.lane_specs()` (code,
not a shell config); to extend what gets measured, an operator edits/adds a suite TOML
under `/etc/hal0/bench/suites/`.
