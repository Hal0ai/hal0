"""HF model update checks — is a newer build of a pulled file available?

hal0 treats a downloaded GGUF as immutable (see registry/pull.py), but
HF repos routinely re-upload the *same filename* with a newer build
(fixed chat template, re-quantised weights, …). This module answers
"did the bytes behind ``<repo>/resolve/main/<file>`` change since we
pulled?" without downloading anything: a ``HEAD`` on the resolve URL
advertises the current LFS object's sha256 in ``X-Linked-ETag`` — the
same header the pull path already verifies against (WS-12) — and the
pull recorded the local file's sha256 in ``model.metadata["sha256"]``.
Remote sha != local sha ⇒ an update is available; re-running the
existing pull machinery atomically swaps the file in place.

Rows that can't be compared stay honest:

* no ``hf_repo``/``hf_filename``  → not checkable (scanned/FLM/upstream)
* no local sha256 (hand-registered / scanned file) → ``unknown``
* non-LFS file (no ``X-Linked-ETag`` sha256)       → ``unknown``
* transport / HTTP failure                          → ``error``

Results are cached in-process: the *remote* sha + checked_at per model.
``update_available`` is always recomputed against the registry's
*current* local sha at read time, so a completed re-pull flips a row to
up-to-date immediately — no cache invalidation dance.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from hal0.registry.model import Model
from hal0.registry.pull import _expected_sha256_from_response, hf_download_url

log = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────────────

# How long a remote-sha probe result stays fresh. Model repos update on
# human timescales; half an hour keeps the Models view snappy without
# hammering HF on every 30s poll. ``refresh=true`` bypasses.
CHECK_TTL_S: float = 30 * 60

# Per-HEAD timeout + fan-out cap. A registry with dozens of HF-sourced
# rows must not open dozens of sockets at once.
_HEAD_TIMEOUT_S: float = 10.0
_MAX_CONCURRENT_CHECKS: int = 8


# ── Result shape ─────────────────────────────────────────────────────────────


@dataclass
class RemoteCheck:
    """One cached remote-sha probe for a (repo, filename) coordinate."""

    remote_sha256: str | None  # None → HF exposed no LFS sha (non-LFS file)
    checked_at: float
    error: str | None = None  # transport/HTTP failure message, sha unusable


# Module-level cache keyed by (hf_repo, hf_filename) — coordinate-level,
# not model-id-level, so two registry rows pointing at the same file
# share one probe. Process-local by design (mirrors _INSPECT_CACHE /
# _SEARCH_CACHE in the api routes).
_CHECK_CACHE: dict[tuple[str, str], RemoteCheck] = {}


def clear_check_cache() -> None:
    """Drop all cached probes (tests + explicit refresh)."""
    _CHECK_CACHE.clear()


def local_sha256(model: Model) -> str | None:
    """The sha256 recorded for the installed file, or None if unknown."""
    meta = getattr(model, "metadata", None) or {}
    sha = meta.get("sha256")
    if isinstance(sha, str) and sha.strip():
        return sha.strip().lower()
    return None


def is_checkable(model: Model) -> bool:
    """True when the row carries HF coordinates we can probe."""
    return bool((model.hf_repo or "").strip() and (model.hf_filename or "").strip())


async def _probe_remote_sha(
    client: httpx.AsyncClient,
    hf_repo: str,
    hf_filename: str,
) -> RemoteCheck:
    """HEAD the resolve URL and extract the advertised LFS sha256.

    Never raises — a failed probe is a data point (``error`` set), not an
    exception, so one flaky repo can't fail the whole registry sweep.
    """
    url = hf_download_url(hf_repo, hf_filename)
    try:
        resp = await client.head(url)
    except httpx.HTTPError as exc:
        return RemoteCheck(
            remote_sha256=None,
            checked_at=time.time(),
            error=f"{exc.__class__.__name__}: {exc}",
        )
    if resp.status_code >= 400:
        return RemoteCheck(
            remote_sha256=None,
            checked_at=time.time(),
            error=f"HTTP {resp.status_code} from huggingface.co",
        )
    # Same header dance as the pull path (WS-12): the LFS sha256 rides
    # ``X-Linked-ETag`` on the redirect hop or the final response.
    sha = _expected_sha256_from_response(resp)
    return RemoteCheck(remote_sha256=sha, checked_at=time.time())


async def check_for_updates(
    models: list[Model],
    *,
    refresh: bool = False,
    hf_token: str | None = None,
    client: httpx.AsyncClient | None = None,
    ttl_s: float = CHECK_TTL_S,
) -> list[dict[str, Any]]:
    """Probe HF for every checkable row and return per-model statuses.

    Each returned dict has the stable wire shape the dashboard reads::

        {
          "model_id": "...",
          "hf_repo": "...", "hf_filename": "...",
          "local_sha256": "..."|None, "remote_sha256": "..."|None,
          "status": "update_available"|"up_to_date"|"unknown"|"error",
          "update_available": bool,
          "checked_at": <epoch float>|None,
          "error": "..."|None,
        }

    Rows without HF coordinates are omitted entirely — they are not
    "up to date", they are simply not this feature's business.

    Cached probes within ``ttl_s`` are reused unless ``refresh`` is set.
    ``update_available`` is computed against the *current* local sha, so
    a stale cache never claims an update for a file that was just
    re-pulled.
    """
    now = time.time()
    checkable = [m for m in models if is_checkable(m)]

    to_probe: list[tuple[str, str]] = []
    seen_coords: set[tuple[str, str]] = set()
    for m in checkable:
        coord = (m.hf_repo.strip(), m.hf_filename.strip())
        if coord in seen_coords:
            continue
        seen_coords.add(coord)
        cached = _CHECK_CACHE.get(coord)
        if refresh or cached is None or (now - cached.checked_at) > ttl_s:
            to_probe.append(coord)

    if to_probe:
        headers: dict[str, str] = {"User-Agent": "hal0/update-check"}
        if hf_token:
            headers["Authorization"] = f"Bearer {hf_token}"
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(_HEAD_TIMEOUT_S),
                follow_redirects=True,
                headers=headers,
            )
        sem = asyncio.Semaphore(_MAX_CONCURRENT_CHECKS)

        async def _one(coord: tuple[str, str]) -> None:
            async with sem:
                _CHECK_CACHE[coord] = await _probe_remote_sha(client, coord[0], coord[1])

        try:
            await asyncio.gather(*(_one(c) for c in to_probe))
        finally:
            if owns_client:
                await client.aclose()

    results: list[dict[str, Any]] = []
    for m in checkable:
        coord = (m.hf_repo.strip(), m.hf_filename.strip())
        cached = _CHECK_CACHE.get(coord)
        local = local_sha256(m)
        remote = cached.remote_sha256 if cached is not None else None
        error = cached.error if cached is not None else None
        if error:
            status = "error"
        elif local and remote:
            status = "update_available" if local != remote else "up_to_date"
        else:
            # Non-LFS file (no remote sha) or a row we never hashed
            # locally (scan / add-from-path) — can't compare honestly.
            status = "unknown"
        results.append(
            {
                "model_id": m.id,
                "hf_repo": coord[0],
                "hf_filename": coord[1],
                "local_sha256": local,
                "remote_sha256": remote,
                "status": status,
                "update_available": status == "update_available",
                "checked_at": cached.checked_at if cached is not None else None,
                "error": error,
            }
        )
    return results


__all__ = [
    "CHECK_TTL_S",
    "RemoteCheck",
    "check_for_updates",
    "clear_check_cache",
    "is_checkable",
    "local_sha256",
]
