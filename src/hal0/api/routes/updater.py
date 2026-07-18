"""Self-update endpoints (mounted under /api/updates).

The route layer owns:
  - release-manifest fetch (delegated to ``hal0.updater.fetch_release_manifest``
    which honours ``HAL0_RELEASES_URL`` for tests + file:// fallback)
  - version comparison against ``hal0.__version__``
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
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request

from hal0 import __version__
from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.config import paths
from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.schema import Hal0Config
from hal0.updater import Updater, fetch_release_manifest, releases_url

log = structlog.get_logger(__name__)

# See slots.py for the writer-gate rationale.

router = APIRouter()


_VALID_CHANNELS = frozenset({"stable", "nightly"})


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


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse a dotted version string into a sortable tuple.

    Falls back to ``(0,)`` on non-numeric components so a malformed
    version doesn't crash the comparison — the route still returns a
    useful payload (``update_available`` may be wrong but the response
    shape is intact).
    """
    parts: list[int] = []
    for piece in (v or "").split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            # Strip non-numeric suffix (e.g. "0.1.0-rc1" → 0.1.0).
            num = "".join(c for c in piece if c.isdigit())
            parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def _is_newer(candidate: str, current: str) -> bool:
    """Return True if PEP 440 version ``candidate`` is strictly newer than ``current``.

    Uses ``packaging.version.Version`` so pip-normalised forms (``0.8.0b3``) order
    correctly against tag-derived manifest forms (``0.8.1-beta.1``); the naive
    ``_version_tuple`` digit-parser read ``0.8.0b3`` as ``(0, 8, 3)`` and missed the
    upgrade. Falls back to the tuple compare only when a string is not valid PEP 440.
    """
    try:
        from packaging.version import InvalidVersion, Version
    except Exception:
        return _version_tuple(candidate) > _version_tuple(current)
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return _version_tuple(candidate) > _version_tuple(current)


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
        path.parent.mkdir(parents=True, exist_ok=True)
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


