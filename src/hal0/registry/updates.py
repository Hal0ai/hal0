"""HuggingFace model update detection.

An installed HF model is "stale" when the repo's ``main`` branch now serves
a *different* file than the one on disk. We detect that by comparing the
content SHA-256 we captured at pull time (``Model.metadata["sha256"]`` — the
streamed digest, which for an LFS-backed file equals HF's advertised LFS
``oid``) against the repo's *current* ``main`` LFS ``oid`` for the same
filename.

Only LFS-backed files carry an ``oid`` we can compare, and only rows that
recorded a ``sha256`` at pull time have a local anchor. When either side is
missing we report ``update_available=False`` rather than guess — the dashboard
must never nag about a model whose freshness it can't actually verify.

The check is a read-only HF metadata fetch (``/api/models/<repo>/tree/main``),
the same endpoint :mod:`hal0.api.routes.models` already uses for inspect. We
do NOT add a ``huggingface_hub`` dependency (see routes/hf.py for the
rationale). Results are cached per repo with a short TTL so the dashboard can
poll the ``/api/models/updates`` surface without hammering HF.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────────────

# Per-repo tree cache TTL. A freshly-uploaded quant surfaces within this
# window; between polls the dashboard re-reads the cached verdict for free.
_TREE_CACHE_TTL_S: float = 600.0  # 10 minutes

_TREE_FETCH_TIMEOUT_S: float = 8.0

# Bound the concurrent HF fanout when checking a whole registry so a large
# catalog doesn't open dozens of sockets at once.
_MAX_CONCURRENCY: int = 6

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# repo → (fetched_at, {repo_relative_path: lfs_oid_hex})
_TREE_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


# ── SHA / oid helpers ────────────────────────────────────────────────────────


def _normalise_oid(raw: Any) -> str | None:
    """Return a lowercase 64-hex sha256, or None if ``raw`` isn't one.

    HF's tree API reports the LFS object hash as ``lfs.oid``; it is usually
    bare 64-hex but is occasionally prefixed (``sha256:<hex>``). Our stored
    ``metadata.sha256`` is always bare hex. Normalise both through here so the
    comparison is apples-to-apples.
    """
    if not isinstance(raw, str):
        return None
    val = raw.strip().lower()
    if val.startswith("sha256:"):
        val = val[len("sha256:") :]
    return val if _SHA256_HEX_RE.match(val) else None


def _stored_sha256(model: Any) -> str | None:
    """Extract the pull-time content sha256 from a registry model, or None."""
    meta = getattr(model, "metadata", None)
    if not isinstance(meta, dict):
        return None
    return _normalise_oid(meta.get("sha256"))


# ── HF tree fetch ────────────────────────────────────────────────────────────


async def _fetch_repo_oids(
    repo: str,
    *,
    client: httpx.AsyncClient,
    hf_token: str | None,
    force: bool = False,
) -> dict[str, str] | None:
    """Return ``{repo_relative_path: lfs_oid_hex}`` for ``repo``'s main branch.

    Returns ``None`` on any failure (unreachable, 4xx/5xx, bad JSON) so a
    single flaky repo degrades to "unknown" rather than failing the whole
    batch. Cached per repo for :data:`_TREE_CACHE_TTL_S`; ``force`` bypasses
    the cache to re-probe.
    """
    now = time.monotonic()
    if not force:
        cached = _TREE_CACHE.get(repo)
        if cached is not None and (now - cached[0]) < _TREE_CACHE_TTL_S:
            return cached[1]

    headers = {"Accept": "application/json"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
    url = f"https://huggingface.co/api/models/{repo}/tree/main"

    try:
        resp = await client.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        log.warning("model.update_tree_unreachable repo=%s error=%s", repo, exc)
        return None
    if resp.status_code >= 400:
        log.warning("model.update_tree_upstream_error repo=%s status=%s", repo, resp.status_code)
        return None
    try:
        payload = resp.json()
    except ValueError:
        log.warning("model.update_tree_bad_json repo=%s", repo)
        return None
    if not isinstance(payload, list):
        return None

    oids: dict[str, str] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path") or entry.get("rfilename")
        if not isinstance(rel, str) or not rel:
            continue
        lfs = entry.get("lfs")
        if not isinstance(lfs, dict):
            continue
        oid = _normalise_oid(lfs.get("oid"))
        if oid is not None:
            oids[rel] = oid

    _TREE_CACHE[repo] = (now, oids)
    return oids


# ── Result record ────────────────────────────────────────────────────────────


@dataclass
class ModelUpdateInfo:
    """Per-model freshness verdict for the ``/api/models/updates`` surface."""

    model_id: str
    hf_repo: str
    hf_filename: str
    update_available: bool
    current_sha: str | None = None
    remote_sha: str | None = None
    # Why an install could NOT be checked (no stored sha, non-LFS remote,
    # repo unreachable). None on a clean checked verdict. Purely diagnostic —
    # the dashboard keys its indicator off ``update_available`` alone.
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "hf_repo": self.hf_repo,
            "hf_filename": self.hf_filename,
            "update_available": self.update_available,
            "current_sha": self.current_sha,
            "remote_sha": self.remote_sha,
            "reason": self.reason,
        }


def _remote_oid_for_file(oids: dict[str, str], hf_filename: str) -> str | None:
    """Resolve the remote LFS oid for ``hf_filename`` within a repo tree.

    Prefers an exact repo-relative-path match; falls back to a basename match
    so a stored coordinate that dropped a subdir prefix still resolves.
    """
    exact = oids.get(hf_filename)
    if exact is not None:
        return exact
    base = hf_filename.rsplit("/", 1)[-1]
    for rel, oid in oids.items():
        if rel.rsplit("/", 1)[-1] == base:
            return oid
    return None


async def _check_one(
    model: Any,
    *,
    client: httpx.AsyncClient,
    hf_token: str | None,
    force: bool,
) -> ModelUpdateInfo:
    """Compute the freshness verdict for a single installed HF model."""
    model_id = getattr(model, "id", "")
    hf_repo = (getattr(model, "hf_repo", "") or "").strip()
    hf_filename = (getattr(model, "hf_filename", "") or "").strip()
    current = _stored_sha256(model)

    info = ModelUpdateInfo(
        model_id=model_id,
        hf_repo=hf_repo,
        hf_filename=hf_filename,
        update_available=False,
        current_sha=current,
    )
    if current is None:
        info.reason = "no_local_sha"
        return info

    oids = await _fetch_repo_oids(hf_repo, client=client, hf_token=hf_token, force=force)
    if oids is None:
        info.reason = "repo_unreachable"
        return info
    remote = _remote_oid_for_file(oids, hf_filename)
    if remote is None:
        # File isn't LFS-backed on main (no oid) or was renamed/removed —
        # nothing we can compare against, so don't claim staleness.
        info.reason = "no_remote_sha"
        return info

    info.remote_sha = remote
    info.update_available = remote != current
    return info


def is_checkable(model: Any) -> bool:
    """True when ``model`` is an installed HF pull we can update-check.

    Requires both HF coordinates (repo + filename) and a pull-time sha to
    anchor the comparison. FLM/NPU rows (no hf_repo) and hand-registered rows
    without a sha are skipped.
    """
    if not (getattr(model, "hf_repo", "") or "").strip():
        return False
    if not (getattr(model, "hf_filename", "") or "").strip():
        return False
    return _stored_sha256(model) is not None


async def check_updates(
    models: list[Any],
    *,
    force: bool = False,
    client: httpx.AsyncClient | None = None,
) -> list[ModelUpdateInfo]:
    """Check a list of registry models for available HF updates.

    Only rows passing :func:`is_checkable` are probed; everything else is
    omitted from the result. Fetches run concurrently (bounded by
    :data:`_MAX_CONCURRENCY`) and share the per-repo tree cache, so N models
    from the same repo cost one HF call.
    """
    checkable = [m for m in models if is_checkable(m)]
    if not checkable:
        return []

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TREE_FETCH_TIMEOUT_S),
            follow_redirects=True,
        )
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _guarded(model: Any) -> ModelUpdateInfo:
        async with sem:
            return await _check_one(model, client=client, hf_token=hf_token, force=force)

    try:
        return await asyncio.gather(*(_guarded(m) for m in checkable))
    finally:
        if owns_client:
            await client.aclose()


def clear_cache() -> None:
    """Drop the per-repo tree cache (test hook / forced global refresh)."""
    _TREE_CACHE.clear()


__all__ = [
    "ModelUpdateInfo",
    "check_updates",
    "clear_cache",
    "is_checkable",
]
