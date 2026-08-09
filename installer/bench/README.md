# Strix Halo Benchmark Support Files (hal0)

GPU inference benchmarking for hal0 / Strix Halo, sweeping **both runtimes — ROCm and
Vulkan** — with the official `llama-bench`, recording structured results for hal0
tracking.

Phase 2 of the bench overhaul (2026-08) absorbed the shell harness that used to live in
this directory into Python (`src/hal0/bench/`, driven by the `hal0 bench` CLI). What's
left here is what genuinely doesn't belong in that package: the server-level A/B
harness, the operator-facing suite/window seeds, and this README.

## What remains in `installer/bench/`

| Path | What it is |
|------|-----------|
| `server_ab.py` | Tier-B server-level A/B harness — the levers `llama-bench` can't see (MTP draft depth, `--cache-reuse`, embed/rerank). Talks to `hal0-api` + the slot port as the unprivileged `hal0` user; no sudo. |
| `suites/*.toml` | Seed copies of the suite definitions (`roster.toml`, `smoke.toml`, `lane-matrix.toml`) installed to `/etc/hal0/bench/suites/` on first install only — operator-owned after that, never clobbered on upgrade. |
| `window.toml` | Seed copy of the `--scheduled` politeness window policy, installed to `/etc/hal0/bench/window.toml` the same way. |

`config.sh`, `run_benchmarks.sh`, `generate_results_json.py`, and `profile-matrix.sh` are
**gone** — the install upgrade path (`installer/install.sh`) actively removes any stale
copies an earlier install left under `/usr/lib/hal0/bench/`, since a root-owned shell
script left behind by an old install would otherwise linger as an unmaintained
privileged surface.

## Where the harness went

The composition, run, and storage logic lives in `src/hal0/bench/`:

- `harness.py` — composes the full `podman run … llama-bench -o json` argv (backend/lane
  matrix, flag dedupe, crash-retry, exclusivity) and hands it to the `hal0-benchctl`
  `exec` seam, which re-validates it structurally and execs it. See that module's
  docstring for the full Phase-1→Phase-2 shape change.
