"""Runner-image download — OCI ``podman pull`` job + durable persistence.

Job-record shape, durable on-disk snapshot pattern, and sweep are a direct
port of :mod:`hal0.registry.pull` (the HuggingFace model-pull engine) — see
that module's docstring. The actual bytes-transfer mechanics differ: this
pulls a whole OCI container image via the existing
``hal0.providers.container.ContainerProvider.pull_image_stream`` layer-progress
generator (``podman pull`` subprocess, one layer-progress dict per output
line) rather than streaming a single HTTP file, so progress here is tracked
in *layers* (``layers_done``/``layers_total``) instead of bytes.
``pull_image_stream`` itself now routes through the rootful ``hal0-podman-rw``
seam when available (runner-images v3 D1a, #2119) so a dashboard-triggered
pull lands in the store slots actually launch from; the event shapes are
identical either way, so nothing here changes. The
job-state machine (queued → running → {completed, failed, cancelled}),
persisted-snapshot format, and SSE-facing ``as_dict()`` shape otherwise
mirror :class:`hal0.registry.pull.PullJob` closely so the orchestration
layer (:mod:`hal0.registry.runner_pull_jobs`) and the frontend hook can
reuse the same patterns.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hal0.config import paths
from hal0.errors import Hal0Error
from hal0.install.perms import ensure_shared_dir
from hal0.registry.runner_image_store import RunnerImageStore

log = logging.getLogger(__name__)

_SANITISE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class RunnerPullError(Hal0Error):
    """Base error for runner-image pull operations."""

    code = "runner_image.pull_failed"
    status = 500


class RunnerPullJobNotFound(RunnerPullError):
    """No pull job for this runner-image id."""

    code = "runner_image.pull_job_not_found"
    status = 404


class RunnerImageNotCatalogued(RunnerPullError):
    """The id doesn't correspond to a known runner-image catalogue row."""

    code = "runner_image.not_catalogued"
    status = 404


class RunnerImageTagInvalid(RunnerPullError):
    """The tag fails OCI tag syntax — refused before it touches any path.

    Tags feed the per-tag snapshot filename (``<id>@<tag>.json``), so they
    are allowlist-validated (:data:`_TAG_RE`, the OCI distribution-spec tag
    grammar) rather than sanitised: a lossy transform would let distinct
    tags collide on one file (``a/b`` vs ``a-b``), and traversal shapes
    (``../``) must be a typed 400, never a filesystem probe.
    """

    code = "runner_image.tag_invalid"
    status = 400


class RunnerPullPathEscape(RunnerPullError):
    """A jobs-dir path candidate resolved outside the pull-jobs directory.

    Unreachable through the public surface — ids are sanitised and tags
    allowlist-validated before a filename is composed — but the containment
    check is the canonical last-line barrier at the single point where
    request-derived data becomes a filesystem path.
    """

    code = "runner_image.path_not_contained"
    status = 400


class RunnerImageTagNotAvailable(RunnerPullError):
    """The requested tag isn't in the row's catalogued ``available_tags``.

    Per-tag pulls (catalogue-v2 follow-up to #2043) are restricted to tags
    the sync probe has actually seen — the headline ``tag`` is always
    allowed (``available_tags`` may be ``[]`` on probe failure), anything
    else must appear in the list. Free-text refs stay out on purpose: the
    catalogue is the honesty boundary.
    """

    code = "runner_image.tag_not_available"
    status = 404


class RunnerPullConflict(RunnerPullError):
    """A pull for a *different* tag of this image is already in flight.

    Job state is keyed one-per-image (jobs dict, snapshot file, marker) —
    honest answer to a second tag while one is downloading is 409, not a
    silent "resumed" handle to the wrong tag.
    """

    code = "runner_image.pull_conflict"
    status = 409


def _sanitise_id(image_id: str) -> str:
    return _SANITISE_RE.sub("-", image_id).strip("-.") or "runner-image"


#: OCI distribution-spec tag grammar. Every character it admits is filename-
#: safe (no ``/``, no ``@``, no leading ``.``), so a validated tag is used
#: verbatim in paths.
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


def validate_pull_tag(tag: str) -> str:
    """Allowlist-validate ``tag`` before it reaches any filesystem path.

    Raises :class:`RunnerImageTagInvalid` (400) on anything outside the OCI
    tag grammar. Returns the tag unchanged so call sites can validate and
    use in one expression.
    """
    if not _TAG_RE.fullmatch(tag):
        raise RunnerImageTagInvalid(
            f"invalid runner-image tag {tag!r} — not an OCI tag", details={"tag": tag}
        )
    return tag


