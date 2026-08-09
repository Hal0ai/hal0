"""harness.py — the Phase-2 absorption of the shell llama-bench harness.

Through Phase 1, ``hal0-benchctl sweep`` was a privileged verb that itself
composed a whole podman/llama-bench matrix (backend x model x context) from
``installer/bench/config.sh`` + ``run_benchmarks.sh`` — bash owned the flag
dedupe, the crash-retry loop, and the result-file naming, and the unprivileged
Python runner only knew the sweep's OUTPUT filename convention well enough to
go find it afterward (see the pre-Phase-2 ``_sweep_output_path`` / ``_locate_
sweep_output`` in ``runner.py``'s history — a filename convention shared
across a privilege boundary, which is exactly the kind of implicit contract
that breaks quietly).

Phase 2 flips that: this module composes the FULL ``podman run … llama-bench
-o json`` argv in Python (:func:`compose_podman_argv`), and the privileged
side (``installer/wrappers/hal0-benchctl`` ``exec`` verb) is reduced to a dumb
validate-and-exec shim with no matrix knowledge of its own — see that script's
header for the validation it performs. The seam's job shrinks from "know the
whole benchmark harness" to "verify this one argv is a legitimate llama-bench
invocation and run it"; the widening surface a Python-composed-but-shell-
executed matrix represents goes away. One result: the engine's ``-o json``
now comes back on STDOUT and is parsed directly (:func:`run_cell`) — there is
no more result FILE for the runner to locate, race against, or clear before a
re-measure.

What ported, and where it changed shape:

* **Backend/lane matrix** (``config.sh`` ``BACKENDS``) → :func:`lane_specs`,
  a pure dict of :class:`LaneSpec`. Values are unchanged (image, bench_bin,
  ubatch, env, dev_args) — only the format moved from a ``|``-delimited shell
  string to a dataclass.
* **Flag dedupe** (``run_benchmarks.sh`` ``dedupe_flag_pairs`` — llama-bench
  folds a REPEATED flag into a value-sweep dimension instead of overriding,
  so later sources must replace earlier ones) → :func:`dedupe_flags`, a
  faithful port: later-source-wins, first-seen order.
* **Crash-retry loop** (the rocmfpx llama-bench init-time segfault — a
  signal exit before any measurement, ~2/3 of launches observed on-box
  2026-07-10) → :func:`run_cell`'s ``MAX_ATTEMPTS`` loop. Same three rules,
  unchanged: a timed-out attempt (rc 124, or rc 137 past the per-attempt cap)
  never crash-retries; ``rc==0`` with EMPTY stdout is the SAME crash
  (``podman run --rm`` races its own cleanup and loses the real exit code) and
  normalises to rc 139 before the retry check; any other ``rc<128`` is a real
  failure and never retries.
* **``--exclusive`` GPU stop/restart** (``run_benchmarks.sh`` — stop every
  active ``hal0-slot@*`` unit except the NPU slot, sleep 3, restart on exit)
  → :class:`ExclusiveSlots`, a context manager the caller wraps around ONE
  Tier-A group's sweep (not per-attempt — one stop/restart serves every
  retry AND the pp/tg sibling that reuses the same memoised sweep).
* **Result provenance** (the ``.meta.json`` sidecar ``cat`` heredoc) →
  built inside :func:`run_cell` with the exact legacy keys (``parsers.py``
  reads ``meta["image"]``). Three keys — ``context``, ``tag``, ``extra`` —
  are legacy v1 concepts with no v2 equivalent: the planner's depth axis
  replaces the named ``ctx32k``/``ctx65k`` configs, and a cell's tuning
  ``flags`` are already a structured list rather than one free-form
  ``--extra`` string or ``--tag`` label. They are kept in the dict (readers
  of an OLD meta.json, and the schema shape, expect the key to exist) but are
  always empty strings for a v2-composed cell — only ``image`` is actually
  consumed downstream (``parsers.parse_llama_bench``).

The seam's own per-attempt wall-clock cap (``timeout --kill-after=30 N``
around the podman client) still lives entirely on the ROOT side of the
privilege boundary — this module never signals the sudo child itself, it only
learns the outcome from the exit code the shim reports (see
:func:`benchctl_exec_argv`'s ``--timeout-s`` and ``run_cell``'s timeout
classification). The caller (``runner.py``) supplies its OWN Python-side
backstop via the injectable ``runner`` callable, sized above the shim's
worst case, for the case a wedged ``sudo`` prompt or a wedged shim never
reaches its own timeout at all.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE, FALLBACK_VULKAN_IMAGE

from .devices import TIER_CPU, BenchDeviceSpec

__all__ = [
    "BENCHCTL",
    "MAX_ATTEMPTS",
    "SYSTEMCTL_SEAM",
    "CellResult",
    "ExclusiveSlots",
    "LaneSpec",
    "benchctl_exec_argv",
    "compose_podman_argv",
    "dedupe_flags",
    "default_lanes",
    "lane_specs",
    "run_cell",
]

BENCHCTL = "/usr/lib/hal0/bin/hal0-benchctl"
SYSTEMCTL_SEAM = "/usr/lib/hal0/bin/hal0-systemctl"
#: Crash-retry cap for the rocmfpx init-time segfault (see module docstring).
#: Matches the shell harness's fixed ``for attempt in 1 2 3 4 5 6``.
MAX_ATTEMPTS = 6

#: The unprivileged runner reads/writes this default when a cell's flags omit
#: an explicit host label — matches ``run_benchmarks.sh``'s ``HOST_LABEL``
#: default. Overridable via the ``HOST_LABEL`` env var for parity with the
#: shell harness's own override.
_DEFAULT_HOST_LABEL = "hal0"

# COMMON_BENCH_ARGS (config.sh): flash attention on, no mmap — applied to
# EVERY cell, ahead of the lane's dev_args and the cell's own flags so later
# sources (a variant's explicit -fa/-mmp) still win via dedupe_flags.
_COMMON_BENCH_FLAGS: tuple[tuple[str, str], ...] = (("-fa", "1"), ("-mmp", "0"))


@dataclass(frozen=True)
class LaneSpec:
    """One backend lane's fixed shape (``config.sh`` ``BACKENDS``): which
    image/binary runs it, its ubatch default, any extra container env, and the
    llama-bench flags that pin the device (``-ngl``/``-dev``)."""

    lane: str
    image: str
    bench_bin: str
    ubatch: int
    env: tuple[str, ...]
    dev_args: tuple[tuple[str, str], ...]


def lane_specs() -> dict[str, LaneSpec]:
    """The fixed backend matrix (``config.sh`` ``BACKENDS``), ported verbatim.

    Both GPU lanes share the unified rocmfpx runner image and its ``/opt/
    rocmfpx/bin/llama-bench`` (the binary matched to the ROCmFPX libllama —
    the stock toolbox's ``/usr/local/bin/llama-bench`` ABI-mismatches and
    segfaults on that image); ``cpu`` runs the lean Vulkan/CPU toolbox at its
    own base-toolbox path. See ``config.sh`` for the full historical
    rationale this ports.
    """
    return {
        "rocm": LaneSpec(
            lane="rocm",
            image=DEFAULT_ROCMFPX_IMAGE,
            bench_bin="/opt/rocmfpx/bin/llama-bench",
            ubatch=2048,
            env=("GGML_HIP_ENABLE_UNIFIED_MEMORY=1",),
            dev_args=(("-ngl", "99"), ("-dev", "ROCm0")),
        ),
        "vulkan_radv": LaneSpec(
            lane="vulkan_radv",
            image=DEFAULT_ROCMFPX_IMAGE,
            bench_bin="/opt/rocmfpx/bin/llama-bench",
            ubatch=512,
            env=(),
            dev_args=(("-ngl", "99"), ("-dev", "Vulkan0")),
        ),
        "cpu": LaneSpec(
            lane="cpu",
            image=FALLBACK_VULKAN_IMAGE,
            bench_bin="/usr/local/bin/llama-bench",
            ubatch=512,
            env=(),
            # No -dev pin: the CPU tier's device resolver emits no device
            # flags at all, so there is no backend device to select.
            dev_args=(("-ngl", "0"),),
        ),
    }


def default_lanes(tier: str) -> list[str]:
    """The lanes a suite sweeps when it does not say ``--backends`` itself
    (``config.sh`` ``BACKEND_ORDER``): TIER-SCOPED, so a CPU-only box never
    queues the unrunnable GPU lanes, and a GPU box never silently pays for a
    CPU lane's hours-long 27B/ctx65k cells by default."""
    return ["cpu"] if tier == TIER_CPU else ["rocm", "vulkan_radv"]


