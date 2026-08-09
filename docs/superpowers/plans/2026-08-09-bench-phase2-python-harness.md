# Bench Phase 2 — Absorb the Shell Harness into Python — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `src/hal0/bench` builds and runs the `podman run … llama-bench -o json`
command itself; `installer/bench/run_benchmarks.sh`, `config.sh`,
`profile-matrix.sh`, and `generate_results_json.py` retire; `installer/wrappers/
hal0-benchctl` shrinks to a dumb argv-validating sudo shim (`exec` + `telemetry`
verbs only).

**Architecture:** A new unprivileged module `src/hal0/bench/harness.py` owns the
backend/lane matrix (importing the image constants from `hal0.config.schema` —
no more hand-mirrored copies), composes the full podman argv using
`hal0.bench.devices.resolve_bench_devices()` in-process, and runs each cell via
`sudo -n hal0-benchctl exec [--timeout-s N] -- podman run …`, capturing stdout
directly (the `<stem>__<lane>__sweep.json` file dance is deleted — handoff says
"migrate the runner to read output directly … better"). Exclusivity moves to
Python via the existing `hal0-systemctl stop|start <slot-id>` sudo seam. The
shim validates every argv element structurally (no matrix knowledge) and execs.

**Tech Stack:** Python 3.12, stdlib only (subprocess/json/dataclasses); bash for
the shim; pytest.

## Global Constraints

- Tests: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/bench -p no:cacheprovider`
- Before any push: `make lint` AND `ruff format --check src tests` (CI runs both).
- `make typecheck` baseline on main ≈537 errors — delta only, don't chase green.
- Preserve Phase-1 behavior contracts:
  - exclusive stop/restart of active GPU slots per Tier-A sweep (memoised per
    (model,lane,depth,config) group);
  - per-attempt `timeout(1)` lives on the ROOT side of the privilege boundary
    (shim `--timeout-s`), timed-out attempt (rc 124, or 137 past the cap) never
    crash-retries;
  - 6× retry on rocmfpx init-segfault: retry only rc≥128; rc==0 with EMPTY
    stdout normalises to rc 139 (podman --rm race) and retries; rc<128 real
    failures never retry;
  - meta provenance keeps the exact keys `backend,image,context,tag,extra,reps,
    ubatch,model_rel,model_path,host,tier,gpu,timestamp` (parsers.py reads it);
  - unknown kinds rejected at plan time; `_KIND_TO_MODE` untouched.
- Security: `hal0-benchctl` + `packaging/sudoers/hal0-benchctl` are a privileged
  surface. The ORCHESTRATOR writes the shim; subagents do NOT touch
  `installer/wrappers/hal0-benchctl`, `packaging/sudoers/`, or `install.sh`.
  The PR is never automerged.
- `server_ab.py` stays (Phase 3 replaces it). Tier-B/C paths unchanged.
- Do not modify `src/hal0/bench/devices.py` behavior (best-tested module; it
  gains one pure helper only, see Task I1).

---

## Interface contract (both agents build against THIS, verbatim)

New file `src/hal0/bench/harness.py`:

```python
from __future__ import annotations
from dataclasses import dataclass
from .devices import BenchDeviceSpec, TIER_CPU

BENCHCTL = "/usr/lib/hal0/bin/hal0-benchctl"
SYSTEMCTL_SEAM = "/usr/lib/hal0/bin/hal0-systemctl"
MAX_ATTEMPTS = 6

@dataclass(frozen=True)
class LaneSpec:
    lane: str                 # "rocm" | "vulkan_radv" | "cpu"
    image: str                # from hal0.config.schema constants — NOT literals
    bench_bin: str            # "/opt/rocmfpx/bin/llama-bench" | "/usr/local/bin/llama-bench"
    ubatch: int               # 2048 rocm, 512 others
    env: tuple[str, ...]      # ("GGML_HIP_ENABLE_UNIFIED_MEMORY=1",) rocm; () others
    dev_args: tuple[tuple[str, str], ...]  # (("-ngl","99"),("-dev","ROCm0")) etc.