@dataclass
class RunnerPullJob:
    """One in-flight (or terminal) runner-image pull, addressable by image id.

    Lives on ``app.state.runner_image_pull_jobs[image_id]``.
    """

    job_id: str
    image_id: str
    image_ref: str
    #: The catalogue tag this job pulls (``image_ref``'s tag half). ``None``
    #: only on in-memory records made without a tag; persisted pre-per-tag
    #: snapshots simply lack the key.
    tag: str | None = None
    state: str = "queued"  # queued → running → {completed,failed,cancelled}
    layers_done: int = 0
    layers_total: int = 0
    line: str | None = None  # last raw podman-pull output line, for UI detail
    started_at: float = 0.0
    finished_at: float | None = None
    error: str | None = None
    error_code: str | None = None
    local_path: str | None = None
    cancel_requested: bool = False
    progress_event: asyncio.Event = field(default_factory=asyncio.Event)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.job_id,
            "image_id": self.image_id,
            "image_ref": self.image_ref,
            "tag": self.tag,
            "state": self.state,
            "layers_done": self.layers_done,
            "layers_total": self.layers_total,
            "line": self.line,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "error_code": self.error_code,
            "local_path": self.local_path,
        }

    def _signal(self) -> None:
        self.progress_event.set()
        self.progress_event = asyncio.Event()


def make_job(image_id: str, image_ref: str, *, tag: str | None = None) -> RunnerPullJob:
    """Create a fresh job record for ``image_id`` (``tag`` = the ref's tag half)."""
    return RunnerPullJob(
        job_id=secrets.token_hex(8),
        image_id=image_id,
        image_ref=image_ref,
        tag=tag,
        state="queued",
        started_at=time.time(),
    )


# ── durable pull-job store (mirrors hal0.registry.pull) ────────────────────


def _pull_jobs_dir() -> Path:
    return paths.var_lib() / "runner-image-pull-jobs"


def _contained_jobs_path(filename: str) -> Path:
    """Resolve ``filename`` inside the pull-jobs dir, refusing escapees.

    The id/tag halves are sanitised/allowlist-validated before a filename
    exists, so escape is unreachable; this containment check is the
    belt-and-braces barrier where request-derived data becomes a path.
    """
    base = _pull_jobs_dir().resolve()
    candidate = (base / filename).resolve()
    if not candidate.is_relative_to(base):
        raise RunnerPullPathEscape(
            "snapshot path escapes the pull-jobs directory", details={"filename": filename}
        )
    return candidate


def pull_job_file(image_id: str, *, tag: str | None = None) -> Path:
    """Snapshot path for one image's pull job — per-tag when the job has one.

    Tagged jobs land at ``<id>@<tag>.json`` so a new tag's pull can't
    overwrite the previous tag's terminal record; untagged jobs keep the
    bare ``<id>.json`` name. The tag half is allowlist-validated and used
    verbatim — ``@`` survives neither ``_sanitise_id`` nor the tag
    grammar, so the separator is unambiguous, and validating (rather than
    sanitising) means distinct tags can never collide on one file.
    """
    stem = _sanitise_id(image_id)
    if tag:
        stem = f"{stem}@{validate_pull_tag(tag)}"
    return _contained_jobs_path(f"{stem}.json")


def persisted_job_files(image_id: str) -> list[Path]:
    """Every per-tag ``<id>@<tag>.json`` snapshot file for ``image_id``
    (name order, no ranking — callers pick by snapshot content, e.g.
    ``started_at``).

    Every writer has passed a tag since #2048, so per-tag files are the
    only ones read; the bare pre-#2048 ``<id>.json`` fallback was sunset
    in the v1.3.0 tranche (#2168).
    """
    jobs_dir = _pull_jobs_dir()
    if not jobs_dir.is_dir():
        return []
    return sorted(jobs_dir.glob(f"{_sanitise_id(image_id)}@*.json"))


def persist_pull_job(job: RunnerPullJob) -> None:
    """Atomically mirror a job snapshot to disk (best-effort, fail-soft)."""
    path = pull_job_file(job.image_id, tag=job.tag)
    try:
        ensure_shared_dir(path.parent)  # 2775, umask-proof (#1896)
        fd, tmp_str = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp_path: Path | None = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(job.as_dict(), f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)  # type: ignore[arg-type]
            tmp_path = None
        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    tmp_path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("runner_image.pull_job_persist_failed image_id=%s error=%s", job.image_id, exc)


