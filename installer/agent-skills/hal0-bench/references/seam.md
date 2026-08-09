# hal0-benchctl seam — internals & layout

Benchmarking is a rootful GPU operation; the unprivileged `hal0` user reaches it only
through this seam, following hal0's D hardened-perms model (cf. `hal0-slotctl`,
`hal0-agentenv`).

Phase 2 of the bench overhaul (2026-08) absorbed the shell harness this seam used to
exec into Python (`hal0.bench.harness`). The unprivileged runner now composes the FULL
`podman run … llama-bench -o json` argv itself; this seam re-validates every element of
that already-composed argv structurally and execs it. No matrix knowledge, no
composition, no retries, no shell evaluation — the grant in
`/etc/sudoers.d/hal0-benchctl` can never be widened into arbitrary command execution.

## Components (installed)

| Path | Owner | Purpose |
|------|-------|---------|
| `/usr/lib/hal0/bin/hal0-benchctl` | `root:root 0755` | the seam — validates the composed argv, execs it |
| `/etc/sudoers.d/hal0-benchctl` | `root:root 0440` | `hal0 ALL=(root) NOPASSWD: …/hal0-benchctl` |
| `/usr/lib/hal0/bench/server_ab.py` | `root:root` | the Tier-B server-level A/B harness (only script left under here — everything else is `src/hal0/bench`) |
| `/var/lib/hal0-bench/` | `hal0:hal0` | the v2 result store — `records.jsonl` (source of truth), `bench.db` (derived index), `artifacts/` (telemetry, logs) |

The seam script itself lives on a **local root-owned path** (not the `/mnt` NFS mount)
precisely so the agent can't tamper with a script that runs as root. There is no
root-owned harness directory to protect any more — the harness is the ordinary
`hal0.bench` package the unprivileged `hal0-api`/CLI process already runs; only the
`podman run` invocation itself crosses into root.

## Seam verbs & validation

- `exec [--timeout-s N] -- podman run ...` — validate one already-composed
  `podman run … --entrypoint llama-bench … -m /abs/model.gguf … -o json` argv and exec
  it. The unprivileged caller supplies the FULL argv; this side re-validates every
  element independently (never trusts the caller's own validation as a control):
  - device flags (`--device=`, `--group-add=`): allowed roots only, and the node must
    exist and be a real character device on THIS host;
  - `--volume=`: exactly the read-only model-store self-mount, nothing else;
  - `-e`: only `GGML_*`/`HSA_*` env vars with safe values;
  - `--security-opt`: only `apparmor=unconfined` / `seccomp=unconfined`;
  - `--entrypoint`: a closed set of shipped `llama-bench` binaries;
  - image: must match the hal0 GHCR namespace;
  - model path: no `..`, must match `^[A-Za-z0-9][A-Za-z0-9._/-]*\.gguf$`, must resolve
    under the model-store root (`hal0.config.paths.model_store_root()`, not a hardcoded
    path);
  - llama-bench flags after `-m <model>`: only `-o json` (required) plus the whitelisted
    tuning flags `-dev -b -ub -ngl -fa -ctk -ctv -p -n -d -r -t -mmp -pg`, with
    numeric/comma/quant-shaped values;
  - `--rm` is required so the container reaps itself.
  The optional `--timeout-s N` wraps the exec in `timeout --kill-after=30 N` — enforced
  on the root side because the unprivileged caller cannot signal this process tree.
- `telemetry start|end <run_id> [tier]` — 1 Hz GPU sampler, unchanged by Phase 2. The
  optional tier (`amd|nvidia|cpu`) is positional because the sudoers grant has no
  `env_keep`; pass `cpu` so a CPU-tier run does not log the box's idle GPU. Missing
  counters are written as JSON `null`, never 0.

Retired verbs (Phase 2): `run`, `run-model`, `sweep` (the runner composes cells itself
now — no privileged sweep composition), `aggregate` (the v1 `index.json`/`SUMMARY.md`
surface is gone; results live in the v2 store + `/api/benchmarks`), `list` (nothing
writes a `runs/` directory any more).

Exclusivity (stopping/restarting GPU slots around an exclusive suite) is Python-side now
too, via the `hal0-systemctl` seam (`ExclusiveSlots` in `hal0.bench.harness`) — it is no
longer part of `hal0-benchctl`'s job.

## Backend/lane matrix

Lives ONLY in `hal0.bench.harness.lane_specs()` (a pure dict of `LaneSpec`) — not in a
shell config file. Images come from `hal0.config.schema` (`DEFAULT_ROCMFPX_IMAGE`,
`FALLBACK_VULKAN_IMAGE`).

- `rocm` — rocmfpx image, `/opt/rocmfpx/bin/llama-bench`, `-ub 2048`,
  `GGML_HIP_ENABLE_UNIFIED_MEMORY=1`, `-ngl 99 -dev ROCm0`.
- `vulkan_radv` — same rocmfpx image + binary, `-ub 512`, `-ngl 99 -dev Vulkan0`.
- `cpu` — the lean Vulkan/CPU toolbox image, `/usr/local/bin/llama-bench`, `-ub 512`,
  `-ngl 0`, no device flags.

`hal0.bench.harness.default_lanes(tier)` picks `cpu` alone when the device resolver
reports the CPU tier, else both GPU lanes — the tier-scoped default a suite's
`[matrix].lanes = ["default"]` (or an explicit lane list) can override. Which cells run
at all, and under what config/depth grid, is declared in a suite TOML under
`/etc/hal0/bench/suites/`, not in this seam.

## Revoke

```bash
sudo rm /etc/sudoers.d/hal0-benchctl     # removes the grant; the seam script stays, agent can't invoke it
```

## Agent-facing surface

The agent does not call this seam directly — it drives the `hal0 bench` CLI (`plan`,
`run`, `status`, `results`, `history`, `reindex`, `devices`, `publish`, `eval`) or the
Benchmarks dashboard, both of which land in `hal0.bench.harness` → this seam for the
actual rootful `podman run`. See the skill's `SKILL.md` for the CLI surface.
