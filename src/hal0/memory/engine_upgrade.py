"""Converge the bundled hindsight-api engine venv onto the pinned version.

The memory engine lives in its own venv at ``${var_lib}/memory/hindsight/.venv``
with an embedded postgres data dir at ``.pg0`` beside it. install.sh creates the
venv on a fresh box but deliberately never touches an existing one, and ``hal0
update`` swaps only the hal0 install tree — so before this pass existed, a box
installed at one engine version stayed there forever (the #1689/#1844 "update
never refreshes X" class; see GH issue for the 0.8.4 → 0.9.2 bump).

This module is the single owner of the engine version pin
(:data:`HINDSIGHT_API_PIN`) and of the upgrade itself
(:func:`upgrade_memory_engine`), which ``run_post_activation_migrations`` runs
as its last pass on both upgrade paths (``hal0 update`` commit and install.sh's
re-run heredoc — GH #1475 single-sequence seam). Fresh installs stay
install.sh's job: the pass no-ops when no venv exists.

Upgrade shape — build-aside, swap, verify, roll back:

* the replacement venv is built at ``.venv.new`` while the old engine keeps
  serving, so a pip/network failure costs nothing;
* the engine is stopped and ``.pg0`` is snapshotted to ``.pg0.pre-<old>``
  BEFORE the new engine ever starts, because hindsight-api runs its alembic
  migrations on startup and they are effectively one-way (an old engine cannot
  run against a migrated schema) — the snapshot is the only rollback;
* after the swap the pass polls ``/health`` and requires ``/version`` to
  report exactly the pin; any postcheck failure restores both the old venv and
  the snapshot.

``hal0 update --rollback`` re-activates the previous hal0 tree but leaves the
engine (and its migrated ``.pg0``) on the new version — acceptable because the
engine's HTTP surface is additive across the supported window, and reverting a
one-way DB migration behind the operator's back would be worse.

Privileges: on the ``hal0 update`` path this runs as the unprivileged ``hal0``
user (the venv and ``.pg0`` are hal0-owned; service stop/start routes through
:class:`hal0.system.seam.SystemCtlSeam`'s companion-unit arms). On the
install.sh path it runs as root — everything the pass creates is chowned back
to ``hal0:hal0`` because postgres refuses a data dir not owned by the running
user.

Never raises for operational failures — returns a status dict (the
``apply_extraction_slot`` posture) so the migration sequence's best-effort
wrapper only ever catches genuine bugs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from hal0.config.paths import var_lib
from hal0.system.seam import SystemCtlSeam

log = structlog.get_logger(__name__)

#: The engine version every box converges on. Single source of truth —
#: install.sh's ``HINDSIGHT_PIN`` shell variable must match
#: (tests/installer/test_hindsight_pin_lockstep.py enforces it).
HINDSIGHT_API_PIN = "0.9.2"

#: Full unit name — must key into ``COMPANION_SERVICE_UNITS`` so the seam
#: routes to the wrapper's ``svc-stop/svc-start hindsight`` arms (a bare
#: ``hindsight-api`` would fall through to a polkit-blocked systemctl).
SERVICE = "hindsight-api.service"

#: Loopback base the unit binds; the same literal install.sh polls.
ENGINE_BASE_URL = "http://127.0.0.1:9177"

#: Interpreter band the engine supports, mirroring installer/lib/preflight.sh's
#: ``HINDSIGHT_PY_MIN_MINOR``/``HINDSIGHT_PY_MAX_MINOR`` (kept as constants
#: there too, "so bumping the supported band is a one-line change").
PY_MIN_MINOR = 11
PY_MAX_MINOR = 13

#: Each pip invocation gets its own cap well under the post-swap migration
#: subprocess envelope (2700s in updater.py) so the outer process always has
#: headroom to emit its JSON result even when a wheel build wedges.
_PIP_TIMEOUT_S = 2400
_VENV_TIMEOUT_S = 120
_PROBE_TIMEOUT_S = 30
_SYSTEMCTL_TIMEOUT_S = 120.0
#: /health poll after the swapped engine starts. Longer than install.sh's 120s
#: fresh-install poll: the first boot on an upgraded ``.pg0`` also runs the
#: engine's alembic chain before the socket comes up.
_HEALTH_POLL_TOTAL_S = 180
_HEALTH_POLL_STEP_S = 3
#: Shorter poll for "did the OLD engine come back" after a rollback.
_ROLLBACK_HEALTH_POLL_TOTAL_S = 60

#: Snapshot must fit with headroom; refuse to gamble the only rollback on a
#: nearly-full disk.
_SNAPSHOT_FREE_FACTOR = 1.5

Runner = Callable[..., subprocess.CompletedProcess]


def hindsight_dir() -> Path:
    """The engine's home: venv, ``.pg0`` and caches live under it."""
    return var_lib() / "memory" / "hindsight"