def dedupe_flags(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Port of ``run_benchmarks.sh``'s ``dedupe_flag_pairs``: llama-bench
    folds a REPEATED flag into a value-sweep dimension instead of overriding
    it (``-fa 1 … -fa 0`` runs BOTH and doubles the rows), so a later source
    must REPLACE an earlier one rather than append. Later-source-wins,
    first-seen order — a flag keeps its original position but the LAST value
    assigned to it."""
    order: list[str] = []
    values: dict[str, str] = {}
    for flag, value in pairs:
        if flag not in values:
            order.append(flag)
        values[flag] = value
    return [(flag, values[flag]) for flag in order]


def compose_podman_argv(
    spec: LaneSpec,
    devices: BenchDeviceSpec,
    model_path: str,
    model_root: str,
    flags: list[tuple[str, str]],
) -> list[str]:
    """The exact ``podman run`` argv for one cell — the shape
    ``installer/wrappers/hal0-benchctl``'s ``exec`` verb validates structurally
    element-by-element, so this function and that shim's validator must never
    drift (see that script's ``exec)`` case).

    Flag precedence (COMMON < lane ``dev_args`` < caller ``flags``) mirrors
    ``run_benchmarks.sh``'s ``dedupe_flag_pairs "${COMMON_BENCH_ARGS[@]}"
    $devargs $ctxargs -r "$reps" $EXTRA`` — the caller's flags (which already
    include the cell's ``-p``/``-n``/``-d``/``-r`` and any config-variant
    tuning) are LAST, so they can override a lane default (e.g. an explicit
    ``-ngl`` variant) exactly as the shell harness allowed.
    """
    argv = [
        "podman",
        "run",
        "--rm",
        *devices.run_flags,
        "--security-opt",
        "apparmor=unconfined",
        "--security-opt",
        "seccomp=unconfined",
        f"--volume={model_root}:{model_root}:ro,z",
    ]
    for kv in spec.env:
        argv += ["-e", kv]
    argv += ["--entrypoint", spec.bench_bin, spec.image, "-m", model_path]
    for flag, value in dedupe_flags([*_COMMON_BENCH_FLAGS, *spec.dev_args, *flags]):
        argv += [flag, str(value)]
    argv += ["-o", "json"]
    return argv


def benchctl_exec_argv(podman_argv: list[str], timeout_s: int | None) -> list[str]:
    """The full seam invocation for one attempt: ``sudo -n hal0-benchctl exec
    [--timeout-s N] -- <podman_argv>``. The per-attempt cap crosses the
    privilege boundary as ``--timeout-s`` — the shim wraps the podman client
    in ``timeout --kill-after=30 N`` on the ROOT side, because this
    (unprivileged) process cannot signal a root-owned child."""
    argv = ["sudo", "-n", BENCHCTL, "exec"]
    if timeout_s:
        argv += ["--timeout-s", str(timeout_s)]
    argv += ["--", *podman_argv]
    return argv


@dataclass
class CellResult:
    """One executed cell: the parsed llama-bench rows (``[]`` on any failure,
    never a partial/guessed value), the provenance sidecar, the FINAL attempt's
    exit code, and its last 4000 chars of log for a failed record's ``note``."""

    rows: list[dict]
    meta: dict
    rc: int
    tail: str


def _default_runner(argv: list[str], timeout_s: int | None) -> tuple[int, str, str]:
    """The bare subprocess runner: trusts the shim's OWN ``--timeout-s`` /
    ``timeout --kill-after=30`` to bound an attempt, no Python-side timeout of
    its own. Production callers (``runner.py``) inject a hardened callable
    with a process-group watchdog as a backstop against a wedged ``sudo``
    prompt or a wedged shim that never reaches its own timeout; tests inject a
    fake that needs no ``sudo``/``podman``/GPU at all."""
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _build_meta(
    spec: LaneSpec,
    devices: BenchDeviceSpec,
    model_rel: str,
    model_path: str,
    flags: list[tuple[str, str]],
) -> dict:
    """The ``.meta.json`` provenance dict, exact legacy keys (see module
    docstring for which are always-empty v1 concepts with no v2 equivalent)."""
    reps: int | None = None
    for flag, value in flags:
        if flag == "-r":
            with contextlib.suppress(ValueError):
                reps = int(value)
    return {
        "backend": spec.lane,
        "image": spec.image,
        "context": "",  # v1 named ctx config (ctx32k/ctx65k) -> planner depth
        "tag": "",  # v1 free-form result-file label -> not a v2 concept
        "extra": "",  # v1 free-form --extra string -> flags is structured
        "reps": reps,
        "ubatch": spec.ubatch,
        "model_rel": model_rel,
        "model_path": model_path,
        "host": os.environ.get("HOST_LABEL", _DEFAULT_HOST_LABEL),
        # The TIER decides the label, not the probe: the v1.0 CPU baseline is
        # normally measured with HAL0_BENCH_TIER=cpu on a box that DOES have a
        # GPU, and the resolver still (correctly) reports that GPU's name.
        # Taking the probed label first would file those CPU numbers under the
        # GPU's name — exactly the per-tier corruption the old config.sh block
        # existed to prevent.
        "tier": devices.tier,
        "gpu": (
            "CPU (no GPU passthrough)"
            if devices.tier == TIER_CPU
            else (devices.gpu_label or "unknown GPU")
        ),
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def run_cell(
    spec: LaneSpec,
    devices: BenchDeviceSpec,
    *,
    model_rel: str,
    model_root: str,
    flags: list[tuple[str, str]],
    timeout_s: int | None,
    log_path: Path,
    runner: Callable[[list[str], int | None], tuple[int, str, str]] | None = None,
) -> CellResult:
    """Run one cell through the seam, with the Phase-1 retry/normalisation
    rules (see module docstring): up to :data:`MAX_ATTEMPTS` attempts, stdout
    parsed as the llama-bench ``-o json`` array, provenance built from what
    actually ran. Never raises on a failed cell — a bad cell is a
    :class:`CellResult` with ``rows=[]`` and a non-zero ``rc``, so the caller's
    session can record it and continue (DESIGN §5.3).
    """
    run = runner or _default_runner
    model_path = f"{model_root}/{model_rel}"
    podman_argv = compose_podman_argv(spec, devices, model_path, model_root, flags)
    exec_argv = benchctl_exec_argv(podman_argv, timeout_s)

    rc = 1
    stdout = ""
    stderr = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        t_start = time.monotonic()
        rc, stdout, stderr = run(exec_argv, timeout_s)
        elapsed = time.monotonic() - t_start
        stdout = stdout or ""
        stderr = stderr or ""

        # A timed-out attempt (the shim's `timeout` TERM at 124, or its
        # --kill-after=30 escalation to KILL at 137) must NEVER crash-retry —
        # rc 124/137 here are indistinguishable from a real container signal
        # exit except by elapsed wall-clock past the cap.
        timed_out = timeout_s is not None and (rc == 124 or (rc == 137 and elapsed >= timeout_s))
        if timed_out:
            break

        # rc 0 with EMPTY stdout is the SAME init-time crash: `podman run
        # --rm` raced its own cleanup and lost the real (signal) exit code.
        # Normalise to a signal exit so the retry check below fires.
        if rc == 0 and not stdout.strip():
            rc = 139

        if rc == 0 or rc < 128:
            break  # success, or a real (non-crash) failure — neither retries
        if attempt < MAX_ATTEMPTS:
            time.sleep(1)

    tail_source = stderr if stderr.strip() else stdout
    tail = tail_source[-4000:]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"exit={rc}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n")

    rows: list[dict] = []
    if rc == 0 and stdout.strip():
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(stdout)
            rows = parsed if isinstance(parsed, list) else []

    meta = _build_meta(spec, devices, model_rel, model_path, flags) if rc == 0 else {}
    return CellResult(rows=rows, meta=meta, rc=rc, tail=tail)


def _default_shell_runner(argv: list[str]) -> tuple[int, str, str]:
    """Bare ``subprocess.run`` for :class:`ExclusiveSlots`'s systemctl calls —
    these are short, local, and unprivileged-to-read/single-verb-privileged-
    to-write, so no watchdog is warranted here."""
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class ExclusiveSlots:
    """Stop every active ``hal0-slot@*`` unit (except the NPU slot, which
    never touches the GPU) for the duration of a Tier-A sweep, and restart
    whatever this instance actually stopped on exit.

    Ports ``run_benchmarks.sh``'s ``--exclusive`` handling: read active slots
    via an unprivileged ``systemctl list-units`` (no seam needed — listing
    units needs no privilege), stop each stopped one via the single-verb
    ``hal0-systemctl stop <id>`` seam, sleep 3s so the GPU is actually idle
    before the container starts, and best-effort restart on exit (a failed
    restart is a WARNING, never an exception — leaving a slot down must not
    also crash the benchmark session that already ran).

    The caller wraps ONE Tier-A group's sweep (not per-attempt): the group's
    pp/tg siblings share one memoised sweep, so they must also share one
    stop/restart, exactly like the shell harness's per-sweep ``--exclusive``.
    """

    def __init__(self, runner: Callable[[list[str]], tuple[int, str, str]] | None = None) -> None:
        self._run = runner or _default_shell_runner
        self._stopped: list[str] = []

    def __enter__(self) -> ExclusiveSlots:
        for slot_id in self._active_slot_ids():
            rc, _out, err = self._run(["sudo", "-n", SYSTEMCTL_SEAM, "stop", slot_id])
            if rc != 0:
                # Best-effort restart of whatever we DID manage to stop before
                # surfacing the failure — never leave a slot down silently.
                self._restore()
                raise RuntimeError(
                    f"[exclusive] could not stop hal0-slot@{slot_id}: {err.strip() or rc}"
                )
            self._stopped.append(slot_id)
        if self._stopped:
            time.sleep(3)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._restore()

    def _restore(self) -> None:
        for slot_id in self._stopped:
            rc, _out, err = self._run(["sudo", "-n", SYSTEMCTL_SEAM, "start", slot_id])
            if rc != 0:
                print(
                    f"[exclusive] WARN: failed to restart hal0-slot@{slot_id}: {err.strip() or rc}",
                    file=sys.stderr,
                )
        self._stopped = []

    def _active_slot_ids(self) -> list[str]:
        rc, out, _err = self._run(
            ["systemctl", "list-units", "hal0-slot@*", "--no-legend", "--state=active"]
        )
        if rc != 0:
            return []
        ids: list[str] = []
        for line in out.splitlines():
            fields = line.split()
            if not fields:
                continue
            unit = fields[0]
            if not (unit.startswith("hal0-slot@") and unit.endswith(".service")):
                continue
            slot_id = unit[len("hal0-slot@") : -len(".service")]
            if slot_id != "npu":
                ids.append(slot_id)
        return ids
