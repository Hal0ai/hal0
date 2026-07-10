"""runner.py — the session runner (DESIGN §5 "Runner behavior").

The runner turns a planner worklist into appended records. The control flow was
always here; Phase 2 fills the two engine-output parsers (now in parsers.py) and
corrects every place the stubs assumed a contract that differs from the real box
(verified on CT105 2026-07-05 — see the results appendix):

  * GPU-window gate (DESIGN §5.2) uses the REAL routes: slot status
    (``/api/slots``, states offline/warming/serving + container_status) and
    throughput history (``/api/stats/throughput/history?window_s=`` — NOT
    ``/api/throughput``). "Busy" = a request in flight; a merely-loaded slot is
    stopped by the seam's own ``--exclusive``, so an exclusive session may
    proceed while a slot is loaded-but-idle.
  * Exclusivity is the seam's own leading ``--exclusive`` flag PER Tier-A sweep
    (DESIGN §0.5 — the session-level ``gpu-quiesce`` verb was deferred and does
    not exist on-box). Tier-A sweeps are memoised per (model,lane,depth) so one
    GPU load (one stop/restart) yields BOTH the pp and tg records.
  * Tier A (pp/tg) goes through ``hal0-benchctl sweep <rel.gguf> <lane>
    [--exclusive] -p .. -n .. -r ..`` (positional, not flags) which writes to
    ``/var/lib/hal0/benchmarks/runs/…__<lane>__sweep.json``; the runner locates
    that output, copies it into the run's artifacts/, and parses it.
  * Tier B/C (chat/embed/rerank/reuse) go through the installed ``server_ab.py``
    (modes ab/reuse/embed/rerank; no depth/mtp/batch on this build).

Per-cell watchdog = 3x expected (DESIGN §5.3); a bad cell records its outcome and
the session CONTINUES. Resumable by construction (DESIGN §5.4): each record is
appended as it finishes, so re-planning after a crash recomputes what's missing.
The runner is unprivileged (User=hal0); only Tier A reaches privilege, only via
the single ``hal0-benchctl`` grant.
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .parsers import parse_llama_bench, parse_server_ab
from .schema import Host, Outcome, Record
from .store import Store

if TYPE_CHECKING:
    from .planner import Cell

DEFAULT_API = "http://127.0.0.1:8080"
BENCHCTL = "/usr/lib/hal0/bin/hal0-benchctl"
SERVER_AB = "/usr/lib/hal0/bench/server_ab.py"
# Where the seam's llama-bench harness writes results (hal0-benchctl RESULTS).
V1_RUNS_DIR = Path("/var/lib/hal0/benchmarks/runs")
MODEL_ROOT = "/mnt/ai-models/"

# Rough per-cell expected wall-clock (seconds) for the watchdog (fires at 3x).
# Generous on purpose — it catches a hang, it is not an SLA.
_EXPECTED_S = {
    "pp": 120, "tg": 180, "chat": 240, "reuse": 180,
    "embed": 120, "rerank": 120, "batch": 600, "mtp": 300,
}
_TIER_A_KINDS = {"pp", "tg"}
# Tier-B cell kind -> server_ab.py --mode (this build: ab/reuse/embed/rerank).
_KIND_TO_MODE = {"chat": "ab", "reuse": "reuse", "embed": "embed", "rerank": "rerank"}
# Default tg decode length when a group has no explicit tg cell n_gen.
_DEFAULT_TG_GEN = 256


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
        return True  # fail-safe
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
    """The model path the seam expects: relative to /mnt/ai-models, ending .gguf
    (hal0-benchctl validate_model). Absolute registry paths are stripped."""
    return gguf[len(MODEL_ROOT):] if gguf.startswith(MODEL_ROOT) else gguf.lstrip("/")


def _run_subprocess(cmd: list[str], timeout_s: float, log_path: Path) -> tuple[int, str]:
    """Run one engine subprocess under the watchdog, teeing output to ``log_path``.
    Returns (returncode, tail). A timeout returns rc=-9 → ``hang``."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(cmd, timeout=timeout_s, capture_output=True, text=True)
        out = (proc.stdout or "") + (proc.stderr or "")
        log_path.write_text(out, encoding="utf-8")
        return proc.returncode, out[-4000:]
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        log_path.write_text((out or "") + f"\n[watchdog] killed after {timeout_s:.0f}s\n")
        return -9, "watchdog-timeout"