def lane_specs() -> dict[str, LaneSpec]: ...
def default_lanes(tier: str) -> list[str]:     # cpu tier -> ["cpu"]; else ["rocm","vulkan_radv"]
def dedupe_flags(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """later-source-wins, first-seen order (port of dedupe_flag_pairs)."""
def compose_podman_argv(
    spec: LaneSpec, devices: BenchDeviceSpec, model_path: str, model_root: str,
    flags: list[tuple[str, str]],
) -> list[str]:
    """['podman','run','--rm',*device_flags,'--security-opt','apparmor=unconfined',
       '--security-opt','seccomp=unconfined',f'--volume={root}:{root}:ro,z',
       *('-e',kv for kv in spec.env),'--entrypoint',spec.bench_bin,spec.image,
       '-m',model_path,*flat(dedupe(common+dev_args+flags)),'-o','json']
    COMMON args: [('-fa','1'),('-mmp','0')]. '%UB%' never appears (caller passes
    real values). devices.run_flags supplies --device/--group-add."""
def benchctl_exec_argv(podman_argv: list[str], timeout_s: int | None) -> list[str]:
    """['sudo','-n',BENCHCTL,'exec',*(['--timeout-s',str(timeout_s)] if timeout_s else []),'--',*podman_argv]"""

@dataclass
class CellResult:
    rows: list[dict]          # parsed llama-bench -o json stdout ([] on failure)
    meta: dict                # provenance, exact legacy keys
    rc: int
    tail: str                 # last 4000 chars of stderr/log

def run_cell(
    spec: LaneSpec, devices: BenchDeviceSpec, *, model_rel: str, model_root: str,
    flags: list[tuple[str, str]], timeout_s: int | None, log_path,
    runner=None,              # injectable callable(argv, timeout) -> (rc, stdout, stderr) for tests
) -> CellResult:
    """One cell: compose argv, up to MAX_ATTEMPTS attempts with the Phase-1
    retry/normalisation rules, stdout parsed as JSON rows, meta built here."""

class ExclusiveSlots:
    """Context manager: on __enter__, list active hal0-slot@* units minus
    hal0-slot@npu (via `systemctl list-units 'hal0-slot@*' --no-legend
    --state=active`, unprivileged read), stop each via
    ['sudo','-n',SYSTEMCTL_SEAM,'stop',<id>]; sleep 3 if any stopped.
    On __exit__, restart each stopped id via 'start' (best-effort, warn on
    failure). Injectable run callable for tests. Slot id = the template
    instance ('agent' from 'hal0-slot@agent.service')."""
```

`runner.py` changes:
- `_tier_a_cmd` now returns `harness.benchctl_exec_argv(compose_podman_argv(...), per_attempt)`
  (used by describe_worklist for dry-run display).
- `_tier_a_record` calls `harness.run_cell(...)` inside `ExclusiveSlots()` when
  `exclusive=True` (retry loop is INSIDE run_cell; slots stop/restart once per
  memoised group, exactly like the old per-sweep `--exclusive`).
- DELETE `_sweep_stem`, `_sweep_output_path`, `_clear_stale_sweep`,
  `_locate_sweep_output`, `v1_runs_dir` (+ their tests): output comes from
  stdout. Artifacts: rows -> `artifacts/llama-bench.json`, meta ->
  `artifacts/meta.json` (same as today, written from CellResult).
- `_rel_gguf`/`_model_roots` stay (model_rel still needed for meta + `-m` path
  build: `model_path = f"{model_root}/{model_rel}"`).
- Watchdog `_run_subprocess` stays for Tier-B/C; Tier-A outer watchdog wraps
  run_cell via the same per-attempt/outer sizing (`_tier_a_timeouts` unchanged).

Retired files (orchestrator removes + updates `installer/install.sh`):
`installer/bench/run_benchmarks.sh`, `installer/bench/config.sh`,
`installer/bench/profile-matrix.sh`, `installer/bench/generate_results_json.py`.
Retired benchctl verbs: `run`, `run-model`, `sweep`, `aggregate`, `list`.

New shim verb (orchestrator writes): `hal0-benchctl exec [--timeout-s N] -- podman run …`
— sequential structural validation (podman literal, `run` literal, `--rm`
required, device flags per the old `_bench_valid_flag` rules, security-opt
whitelist, exact `--volume=$MODEL_ROOT:$MODEL_ROOT:ro,z`, `-e` limited to
`GGML_*`/`HSA_*` KEY=VAL, entrypoint whitelist
`(/opt/rocmfpx/bin|/usr/local/bin)/llama-bench`, image
`ghcr.io/hal0ai/<repo>:<tag>` pattern, `-m` under `$MODEL_ROOT` no-traversal
`.gguf`, llama-bench flag whitelist `-b -ub -ngl -fa -ctk -ctv -p -n -d -r -t
-mmp -pg -dev -o` with safe value patterns, `-o json` required) then
`exec [timeout --kill-after=30 N] podman …`. `telemetry` verb unchanged.

---

## Task T — port the harness tests (test agent, red-first)

**Files:**
- Create: `tests/bench/test_harness.py` (unit tests for harness.py per contract)
- Create: `tests/bench/test_benchctl_shim.py` (bash shim: argv validation + exec,
  stub `podman`/`timeout` on PATH; port TestSeamWhitelist + telemetry tests from
  `tests/bench/test_harness_matrix.py`)
- Delete: `tests/bench/test_harness_matrix.py` (its matrix/backend-order/label
  assertions re-land as harness.py unit tests; telemetry+whitelist parts move to
  test_benchctl_shim.py)

Cover at minimum (translating the old suite):
- lane_specs: cpu lane exists, uses FALLBACK_VULKAN_IMAGE + /usr/local/bin
  binary, `-ngl 0`, no `-dev`; gpu lanes use DEFAULT_ROCMFPX_IMAGE, `-ngl 99` +
  their `-dev` pin; images imported from hal0.config.schema (assert identity
  with the schema constants, not string literals); common args carry no `-ngl`.
- default_lanes: cpu tier -> [cpu]; amd/nvidia -> [rocm, vulkan_radv].
- dedupe_flags: later-source-wins, first-seen order, comma sweep values pass
  through, `-fa 1` then `-fa 0` yields single `-fa 0`.
- compose_podman_argv: cpu composes NO --device/--group-add; gpu lane carries
  resolver flags verbatim; exactly one `-ngl`; `-o json` last; volume ro.
- run_cell: retry on rc=139 (6 attempts max), rc0+empty-stdout normalised to
  139 and retried, rc=1 fails without retry, rc=124 with timeout_s set fails
  without retry, success parses stdout JSON rows and builds meta with the exact
  legacy keys, meta["tier"]/"gpu" from BenchDeviceSpec, cpu tier gpu label
  "CPU (no GPU passthrough)".
- ExclusiveSlots: stops active non-npu slots, restarts on exit even when body
  raises, no-op when none active.
- Shim (bash, via subprocess with stubbed PATH): accepts a canonical rocm argv;
  accepts cpu argv (no devices); rejects `-m` outside MODEL_ROOT / with `..`;
  rejects non-whitelisted podman flag (`--privileged`), bad security-opt, rw
  volume, non-ghcr image, bad entrypoint, `-o` not json, unknown bench flag;
  `--timeout-s 5` wraps with `timeout --kill-after=30 5`; retired verbs
  (`run`, `run-model`, `sweep`, `aggregate`, `list`) exit 2; telemetry tests
  ported unchanged.
- runner: `_tier_a_cmd` argv-shape tests updated to the new
  `sudo -n … exec --timeout-s N -- podman run …` shape (keep the Phase-1
  assertions: `-d <depth>` present, `-p 512`, caller `-r` wins).

Steps: write tests against the contract, run
`HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/bench/test_harness.py tests/bench/test_benchctl_shim.py -p no:cacheprovider`
— expect FAIL (module/shim missing) except ported telemetry tests, commit
`test(bench): port shell-harness matrix tests to the python harness (red)`.

## Task I — implementation (implementation agent)

**Files:**
- Create: `src/hal0/bench/harness.py` (contract above, module docstring in the
  house style explaining the Phase-2 absorption + retry/exclusivity contracts)
- Modify: `src/hal0/bench/runner.py` (per contract above)
- Modify: `src/hal0/bench/devices.py` — ONLY if needed, add a pure accessor for
  run flags list (it already renders `BENCH_RUN_FLAG=` lines; expose
  `spec.run_flags` if not already a field — do not change resolution logic)
- Modify: `src/hal0/bench/cli.py` — `cmd_devices` etc. untouched; check
  `--dry-run` output path still works via describe_worklist
- Modify: `tests/bench/test_runner.py` — update argv-shape/locate tests to the
  new seam shape and stdout capture (delete tests of deleted helpers)

Steps: implement harness.py, wire runner.py, make Task T tests green (pull the
test agent's branch if merged first, else write against the contract), run the
full bench suite, `make lint` + `ruff format --check src tests`, commit
`feat(bench): absorb the shell harness into python (runner composes podman argv)`.

## Task O — shim + packaging (ORCHESTRATOR ONLY)

- Rewrite `installer/wrappers/hal0-benchctl`: keep header provenance comments,
  `_resolve_model_root`, `die`; new `exec` verb with sequential validation;
  keep `telemetry`; drop run/run-model/sweep/aggregate/list + the harness
  dependency; drop `post()` chown for exec (root writes nothing under RESULTS;
  telemetry keeps its artifacts writes as today).
- `packaging/sudoers/hal0-benchctl`: comment refresh only (grant line
  unchanged).
- `installer/install.sh`: stop installing the four retired files; keep
  server_ab.py + README.md; leave `${LIB_DIR}/bench` dir creation (server_ab
  lives there); add cleanup `rm -f` for the retired installed copies
  (upgrade path).

## Task D — docs/skill sweep (docs agent, after shape settles)

**Files:**
- Rewrite: `installer/agent-skills/hal0-bench/SKILL.md` + `references/seam.md`
  (+ any other references/): the agent now drives the v2 CLI (`hal0 bench plan/
  run/status/results/history`) and the API; the seam is `exec` + `telemetry`
  only; `index.json`/`SUMMARY.md`/`aggregate` are gone — results live in the
  v2 store (`records.jsonl` / `bench.db` / `/api/benchmarks`).
- Rewrite: `installer/bench/README.md` (server_ab.py + suites/window seeds are
  what remains; point at src/hal0/bench for the harness).
- Update: `docs/reference/model-roster-benchmark.mdx` (mentions of the shell
  harness/benchctl verbs).

## Integration (orchestrator)

1. Merge T + I branches, reconcile, full bench suite green.
2. Full unit suite (`HAL0_HOME=$(mktemp -d) uv run --extra dev pytest -p no:cacheprovider`, ≈17 min).
3. `code-review` skill on the diff (mandatory for the seam).
4. Conventional commit(s), PR `refactor(bench): absorb the shell harness into
   python; benchctl becomes a validate-and-exec shim`, body flags the
   privileged-surface diff for operator review. NO automerge.
