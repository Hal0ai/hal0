"""Self-update endpoints (mounted under /api/updates).

The route layer owns:
  - release-manifest fetch (delegated to ``hal0.updater.fetch_release_manifest``
    which honours ``HAL0_RELEASES_URL`` for tests + file:// fallback)
  - version comparison against ``installed_version()``
  - channel read/write (persisted in ``hal0.toml`` via ``Hal0Config.telemetry.channel``)
  - apply-job bookkeeping (queued / running / applied / failed) - jobs
    live on ``app.state.update_jobs`` AND are mirrored to disk under
    ``/var/lib/hal0/update-jobs/<id>.json`` so a daemon restart mid-apply
    doesn't 404 the CLI's status poll.

The actual symlink swap / cosign verify lives in the ``Updater`` class;
the route layer calls ``Updater.apply()`` / ``Updater.rollback()``. After
a successful apply the route try-restarts ``hal0-api.service`` fail-soft
(a restart failure is recorded on the job, never tears down the new tree).

Endpoints:
    GET  /api/updates/check              — release-manifest fetch + diff
    POST /api/updates/apply              — kick off background update job
    GET  /api/updates/status/{job_id}    — job state lookup
    POST /api/updates/rollback           — invoke Updater.rollback()
    GET  /api/updates/channel            — current channel (stable | nightly)
    PUT  /api/updates/channel            — set channel
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

import structlog
from fastapi import APIRouter, Request

from hal0 import __version__
from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.config import paths
from hal0.config.loader import hal0_config_txn, load_hal0_config
from hal0.config.schema import Hal0Config
from hal0.install.perms import ensure_shared_dir
from hal0.release.policy import ReleaseKind
from hal0.updater import (
    Updater,
    fetch_release_manifest,
    releases_url,
    releases_url_override,
    validate_release_version,
)
from hal0.updater.updater import _is_newer, installed_version

log = structlog.get_logger(__name__)

# See slots.py for the writer-gate rationale.

router = APIRouter()


_VALID_CHANNELS = frozenset(get_args(ReleaseKind))


class UpdateError(Hal0Error):
    """Generic updater envelope error."""

    code = "system.update_error"
    status = 500


class UpdateJobNotFound(Hal0Error):
    """No such update job id."""

    code = "system.update_job_not_found"
    status = 404


class UpdateEditableRefused(Hal0Error):
    """Refuses an update on an editable/dev install (409, not a 500)."""

    code = "system.update_editable_install"
    status = 409


class ComponentNotFound(Hal0Error):
    code = "system.component_not_found"
    status = 404


class ComponentConvergeBusy(Hal0Error):
    code = "system.component_converge_busy"
    status = 409


def _refuse_if_editable() -> None:
    """Preflight: hard-refuse an update on an editable/dev install (audit 4.1).

    The ``Updater`` re-checks this inside apply/prepare/commit, but surfacing
    it at the route boundary turns a background-job failure into an immediate,
    actionable 409 instead of a queued job that silently fails later. Detection
    is metadata-driven (:func:`~hal0.updater.updater._editable_install_path`)
    so an editable checkout cloned from git is caught too.
    """
    from hal0.updater.updater import _editable_install_path, _is_editable_install

    if not _is_editable_install():
        return
    path = _editable_install_path() or "the current source tree"
    raise UpdateEditableRefused(
        f"hal0 is installed in editable mode from {path}. "
        "Install from release wheel with `pip install hal0`.",
        details={"editable_path": path},
    )


def _current_channel(request: Request) -> str:
    """Read the current channel from the cached Hal0Config (falls back to load)."""
    cfg = getattr(request.app.state, "hal0_config", None)
    if cfg is None:
        cfg = load_hal0_config()
        request.app.state.hal0_config = cfg
    return cfg.telemetry.channel


def _update_jobs(request: Request) -> dict[str, dict[str, Any]]:
    """Per-process in-memory job registry; created lazily on first use."""
    jobs = getattr(request.app.state, "update_jobs", None)
    if jobs is None:
        jobs = {}
        request.app.state.update_jobs = jobs
    return jobs


# ── durable job store (#509) ───────────────────────────────────────────────────
#
# The in-memory ``update_jobs`` dict is process-local, so an ``hal0-api``
# restart mid-apply would 404 the CLI's status poll into a 600s timeout.
# We mirror each job snapshot to ``/var/lib/hal0/update-jobs/<id>.json``
# (atomic write) and fall back to disk on status lookup.


def _jobs_dir() -> Path:
    """Return ``/var/lib/hal0/update-jobs`` (HAL0_HOME-aware via paths)."""
    return paths.var_lib() / "update-jobs"


def _job_file(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.json"


def _persist_job(job: dict[str, Any]) -> None:
    """Atomically write a job snapshot to disk (best-effort, fail-soft).

    A failure to persist must never break the apply flow - the in-memory
    registry remains authoritative for the running process.
    """
    job_id = job.get("id")
    if not job_id:
        return
    path = _job_file(str(job_id))
    try:
        # update-jobs/ is declared 2775; a bare mkdir under the daemon's
        # UMask=0022 births 2755 and re-drifts `doctor perms` after every
        # update job (#1896 class, precedent: registry/pull.py persist_pull_job).
        ensure_shared_dir(path.parent)
        fd, tmp_str = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(job, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            tmp_path = None  # type: ignore[assignment]
        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    tmp_path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("updater.job_persist_failed", job_id=job_id, error=str(exc))


def _load_persisted_job(job_id: str) -> dict[str, Any] | None:
    """Read a persisted job snapshot from disk, or None if absent/unreadable."""
    path = _job_file(job_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        loaded = json.loads(raw)
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _try_restart_hal0_api() -> tuple[bool | None, str | None]:
    """``systemctl try-restart hal0-api.service`` - fail-soft.

    Returns ``(restarted, error)``. No-ops cleanly when ``systemctl`` is
    absent (tests / dev hosts) and never raises: a restart failure must not
    tear down the just-installed tree.

    P3-perms: hal0-api runs ``User=hal0`` now, which can't restart its own
    systemd unit directly — when this process is running as the hal0 service
    user, route through ``sudo -n hal0-systemctl restart-self``
    (installer/wrappers/hal0-systemctl) instead of a direct
    ``systemctl try-restart`` (dev/tests/a pre-flip install keep the direct
    call, unchanged). See :mod:`hal0.system.seam`.

    #1691, same incident class as #1548: the restart-self wrapper already
    passes ``--no-block`` so ``systemctl`` itself returns as soon as the
    restart is QUEUED, without waiting for the old unit to stop. But that
    ``sudo``/``systemctl`` call is still a CHILD of this process, hence
    still inside hal0-api.service's own cgroup — systemd can start tearing
    the old unit down (SIGTERMing the whole cgroup, this child included)
    before the queued call finishes exiting cleanly. A signal-killed
    ``returncode`` (negative) here is therefore ambiguous, not a definite
    failure: it can BE the restart succeeding, reaping its own caller mid
    flight. Report ``restarted=None`` for it — the same "terminal, bounce
    still in flight" state #1548 introduced for the job level — rather than
    ``False``, which the CLI treats as a real failure and warns on.
    """
    systemctl = shutil.which("systemctl")
    if not systemctl:
        log.info("updater.restart_skipped", reason="systemctl not found")
        return (False, "systemctl not found")
    from hal0.system.seam import SEAM_BIN, is_hal0_service_user

    argv = (
        ["sudo", "-n", SEAM_BIN, "restart-self"]
        if is_hal0_service_user()
        else [systemctl, "try-restart", "hal0-api.service"]
    )
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("updater.restart_errored", error=str(exc))
        return (False, str(exc))
    if proc.returncode < 0:
        err = (proc.stderr or "").strip()[:300] or f"systemctl exited {proc.returncode}"
        log.info("updater.restart_signalled", returncode=proc.returncode, stderr=err)
        return (None, err)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()[:300] or f"systemctl exited {proc.returncode}"
        log.warning("updater.restart_nonzero", returncode=proc.returncode, stderr=err)
        return (False, err)
    log.info("updater.restart_ok")
    return (True, None)


async def _run_apply_job(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    channel: str,
    version: str | None,
) -> None:
    """Background task that drives ``Updater.apply()`` and records progress.

    Persists each state transition to disk so a daemon restart mid-apply
    doesn't strand the CLI's status poll. On a successful apply it
    try-restarts ``hal0-api.service`` fail-soft - a restart failure is
    recorded on the job (``restarted`` / ``restart_error``) but never
    re-fails the apply or tears down the new tree.
    """
    job = jobs[job_id]
    job["state"] = "running"
    job["updated_at"] = time.time()
    _persist_job(job)
    try:
        updater = Updater(channel=channel, job_id=job_id)
        await updater.apply(version)
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        job["error_code"] = type(exc).__name__
    else:
        # Bounce hal0-api so the new tree is actually serving. Fail-soft:
        # the swap already succeeded - a restart hiccup is a breadcrumb,
        # not a rollback trigger.
        #
        # The terminal state is persisted BEFORE the restart is attempted,
        # because the restart can kill this very process: when hal0-api runs
        # as the service user we shell `systemctl restart hal0-api.service`
        # from inside the unit's own cgroup, and systemd SIGTERMs us while
        # that call is still in flight. Writing "applied" afterwards meant a
        # successful update frequently never recorded its own success — the
        # CLI then re-attached after the restart, read a stale "running"
        # snapshot and eventually failed a successful update (#1540).
        #
        # `restarted=None` is the third state this introduces: "terminal, but
        # the restart outcome is not known yet". A poll that observes it
        # knows the update itself succeeded and that the bounce is still in
        # flight - distinct from False, which is a restart that definitively
        # failed. The CLI only warns on False, so None stays quiet.
        job["restarted"] = None
        job["restart_error"] = None
        job["state"] = "applied"
        job["updated_at"] = time.time()
        _persist_job(job)

        restarted, restart_error = await asyncio.to_thread(_try_restart_hal0_api)
        job["restarted"] = restarted
        job["restart_error"] = restart_error
    finally:
        job["updated_at"] = time.time()
        _persist_job(job)


async def _run_prepare_job(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    channel: str,
    version: str | None,
) -> None:
    """Background task that STAGES an update via ``Updater.prepare()``.

    Downloads + cosign-verifies + extracts the release without touching the
    running system, then attaches the (verified) release notes + resolved
    version to the job and transitions it to the terminal ``prepared`` state.
    Does NOT restart hal0-api — nothing is active yet. The caller reviews the
    notes and then POSTs ``/commit`` to activate.
    """
    job = jobs[job_id]
    job["state"] = "running"
    job["updated_at"] = time.time()
    _persist_job(job)
    try:
        updater = Updater(channel=channel, job_id=job_id)
        result = await updater.prepare(version)
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        job["error_code"] = type(exc).__name__
    else:
        job["resolved_version"] = result.get("version")
        job["notes"] = result.get("notes")
        job["state"] = "prepared"
    finally:
        job["updated_at"] = time.time()
        _persist_job(job)


async def _run_commit_job(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    channel: str,
    version: str,
) -> None:
    """Background task that ACTIVATES a prepared version via ``Updater.commit()``.

    Runs the migrations + symlink swap + venv re-pip, then try-restarts
    ``hal0-api.service`` fail-soft (same policy as ``_run_apply_job``). Requires
    that ``/prepare`` staged ``version`` first; ``commit()`` raises otherwise and
    the job goes to ``failed``.

    The commit result's ``convergence`` block is attached to the job so the CLI
    can refuse to report a clean success on a box that is still on the pre-v1.0
    slot/model shape.
    """
    job = jobs[job_id]
    job["state"] = "running"
    job["updated_at"] = time.time()
    _persist_job(job)
    try:
        updater = Updater(channel=channel, job_id=job_id)
        result = await updater.commit(version)
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        job["error_code"] = type(exc).__name__
    else:
        # Convergence must land on the job BEFORE the terminal-state persist
        # below, not after the restart the way it read before (#1960 B3):
        # the restart a few lines down is the exact #1540 mechanism — it
        # shells out from inside hal0-api's own cgroup and can SIGTERM this
        # very process while it's in flight — so anything set only
        # afterwards may never reach disk. A red (unconverged) post-swap
        # migration failure has to survive that race, or a CLI that
        # re-attaches after the restart reads `convergence: None` and
        # _print_convergence reports a clean success for a run that wasn't.
        job["convergence"] = (result or {}).get("convergence")

        # Terminal state first, then the restart - see the long note in
        # _run_apply_job. commit() is the path #1540 was reported against:
        # the restart kills this process, so "applied" has to already be on
        # disk when it happens or a successful update reports failure.
        job["restarted"] = None
        job["restart_error"] = None
        job["state"] = "applied"
        job["updated_at"] = time.time()
        _persist_job(job)

        restarted, restart_error = await asyncio.to_thread(_try_restart_hal0_api)
        job["restarted"] = restarted
        job["restart_error"] = restart_error
    finally:
        job["updated_at"] = time.time()
        _persist_job(job)


# ── /state ─────────────────────────────────────────────────────────────────


_FLM_BIN_CANDIDATES = ("flm",)


def _probe_version(candidates: tuple[str, ...]) -> str | None:
    """Run ``<bin> --version`` against the first resolvable candidate.

    Returns the trimmed first line of stdout, or ``None`` if no candidate
    resolves or the call fails. The 1s timeout is conservative — these
    are local-process probes that should answer in well under that.
    """
    for cand in candidates:
        binpath = cand if "/" in cand else shutil.which(cand)
        if not binpath:
            continue
        try:
            out = subprocess.run(
                [binpath, "--version"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        text = (out.stdout or out.stderr or "").strip()
        if not text:
            return None
        return text.splitlines()[0].strip()
    return None


def _flm_toolbox_image() -> str:
    """Return the FLM runner's image reference (``manifest.json`` ``runners.flm``).

    Returns an empty string when the pin cannot be read so callers can
    degrade gracefully.
    """
    try:
        from hal0.runners import RUNNER_IMAGES, resolve_runner_image

        return resolve_runner_image(RUNNER_IMAGES["flm"])
    except Exception:  # pragma: no cover
        return ""


#: The runner-images build suffix on a tag: ``-r`` plus the short revision of
#: the recipe (``0.9.44-re14329d`` is FLM 0.9.44 built from recipe e14329d).
_RUNNER_BUILD_SUFFIX_RE = re.compile(r"-r[0-9a-f]{7,40}$")


def _parse_flm_version_from_image(image_ref: str | None) -> str | None:
    """``ghcr.io/hal0ai/hal0-runner-flm:0.9.44-r1a2b3c4@sha256:…`` → ``0.9.44``.

    Drops any ``@digest``, takes the tag (the part after the last ``:`` of the
    final path component, so a registry port is never mistaken for one), and
    strips the runner-images build suffix (``-r<revision>``). Returns ``None``
    when the ref is empty or has no tag.
    """
    if not image_ref:
        return None
    body = image_ref.strip().split("@", 1)[0]
    last = body.rsplit("/", 1)[-1]
    if ":" not in last:
        return None
    tag = last.rsplit(":", 1)[-1].strip()
    tag = _RUNNER_BUILD_SUFFIX_RE.sub("", tag)
    return tag or None


@router.get("/state")
async def update_state(request: Request) -> dict[str, Any]:
    """Aggregate update state for the Settings → Updates surface.

    Combines the hal0 self-update channel + the bundled FLM toolbox image
    version so the dashboard renders real versions instead of hardcoded
    literals (issue #233).

    Response shape (matches ``ui/src/api/hooks/useUpdates.ts``)::

        {
            "hal0": {
                "current": "0.3.0-alpha.1",
                "available": "0.3.0" | null,
                "channel": "stable"
            },
            "flm":      {"current": "0.9.43", "source": "toolbox-image"},
            "autoCheck": true
        }

    The ``flm.current`` version is derived from the bundled toolbox image
    tag (``manifest.json`` ``runners.flm``). Failures in any single probe degrade gracefully — the
    corresponding field comes back as ``None`` rather than 5xx'ing the whole
    response.
    """
    channel = _current_channel(request)
    # #2629: current/VERSION, not the wheel base version (no nightly stamp).
    installed = installed_version()

    # hal0 self-update: reuse ``check_updates`` semantics but tolerate
    # a manifest fetch failure (dashboard shouldn't go blank just
    # because GitHub is rate-limiting).
    hal0_available: str | None = None
    hal0_revoked = False
    hal0_revoked_reason = ""
    hal0_revoked_version: str | None = None
    try:
        manifest = await fetch_release_manifest(channel)
        if isinstance(manifest, dict):
            latest_raw = manifest.get("version") or manifest.get("latest_version") or ""
            latest = str(latest_raw)
            hal0_revoked = bool(manifest.get("revoked", False))
            hal0_revoked_reason = str(manifest.get("revoked_reason") or "")
            if hal0_revoked and latest:
                hal0_revoked_version = latest
            # A revoked latest is not offered (but its reason is surfaced).
            if latest and not hal0_revoked and _is_newer(latest, installed):
                hal0_available = latest
    except (OSError, ValueError):
        pass

    # Derive FLM version from the bundled toolbox image tag — the source of
    # truth post PR #822. Gracefully degrades to None if the import fails
    # (e.g. FLM provider not installed on this host).
    flm_version = _parse_flm_version_from_image(_flm_toolbox_image())

    return {
        "hal0": {
            "current": installed,
            "available": hal0_available,
            "channel": channel,
            # #2128: non-None when HAL0_RELEASES_URL bypasses channel
            # resolution — the dashboard discloses it next to the channel.
            "releases_url_override": releases_url_override(),
            "revoked": hal0_revoked,
            "revoked_reason": hal0_revoked_reason,
            "revoked_version": hal0_revoked_version,
        },
        "flm": {
            "current": flm_version,
            "source": "toolbox-image",
        },
        "autoCheck": True,
    }


# ── /check ─────────────────────────────────────────────────────────────────


@router.get("/check")
async def check_updates(request: Request) -> dict[str, Any]:
    """Fetch the release manifest and compare against the running version.

    Response shape::

        {
            "current": "0.0.0",
            "latest": "0.1.0",
            "channel": "stable",
            "update_available": true,
            "manifest_url": "https://releases.hal0.dev/latest.json",
            "releases_url_override": null,
            "manifest": { ... raw JSON from the release service ... }
        }

    Honours ``HAL0_RELEASES_URL`` (env var) so tests + dev installs can
    point at a local file. When that override is active, ``channel`` is
    reported but was NOT consulted — ``releases_url_override`` carries the
    override URL so every consumer can disclose the bypass (#2128).
    Transport failures and bad JSON surface as typed envelopes
    (system.update_error) — the dashboard renders these as "couldn't
    check for updates" without crashing.
    """
    channel = _current_channel(request)
    # #2629: current/VERSION, not the wheel base version (no nightly stamp).
    installed = installed_version()
    url = releases_url(channel)
    url_override = releases_url_override()
    try:
        manifest = await fetch_release_manifest(channel)
    except OSError as exc:
        raise UpdateError(
            f"could not fetch release manifest: {exc}",
            details={"channel": channel, "url": url, "error": str(exc)},
        ) from exc
    except ValueError as exc:
        raise UpdateError(
            f"release manifest is not valid JSON: {exc}",
            details={"channel": channel, "url": url, "error": str(exc)},
        ) from exc

    latest = ""
    revoked = False
    revoked_reason = ""
    if isinstance(manifest, dict):
        latest_raw = manifest.get("version") or manifest.get("latest_version") or ""
        latest = str(latest_raw)
        revoked = bool(manifest.get("revoked", False))
        revoked_reason = str(manifest.get("revoked_reason") or "")

    # A revoked (yanked) latest is never offered as an available update.
    update_available = bool(latest) and not revoked and _is_newer(latest, installed)

    def _components_pending() -> int:
        try:
            from hal0.components.status import component_status_snapshot

            return int(component_status_snapshot()["pending"])
        except Exception:
            return 0  # courtesy field — never fail /check over it

    return {
        "current": installed,
        "latest": latest or None,
        "channel": channel,
        "update_available": update_available,
        "revoked": revoked,
        "revoked_reason": revoked_reason,
        "manifest_url": url,
        # #2128: when HAL0_RELEASES_URL is set, `channel` above was NOT
        # consulted — this field lets every surface disclose the bypass.
        "releases_url_override": url_override,
        "manifest": manifest if isinstance(manifest, dict) else {},
        "components_pending": await asyncio.to_thread(_components_pending),
    }


# ── /apply ─────────────────────────────────────────────────────────────────


@router.post("/apply", status_code=202)
async def apply_update(request: Request) -> dict[str, Any]:
    """Kick off an update job in the background; return a job id.

    Body (optional)::

        {"version": "0.1.0"}   # pin a specific version; omit for latest

    The actual update work happens in ``_run_apply_job`` which calls
    ``Updater.apply()``. The route returns immediately with the queued-job
    snapshot; poll ``/api/updates/status/{job_id}`` for state transitions.

    Returns **202 Accepted** because the work is queued, not completed —
    matches ``/api/models/{id}/pull`` and the rest of hal0's async-job
    endpoints (issue #37). Failure paths still raise typed 4xx envelopes
    via the middleware.
    """
    _refuse_if_editable()
    try:
        body = await request.json()
    except Exception:
        body = {}
    version = _body_version(body)

    channel = _current_channel(request)
    jobs = _update_jobs(request)
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "id": job_id,
        "state": "queued",
        "channel": channel,
        "version": version,
        "created_at": time.time(),
        "updated_at": time.time(),
        "error": None,
    }
    # Persist the queued snapshot before returning so a status poll always
    # resolves, even if the daemon restarts before the background task runs.
    _persist_job(jobs[job_id])
    # Fire-and-forget; the route returns the queued snapshot. The
    # background task transitions the entry to running → applied | failed.
    # We retain a reference on app.state so the task isn't GC'd while
    # running (per RUF006 / asyncio docs).
    bg_tasks = getattr(request.app.state, "_update_bg_tasks", None)
    if bg_tasks is None:
        bg_tasks = set()
        request.app.state._update_bg_tasks = bg_tasks
    task = asyncio.create_task(_run_apply_job(jobs, job_id, channel, version))
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)
    return dict(jobs[job_id])


def _spawn_update_task(request: Request, coro: Any) -> None:
    """Track a fire-and-forget update background task on app.state (RUF006)."""
    bg_tasks = getattr(request.app.state, "_update_bg_tasks", None)
    if bg_tasks is None:
        bg_tasks = set()
        request.app.state._update_bg_tasks = bg_tasks
    task = asyncio.create_task(coro)
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)


def _body_version(body: Any) -> str | None:
    """Extract ``version``, normalising one compatible leading ``v`` only."""
    if isinstance(body, dict):
        version = body.get("version")
        if isinstance(version, str) and version:
            return version[1:] if version.startswith("v") else version
    return None


@router.post("/prepare", status_code=202)
async def prepare_update(request: Request) -> dict[str, Any]:
    """Stage an update (download + cosign-verify + extract) WITHOUT activating it.

    Returns a queued-job snapshot (202). Poll ``/api/updates/status/{job_id}``
    until state ``prepared`` — the job then carries ``resolved_version`` and
    ``notes`` (release notes read from the *verified* tree) — then POST
    ``/api/updates/commit`` with that version to activate. Nothing about the
    running system changes until commit, so an abandoned prepare is harmless.

    Body (optional): ``{"version": "0.1.0"}`` — pin a version; omit for latest.
    """
    _refuse_if_editable()
    try:
        body = await request.json()
    except Exception:
        body = {}
    version = _body_version(body)
    channel = _current_channel(request)
    jobs = _update_jobs(request)
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "id": job_id,
        "state": "queued",
        "phase": "prepare",
        "channel": channel,
        "version": version,
        "created_at": time.time(),
        "updated_at": time.time(),
        "error": None,
    }
    _persist_job(jobs[job_id])
    _spawn_update_task(request, _run_prepare_job(jobs, job_id, channel, version))
    return dict(jobs[job_id])


@router.post("/commit", status_code=202)
async def commit_update(request: Request) -> dict[str, Any]:
    """Activate a previously ``/prepare``d version.

    Body (required): ``{"version": "0.1.0"}`` — the ``resolved_version`` returned
    by the prepare job. Returns a queued-job snapshot (202); poll
    ``/status/{job_id}`` until ``applied`` | ``failed``. The daemon try-restarts
    hal0-api fail-soft after a successful commit.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    version = _body_version(body)
    if not version:
        raise BadRequest(
            "commit requires a 'version' — the resolved_version from /prepare",
            details={"hint": "POST /api/updates/prepare first, then commit its resolved_version"},
        )
    try:
        version = validate_release_version(version)
    except ValueError as exc:
        raise BadRequest(
            "commit version must be an exact supported release version",
            details={"version": version, "error": str(exc)},
        ) from exc
    channel = _current_channel(request)
    jobs = _update_jobs(request)
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "id": job_id,
        "state": "queued",
        "phase": "commit",
        "channel": channel,
        "version": version,
        "created_at": time.time(),
        "updated_at": time.time(),
        "error": None,
    }
    _persist_job(jobs[job_id])
    _spawn_update_task(request, _run_commit_job(jobs, job_id, channel, version))
    return dict(jobs[job_id])


@router.get("/status/{job_id}")
async def update_status(job_id: str, request: Request) -> dict[str, Any]:
    """Return the current snapshot of an update job by id.

    Reads the in-memory registry first, then falls back to the on-disk
    store (``/var/lib/hal0/update-jobs/<id>.json``) so a status poll still
    resolves after an ``hal0-api`` restart wiped the process-local dict.
    """
    jobs = _update_jobs(request)
    job = jobs.get(job_id)
    if job is None:
        persisted = _load_persisted_job(job_id)
        if persisted is not None:
            # Re-seed the in-memory registry so subsequent polls are fast.
            jobs[job_id] = persisted
            return dict(persisted)
        raise UpdateJobNotFound(
            f"no update job with id {job_id!r}",
            details={"job_id": job_id},
        )
    return dict(job)


# ── /rollback ──────────────────────────────────────────────────────────────


@router.post("/rollback")
async def rollback_update(request: Request) -> dict[str, Any]:
    """Invoke ``Updater.rollback()`` to revert to the retained previous version.

    ``Updater.rollback()`` raises typed ``Hal0Error`` subclasses
    (e.g. ``UpdateRollbackUnavailable`` when there's no previous record),
    which the middleware renders as structured envelopes. Anything else is
    wrapped as a generic ``system.update_error``.

    Deliberately does NOT restart ``hal0-api`` (#1549, adjudicated by-design
    as ``update-rollback-defers-hal0-api-restart``): rollback is the only
    mutating updater operation that is not a background job, and an in-route
    ``try-restart`` would kill this very process mid-response — the exact
    stale-terminal-state race #1540 fixed for apply/commit by persisting the
    job BEFORE the restart. Until rollback grows that job machinery, the
    contract is: the payload says loudly that a restart is still required
    (``restart_required`` + ``running_version``), and every caller — CLI,
    dashboard — surfaces it (#2027/#2145).
    """
    channel = _current_channel(request)
    updater = Updater(channel=channel)
    try:
        result = await updater.rollback()
    except Hal0Error:
        # Already-typed updater errors surface as their own envelope.
        raise
    except Exception as exc:
        raise UpdateError(
            f"rollback failed: {exc}",
            details={"channel": channel, "error": str(exc)},
        ) from exc
    # Pass through what rollback() actually computed instead of discarding it
    # (#1541). Three fields matter to the caller and none were reaching it:
    #   * rolled_back_to  — the install tree now active, so the CLI can name
    #     the version it reverted to rather than just "rolled back".
    #   * previous_now    — what the retained-previous slot now holds.
    #   * schema_warning  — flags a forward-only migration that the reverted
    #     tree may not understand. This was the worst of the three to drop:
    #     it is a data-hazard warning on an emergency path, and it was being
    #     discarded before any operator could see it.
    # Plus the restart contract (#2027/#2145): this process IS the not-yet-
    # restarted daemon, so its own ``__version__`` is authoritative for what
    # is still serving. ``restart_required`` is False only when the tree we
    # just swapped to is the very version already running (a double-rollback
    # bounced back, or a dev tree without versioned dirs is indeterminate →
    # True, the safe answer).
    rolled_back_to = str(result.get("rolled_back_to") or "")
    reverted_version = Path(rolled_back_to).name.removeprefix("hal0-") if rolled_back_to else ""
    return {
        "rolled_back": True,
        "channel": channel,
        "running_version": __version__,
        "restart_required": reverted_version != __version__,
        **result,
    }


# ── /channel ───────────────────────────────────────────────────────────────


@router.get("/channel")
async def get_channel(request: Request) -> dict[str, str]:
    """Return the configured update channel (stable | preview | nightly)."""
    return {"channel": _current_channel(request)}


@router.put("/channel")
async def set_channel(request: Request) -> dict[str, str]:
    """Set the update channel.

    Body::

        {"channel": "stable"}   # or "preview" / "nightly"

    Before switching, validates the target channel's manifest through
    :meth:`Updater.check`. Only a reachable, schema-valid, channel-coherent
    manifest is persisted to ``/etc/hal0/hal0.toml`` (telemetry.channel), via
    the same atomic write path as ``/api/settings``.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            details={"error": str(exc)},
            code="request.invalid_json",
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.not_an_object")
    channel = body.get("channel")
    if not isinstance(channel, str) or channel not in _VALID_CHANNELS:
        raise BadRequest(
            f"channel must be one of {sorted(_VALID_CHANNELS)}",
            details={"got": channel, "allowed": sorted(_VALID_CHANNELS)},
            code="channel.unknown",
        )

    # Validate BEFORE taking the config lock and before touching either the
    # file or the app-state cache. This is required even for an idempotent
    # PUT: success authenticates the channel's current manifest, not merely
    # the requested config value. It is also a network round-trip, so it stays
    # outside the critical section — no other config writer should queue
    # behind a manifest fetch (#1721).
    await Updater(channel=channel).check()

    # Serialized read-modify-write (#1721). Unlocked, and starting from the
    # possibly-stale ``app.state.hal0_config`` startup snapshot, this route
    # could persist a whole pre-read config over a concurrent settings /
    # memory-graph / models / auth write and erase that section.
    async with hal0_config_txn(request) as txn:
        merged_raw = txn.config.model_dump(mode="python")
        merged_raw.setdefault("telemetry", {})["channel"] = channel
        try:
            merged = Hal0Config.model_validate(merged_raw)
        except Exception as exc:
            raise BadRequest(
                f"could not validate channel update: {exc}",
                details={"error": str(exc)},
                code="channel.invalid",
            ) from exc

        try:
            txn.save(merged)
        except OSError as exc:
            raise UpdateError(
                f"could not persist channel to hal0.toml: {exc}",
                details={"error": str(exc)},
            ) from exc
    return {"channel": channel}


# ── /components (spec 2026-08-30 §3) ───────────────────────────────────────


@router.get("/components")
async def list_components(request: Request) -> dict[str, Any]:
    """Per-component version/converge status (catalog x state x live probes)."""
    from hal0.components.status import component_status_snapshot

    return await asyncio.to_thread(component_status_snapshot)


async def _run_component_job(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    component_id: str,
    arm_kwargs: dict[str, Any],
    upstreams: Any = None,
) -> None:
    from hal0.components.registry import component_by_id
    from hal0.components.runner import converge_component

    job = jobs[job_id]
    job["state"] = "running"
    job["updated_at"] = time.time()
    _persist_job(job)
    comp = component_by_id(component_id)
    try:
        result = await asyncio.to_thread(converge_component, comp, job_id=job_id, **arm_kwargs)
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
    else:
        if component_id == "hermes" and result.get("status") in {"converged", "stale", "upgraded"}:
            try:
                from hal0.agents.hermes_upstreams import sync_hermes_upstreams

                result["hermes_upstreams"] = await sync_hermes_upstreams(upstreams)
            except Exception as exc:
                log.warning("updater.hermes_upstreams_failed", error=str(exc))
        job["component_result"] = result
        failed = result.get("status") in (
            "build_failed",
            "snapshot_failed",
            "rolled_back",
            "activation_failed",
        )
        job["state"] = "failed" if failed else "applied"
        if failed:
            job["error"] = result.get("error")
    finally:
        job["updated_at"] = time.time()
        _persist_job(job)


@router.post("/components/{component_id}/converge", status_code=202)
async def converge_component_route(component_id: str, request: Request) -> dict[str, Any]:
    """Re-run one component's converge arm (spec §3 — the retry surface)."""
    from hal0.components.registry import component_by_id
    from hal0.components.runner import _arm_kwargs

    comp = component_by_id(component_id)
    if comp is None:
        raise ComponentNotFound(
            f"no component {component_id!r}", details={"component": component_id}
        )
    # Parse the body BEFORE the busy check, deliberately: `await
    # request.json()` yields control back to the event loop, so if the busy
    # check ran first, two concurrent converge POSTs could both observe an
    # empty/idle `jobs` dict, both pass the check, and both register + spawn
    # a job — running two converge arms for the same component at once.
    # With no `await` between the check and the job-dict write below, the
    # check-then-register span is atomic from the event loop's perspective.
    try:
        body = await request.json()
    except Exception:
        body = {}
    jobs = _update_jobs(request)
    if any(j.get("state") in ("queued", "running") for j in jobs.values()):
        raise ComponentConvergeBusy(
            "an update or converge job is already in flight",
            details={"component": component_id},
        )
    # Reuse runner._arm_kwargs so the per-component flag mapping (e.g. the
    # hindsight arm's ``upgrade`` kwarg spelling) isn't duplicated here.
    # job_id is dropped from the base mapping and passed explicitly by
    # ``_run_component_job`` once the job id is known.
    arm_kwargs: dict[str, Any] = _arm_kwargs(
        comp, job_id=None, apply=True, catalogue_sync=True, engine=True, hermes_install=True
    )
    arm_kwargs.pop("job_id", None)
    if component_id == "openwebui" and isinstance(body, dict):
        target = body.get("target")
        if isinstance(target, str):
            from hal0.openwebui.image_pin import normalize_digest

            normalized = normalize_digest(target)
            if normalized is None:
                raise BadRequest("target must be a sha256 digest", details={"target": target})
            arm_kwargs["target_digest"] = normalized
        if body.get("clear_override") is True:
            arm_kwargs["clear_override"] = True

    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "id": job_id,
        "state": "queued",
        "phase": f"converge:{component_id}",
        "created_at": time.time(),
        "updated_at": time.time(),
        "error": None,
    }
    _persist_job(jobs[job_id])
    _spawn_update_task(
        request,
        _run_component_job(
            jobs, job_id, component_id, arm_kwargs, upstreams=request.app.state.upstreams
        ),
    )
    return dict(jobs[job_id])


# ── /slot-drift + /restart-slots (installer-setup WS-J, #1111) ───────────────
#
# ``rerender_slot_units`` (updater) rewrites each on-disk slot unit through
# current code after an update but deliberately never restarts a running
# process — a bounce could kill a mid-inference request. The freshly-rendered
# unit therefore describes the argv a restart WOULD use while the live
# container keeps serving the pre-update argv: "post-update drift". These two
# endpoints surface that drift (banner) and let an operator clear it on demand.


def _slot_manager(request: Request) -> Any:
    """Pull the SlotManager off app.state (wired in the lifespan).

    Mirrors ``routes/slots._get_slot_manager`` — a missing manager is an
    internal invariant (lifespan should always wire it), surfaced as a typed
    envelope rather than an AttributeError.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        raise UpdateError(
            "slot_manager not initialised on app.state",
            details={"hint": "lifespan did not run"},
        )
    return sm


async def _collect_slot_drift(sm: Any) -> list[dict[str, Any]]:
    """Return one entry per slot whose RUNNING argv lags a fresh render.

    Reuses the reconcile seam (#1103): ``SlotManager.compute_config_drift``
    compares the live container's argv (``ContainerProvider.running_argv``)
    against the command a restart would render now (``expected_argv`` — the
    same ``container_spec`` plan path ``_render_unit_text`` bakes into the
    unit file). ``rerender_slot_units`` rewrites the on-disk unit after an
    update but never bounces the process, so the running argv stays stale
    until an explicit restart — precisely the drift this reports.

    Never raises: a probe failure on one slot logs and skips so a single bad
    slot can't blank the whole banner. Inactive slots (drift == None) are
    dropped — a stopped slot cannot run a stale process. A running slot whose
    argv cannot be rendered carries ``render_error`` (the reason) on its entry
    (#2660).
    """
    try:
        slots = await sm.list()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("updater.slot_drift_list_failed", error=str(exc))
        return []
    drifted: list[dict[str, Any]] = []
    for slot in slots:
        name = getattr(slot, "name", None)
        if not name:
            continue
        try:
            payload = await sm.compute_config_drift(name)
        except Exception as exc:
            log.warning("updater.slot_drift_probe_failed", slot=name, error=str(exc))
            continue
        if payload and payload.get("drifted"):
            entry: dict[str, Any] = {"slot": name, "diffs": payload.get("diffs") or []}
            if payload.get("render_error"):
                entry["render_error"] = payload["render_error"]
            drifted.append(entry)
    return drifted


@router.get("/slot-drift")
async def slot_drift(request: Request) -> dict[str, Any]:
    """Report slots whose running process lags the freshly-rendered unit.

    Response shape (consumed by ``useSlotDrift`` + the CLI post-update
    summary)::

        {
            "count": 1,
            "slots": [
                {"slot": "chat", "diffs": [
                    {"key": "--ctx-size", "running": "4096", "rendered": "131072"}
                ]}
            ]
        }

    ``count == 0`` is the clean case — nothing needs a restart. A slot whose
    launch command cannot be rendered from its current config also carries
    ``"render_error": "<reason>"`` and a ``render_error`` diff (#2660).
    """
    sm = _slot_manager(request)
    drifted = await _collect_slot_drift(sm)
    return {
        "count": len(drifted),
        "slots": drifted,
        "auto_restart": getattr(request.app.state, "post_start_image_drift_restart", None),
    }


async def _slots_in_flight(
    sm: Any, names: list[str], *, treat_unknown_as_busy: bool = False
) -> set[str]:
    """Names among ``names`` that llama-server reports as processing a request.

    Source: the live ``/metrics`` scrape (``requests_processing``,
    ``hal0.slots.metrics_collect.llama_metrics``). A slot with no port, no
    ``--metrics`` endpoint (ComfyUI, TTS/STT, FLM, ...) or a failed/timed-out
    scrape yields no reading. By default that is NOT counted busy -- there is
    no other reliable in-flight signal, so "unknown" must not block a restart
    the operator asked for (#2096). With ``treat_unknown_as_busy`` (the
    unattended boot pass, #2634) a missing reading counts as busy: only a slot
    whose reading shows ``requests_processing == 0`` is safe to bounce.
    """
    from hal0.slots import metrics_collect

    try:
        ports = {getattr(s, "name", None): getattr(s, "port", 0) or 0 for s in await sm.list()}
    except Exception:  # pragma: no cover - defensive
        return set(names) if treat_unknown_as_busy else set()
    busy: set[str] = set()
    for name in names:
        try:
            reading = await metrics_collect.llama_metrics(int(ports.get(name) or 0))
        except Exception:
            reading = {}
        if reading.get("requests_processing") is None:
            if treat_unknown_as_busy:
                busy.add(name)
        elif int(reading["requests_processing"] or 0) > 0:
            busy.add(name)
    return busy


@router.post("/restart-slots")
async def restart_drifted_slots(request: Request) -> dict[str, Any]:
    """Bounce ONLY the drifted slots — the explicit opt-in behind
    ``hal0 update --restart-slots``.

    A plain update never reaches this path, so it never auto-kills a slot
    that may be mid-inference: the fresh argv only takes effect once the
    operator asks for it here. Optionally restrict to a subset via
    ``{"slots": ["chat", ...]}``; an omitted / empty list means "all
    currently-drifted slots". ``{"skip_busy": true}`` leaves any slot whose
    llama-server reports an in-flight request alone and lists it under
    ``skipped_busy`` (used by the post-update image-drift restart, #2096).

    A slot whose argv cannot be rendered (``render_error``, #2660) is listed
    under ``skipped_render_error`` instead of restarted, unless the operator
    named it in ``slots`` without ``skip_busy``: a restart would fail to
    render and take the slot down, so only an explicit, attended request may
    bounce it. The unnamed sweep and the ``skip_busy`` automatic sweep skip it.

    Response::

        {"restarted": ["chat"], "failed": [], "skipped_busy": [],
         "skipped_render_error": [], "count": 1}

    A per-slot restart failure is recorded in ``failed`` (never re-raised) so
    one wedged slot can't abort the rest of the sweep.
    """
    sm = _slot_manager(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    only: set[str] | None = None
    if isinstance(body, dict):
        raw = body.get("slots")
        if isinstance(raw, list) and raw:
            only = {str(s) for s in raw}
    skip_busy = isinstance(body, dict) and body.get("skip_busy") is True
    return await restart_drifted(
        sm, only=only, skip_busy=skip_busy, skip_render_error=only is None or skip_busy
    )


#: One restart sweep at a time. The CLI's post-apply POST /restart-slots and
#: the post-start pass can arrive within seconds of each other for the same
#: slots; serialising them here (drift is re-read under the lock) means the
#: second sees the first's restarts as no longer drifted and does nothing,
#: instead of interleaving a second unload/load on the same slot.
_RESTART_SWEEP_LOCK = asyncio.Lock()


async def restart_drifted(
    sm: Any,
    *,
    only: set[str] | None = None,
    skip_busy: bool = False,
    treat_unknown_as_busy: bool = False,
    skip_render_error: bool = True,
) -> dict[str, Any]:
    """Bounce the drifted slots (optionally a subset); the one restart path.

    Shared by ``POST /restart-slots`` and the post-start image-drift pass
    (#2096). Per-slot failures are recorded, never re-raised. With
    ``skip_busy`` a slot llama-server reports as processing is left alone and
    listed under ``skipped_busy``; ``treat_unknown_as_busy`` (unattended
    callers, #2634) extends that to slots with no metrics reading. With
    ``skip_render_error`` (the default) a slot whose argv cannot be rendered
    is listed under ``skipped_render_error`` instead of bounced (#2660).
    Sweeps are serialised on :data:`_RESTART_SWEEP_LOCK`; drift is read
    inside the lock.
    """
    async with _RESTART_SWEEP_LOCK:
        return await _restart_drifted_locked(
            sm,
            only=only,
            skip_busy=skip_busy,
            treat_unknown_as_busy=treat_unknown_as_busy,
            skip_render_error=skip_render_error,
        )


async def _restart_drifted_locked(
    sm: Any,
    *,
    only: set[str] | None,
    skip_busy: bool,
    treat_unknown_as_busy: bool,
    skip_render_error: bool,
) -> dict[str, Any]:
    drifted = await _collect_slot_drift(sm)
    selected = [d for d in drifted if only is None or d["slot"] in only]
    skipped_render_error = (
        [d["slot"] for d in selected if d.get("render_error")] if skip_render_error else []
    )
    targets = [d["slot"] for d in selected if d["slot"] not in skipped_render_error]
    skipped_busy: list[str] = []
    if skip_busy and targets:
        busy = await _slots_in_flight(sm, targets, treat_unknown_as_busy=treat_unknown_as_busy)
        skipped_busy = [n for n in targets if n in busy]
        targets = [n for n in targets if n not in busy]
    restarted: list[str] = []
    failed: list[dict[str, str]] = []
    for name in targets:
        try:
            await sm.restart(name)
        except Exception as exc:
            log.warning("updater.slot_restart_failed", slot=name, error=str(exc))
            failed.append({"slot": name, "error": str(exc)})
            continue
        log.info("updater.slot_restarted", slot=name)
        restarted.append(name)
    return {
        "restarted": restarted,
        "failed": failed,
        "skipped_busy": skipped_busy,
        "skipped_render_error": skipped_render_error,
        "count": len(restarted),
    }


#: Seconds the post-start pass waits before probing, so the app is serving and
#: slot containers have been adopted before drift is computed.
POST_START_SETTLE_S = 15.0


async def post_start_image_drift_restart(
    state: Any, *, enabled: bool = True, settle_s: float = POST_START_SETTLE_S
) -> None:
    """One-shot, every hal0-api start: bounce slots running a replaced image (#2096).

    An image-drifted running container serves a build the installed release
    replaced, whichever path (CLI, dashboard, manual) put the new code in
    place. Busy slots are skipped, and so is any slot with no metrics reading
    (no ``--metrics``, non-llama runtime, timed-out scrape): unattended, only
    a slot proven idle is bounced (#2634). A slot that cannot re-render is
    never bounced and lands in ``skipped_render_error`` (#2660). The outcome lands on
    ``state.post_start_image_drift_restart`` for ``/slot-drift``; nothing is
    stored when no slot qualified. Never raises.
    """
    if not enabled:
        return
    try:
        if settle_s > 0:
            await asyncio.sleep(settle_s)
        sm = getattr(state, "slot_manager", None)
        if sm is None:
            return
        drifted = await _collect_slot_drift(sm)
        names = {
            d["slot"]
            for d in drifted
            if any(isinstance(x, dict) and x.get("key") == "image" for x in d["diffs"])
        }
        if not names:
            return
        out = await restart_drifted(sm, only=names, skip_busy=True, treat_unknown_as_busy=True)
        result = {
            "restarted": out["restarted"],
            "skipped_busy": out["skipped_busy"],
            "skipped_render_error": out["skipped_render_error"],
            "failed": out["failed"],
            "at": datetime.now(UTC).isoformat(),
        }
        state.post_start_image_drift_restart = result
        log.info("updater.post_start_image_drift_restart", **result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("updater.post_start_image_drift_restart_failed", error=str(exc))
