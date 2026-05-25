"""Self-update endpoints (mounted under /api/updates).

The route layer owns:
  - release-manifest fetch (delegated to ``hal0.updater.fetch_release_manifest``
    which honours ``HAL0_RELEASES_URL`` for tests + file:// fallback)
  - version comparison against ``hal0.__version__``
  - channel read/write (persisted in ``hal0.toml`` via ``Hal0Config.telemetry.channel``)
  - apply-job bookkeeping (queued / running / applied / failed) — jobs
    live on ``app.state.update_jobs`` so the dashboard can poll status
    without touching the updater module directly.

The actual symlink swap / cosign verify is Team D's domain inside the
``Updater`` class; the route layer calls ``Updater.apply()`` /
``Updater.rollback()`` which currently raise ``NotImplementedError``.
A failing apply surfaces as a job in the ``failed`` state with the
NotImplementedError message attached — the route surface is real.

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
import shutil
import subprocess
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request

from hal0 import __version__
from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.schema import Hal0Config
from hal0.updater import Updater, fetch_release_manifest, releases_url

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


async def _run_apply_job(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    channel: str,
    version: str | None,
) -> None:
    """Background task that drives ``Updater.apply()`` and records progress.

    The actual update work is Team D's. Until then, ``Updater.apply()``
    raises ``NotImplementedError`` and we land the job in the ``failed``
    state with the exception message. That keeps the dashboard's polling
    flow exercising real states (queued → running → failed) instead of
    just hitting a 501 wall.
    """
    job = jobs[job_id]
    job["state"] = "running"
    job["updated_at"] = time.time()
    try:
        updater = Updater(channel=channel)
        await updater.apply(version)
    except NotImplementedError as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        job["error_code"] = "system.update_pending"
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        job["error_code"] = type(exc).__name__
    else:
        job["state"] = "applied"
    finally:
        job["updated_at"] = time.time()


# ── /state ─────────────────────────────────────────────────────────────────


_LEMONADE_BIN_CANDIDATES = ("/opt/lemonade/lemonade", "lemonade")
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


def _parse_lemonade_version(raw: str | None) -> str | None:
    """``lemonade version 10.6.0`` → ``v10.6.0``."""
    if not raw:
        return None
    # Pull the last whitespace-delimited token; prepend ``v`` if numeric.
    last = raw.split()[-1] if raw.split() else raw
    return f"v{last}" if last and last[0].isdigit() else last


def _parse_flm_version(raw: str | None) -> str | None:
    """``FLM v0.9.42`` → ``v0.9.42``."""
    if not raw:
        return None
    parts = raw.split()
    return parts[-1] if parts else raw


@router.get("/state")
async def update_state(request: Request) -> dict[str, Any]:
    """Aggregate update state for the Settings → Updates surface.

    Combines the hal0 self-update channel + local probes of bundled
    components (lemonade, flm) so the dashboard renders real versions
    instead of hardcoded literals (issue #233).

    Response shape (matches ``ui/src/api/hooks/useUpdates.ts``)::

        {
            "hal0": {
                "current": "0.3.0-alpha.1",
                "available": "0.3.0" | null,
                "channel": "stable"
            },
            "lemonade": {"current": "v10.6.0", "pinned": true, "channel": "stable"},
            "flm":      {"current": "v0.9.42", "source": "manual-deb"},
            "autoCheck": true
        }

    Failures in any single probe degrade gracefully — the corresponding
    field comes back as ``None`` rather than 5xx'ing the whole response.
    """
    channel = _current_channel(request)

    # hal0 self-update: reuse ``check_updates`` semantics but tolerate
    # a manifest fetch failure (dashboard shouldn't go blank just
    # because GitHub is rate-limiting).
    hal0_available: str | None = None
    try:
        manifest = await fetch_release_manifest(channel)
        if isinstance(manifest, dict):
            latest_raw = manifest.get("version") or manifest.get("latest_version") or ""
            latest = str(latest_raw)
            if latest and _version_tuple(latest) > _version_tuple(__version__):
                hal0_available = latest
    except (OSError, ValueError):
        pass

    lemonade_raw = await asyncio.to_thread(_probe_version, _LEMONADE_BIN_CANDIDATES)
    flm_raw = await asyncio.to_thread(_probe_version, _FLM_BIN_CANDIDATES)

    return {
        "hal0": {
            "current": __version__,
            "available": hal0_available,
            "channel": channel,
        },
        "lemonade": {
            "current": _parse_lemonade_version(lemonade_raw),
            "pinned": True,
            "channel": channel,
        },
        "flm": {
            "current": _parse_flm_version(flm_raw),
            "source": "manual-deb",
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
    if isinstance(manifest, dict):
        latest_raw = manifest.get("version") or manifest.get("latest_version") or ""
        latest = str(latest_raw)

    update_available = bool(latest) and _version_tuple(latest) > _version_tuple(__version__)
    return {
        "current": __version__,
        "latest": latest or None,
        "channel": channel,
        "update_available": update_available,
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
    try:
        body = await request.json()
    except Exception:
        body = {}
    version: str | None = None
    if isinstance(body, dict):
        v = body.get("version")
        if isinstance(v, str) and v.strip():
            version = v.strip()

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


@router.get("/status/{job_id}")
async def update_status(job_id: str, request: Request) -> dict[str, Any]:
    """Return the current snapshot of an update job by id."""
    jobs = _update_jobs(request)
    job = jobs.get(job_id)
    if job is None:
        raise UpdateJobNotFound(
            f"no update job with id {job_id!r}",
            details={"job_id": job_id},
        )
    return dict(job)


# ── /rollback ──────────────────────────────────────────────────────────────


@router.post("/rollback")
async def rollback_update(request: Request) -> dict[str, Any]:
    """Invoke ``Updater.rollback()`` to revert to the retained previous version.

    Until Team D ports the real symlink swap, this surfaces a typed
    envelope (``code: "system.update_pending"``) carrying the
    NotImplementedError message — keeps the surface real without
    pretending the rollback succeeded.
    """
    channel = _current_channel(request)
    updater = Updater(channel=channel)
    try:
        await updater.rollback()
    except NotImplementedError as exc:
        # 5xx: feature not yet implemented on the server side; not a
        # client validation failure. Leave at the default 500 envelope
        # until Team D ports the real symlink-swap path.
        raise Hal0Error(
            f"rollback not yet implemented: {exc}",
            details={"channel": channel, "owner": "team-d"},
        ) from exc
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