def _run(
    runner: Runner, args: list[str], *, timeout: float, **kwargs: Any
) -> subprocess.CompletedProcess:
    return runner(args, capture_output=True, text=True, timeout=timeout, **kwargs)


def _http_get_json(url: str, timeout: float = 5.0) -> dict[str, Any] | None:
    """Best-effort GET returning parsed JSON, ``None`` on any failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _chown_tree(path: Path) -> None:
    """``chown -R hal0:hal0`` when running as root (install.sh path).

    Postgres refuses a data dir not owned by the running user, and the unit
    runs as ``hal0`` — a root-built ``.venv.new`` or root-copied snapshot must
    not stay root-owned. Best-effort: on a box with no ``hal0`` user (dev/CI)
    ownership is irrelevant because the caller isn't root either.
    """
    if os.geteuid() != 0:
        return
    try:
        for p in [path, *path.rglob("*")]:
            shutil.chown(p, user="hal0", group="hal0")
    except (LookupError, OSError) as exc:
        log.warning("updater.memory_engine_chown_failed", path=str(path), error=str(exc))


def _installed_version(runner: Runner, venv: Path) -> str | None:
    """The ``hindsight-api`` dist version inside ``venv``, or ``None``.

    Asked of the venv's own interpreter — the running process's dist set is
    the wrong one. ``None`` covers both "no such dist" and "interpreter
    broken": either way the venv needs a rebuild, and only the rebuilt venv's
    own probe (never pip's exit code) proves success.
    """
    py = venv / "bin" / "python"
    try:
        proc = _run(
            runner,
            [
                str(py),
                "-c",
                "import importlib.metadata as m; print(m.version('hindsight-api'))",
            ],
            timeout=_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    version = (proc.stdout or "").strip()
    return version or None


def _interpreter_in_band(runner: Runner, py: str) -> bool:
    try:
        proc = _run(
            runner,
            [py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            timeout=_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    try:
        major, minor = ((proc.stdout or "").strip()).split(".")
        return int(major) == 3 and PY_MIN_MINOR <= int(minor) <= PY_MAX_MINOR
    except ValueError:
        return False


def _resolve_interpreter(runner: Runner, venv: Path) -> tuple[str, bool]:
    """Pick the interpreter for the replacement venv.

    Returns ``(python, fallback)`` where ``fallback=True`` means no in-band
    3.11-3.13 interpreter was found and pip must bypass the requires-python
    gate (litellm's metadata-only 3.14 cap — the same dance install.sh's
    fresh-install branch does). Mirrors preflight.sh's
    ``resolve_hindsight_python`` order, minus interpreter auto-install (a
    root apt operation that stays preflight-only):

    1. ``HAL0_HINDSIGHT_PYTHON`` — operator override, honored verbatim.
    2. The existing venv's own base interpreter (realpath through the
       ``bin/python`` symlink) — on the ``hal0 update`` path preflight never
       ran, and the interpreter that built the current venv already proved
       itself. Falls back to ``pyvenv.cfg``'s ``home`` when the symlink
       dangles.
    3. ``python3.13`` / ``python3.12`` / ``python3.11`` on PATH.
    4. This process's own base interpreter, flagged as out-of-band.
    """
    override = os.environ.get("HAL0_HINDSIGHT_PYTHON")
    if override:
        return override, not _interpreter_in_band(runner, override)

    base = Path(os.path.realpath(venv / "bin" / "python"))
    if not base.exists():
        cfg = venv / "pyvenv.cfg"
        try:
            for line in cfg.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "home" and value.strip():
                    candidate = Path(value.strip()) / "python3"
                    if candidate.exists():
                        base = candidate
                    break
        except OSError:
            pass
    if base.exists() and _interpreter_in_band(runner, str(base)):
        return str(base), False

    for name in ("python3.13", "python3.12", "python3.11"):
        found = shutil.which(name)
        if found and _interpreter_in_band(runner, found):
            return found, False

    return os.path.realpath(sys.executable), True


def _du_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _prune_keep_newest(parent: Path, pattern: str) -> None:
    """Delete all-but-the-newest ``pattern`` entries under ``parent``.

    Called only from the converged branch: debris from PRIOR upgrades is
    removed only once a LATER run has proven the box healthy on the pin, so a
    failed upgrade always keeps its forensics and its rollback snapshot.
    """
    entries = sorted(parent.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in entries[1:]:
        shutil.rmtree(stale, ignore_errors=True)


def _svc(seam: SystemCtlSeam, verb: str, *, check: bool = True) -> None:
    seam.systemctl("systemctl", verb, SERVICE, check=check, timeout=_SYSTEMCTL_TIMEOUT_S)


def _poll_health(http_get: Callable[[str], dict | None], total_s: float) -> bool:
    deadline = time.monotonic() + total_s
    while time.monotonic() < deadline:
        if http_get(f"{ENGINE_BASE_URL}/health") is not None:
            return True
        time.sleep(_HEALTH_POLL_STEP_S)
    return False


def upgrade_memory_engine(
    *,
    job_id: str | None = None,
    upgrade: bool = True,
    seam: SystemCtlSeam | None = None,
    runner: Runner | None = None,
    http_get: Callable[[str], dict | None] | None = None,
    hs_dir: Path | None = None,
) -> dict[str, Any]:
    """Bring the engine venv to :data:`HINDSIGHT_API_PIN`, or report why not.

    ``upgrade=False`` is the boot-time diagnose-only mode
    (``check_outstanding_migrations``): staleness is logged with the remedy
    but no pip runs, no service stops, and no one-way DB migration is
    triggered outside an operator-visible update — the
    ``skip_image_retag``/``repair_hermes_venv=False`` posture.

    ``seam``/``runner``/``http_get``/``hs_dir`` are test injection points
    (default-constructed = production behaviour), mirroring
    :func:`hal0.memory.extraction_env.apply_extraction_slot`.

    Returns a status dict whose ``status`` is one of ``skipped``,
    ``converged``, ``stale``, ``build_failed``, ``snapshot_failed``,
    ``upgraded``, ``rolled_back``. Only genuine bugs raise.
    """
    seam = seam if seam is not None else SystemCtlSeam()
    runner = runner if runner is not None else subprocess.run
    http_get = http_get if http_get is not None else _http_get_json
    hs = hs_dir if hs_dir is not None else hindsight_dir()

    venv = hs / ".venv"
    venv_new = hs / ".venv.new"
    pg = hs / ".pg0"

    if os.environ.get("HAL0_SKIP_HINDSIGHT") == "1":
        return {"status": "skipped", "reason": "HAL0_SKIP_HINDSIGHT=1"}

    # Mid-swap crash recovery: a previous run renamed .venv away and died
    # before renaming .venv.new in. Restore the newest .venv.old-* so the
    # normal flow (detect → rebuild) can proceed deterministically.
    if not venv.exists():
        old_venvs = sorted(hs.glob(".venv.old-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if old_venvs:
            os.rename(old_venvs[0], venv)
            log.warning(
                "updater.memory_engine_midswap_recovered",
                job_id=job_id,
                restored=old_venvs[0].name,
            )

    if not os.access(venv / "bin" / "hindsight-api", os.X_OK):
        # Fresh install (or a box that never had the engine) — install.sh's
        # job, not this pass's: it owns interpreter preflight, unit install
        # and bank seeding.
        return {"status": "skipped", "reason": "no engine venv (fresh install is install.sh's job)"}

    installed = _installed_version(runner, venv) or "unknown"
    if installed == HINDSIGHT_API_PIN:
        _prune_keep_newest(hs, ".venv.old-*")
        _prune_keep_newest(hs, ".pg0.pre-*")
        return {"status": "converged", "version": installed}

    if not upgrade:
        log.warning(
            "updater.memory_engine_stale",
            job_id=job_id,
            installed=installed,
            pinned=HINDSIGHT_API_PIN,
            remedy="run 'hal0 update' or re-run install.sh",
        )
        return {"status": "stale", "installed": installed, "pinned": HINDSIGHT_API_PIN}

    pg_snap = hs / f".pg0.pre-{installed}"
    venv_old = hs / f".venv.old-{installed}"
    result: dict[str, Any] = {
        "from": installed,
        "to": HINDSIGHT_API_PIN,
        "snapshot": str(pg_snap),
    }

    # ── Build aside (engine still serving; a failure here costs nothing) ──
    py, fallback = _resolve_interpreter(runner, venv)
    shutil.rmtree(venv_new, ignore_errors=True)
    try:
        proc = _run(runner, [py, "-m", "venv", str(venv_new)], timeout=_VENV_TIMEOUT_S)
        if proc.returncode != 0:
            raise subprocess.SubprocessError(proc.stderr or "venv creation failed")
        pip = str(venv_new / "bin" / "pip")
        _run(runner, [pip, "install", "--upgrade", "pip", "wheel", "-q"], timeout=_PIP_TIMEOUT_S)
        spec = f"hindsight-api=={HINDSIGHT_API_PIN}"
        proc = _run(runner, [pip, "install", spec, "-q"], timeout=_PIP_TIMEOUT_S)
        if proc.returncode != 0 and not fallback:
            # Same two-attempt dance as install.sh's fresh-install branch:
            # litellm's requires-python gate is metadata-only.
            fallback = True
        if proc.returncode != 0:
            proc = _run(
                runner,
                [pip, "install", "--ignore-requires-python", spec, "-q"],
                timeout=_PIP_TIMEOUT_S,
            )
            if proc.returncode != 0:
                raise subprocess.SubprocessError(proc.stderr or "pip install failed")
        built = _installed_version(runner, venv_new)
        if built != HINDSIGHT_API_PIN or not os.access(venv_new / "bin" / "hindsight-api", os.X_OK):
            # pip exit codes lie (#2021); only the venv's own probe is truth.
            raise subprocess.SubprocessError(
                f"built venv reports {built!r}, expected {HINDSIGHT_API_PIN!r}"
            )
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(venv_new, ignore_errors=True)
        log.warning(
            "updater.memory_engine_build_failed",
            job_id=job_id,
            error=str(exc),
            remedy=(
                f"engine still on {installed}; re-run 'hal0 update' or install.sh "
                "when the cause (network, disk, interpreter) is fixed"
            ),
        )
        return {**result, "status": "build_failed", "error": str(exc)}
    _chown_tree(venv_new)

    # ── Stop the engine; quiesce .pg0 for a consistent snapshot ──
    try:
        _svc(seam, "stop")
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(venv_new, ignore_errors=True)
        log.warning("updater.memory_engine_stop_failed", job_id=job_id, error=str(exc))
        return {**result, "status": "build_failed", "error": f"stop failed: {exc}"}

    # ── Snapshot: the only rollback for a one-way alembic migration ──
    if pg.exists() and not pg_snap.exists():
        free = shutil.disk_usage(hs).free
        needed = int(_du_bytes(pg) * _SNAPSHOT_FREE_FACTOR)
        if free < needed:
            shutil.rmtree(venv_new, ignore_errors=True)
            _svc(seam, "start", check=False)
            log.warning(
                "updater.memory_engine_snapshot_no_space",
                job_id=job_id,
                free=free,
                needed=needed,
                remedy=f"free {needed} bytes under {hs} and re-run — never upgrading "
                "without a .pg0 snapshot (the DB migration is one-way)",
            )
            return {**result, "status": "snapshot_failed", "error": "insufficient disk space"}
        proc = _run(runner, ["cp", "-a", str(pg), str(pg_snap)], timeout=_PIP_TIMEOUT_S)
        if proc.returncode != 0:
            shutil.rmtree(pg_snap, ignore_errors=True)
            shutil.rmtree(venv_new, ignore_errors=True)
            _svc(seam, "start", check=False)
            log.warning(
                "updater.memory_engine_snapshot_failed",
                job_id=job_id,
                error=proc.stderr,
            )
            return {**result, "status": "snapshot_failed", "error": proc.stderr}
        _chown_tree(pg_snap)

    # ── Swap (two same-fs renames) ──
    shutil.rmtree(venv_old, ignore_errors=True)
    os.rename(venv, venv_old)
    os.rename(venv_new, venv)

    # ── Start + postcheck ──
    healthy = False
    try:
        _svc(seam, "start")
        healthy = _poll_health(http_get, _HEALTH_POLL_TOTAL_S)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("updater.memory_engine_start_failed", job_id=job_id, error=str(exc))

    reported: str | None = None
    if healthy:
        version_payload = http_get(f"{ENGINE_BASE_URL}/version") or {}
        reported = version_payload.get("api_version")
        if reported == HINDSIGHT_API_PIN:
            log.info(
                "updater.memory_engine_upgraded",
                job_id=job_id,
                installed=installed,
                pinned=HINDSIGHT_API_PIN,
            )
            return {**result, "status": "upgraded"}

    # ── Rollback: a new engine process launched, so alembic may have begun —
    # restore BOTH the venv and .pg0 (copy, so the snapshot survives a retry).
    error = (
        f"/version reported {reported!r}, expected {HINDSIGHT_API_PIN!r} — check "
        "/etc/systemd/system/hindsight-api.service.d/ for an ExecStart override"
        if healthy
        else f"engine failed /health within {_HEALTH_POLL_TOTAL_S}s after swap"
    )
    _svc(seam, "stop", check=False)
    failed_venv = hs / f".venv.failed-{HINDSIGHT_API_PIN}"
    shutil.rmtree(failed_venv, ignore_errors=True)
    os.rename(venv, failed_venv)
    os.rename(venv_old, venv)
    if pg_snap.exists():
        shutil.rmtree(pg, ignore_errors=True)
        proc = _run(runner, ["cp", "-a", str(pg_snap), str(pg)], timeout=_PIP_TIMEOUT_S)
        if proc.returncode == 0:
            _chown_tree(pg)
        else:
            log.warning(
                "updater.memory_engine_pg_restore_failed",
                job_id=job_id,
                error=proc.stderr,
                remedy=f"restore {pg_snap} to {pg} by hand, then restart {SERVICE}",
            )
    _svc(seam, "start", check=False)
    old_healthy = _poll_health(http_get, _ROLLBACK_HEALTH_POLL_TOTAL_S)
    log.warning(
        "updater.memory_engine_upgrade_failed",
        job_id=job_id,
        error=error,
        old_engine_healthy=old_healthy,
        remedy=(
            f"rolled back to {installed} (db restored from {pg_snap}); inspect "
            f"'journalctl -u {SERVICE}' and {failed_venv}, then re-run 'hal0 update'"
            if old_healthy
            else f"engine down after rollback — run 'systemctl restart {SERVICE}' and "
            f"check the journal; snapshot {pg_snap} is preserved for retry"
        ),
    )
    return {
        **result,
        "status": "rolled_back",
        "error": error,
        "old_engine_healthy": old_healthy,
    }
