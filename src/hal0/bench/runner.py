"""runner.py — the session runner (DESIGN §5 "Runner behavior").

The runner turns a planner worklist into appended records. The control flow was
always here; Phase 2 fills the two engine-output parsers (now in parsers.py) and
corrects every place the stubs assumed a contract that differs from the real box
(verified on CT105 2026-07-05 — see the results appendix):

  * GPU-window gate (DESIGN §5.2) uses the REAL routes: slot status
    (``/api/slots``, states offline/warming/serving + container_status) and
    throughput history (``/api/stats/throughput/history?window_s=`` — NOT
    ``/api/throughput``). "Busy" = a request in flight; a merely-loaded slot is
    stopped by the exclusive window's own slot stop/restart, so an exclusive
    session may proceed while a slot is loaded-but-idle.
  * Exclusivity (Phase 2, ``hal0.bench.harness.ExclusiveSlots``) stops every
    active ``hal0-slot@*`` unit for the duration of ONE Tier-A group's sweep
    and restarts them on exit — a direct port of the shell harness's per-sweep
    ``--exclusive`` (DESIGN §0.5 — the session-level ``gpu-quiesce`` verb was
    deferred and does not exist on-box). Tier-A sweeps are memoised per
    (model,lane,depth,config) so one GPU load (one stop/restart) yields BOTH
    the pp and tg records.
  * Tier A (pp/tg): Phase 2 absorbs the shell harness
    (``installer/bench/run_benchmarks.sh`` + ``config.sh``) into
    ``hal0.bench.harness`` — the runner COMPOSES the full ``podman run …
    llama-bench -o json`` argv itself (``harness.compose_podman_argv``); the
    privileged ``hal0-benchctl exec`` verb is a dumb validate-and-exec shim
    with no matrix knowledge of its own. The engine's JSON comes back on
    STDOUT (``harness.run_cell``, with the Phase-1 crash-retry/normalisation
    loop now living in Python) — there is no more result FILE to locate, race
    against, or clear before a re-measure; artifacts are written straight from
    the parsed rows. ``-d`` is the cell's depth axis (KV fill); ``-p``/``-n``
    are the fixed pp/tg measurement sizes.
  * Tier B/C (embed/rerank/reuse) go through the installed ``server_ab.py``
    (modes reuse/embed/rerank; no depth/mtp/batch on this build). Bench Phase 3
    (docs/superpowers/plans/2026-08-09-bench-phase3-oss-adapters.md): ``chat``
    no longer goes through server_ab at all — it drives GuideLLM
    (``adapters/guidellm.py``) against the slot's HTTP endpoint directly, and
    two new kinds (``http_pp``/``http_tg``) drive llama-benchy
    (``adapters/llama_benchy.py``) the same way for llama-bench vocabulary
    (pp/tg x depth) measured over HTTP.

Per-cell watchdog = 3x expected (DESIGN §5.3); a bad cell records its outcome and
the session CONTINUES. Resumable by construction (DESIGN §5.4): each record is
appended as it finishes, so re-planning after a crash recomputes what's missing.
The runner is unprivileged (User=hal0); only Tier A reaches privilege, only via
the single ``hal0-benchctl`` grant.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import signal
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import harness
from .adapters import guidellm, llama_benchy
from .devices import BenchDeviceError, BenchDeviceSpec, resolve_bench_devices
from .parsers import Parsed, parse_llama_bench, parse_server_ab
from .schema import Engine, Host, Outcome, Record
from .store import Store

if TYPE_CHECKING:
    from .planner import Cell

DEFAULT_API = "http://127.0.0.1:8080"
# Re-exported for callers/tests that reach the seam path through runner.py;
# the actual argv composition always uses harness.BENCHCTL directly.
BENCHCTL = harness.BENCHCTL
SERVER_AB = "/usr/lib/hal0/bench/server_ab.py"
# The historic model-store root, kept ONLY as a legacy strip candidate — the
# live root comes from hal0.config.paths.model_store_root() (see _model_roots).
LEGACY_MODEL_ROOT = "/mnt/ai-models"


def _model_root() -> str:
    """The LIVE model-store root (``hal0.config.paths.model_store_root``) —
    the same resolver ``hal0-benchctl``'s ``exec`` verb uses independently to
    compute its own ``$MODEL_ROOT``. The composed ``--volume=``/``-m`` argv
    must use THIS root, not whichever historic root a registry path happened to
    be stripped against (see ``_rel_gguf``): the shim re-resolves the root
    itself and will reject an argv built against a different one (#1516)."""
    from hal0.config.paths import model_store_root

    return model_store_root().rstrip("/")


def _model_roots() -> list[str]:
    """Roots an absolute registry gguf path may live under, longest first: the
    box's RESOLVED model store (hal0.config.paths.model_store_root — the same
    resolver the pull engine and slot mounts use) plus the historic
    ``/mnt/ai-models``. The old code stripped ONLY the historic literal, so on
    a default-config box (store at /var/lib/hal0/models) every Tier-A cell
    reached the seam with an unstrippable absolute path and died "model not
    found" (#1516)."""
    from hal0.config.paths import model_store_root

    roots = {model_store_root().rstrip("/"), LEGACY_MODEL_ROOT}
    return sorted(roots, key=len, reverse=True)


# Rough per-cell expected wall-clock (seconds) for the watchdog (fires at 3x).
# Generous on purpose — it catches a hang, it is not an SLA.
_EXPECTED_S = {
    "pp": 120,
    "tg": 180,
    "chat": 240,
    "reuse": 180,
    "embed": 120,
    "rerank": 120,
    "batch": 600,
    "mtp": 300,
    # Phase-3 OSS-adapter kinds (llama-benchy over HTTP): a bit more than the
    # Tier-A seam values above for the HTTP round-trip + warmup overhead.
    "http_pp": 150,
    "http_tg": 210,
}
_TIER_A_KINDS = {"pp", "tg"}
# Bench Phase 3 (docs/superpowers/plans/2026-08-09-bench-phase3-oss-adapters.md
# design decision 1): "chat" drives GuideLLM (adapters/guidellm.py), not
# server_ab, so it is deliberately NOT in this map any more. "http_pp"/
# "http_tg" drive llama-benchy (adapters/llama_benchy.py) — also not
# server_ab, so also not here.
_GUIDELLM_KINDS = {"chat"}
_LLAMA_BENCHY_KINDS = {"http_pp", "http_tg"}
# Tier-B cell kind -> server_ab.py --mode (this build: reuse/embed/rerank).
# No .get() fallback anywhere: an unknown kind is rejected at plan time
# (planner.KNOWN_KINDS) and raises here if one slips through.
_KIND_TO_MODE = {"reuse": "reuse", "embed": "embed", "rerank": "rerank"}
# Default tg decode length when a group has no explicit tg cell n_gen, and the
# fixed pp prompt length (mirrors planner._PP_PROMPT — the depth axis is -d,
# not the prompt length).
_DEFAULT_TG_GEN = 256
_DEFAULT_PP_PROMPT = 512


@dataclass
class SessionResult:
    suite_id: str
    cells_total: int = 0
    cells_ok: int = 0
    cells_failed: int = 0
    duration_s: float = 0.0
    aborted: str | None = None
    run_ids: list[str] = field(default_factory=list)


def _new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


# --------------------------------------------------------------------------- #
# host block
# --------------------------------------------------------------------------- #


def fetch_host(api: str = DEFAULT_API, exclusive: bool = True) -> Host:
    """Build the record host block (DESIGN §3.2) from the live box: gpu/platform/
    mem from ``/api/hardware``, hal0 version from ``/api/health`` (verified on-box
    2026-07-05: ``/api/meta`` is empty; the version lives in the health payload
    ``{"name","version"}``). hal0_version is required (schema.Host) so every record
    attributes to a release. Best-effort — a field we can't read stays default."""
    hw = _get_json(api, "/api/hardware") or {}
    health = _get_json(api, "/api/health") or {}
    kernel = (hw.get("extra") or {}).get("kernel") or hw.get("kernel") or ""
    return Host(
        name=hw.get("hostname") or health.get("name") or "hal0",
        platform=hw.get("platform") or "",
        gpu=_friendly_gpu(hw),
        kernel=kernel.removeprefix("Linux version ").strip(),
        mem_gb=round(int(hw.get("unified_memory_mb") or hw.get("ram_mb") or 0) / 1024),
        hal0_version=health.get("version") or "",
        exclusive=exclusive,
    )


def _friendly_gpu(hw: dict) -> str:
    """A display GPU label. hal0's ``gpu_name`` is a raw PCI string ("… Device
    1586 (rev c1)"); prefer the marketing name that ``cpu_name`` embeds on APUs
    ("… w/ Radeon 8060S") + the gfx target the platform implies, matching the v1
    ``Radeon 8060S (gfx1151)`` label the docs already use. Falls back to whatever
    name is reported."""
    raw = hw.get("gpu_name") or ""
    gpus = hw.get("gpus") or []
    name = (gpus[0].get("name") if gpus and isinstance(gpus[0], dict) else "") or raw
    if "Radeon" in name and "Device" not in name:
        return name
    m = re.search(r"Radeon\s+[0-9A-Za-z]+", hw.get("cpu_name") or "")
    if m:
        gfx = " (gfx1151)" if hw.get("platform") == "strix-halo" else ""
        return f"{m.group(0)}{gfx}"
    return name


def _get_json(api: str, path: str, timeout: float = 5.0) -> Any:
    try:
        with urllib.request.urlopen(f"{api.rstrip('/')}{path}", timeout=timeout) as resp:
            return json.loads(resp.read() or b"null")
    except (urllib.error.URLError, OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# GPU-window gate (DESIGN §5.2)
# --------------------------------------------------------------------------- #

_SLOTS_BUSY_STATES = {"serving", "warming", "running", "loaded", "active"}


def _traffic_in_flight(api: str, quiet_min: int = 10) -> bool:
    """True if any tokens moved in the last ``quiet_min`` minutes — i.e. a request
    is (or just was) in flight. Reads the REAL throughput route
    ``/api/stats/throughput/history?window_s=`` (samples are emitted only for
    non-empty bins; idle ⇔ no samples / all total_tps==0). A failure to read is
    treated as "in flight" (fail-safe: never measure over live traffic)."""
    data = _get_json(api, f"/api/stats/throughput/history?window_s={quiet_min * 60}")
    if data is None:
        # Fail-safe: never measure over traffic we can't see. But say so — a
        # down/renamed stats route otherwise declines every scheduled session
        # forever with no trace.
        import sys

        print(
            f"[gate] cannot read {api}/api/stats/throughput/history — treating GPU as busy",
            file=sys.stderr,
        )
        return True
    samples = data.get("samples") or []
    return any(float(s.get("total_tps") or 0) > 0 for s in samples)


def _gpu_slot_serving(api: str) -> bool:
    """True if a LOCAL GPU slot is actively serving/warming (container up). Used to
    stamp ``skipped-contended`` on non-exclusive (smoke) cells. Remote providers
    (e.g. minimax) are ignored — they aren't the box GPU."""
    slots = _get_json(api, "/api/slots")
    items = slots if isinstance(slots, list) else (slots or {}).get("slots") or []
    for s in items:
        if s.get("runtime") not in ("container", None) and s.get("device_class") != "gpu":
            continue
        if s.get("container_status") == "running":
            return True
        serving = s.get("status") in _SLOTS_BUSY_STATES or s.get("state") in _SLOTS_BUSY_STATES
        if serving and (s.get("device_class") == "gpu" or s.get("runtime") == "container"):
            return True
    return False


# --------------------------------------------------------------------------- #
# Tier A — llama-bench via the seam
# --------------------------------------------------------------------------- #


def _rel_gguf(gguf: str) -> str:
    """The model path the seam expects: relative to the model-store root,
    ending .gguf (hal0-benchctl validate_model). Absolute registry paths are
    stripped against the RESOLVED store root(s), not a hardcoded literal
    (#1516)."""
    for root in _model_roots():
        prefix = root + "/"
        if gguf.startswith(prefix):
            return gguf[len(prefix) :]
    return gguf.lstrip("/")


def _run_subprocess(cmd: list[str], timeout_s: float, log_path: Path) -> tuple[int, str]:
    """Run one engine subprocess under the watchdog, teeing output to ``log_path``.
    Returns (returncode, tail). A timeout returns rc=-9 → ``hang``.

    The child gets its own session so a timeout kills the whole PROCESS GROUP,
    not just the immediate child (the old ``subprocess.run(timeout=)`` killed
    only the parent, orphaning e.g. a server_ab HTTP wait). The Tier-A ``sudo``
    child is root-owned and unkillable from here (kill(2) → EPERM — the old
    code would CRASH the session with PermissionError on a Tier-A hang); its
    real timeout runs INSIDE the seam (``benchctl exec --timeout-s``), and
    this watchdog is sized above the seam's worst case as a backstop."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout_s)
        log_path.write_text(out or "", encoding="utf-8")
        return proc.returncode, (out or "")[-4000:]
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(proc.pid, sig)
            try:
                out, _ = proc.communicate(timeout=10)
                break
            except subprocess.TimeoutExpired:
                out = ""
        else:
            out = ""
        log_path.write_text((out or "") + f"\n[watchdog] killed after {timeout_s:.0f}s\n")
        return -9, "watchdog-timeout"


def _classify(rc: int, tail: str) -> Outcome:
    if rc == -9:
        return Outcome.HANG
    low = tail.lower()
    if "out of memory" in low or "hiperroroutofmemory" in low.replace("_", ""):
        return Outcome.OOM
    if rc != 0:
        return Outcome.FAILED
    return Outcome.OK


def _tier_a_per_attempt_s(cell) -> int:
    """The per-attempt seam timeout for a Tier-A sweep, seconds.

    The REAL kill lives in the seam (``timeout`` around the podman run — the
    unprivileged runner cannot signal the root-owned sudo child). One attempt
    covers the shared pp+tg sweep, scaled by depth (a 128k KV fill takes
    minutes before any measurement).

    This is the ONLY number the runner picks; the actual outer bound on a
    cell is now emergent, not a separately-sized watchdog:
      * the shim wraps each attempt in ``timeout --kill-after=30 N`` on the
        ROOT side (N = this value);
      * ``_tier_a_runner``'s Python-side backstop adds another +35s margin
        per attempt, for the case sudo/the shim itself wedges before ever
        reaching its own timeout;
      * ``harness.run_cell`` retries a crash (not a timeout) up to
        ``harness.MAX_ATTEMPTS`` (6) times.
    So the true worst case for one cell is ``MAX_ATTEMPTS * (N + 35)``
    seconds, not a separately-enforced outer watchdog."""
    return int(3 * (_EXPECTED_S["pp"] + _EXPECTED_S["tg"]) + cell.depth // 50)


# --------------------------------------------------------------------------- #
# Command builders (shared by the executor and `run --dry-run`)
# --------------------------------------------------------------------------- #


def _tier_a_flags(cell, pp_prompt: int, tg_gen: int) -> list[tuple[str, str]]:
    """The cell's own llama-bench flags (before lane/common defaults are
    folded in by ``harness.compose_podman_argv``'s dedupe): the fixed
    measurement sizes, the depth axis, repetitions, then any config-variant
    tuning flags (validated at plan time), sorted for a stable, memoisation-
    key-matching command.

    The depth axis is llama-bench ``-d`` (KV fill before the measurement) —
    NOT ``-p``. The old shape passed ``-p <depth>``, so a tg cell at depth
    32768 decoded from an EMPTY context: different cell_keys, identical
    numbers. ``-p``/``-n`` are the fixed measurement sizes (pp512/tg-n_gen)."""
    flags: list[tuple[str, str]] = [
        ("-p", str(pp_prompt)),
        ("-n", str(tg_gen)),
        ("-d", str(cell.depth)),
        ("-r", str(cell.reps)),
    ]
    for flag in sorted(getattr(cell, "flags", None) or {}):
        flags.append((flag, str(cell.flags[flag])))
    return flags


def _tier_a_cmd(cell, pp_prompt: int, tg_gen: int, devices: BenchDeviceSpec) -> list[str]:
    """The exact ``sudo -n hal0-benchctl exec -- podman run …`` argv for a
    Tier-A group (Phase 2: composed here, not by a privileged sweep verb — see
    ``hal0.bench.harness``). Exclusivity no longer rides the argv at all: it is
    a Python-side context manager (``harness.ExclusiveSlots``) the caller wraps
    around the actual execution, so this function is pure command COMPOSITION
    (safe to call from ``describe_worklist`` with no slots touched).

    Raises ``KeyError`` for an unknown ``cell.lane`` — planner.plan() rejects
    that at plan time (finding 5), but a defensive caller (tests, a future
    plan-bypassing entry point) can still land here, so the KeyError is a
    deliberate, documented failure mode rather than a silent default."""
    spec = harness.lane_specs()[cell.lane]
    model_root = _model_root()
    model_path = f"{model_root}/{_rel_gguf(cell.identity.model.gguf)}"
    flags = _tier_a_flags(cell, pp_prompt, tg_gen)
    podman_argv = harness.compose_podman_argv(spec, devices, model_path, model_root, flags)
    per_attempt = _tier_a_per_attempt_s(cell)
    return harness.benchctl_exec_argv(podman_argv, per_attempt)


def _tier_bc_cmd(
    cell, slot: str, api: str, out: Path | str = "<artifacts>/server-ab.json"
) -> list[str]:
    """The exact `server_ab.py` argv for a Tier-B/C cell (reuse/embed/rerank —
    "chat" moved to GuideLLM in Phase 3, see ``_GUIDELLM_KINDS`` /
    ``_guidellm_record``, so this never composes an ``ab``-mode argv any
    more)."""
    mode = _KIND_TO_MODE[cell.kind]
    return [
        SERVER_AB,
        "--mode",
        mode,
        "--slot",
        slot,
        "--api",
        api,
        "--n",
        str(cell.reps),
        "--out",
        str(out),
    ]


def describe_worklist(cells: list[Cell], exclusive: bool, api: str) -> list[str]:
    # ``exclusive`` no longer changes the composed argv (see _tier_a_cmd) — it
    # is kept in the signature for parity with run_session/cli.py's call site;
    # a dry-run listing never touches slots regardless of this flag.
    """Human-readable ordered worklist + the EXACT seam/server_ab command each
    cell would run (for `run --dry-run`, DESIGN §5 / bring-up Phase 4). Pure —
    resolves no slots, touches no GPU, and NEVER raises: device resolution is
    attempted once up front (so every Tier-A line shows the real ``--device=``/
    ``--group-add=`` flags), but a resolution failure degrades to composing
    without device passthrough plus a trailing note, rather than aborting a
    read-only preview. Marks the pp/tg sibling that reuses a group's single
    memoised sweep."""
    sizes = _group_sizes(cells)
    seen: set[tuple] = set()
    lines: list[str] = []
    device_note = ""
    try:
        devices = resolve_bench_devices()
    except BenchDeviceError as exc:
        devices = BenchDeviceSpec(tier="cpu")
        device_note = (
            f"\n  (! device resolution failed: {exc} — argv shown WITHOUT GPU passthrough)"
        )
    for i, c in enumerate(cells, 1):
        cfg = (
            f"  cfg:{c.config_label}" if getattr(c, "config_label", "default") != "default" else ""
        )
        label = f"{i:2d}. {c.model_id}  {c.lane}  {c.kind}  d{c.depth}{cfg}  ({c.reason})"
        if c.kind in _TIER_A_KINDS:
            key = _group_key(c)
            if key in seen:
                cmd_str = "(reuses this group's memoised sweep — no extra GPU load)"
            elif c.lane not in harness.lane_specs():
                # Defensive (finding 5): planner.plan() rejects an unknown
                # lane at plan time, but this function's docstring promises
                # it NEVER raises, so a cell that reached here some other way
                # (a hand-built worklist, a future bypass) degrades to a note
                # instead of a bare KeyError.
                seen.add(key)
                cmd_str = f"(! unknown lane {c.lane!r} — no seam command composed)"
            else:
                seen.add(key)
                pp, tg = sizes.get(key, (_DEFAULT_PP_PROMPT, _DEFAULT_TG_GEN))
                cmd_str = " ".join(_tier_a_cmd(c, pp, tg, devices))
        elif c.kind in _GUIDELLM_KINDS:
            profile_kind, profile_options = _guidellm_profile(c)
            guidellm_req = guidellm.GuidellmRequest(
                endpoint=f"http://127.0.0.1:<port-for:{c.model_id}>",
                model=c.model_id,
                profile_kind=profile_kind,
                output_path="<artifacts>/guidellm-benchmarks.json",
                profile_options=profile_options,
                max_requests=max(int(c.reps), 1),
                # #1773: the planner already resolved+gated this at plan
                # time (planner._resolve_tokenizer) — never re-derive it
                # here, and never fall back to c.model_id (a local-only
                # slot id, not a HF repo).
                tokenizer=c.tokenizer or None,
            )
            cmd_str = " ".join(guidellm.build_argv(guidellm_req))
        elif c.kind in _LLAMA_BENCHY_KINDS:
            pp = int(c.identity.workload.n_prompt or _DEFAULT_PP_PROMPT)
            tg = int(c.identity.workload.n_gen or _DEFAULT_TG_GEN)
            benchy_req = llama_benchy.LlamaBenchyRequest(
                endpoint=f"http://127.0.0.1:<port-for:{c.model_id}>/v1",
                model=c.model_id,
                pp=pp,
                tg=tg,
                result_path=Path("<artifacts>/llama-benchy-report.json"),
                depths=(c.depth,),
                reps=max(int(c.reps), 1),
            )
            cmd_str = " ".join(llama_benchy.build_argv(benchy_req))
        else:
            cmd_str = " ".join(_tier_bc_cmd(c, f"<slot-for:{c.model_id}>", api))
        lines.append(f"{label}\n      {cmd_str}")
    if device_note:
        lines.append(device_note.strip())
    return lines


# --------------------------------------------------------------------------- #
# Session driver
# --------------------------------------------------------------------------- #


def run_session(
    cells: list[Cell],
    store: Store,
    host: Host,
    *,
    suite_id: str,
    api: str = DEFAULT_API,
    budget_min: int | None = None,
    exclusive: bool = True,
    dry_run: bool = False,
    should_continue: Callable[[], bool] | None = None,
    trigger: str = "manual",
) -> SessionResult:
    """Drive a worklist to completion (DESIGN §5). One cell at a time, append as we
    go, honour the budget wall. Tier-A sweeps are memoised per (model,lane,depth)
    so the pp and tg siblings share one GPU load / one exclusive stop-restart."""
    result = SessionResult(suite_id=suite_id, cells_total=len(cells))
    if not cells or dry_run:
        return result

    # Pre-gate (DESIGN §5.2): never measure while a request is in flight.
    if _traffic_in_flight(api):
        result.aborted = "gpu-contended"
        return result

    started = time.monotonic()
    budget_s = budget_min * 60 if budget_min else None
    # (pp prompt, tg decode) sizes per (model,lane,depth,config) group.
    sizes = _group_sizes(cells)
    sweep_cache: dict[tuple, tuple[list[dict], dict, Outcome]] = {}

    try:
        for cell in cells:
            if budget_s is not None and (time.monotonic() - started) > budget_s:
                result.aborted = "budget-exhausted"
                break
            # Pause/Stop from the web control surface (checked between cells — a
            # sweep can't be suspended mid-run). The item's remaining cells stay
            # stale and resume on the next Start (DESIGN §5.4 resumability).
            if should_continue is not None and not should_continue():
                result.aborted = "stopped"
                break
            # Between cells, re-check for manually-started traffic ONLY for a
            # non-exclusive session (an exclusive sweep restarts slots itself, so
            # a between-cell traffic check would false-abort — DESIGN §5.2).
            if not exclusive and _traffic_in_flight(api):
                result.aborted = "gpu-contended-midsession"
                break

            run_id = _new_run_id()
            artifacts = store.artifacts_dir(run_id)

            if cell.kind in _TIER_A_KINDS:
                # The per-attempt seam timeout (crosses the privilege boundary
                # as --timeout-s); the OUTER 6-attempt bound is now an emergent
                # property of harness.run_cell's own retry loop rather than a
                # separately-enforced Python watchdog (see harness.py header).
                per_attempt = _tier_a_per_attempt_s(cell)
                record = _tier_a_record(
                    cell,
                    artifacts,
                    per_attempt,
                    exclusive,
                    sizes,
                    sweep_cache,
                    host,
                    suite_id,
                    run_id,
                    api,
                    trigger,
                )
            elif cell.kind in _GUIDELLM_KINDS:
                watchdog_s = 3 * _EXPECTED_S.get(cell.kind, 300)
                record = _guidellm_record(
                    cell, artifacts, watchdog_s, api, host, suite_id, run_id, trigger
                )
            elif cell.kind in _LLAMA_BENCHY_KINDS:
                watchdog_s = 3 * _EXPECTED_S.get(cell.kind, 300)
                record = _llama_benchy_record(
                    cell, artifacts, watchdog_s, api, host, suite_id, run_id, trigger
                )
            else:
                watchdog_s = 3 * _EXPECTED_S.get(cell.kind, 300)
                record = _tier_bc_record(
                    cell,
                    artifacts,
                    watchdog_s,
                    api,
                    exclusive,
                    host,
                    suite_id,
                    run_id,
                    trigger,
                )

            store.append_record(record)  # append-as-we-go = resumable
            result.run_ids.append(run_id)
            if record.outcome is Outcome.OK:
                result.cells_ok += 1
            else:
                result.cells_failed += 1  # a bad cell never kills the session
    finally:
        result.duration_s = round(time.monotonic() - started, 1)

    return result


def _group_key(c) -> tuple:
    """The memoisation key for a shared Tier-A sweep: (model, lane, depth,
    config). The config dimension keeps two flag variants of the same model/lane
    from sharing one sweep — different flags measure different things."""
    return (c.model_id, c.lane, c.depth, getattr(c, "config_label", "default"))


def _group_sizes(cells: list[Cell]) -> dict[tuple, tuple[int, int]]:
    """(pp prompt length, tg decode length) for each group's shared sweep: the
    pp cell's n_prompt and the tg cell's n_gen where the group has them, else
    the defaults. One sweep serves both siblings, so both sizes ride one argv."""
    out: dict[tuple, tuple[int, int]] = {}
    for c in cells:
        if c.kind not in _TIER_A_KINDS:
            continue
        key = _group_key(c)
        pp, tg = out.get(key, (_DEFAULT_PP_PROMPT, _DEFAULT_TG_GEN))
        if c.kind == "pp":
            pp = int(c.identity.workload.n_prompt or _DEFAULT_PP_PROMPT)
        else:
            tg = int(c.identity.workload.n_gen or _DEFAULT_TG_GEN)
        out[key] = (pp, tg)
    return out


def _tier_a_runner(
    per_attempt: int,
) -> Callable[[list[str], int | None], tuple[int, str, str]]:
    """The production callable ``harness.run_cell`` uses to execute each
    attempt: a ``Popen``-based watchdog with its OWN (Python-side) timeout as
    a backstop above the seam's ``timeout --kill-after=30`` — the sudo/shim
    child is root-owned and unkillable from here once wedged (``kill(2)`` ->
    ``EPERM``), so this exists only to bound a wedged ``sudo`` PROMPT or a
    wedged shim that never reaches its own timeout at all; the shim's real
    per-attempt cap still does the normal-case killing. stdout/stderr are
    captured SEPARATELY here, unlike ``_run_subprocess``'s merged stream —
    the engine's ``-o json`` result now lives on stdout and must never be
    corrupted by interleaved stderr."""
    margin = 35  # the shim's own --kill-after=30 grace, plus a little slack

    def _run(argv: list[str], timeout_s: int | None) -> tuple[int, str, str]:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=(timeout_s or per_attempt) + margin)
            return proc.returncode, out or "", err or ""
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(proc.pid, sig)
                try:
                    out, err = proc.communicate(timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    out, err = "", ""
            else:
                out, err = "", ""
            return -9, out, (err or "") + "\n[watchdog] outer python-side kill\n"

    return _run


def _tier_a_record(
    cell,
    artifacts,
    per_attempt,
    exclusive,
    sizes,
    cache,
    host,
    suite_id,
    run_id,
    api,
    trigger="manual",
) -> Record:
    """Run (or reuse) the Tier-A sweep for this cell's group and parse its kind.

    Exclusivity (``harness.ExclusiveSlots``) wraps ONLY the actual seam
    execution for a non-memoised group — a cache hit (the pp/tg sibling
    reusing the group's already-swept output) touches no slots at all,
    matching the shell harness's per-SWEEP (not per-cell) ``--exclusive``."""
    key = _group_key(cell)
    if key in cache:
        rows, meta, outcome = cache[key]  # sibling already swept this group
        tail = ""
    else:
        pp, tg = sizes.get(key, (_DEFAULT_PP_PROMPT, _DEFAULT_TG_GEN))
        model_root = _model_root()
        model_rel = _rel_gguf(cell.identity.model.gguf)
        flags = _tier_a_flags(cell, pp, tg)
        log = artifacts / "sweep-benchctl.log"
        run_kwargs = {
            "model_rel": model_rel,
            "model_root": model_root,
            "flags": flags,
            "timeout_s": per_attempt,
            "log_path": log,
            "runner": _tier_a_runner(per_attempt),
        }
        try:
            # The lane lookup lives INSIDE this try (finding 5): planner.plan()
            # already rejects an unknown lane at plan time, but a bogus-lane
            # cell that reaches here some other way must record FAILED, not
            # crash the whole session with a bare KeyError.
            spec = harness.lane_specs()[cell.lane]
            devices = resolve_bench_devices()
            if exclusive:
                with harness.ExclusiveSlots():
                    result = harness.run_cell(spec, devices, **run_kwargs)
            else:
                result = harness.run_cell(spec, devices, **run_kwargs)
        except (BenchDeviceError, RuntimeError, KeyError) as exc:
            # Device resolution failed, ExclusiveSlots could not stop a slot,
            # or the cell's lane isn't in harness.lane_specs() — a bad cell
            # records its outcome and the session CONTINUES (DESIGN §5.3), it
            # never crashes run_session.
            rows, meta, outcome, tail = [], {}, Outcome.FAILED, str(exc)
        else:
            rows, meta, tail = result.rows, result.meta, result.tail
            outcome = _classify(result.rc, tail) if (result.rc != 0 or not rows) else Outcome.OK
        cache[key] = (rows, meta, outcome)

    # Copy the shared engine output into THIS cell's artifacts (self-contained).
    if rows:
        (artifacts / "llama-bench.json").write_text(json.dumps(rows, indent=2))
    if meta:
        (artifacts / "meta.json").write_text(json.dumps(meta, indent=2))

    parsed = parse_llama_bench(rows, meta, cell.kind, cell.depth)
    if outcome is Outcome.OK and not parsed.reps:
        outcome = Outcome.FAILED  # sweep succeeded but this kind's row is missing
    return _assemble(cell, parsed, outcome, host, suite_id, run_id, artifacts, tail, api, trigger)


def _tier_bc_record(
    cell,
    artifacts,
    timeout_s,
    api,
    exclusive,
    host,
    suite_id,
    run_id,
    trigger="manual",
) -> Record:
    """Run a Tier-B/C server_ab cell and parse it. Slot name resolves from the
    registry model id via /api/slots (a live server_ab slot), falling back to the
    model id itself."""
    slot = _slot_for_model(api, cell.model_id) or cell.model_id
    out = artifacts / "server-ab.json"
    log = artifacts / "server-ab.log"
    cmd = _tier_bc_cmd(cell, slot, api, out)
    rc, tail = _run_subprocess(cmd, timeout_s, log)
    doc: dict = {}
    if out.exists():
        try:
            doc = json.loads(out.read_text())
        except (OSError, json.JSONDecodeError):
            doc = {}
    parsed = parse_server_ab(doc, cell.kind)
    if rc != 0 or not parsed.reps:
        outcome = _classify(rc, tail) if rc != 0 else Outcome.FAILED
    else:
        outcome = Outcome.OK
    return _assemble(cell, parsed, outcome, host, suite_id, run_id, artifacts, tail, api, trigger)


def _get_slot(api: str, model_id: str) -> dict[str, Any] | None:
    """The full ``/api/slots`` entry for a model id, or ``None`` if no slot is
    currently serving it. The single lookup both ``_slot_for_model`` (server_ab
    needs a slot NAME) and the Phase-3 adapters (guidellm/llama-benchy need
    the slot's actual HTTP port) build on."""
    slots = _get_json(api, "/api/slots")
    items = slots if isinstance(slots, list) else (slots or {}).get("slots") or []
    for s in items:
        if s.get("model_id") == model_id or s.get("model_default") == model_id:
            return s
    return None


def _slot_for_model(api: str, model_id: str) -> str | None:
    slot = _get_slot(api, model_id)
    return slot.get("name") if slot else None


# --------------------------------------------------------------------------- #
# Phase-3 OSS adapters — GuideLLM (chat) / llama-benchy (http_pp/http_tg)
# --------------------------------------------------------------------------- #


def _guidellm_profile(cell) -> tuple[str, dict[str, Any]]:
    """The GuideLLM profile kind + options for a cell (Bench Phase 3 design
    decision 1): ``synchronous`` for concurrency 1 (every shipped suite
    today), ``concurrent`` with ``streams=N`` for concurrency>1. No suite
    surface exists yet for a ``sweep`` profile — that is deliberately left
    for a later suite opt-in, not built here."""
    concurrency = int(cell.identity.workload.concurrency or 1)
    if concurrency <= 1:
        return "synchronous", {}
    return "concurrent", {"streams": concurrency}


def _no_slot_record(cell, engine_kind, host, suite_id, run_id, artifacts, api, trigger) -> Record:
    """A cell whose model has no live serving slot: never a crash, always a
    FAILED record with an actionable note (mirrors the rest of this module's
    "a bad cell records its outcome and the session continues" rule)."""
    parsed = Parsed(engine_observed=Engine(kind=engine_kind))
    note = f"no live slot found for model {cell.model_id!r}"
    return _assemble(
        cell, parsed, Outcome.FAILED, host, suite_id, run_id, artifacts, note, api, trigger
    )


def _guidellm_record(
    cell, artifacts, timeout_s, api, host, suite_id, run_id, trigger="manual"
) -> Record:
    """Run a ``chat`` cell through the GuideLLM adapter against the model's
    live slot endpoint (design decision 1) instead of server_ab's ``ab``
    mode. Never raises: a missing slot, a failed run, or an empty parse all
    produce a FAILED/HANG/OOM record, same as every other cell kind here."""
    slot = _get_slot(api, cell.model_id)
    if not slot or not slot.get("port"):
        return _no_slot_record(cell, "guidellm", host, suite_id, run_id, artifacts, api, trigger)

    endpoint = f"http://127.0.0.1:{int(slot['port'])}"
    profile_kind, profile_options = _guidellm_profile(cell)
    out_path = artifacts / "guidellm-benchmarks.json"
    request = guidellm.GuidellmRequest(
        endpoint=endpoint,
        model=cell.model_id,
        profile_kind=profile_kind,
        output_path=str(out_path),
        profile_options=profile_options,
        max_requests=max(int(cell.reps), 1),
        # #1773: plan-time resolved/gated tokenizer (planner._resolve_tokenizer)
        # — never derive it here or fall back to cell.model_id.
        tokenizer=cell.tokenizer or None,
    )
    result = guidellm.run_guidellm(request, timeout_s=timeout_s)
    parsed = (
        guidellm.parse_benchmarks(result.doc, profile_kind)
        if result.doc is not None
        else Parsed(engine_observed=Engine(kind="guidellm"))
    )
    outcome = result.outcome
    if outcome is Outcome.OK and not parsed.reps:
        outcome = Outcome.FAILED
    return _assemble(
        cell, parsed, outcome, host, suite_id, run_id, artifacts, result.tail, api, trigger
    )


def _llama_benchy_record(
    cell, artifacts, timeout_s, api, host, suite_id, run_id, trigger="manual"
) -> Record:
    """Run an ``http_pp``/``http_tg`` cell through the llama-benchy adapter
    against the model's live slot endpoint (design decision 3): llama-bench
    vocabulary (pp/tg x depth) measured over HTTP, complementing the Tier-A
    podman-exec'd path. Never raises, same rule as every other cell kind."""
    slot = _get_slot(api, cell.model_id)
    if not slot or not slot.get("port"):
        return _no_slot_record(
            cell, "llama-benchy", host, suite_id, run_id, artifacts, api, trigger
        )

    endpoint = f"http://127.0.0.1:{int(slot['port'])}/v1"
    pp = int(cell.identity.workload.n_prompt or _DEFAULT_PP_PROMPT)
    tg = int(cell.identity.workload.n_gen or _DEFAULT_TG_GEN)
    out_path = artifacts / "llama-benchy-report.json"
    request = llama_benchy.LlamaBenchyRequest(
        endpoint=endpoint,
        model=cell.model_id,
        pp=pp,
        tg=tg,
        result_path=out_path,
        depths=(cell.depth,),
        reps=max(int(cell.reps), 1),
        timeout_s=timeout_s,
    )
    result = llama_benchy.run_llama_benchy(request)
    kind = "pp" if cell.kind == "http_pp" else "tg"
    parsed = (
        llama_benchy.parse_benchy(result.doc, kind, depth=cell.depth)
        if result.doc is not None
        else Parsed(engine_observed=Engine(kind="llama-benchy"))
    )
    outcome = result.outcome
    if outcome is Outcome.OK and not parsed.reps:
        outcome = Outcome.FAILED
    return _assemble(
        cell, parsed, outcome, host, suite_id, run_id, artifacts, result.tail, api, trigger
    )


def _assemble(
    cell,
    parsed,
    outcome,
    host,
    suite_id,
    run_id,
    artifacts,
    tail,
    api,
    trigger="manual",
) -> Record:
    """Build the schema-2 record from a Parsed result + the planned cell.

    cell_key convergence (DESIGN §5.4): the record REUSES the planned identity so
    plan↔run hash identically. The observed engine provenance (image/build/
    tool_version) parsed from real output is stamped onto ``identity.engine``
    for DISPLAY, and the planned cell_key is passed explicitly so staleness
    still converges even though the observed engine block was not knowable at
    plan time (see appendix). ``tool_version`` (Bench Phase 3: guidellm/
    llama-benchy/tool-eval-bench) is display + comparability provenance, not
    identity (schema.Engine's docstring) — like image/build, it never feeds
    cell_key."""
    identity = cell.identity
    obs = parsed.engine_observed
    if obs.image or obs.llamacpp_build:
        identity.engine.image = obs.image or identity.engine.image
        identity.engine.llamacpp_build = obs.llamacpp_build or identity.engine.llamacpp_build
    if obs.tool_version:
        identity.engine.tool_version = obs.tool_version
    # Stamp the RESOLVED argv/kv the engine actually ran (from the output) onto
    # the record for display — the plan-time config often has empty argv (a model
    # with no registry extra_args). DISPLAY-only: cell_key stays the planned one.
    cfg = parsed.config_observed
    if cfg is not None:
        if cfg.argv:
            identity.config.argv = cfg.argv
        if cfg.kv:
            identity.config.kv = cfg.kv
        if cfg.ctx:
            identity.config.ctx = cfg.ctx
    # Non-exclusive cell measured while a GPU slot was serving → not clean enough
    # to publish (DESIGN §4 smoke): downgrade an otherwise-ok outcome.
    if outcome is Outcome.OK and not host.exclusive and _gpu_slot_serving(api):
        outcome = Outcome.SKIPPED_CONTENDED
    note = (
        ""
        if outcome is Outcome.OK
        else (f"{outcome.value}: {tail[-200:]}" if tail else outcome.value)
    )
    return Record(
        run_id=run_id,
        suite=suite_id,
        trigger=trigger,
        identity=identity,
        host=host,
        outcome=outcome,
        cell_key=cell.cell_key,  # the PLANNED content-address (convergence)
        config=getattr(cell, "config_label", "default"),  # display label for the variant
        reps=parsed.reps,
        summary=parsed.summary,
        telemetry=parsed.telemetry,
        artifacts=str(artifacts.relative_to(artifacts.parents[1])),
        note=note,
    )