def list_persisted_jobs() -> list[dict[str, Any]]:
    """Read all persisted job snapshots. Best-effort — bad files are skipped."""
    jobs_dir = _pull_jobs_dir()
    if not jobs_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(jobs_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                results.append(data)
        except (OSError, ValueError):
            continue
    return results


def sweep_pull_jobs(max_age_days: int = 14) -> int:
    """Garbage-collect stale terminal job snapshots. Mirrors pull.sweep_pull_jobs."""
    jobs_dir = _pull_jobs_dir()
    if not jobs_dir.is_dir():
        return 0
    terminal = {"completed", "failed", "cancelled"}
    cutoff = max_age_days * 86400
    now = time.time()
    removed = 0
    for path in jobs_dir.glob("*.json"):
        try:
            if now - path.stat().st_mtime <= cutoff:
                continue
            with path.open(encoding="utf-8") as f:
                state = json.load(f).get("state")
            if state not in terminal:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        except (OSError, ValueError):
            continue
    return removed


# ── local install path ──────────────────────────────────────────────────────


def _runner_images_root() -> Path:
    """On-disk marker root for pulled runner images.

    podman itself owns the actual image storage (container storage
    backend); this directory holds a lightweight per-image marker file
    (the image ref + pull timestamp) so ``local_path`` has something
    concrete to point at, mirroring the "installed path" concept the
    model registry uses. It's the value the sibling slot-edit-drawer
    branch's Runner Image dropdown would read as a locally-available marker.
    """
    return paths.var_lib() / "runner-images"


def local_marker_path(image_id: str) -> Path:
    return _runner_images_root() / f"{_sanitise_id(image_id)}.json"


def write_local_marker(image_id: str, image_ref: str) -> Path:
    """Record a completed pull. Returns the marker path (used as local_path)."""
    path = local_marker_path(image_id)
    ensure_shared_dir(path.parent)  # 2775, umask-proof (#1896)
    path.write_text(
        json.dumps(
            {"image_id": image_id, "image": image_ref, "pulled_at": datetime.now(UTC).isoformat()}
        ),
        encoding="utf-8",
    )
    return path


# ── retry discipline ─────────────────────────────────────────────────────────
#
# Mirrors installer/lib/pull-retry.sh's bash classifier/backoff so a manual
# `install.sh` image pull and a dashboard-triggered runner-image pull fail
# for the same reasons and back off on the same schedule. Kept in sync by
# hand (there is no shared source between a bash and a Python regex) —
# tests/registry/test_runner_pull_retry.py pins both against the same
# fixture strings.

#: Non-retryable failure signatures: auth/permission, 404/manifest-unknown,
#: and out-of-space/daemon-unreachable are never fixed by trying again.
_NONRETRYABLE_PULL_RE = re.compile(
    r"unauthorized|authentication required|denied|forbidden|not[\s-]?found"
    r"|manifest unknown|\b404\b|no space left on device|cannot connect to the .*daemon",
    re.IGNORECASE,
)

#: Seconds to wait before retry N (1-indexed); past the table's end the last
#: value doubles per extra step (see :func:`pull_backoff_delay`).
_DEFAULT_PULL_RETRY_DELAYS: tuple[int, ...] = (5, 15, 30, 60)


def is_retryable_pull_error(message: str | None) -> bool:
    """``True`` unless *message* matches a known non-retryable signature.

    Everything else (DNS blip, connection reset, registry 5xx, timeout) is
    treated as transient and gets the backoff table. An empty/``None``
    message (an exception with no useful text) is treated as retryable —
    the conservative default when there's nothing to classify against.
    """
    if not message:
        return True
    return not _NONRETRYABLE_PULL_RE.search(message)


def pull_backoff_delay(attempt: int, delays: tuple[int, ...] = _DEFAULT_PULL_RETRY_DELAYS) -> int:
    """Seconds to sleep before retry ``#attempt`` (1-indexed: the wait
    before the 2nd overall attempt is ``pull_backoff_delay(1)``)."""
    idx = attempt - 1
    if idx < len(delays):
        return delays[idx]
    extra = idx - (len(delays) - 1)
    return int(delays[-1] * (2**extra))


# ── job execution ────────────────────────────────────────────────────────────


async def run_runner_pull(
    job: RunnerPullJob,
    *,
    store: RunnerImageStore,
    provider: Any,
    max_attempts: int = 1,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Drive one runner-image pull to completion, updating ``job`` as it goes.

    ``provider`` is any object exposing ``pull_image_stream(image) ->
    AsyncIterator[dict]`` — the real caller passes
    ``hal0.providers.container.container_provider()``; tests pass a fake.
    Cancellation is cooperative: ``job.cancel_requested`` is checked between
    generator yields (podman itself has no cancel signal short of killing
    the subprocess, which ``pull_image_stream`` already does on generator
    close/GC).

    ``max_attempts`` (default 1 — no retry) bounds how many times a
    *retryable* failure (see :func:`is_retryable_pull_error`) is retried,
    with :func:`pull_backoff_delay` backoff between attempts. A
    non-retryable failure (auth/permission, 404/manifest-unknown, disk-full)
    ends the job on the first occurrence regardless of ``max_attempts`` —
    :mod:`hal0.registry.runner_pull_jobs` is the caller that opts real
    dashboard-triggered pulls into multi-attempt retry; direct callers (and
    every existing test) that omit the argument keep the original
    single-attempt behaviour byte-for-byte. ``sleep_fn`` is the backoff
    sleep, injectable so tests never wait on a real clock.

    On an apparent success, verifies the image actually landed in local
    storage via ``provider.image_present(image_ref)`` (tri-state: True/False
    verified, None means "couldn't check" — see
    :meth:`hal0.providers.container.ContainerProvider.image_present`) when
    the provider exposes that method; a provider without it (every test
    fake) skips verification and trusts the ``completed`` event, unchanged
    from before this was added. ``False`` is treated as a retryable failure
    — a pull that exits 0 without the image present is not success.
    """
    job.state = "running"
    job._signal()

    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        if job.cancel_requested:
            job.state = "cancelled"
            job.finished_at = time.time()
            job._signal()
            return

        job.layers_done = 0
        job.layers_total = 0
        outcome: str | None = None  # "completed" | "failed" | None (cancelled, returned already)

        try:
            agen = provider.pull_image_stream(job.image_ref)
            async for event in agen:
                if job.cancel_requested:
                    job.state = "cancelled"
                    job.finished_at = time.time()
                    with contextlib.suppress(Exception):
                        await agen.aclose()
                    job._signal()
                    return
                state = event.get("state")
                if state == "pulling":
                    job.layers_done = int(event.get("layer") or 0)
                    job.layers_total = int(event.get("total_layers") or 0)
                    job.line = event.get("line")
                    job._signal()
                elif state == "completed":
                    job.layers_done = int(event.get("layer") or job.layers_done)
                    job.layers_total = int(event.get("total_layers") or job.layers_total)
                    outcome = "completed"
                    break
                elif state == "failed":
                    last_error = str(event.get("error") or "pull failed")
                    outcome = "failed"
                    break
        except asyncio.CancelledError:
            job.state = "cancelled"
            job.finished_at = time.time()
            job._signal()
            raise
        except Exception as exc:  # defensive: never leave a job stuck "running"
            log.exception("runner_image.pull_unhandled_error image_id=%s", job.image_id)
            last_error = str(exc)
            outcome = "failed"

        if outcome == "completed":
            verify = getattr(provider, "image_present", None)
            present: bool | None = True
            if verify is not None:
                try:
                    present = await asyncio.to_thread(verify, job.image_ref)
                except Exception:
                    present = None  # inconclusive — don't punish a flaky verifier
            if present is False:
                last_error = f"pull reported success but {job.image_ref} is not in local storage"
                outcome = "failed"
            else:
                marker = write_local_marker(job.image_id, job.image_ref)
                job.local_path = str(marker)
                job.state = "completed"
                job.finished_at = time.time()
                with contextlib.suppress(Exception):
                    store.set_local_state(
                        job.image_id,
                        local_path=str(marker),
                        downloaded_at=datetime.now(UTC).isoformat(),
                    )
                job._signal()
                return

        # outcome == "failed" here (event, exception, or failed post-pull verify).
        if attempt >= max_attempts or not is_retryable_pull_error(last_error):
            job.state = "failed"
            job.error = last_error or "pull failed"
            job.error_code = "runner_image.pull_failed"
            job.finished_at = time.time()
            job._signal()
            return

        job.line = f"retry {attempt + 1}/{max_attempts} after: {last_error}"
        job._signal()
        await sleep_fn(pull_backoff_delay(attempt))


__all__ = [
    "RunnerImageNotCatalogued",
    "RunnerImageTagInvalid",
    "RunnerImageTagNotAvailable",
    "RunnerPullConflict",
    "RunnerPullError",
    "RunnerPullJob",
    "RunnerPullJobNotFound",
    "RunnerPullPathEscape",
    "is_retryable_pull_error",
    "list_persisted_jobs",
    "local_marker_path",
    "make_job",
    "persist_pull_job",
    "persisted_job_files",
    "pull_backoff_delay",
    "pull_job_file",
    "run_runner_pull",
    "sweep_pull_jobs",
    "validate_pull_tag",
    "write_local_marker",
]