- `planner.py` — decides what's stale against a suite's selector/matrix/staleness.
- `runner.py` — drives one run session (a suite's worklist, scheduling, politeness).
- `store.py` — the result store: append-only `records.jsonl` + derived `bench.db`.
- `suites.py` — loads suite TOML into typed `Suite`/`Matrix`/`Selector` dataclasses.
- `devices.py` — GPU device-node resolution (`hal0 bench devices`).
- `publish.py`, `regress.py`, `cli.py` — roster publishing, regression detection, and the
  `hal0 bench` CLI itself.

Operators and agents drive all of this through `hal0 bench <verb>` (see
`installer/agent-skills/hal0-bench/SKILL.md`), not by invoking anything under this
directory directly.

## Layout & privilege model (D hardened-perms)

Benchmark containers are **rootful** (need `/dev/kfd` + root's podman image store); the
unprivileged `hal0` agent/API process never runs `podman` itself — it goes through the
`hal0-benchctl` sudo seam, exactly like `hal0-slotctl`. Since Phase 2 the seam is a dumb
validate-and-exec shim: the Python side composes the argv, the seam only re-checks it.

| Path | Owner | Purpose |
|------|-------|---------|
| `/usr/lib/hal0/bin/hal0-benchctl` | `root:root 0755` | the seam (`exec`/`telemetry` only) |
| `/etc/sudoers.d/hal0-benchctl` | `root:root 0440` | the grant |
| `/usr/lib/hal0/bench/server_ab.py` | `root:root` | the only script installed under the old harness path now |
| `/var/lib/hal0-bench/` | `hal0:hal0` | the v2 result store — `records.jsonl`, `bench.db`, `artifacts/` |
| `/var/lib/hal0/benchmarks/server-ab/` | `hal0:hal0` | `server_ab.py` output |

## Usage

Agents/operators use the CLI, not the seam directly:

```bash
hal0 bench plan --suite roster                      # what's stale (no GPU, no writes)
hal0 bench run  --suite roster --budget-min 120      # run the plan (or a slice of it)
hal0 bench run  --suite lane-matrix                  # the tuning-oriented lane/depth sweep
hal0 bench results --json                            # current values from bench.db
hal0 bench history --cell <cell_key> --json           # trend for one cell over time
```

`hal0 bench run --suite <path-to-toml>` also accepts an ad-hoc suite file directly, not
just an id under `/etc/hal0/bench/suites/`. See `installer/agent-skills/hal0-bench/
SKILL.md` for the full verb list and `references/seam.md` for what the seam itself
validates.

## GPU device passthrough (issue #1303)

Device selection is never hardcoded — `hal0.bench.devices` reuses the same helpers the
production slot containers use (`hal0.providers._gpu`) plus the `hal0 probe` snapshot in
`hardware.json`, so bench and slot containers derive their GPU nodes and render/video
GIDs from one source of truth:

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
device-node shape + must be a character device, otherwise the run aborts **before** any
container starts, naming the paths it checked):

| Variable | Meaning |
|----------|---------|
| `HAL0_BENCH_GPU_DEVICES` | full node list, comma/colon separated |
| `HAL0_BENCH_KFD_DEVICE` / `_CARD_DEVICE` / `_RENDER_DEVICE` | individual nodes |
| `HAL0_BENCH_GPU_GROUPS` | numeric GIDs for `--group-add` |
| `HAL0_BENCH_TIER` | pin `amd` / `nvidia` / `cpu` |
| `HAL0_BENCH_KFD_PATH` / `HAL0_BENCH_DRI_DIR` | relocate the discovery roots |
| `HAL0_BENCH_PYTHON` | interpreter used to run the resolver |

## GPU contention

One iGPU, shared with the live inference slots. A suite marked `exclusive = true` stops
the active GPU slots before running and restarts them on exit (briefly offlines
production); a non-exclusive suite (e.g. `smoke`) may run over live traffic, and its
cells are stamped `outcome="skipped-contended"` rather than `"ok"`. `hal0-slot@npu` is
GPU-free and ignored by exclusivity either way.

## Tuning / extending

- **Backend/lane matrix** — `hal0.bench.harness.lane_specs()` (code, not a shell config).
  Add a lane there, then extend the entrypoint whitelist in
  `installer/wrappers/hal0-benchctl`'s `validate_entrypoint` if it ships a new binary —
  the seam rejects anything not on that closed set.
- **What gets measured, at what depth/config** — a suite TOML under
  `/etc/hal0/bench/suites/` (seeds in `suites/` here). `[matrix].configs` is the
  flag-tuning axis: a list of `{label, flags}` variants, each a whitelisted llama-bench
  tuning-flag set (`-b -ub -ngl -fa -ctk -ctv -t -mmp -pg`); every variant becomes its
  own measured cell. `lane-matrix.toml` is the suite feeding `hal0-tune`'s sweeps.
- **When it's polite to run on a schedule** — `window.toml` (seed here, operator-owned
  at `/etc/hal0/bench/window.toml`), consulted only by `hal0 bench run --scheduled`.

## Server-level A/B (`server_ab.py`)

Tier B: the server-only levers `llama-bench` can't see — MTP draft depth
(`--spec-draft-n-max`), `--cache-reuse` on a shared-prefix trace, poll, and the
embed/rerank endpoints. Talks to `hal0-api` + the slot port as the `hal0` user (no
sudo), always restores the slot's original `extra_args`, and writes JSON to
`/var/lib/hal0/benchmarks/server-ab/`. Supersedes the ad-hoc `/root/bench_mtp.py`.

## Scope & roadmap

Now: `hal0 bench` (Python harness + CLI + API) drives the toolbox sweep (raw
`llama-bench` across backends) via declarative suites, including the tuning-oriented
`lane-matrix` config-grid suite, plus `server_ab.py` for Tier-B server-level A/Bs incl.
MTP/draft-speculative. Deferred: RPC bench (needs ≥2 nodes), pi-bench (coding-agent
eval). The `hal0 bench` CLI + `/api/benchmarks` routes are the landed end-state this
directory used to describe as future work.