def _clear_stale_sweep(gguf: str, lane: str) -> None:
    """Remove any prior seam output for this (model,lane) sweep BEFORE running it.

    Verified on-box 2026-07-05: the seam's ``run_benchmarks.sh`` is idempotent —
    it prints ``[skip exists]`` and does NOTHING if ``<stem>__<lane>__sweep.json``
    already exists (``ran=0 skipped=1``), so a re-measure (provenance drift, or
    re-running a failed cell) would otherwise reuse stale numbers / produce no
    fresh file. benchlab owns the measurement, so it clears the cached artifact
    (results are chowned hal0:hal0, so the unprivileged runner can remove them)
    to guarantee the sweep actually runs and writes current numbers."""
    stem = Path(_rel_gguf(gguf)).name.removesuffix(".gguf")
    base = f"{stem}__{lane}__sweep"
    for suffix in (".json", ".meta.json", ".json.failed"):
        with contextlib.suppress(OSError):
            (V1_RUNS_DIR / f"{base}{suffix}").unlink()


def _locate_sweep_output(lane: str, since_wall: float) -> tuple[list[dict], dict] | None:
    """Find the ``…__<lane>__sweep.json`` (+ sibling ``.meta.json``) the seam just
    wrote (DESIGN §1 — the seam owns the results dir, not a --json-out path). Picks
    the newest matching file modified since the sweep started."""
    if not V1_RUNS_DIR.is_dir():
        return None
    cands = [
        p for p in V1_RUNS_DIR.glob(f"*__{lane}__sweep.json")
        if p.stat().st_mtime >= since_wall - 5
    ]
    if not cands:
        return None
    out = max(cands, key=lambda p: p.stat().st_mtime)
    try:
        rows = json.loads(out.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    meta_path = out.with_name(out.name.replace(".json", ".meta.json"))
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            meta = {}
    return (rows if isinstance(rows, list) else [], meta)


def _classify(rc: int, tail: str) -> Outcome:
    if rc == -9:
        return Outcome.HANG
    low = tail.lower()
    if "out of memory" in low or "hiperroroutofmemory" in low.replace("_", ""):
        return Outcome.OOM
    if rc != 0:
        return Outcome.FAILED
    return Outcome.OK


# --------------------------------------------------------------------------- #
# Command builders (shared by the executor and `run --dry-run`)
# --------------------------------------------------------------------------- #


def _tier_a_cmd(cell, exclusive: bool, tg_gen: int) -> list[str]:
    """The exact `hal0-benchctl sweep` argv for a Tier-A group (positional, the
    seam's real shape). `--exclusive` is the seam's own GPU stop/restart."""
    cmd = ["sudo", "-n", BENCHCTL, "sweep", _rel_gguf(cell.identity.model.gguf), cell.lane]
    if exclusive:
        cmd.append("--exclusive")
    cmd += ["-p", str(cell.depth), "-n", str(tg_gen), "-r", str(cell.reps)]
    # config-variant tuning flags (validated at plan time), sorted for a stable
    # command that matches the memoisation key.
    for flag in sorted(getattr(cell, "flags", None) or {}):
        cmd += [flag, str(cell.flags[flag])]
    return cmd


def _tier_bc_cmd(cell, slot: str, api: str, out: Path | str = "<artifacts>/server-ab.json") -> list[str]:
    """The exact `server_ab.py` argv for a Tier-B/C cell."""
    return [
        SERVER_AB, "--mode", _KIND_TO_MODE.get(cell.kind, "ab"),
        "--slot", slot, "--api", api, "--n", str(cell.reps), "--out", str(out),
    ]


def describe_worklist(cells: list[Cell], exclusive: bool, api: str) -> list[str]:
    """Human-readable ordered worklist + the EXACT seam/server_ab command each
    cell would run (for `run --dry-run`, DESIGN §5 / bring-up Phase 4). Pure —
    resolves no slots, touches no GPU. Marks the pp/tg sibling that reuses a
    group's single memoised sweep."""
    tg_gen = _tg_gen_by_group(cells)
    seen: set[tuple] = set()
    lines: list[str] = []
    for i, c in enumerate(cells, 1):
        cfg = f"  cfg:{c.config_label}" if getattr(c, "config_label", "default") != "default" else ""
        label = f"{i:2d}. {c.model_id}  {c.lane}  {c.kind}  d{c.depth}{cfg}  ({c.reason})"
        if c.kind in _TIER_A_KINDS:
            key = _group_key(c)
            if key in seen:
                cmd_str = "(reuses this group's memoised sweep — no extra GPU load)"
            else:
                seen.add(key)
                cmd_str = " ".join(_tier_a_cmd(c, exclusive, tg_gen.get(key, _DEFAULT_TG_GEN)))
        else:
            cmd_str = " ".join(_tier_bc_cmd(c, f"<slot-for:{c.model_id}>", api))
        lines.append(f"{label}\n      {cmd_str}")
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
    # tg decode length per (model,lane,depth) group, from the group's tg cell.
    tg_gen = _tg_gen_by_group(cells)
    sweep_cache: dict[tuple[str, str, int], tuple[list[dict], dict, Outcome]] = {}

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
            watchdog_s = 3 * _EXPECTED_S.get(cell.kind, 300)

            if cell.kind in _TIER_A_KINDS:
                record = _tier_a_record(
                    cell, artifacts, watchdog_s, exclusive, tg_gen, sweep_cache,
                    host, suite_id, run_id, api,
                )
            else:
                record = _tier_bc_record(
                    cell, artifacts, watchdog_s, api, exclusive, host, suite_id, run_id,
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


def _tg_gen_by_group(cells: list[Cell]) -> dict[tuple, int]:
    """Decode length for each group's shared sweep: the tg cell's n_gen if the
    group has one, else the default."""
    out: dict[tuple, int] = {}
    for c in cells:
        if c.kind not in _TIER_A_KINDS:
            continue
        key = _group_key(c)
        if c.kind == "tg":
            out[key] = int(c.identity.workload.n_gen or _DEFAULT_TG_GEN)
        out.setdefault(key, _DEFAULT_TG_GEN)
    return out


def _tier_a_record(
    cell, artifacts, timeout_s, exclusive, tg_gen_map, cache, host, suite_id, run_id, api,
) -> Record:
    """Run (or reuse) the Tier-A sweep for this cell's group and parse its kind."""
    key = _group_key(cell)
    if key in cache:
        rows, meta, outcome = cache[key]  # sibling already swept this group
        tail = ""
    else:
        cmd = _tier_a_cmd(cell, exclusive, tg_gen_map.get(key, _DEFAULT_TG_GEN))
        log = artifacts / "sweep-benchctl.log"
        # Force a fresh run: the seam skips a cell whose output already exists.
        _clear_stale_sweep(cell.identity.model.gguf, cell.lane)
        since = time.time()
        rc, tail = _run_subprocess(cmd, timeout_s, log)
        located = _locate_sweep_output(cell.lane, since)
        if located is None:
            rows, meta = [], {}
            outcome = _classify(rc, tail) if rc != 0 else Outcome.FAILED  # ran ok but no output
        else:
            rows, meta = located
            outcome = _classify(rc, tail) if (rc != 0 or not rows) else Outcome.OK
        cache[key] = (rows, meta, outcome)

    # Copy the shared engine output into THIS cell's artifacts (self-contained).
    if rows:
        (artifacts / "llama-bench.json").write_text(json.dumps(rows, indent=2))
    if meta:
        (artifacts / "meta.json").write_text(json.dumps(meta, indent=2))

    parsed = parse_llama_bench(rows, meta, cell.kind, cell.depth)
    if outcome is Outcome.OK and not parsed.reps:
        outcome = Outcome.FAILED  # sweep succeeded but this kind's row is missing
    return _assemble(cell, parsed, outcome, host, suite_id, run_id, artifacts, tail, api)


def _tier_bc_record(
    cell, artifacts, timeout_s, api, exclusive, host, suite_id, run_id,
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
    return _assemble(cell, parsed, outcome, host, suite_id, run_id, artifacts, tail, api)


def _slot_for_model(api: str, model_id: str) -> str | None:
    slots = _get_json(api, "/api/slots")
    items = slots if isinstance(slots, list) else (slots or {}).get("slots") or []
    for s in items:
        if s.get("model_id") == model_id or s.get("model_default") == model_id:
            return s.get("name")
    return None


def _assemble(
    cell, parsed, outcome, host, suite_id, run_id, artifacts, tail, api,
) -> Record:
    """Build the schema-2 record from a Parsed result + the planned cell.

    cell_key convergence (DESIGN §5.4): the record REUSES the planned identity so
    plan↔run hash identically. The observed engine provenance (image/build) parsed
    from real output is stamped onto ``identity.engine`` for DISPLAY, and the
    planned cell_key is passed explicitly so staleness still converges even though
    the observed engine block was not knowable at plan time (see appendix)."""
    identity = cell.identity
    obs = parsed.engine_observed
    if obs.image or obs.llamacpp_build:
        identity.engine.image = obs.image or identity.engine.image
        identity.engine.llamacpp_build = obs.llamacpp_build or identity.engine.llamacpp_build
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
    note = "" if outcome is Outcome.OK else (f"{outcome.value}: {tail[-200:]}" if tail else outcome.value)
    return Record(
        run_id=run_id,
        suite=suite_id,
        trigger="manual",
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