def _try_restart_hal0_api() -> tuple[bool, str | None]:
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
        updater = Updater(channel=channel)
        await updater.apply(version)
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        job["error_code"] = type(exc).__name__
    else:
        # Bounce hal0-api so the new tree is actually serving. Fail-soft:
        # the swap already succeeded - a restart hiccup is a breadcrumb,
        # not a rollback trigger. Record the restart outcome BEFORE flipping
        # the job to its terminal "applied" state so a status poll never
        # observes "applied" without the restart breadcrumb attached.
        restarted, restart_error = await asyncio.to_thread(_try_restart_hal0_api)
        job["restarted"] = restarted
        job["restart_error"] = restart_error
        job["state"] = "applied"
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
        updater = Updater(channel=channel)
        result = await updater.prepare(version)
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        job["error_code"] = type(exc).__name__
    else:
        job["resolved_version"] = result.get("version")
        job["notes"] = result.get("notes")
        job["cosign_skipped"] = result.get("cosign_skipped")
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
    """
    job = jobs[job_id]
    job["state"] = "running"
    job["updated_at"] = time.time()
    _persist_job(job)
    try:
        updater = Updater(channel=channel)
        await updater.commit(version)
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        job["error_code"] = type(exc).__name__
    else:
        restarted, restart_error = await asyncio.to_thread(_try_restart_hal0_api)
        job["restarted"] = restarted
        job["restart_error"] = restart_error
        job["state"] = "applied"
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
    """Return the bundled FLM toolbox image reference (source of truth post-PR #822).

    Delegates to ``providers.flm._DEFAULT_FLM_IMAGE`` so the version is
    never duplicated — updating the image constant updates the updater
    surface automatically. Returns an empty string on import failure so
    callers can degrade gracefully.
    """
    try:
        from hal0.providers.flm import _DEFAULT_FLM_IMAGE

        return _DEFAULT_FLM_IMAGE
    except Exception:  # pragma: no cover
        return ""


def _parse_flm_version(raw: str | None) -> str | None:
    """``FLM v0.9.43`` → ``v0.9.43`` (probe output normalisation).

    Strips the leading ``FLM `` prefix emitted by ``flm --version`` and
    returns the version token. Returns ``None`` on empty / ``None`` input.
    """
    if not raw:
        return None
    parts = raw.split()
    return parts[-1] if parts else raw


def _parse_flm_version_from_image(image_ref: str | None) -> str | None:
    """``ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44`` → ``0.9.44``.

    Parses the tag off an OCI image reference (everything after the last
    ``:``) and returns it, or ``None`` if the ref is empty / has no tag.
    """
    if not image_ref:
        return None
    # image_ref may be "repo/image:tag" or "registry/repo/image:tag".
    # The tag is always the fragment after the last colon, provided it
    # exists and is non-empty.
    if ":" not in image_ref:
        return None
    tag = image_ref.rsplit(":", 1)[-1].strip()
    return tag if tag else None


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
    tag (``providers.flm._DEFAULT_FLM_IMAGE``) — the single source of truth
    post PR #822. Failures in any single probe degrade gracefully — the
    corresponding field comes back as ``None`` rather than 5xx'ing the whole
    response.
    """
    channel = _current_channel(request)

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
            if latest and not hal0_revoked and _is_newer(latest, __version__):
                hal0_available = latest
    except (OSError, ValueError):
        pass

    # Derive FLM version from the bundled toolbox image tag — the source of
    # truth post PR #822. Gracefully degrades to None if the import fails
    # (e.g. FLM provider not installed on this host).
    flm_version = _parse_flm_version_from_image(_flm_toolbox_image())

    return {
        "hal0": {
            "current": __version__,
            "available": hal0_available,
            "channel": channel,
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
            "manifest": { ... raw JSON from the release service ... }
        }

    Honours ``HAL0_RELEASES_URL`` (env var) so tests + dev installs can
    point at a local file. Transport failures and bad JSON surface as
    typed envelopes (system.update_error) — the dashboard renders these
    as "couldn't check for updates" without crashing.
    """
    channel = _current_channel(request)
    url = releases_url(channel)
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
    update_available = bool(latest) and not revoked and _is_newer(latest, __version__)
    return {
        "current": __version__,
        "latest": latest or None,
        "channel": channel,
        "update_available": update_available,
        "revoked": revoked,
        "revoked_reason": revoked_reason,
        "manifest_url": url,
        "manifest": manifest if isinstance(manifest, dict) else {},
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
    version: str | None = None
    if isinstance(body, dict):
        v = body.get("version")
        if isinstance(v, str) and v.strip():
            # Strip a leading "v" so {"version": "v0.1.1"} and "0.1.1"
            # drive the same target - matches the CLI's --target handling
            # (#510). lstrip is fine here: versions never start with "v".
            version = v.strip().lstrip("v") or None

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
    """Extract a normalised ``version`` from a request body (strip a leading v)."""
    if isinstance(body, dict):
        v = body.get("version")
        if isinstance(v, str) and v.strip():
            return v.strip().lstrip("v") or None
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
    """
    channel = _current_channel(request)
    updater = Updater(channel=channel)
    try:
        await updater.rollback()
    except Hal0Error:
        # Already-typed updater errors surface as their own envelope.
        raise
    except Exception as exc:
        raise UpdateError(
            f"rollback failed: {exc}",
            details={"channel": channel, "error": str(exc)},
        ) from exc
    return {"rolled_back": True, "channel": channel}


# ── /channel ───────────────────────────────────────────────────────────────


@router.get("/channel")
async def get_channel(request: Request) -> dict[str, str]:
    """Return the configured update channel (stable | nightly)."""
    return {"channel": _current_channel(request)}


@router.put("/channel")
async def set_channel(request: Request) -> dict[str, str]:
    """Set the update channel.

    Body::

        {"channel": "stable"}   # or "nightly"

    Persists to ``/etc/hal0/hal0.toml`` (telemetry.channel) via the same
    atomic write path as ``/api/settings``. The new channel takes effect
    immediately for subsequent ``/check`` calls.
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

    current = getattr(request.app.state, "hal0_config", None)
    if current is None:
        current = load_hal0_config()
    merged_raw = current.model_dump(mode="python")
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
        save_hal0_config(merged)
    except OSError as exc:
        raise UpdateError(
            f"could not persist channel to hal0.toml: {exc}",
            details={"error": str(exc)},
        ) from exc
    request.app.state.hal0_config = merged
    return {"channel": channel}


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
    dropped — a stopped slot cannot run a stale process.
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
            drifted.append({"slot": name, "diffs": payload.get("diffs") or []})
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

    ``count == 0`` is the clean case — nothing needs a restart.
    """
    sm = _slot_manager(request)
    drifted = await _collect_slot_drift(sm)
    return {"count": len(drifted), "slots": drifted}


@router.post("/restart-slots")
async def restart_drifted_slots(request: Request) -> dict[str, Any]:
    """Bounce ONLY the drifted slots — the explicit opt-in behind
    ``hal0 update --restart-slots``.

    A plain update never reaches this path, so it never auto-kills a slot
    that may be mid-inference: the fresh argv only takes effect once the
    operator asks for it here. Optionally restrict to a subset via
    ``{"slots": ["chat", ...]}``; an omitted / empty list means "all
    currently-drifted slots".

    Response::

        {"restarted": ["chat"], "failed": [], "count": 1}

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

    drifted = await _collect_slot_drift(sm)
    targets = [d["slot"] for d in drifted if only is None or d["slot"] in only]
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
    return {"restarted": restarted, "failed": failed, "count": len(restarted)}
