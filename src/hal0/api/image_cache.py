"""On-disk PNG cache for ``/v1/images/generations`` URL responses.

When the OpenAI body asks for ``response_format: "url"``, the
``/v1/images/generations`` route saves the generated PNG under
``/var/lib/hal0/images/cache/<uuid>.png`` and hands the client back a
hal0-hosted URL like ``/api/images/cache/<uuid>.png``. This module owns
the read/write/eviction surface for that cache.

LRU eviction policy:
    * Keep at most ``MAX_ENTRIES`` files (default 100).
    * Keep at most ``MAX_TOTAL_BYTES`` total (default 1 GB).
    * Whichever ceiling is hit first triggers eviction. Eviction
      removes the oldest files (by ``st_mtime``) until both ceilings
      are satisfied with margin.
    * Evictions run synchronously inside ``write_png`` — image gen is
      already a high-latency operation; an extra few ms of cleanup
      doesn't matter.

# NOTE: The cache directory is intentionally NOT cleared at install or
# uninstall time. Operators may want to retrieve recently-generated
# images after a hal0 update; the cache is small enough that "1 GB
# of disk forever" is a reasonable default. ``hal0 cache clear``
# (Phase 2) will give them a manual purge path.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import uuid
from pathlib import Path

from hal0.config import paths
from hal0.install.perms import ensure_shared_dir

log = logging.getLogger(__name__)


# ── Tunables ──────────────────────────────────────────────────────────────────
# Generous defaults — image-gen workflows produce 1-4 MB PNGs, so 100 files
# at ~3 MB each is ~300 MB of disk for a comfortable history buffer.
MAX_ENTRIES: int = 100
MAX_TOTAL_BYTES: int = 1 * 1024 * 1024 * 1024  # 1 GB

# Filename safety: we generate UUIDs ourselves, but the read path accepts
# the same regex so a path-traversal attempt (``..`` or absolute) bounces
# at the API boundary instead of inside Path resolution.
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,40}$")


# ── Path helpers ──────────────────────────────────────────────────────────────


def cache_dir() -> Path:
    """Return ``/var/lib/hal0/images/cache``, creating it on demand."""
    p = paths.var_lib() / "images" / "cache"
    # images/cache/ is declared 2775; a bare mkdir under the daemon's
    # UMask=0022 births 2755 and re-drifts `doctor perms` after every image
    # generation (#1896 class, precedent: registry/pull.py persist_pull_job).
    ensure_shared_dir(p)
    return p


def _png_path(name: str) -> Path | None:
    """Resolve a cache name to a path, or None if the name is unsafe."""
    stem = name[:-4] if name.endswith(".png") else name
    if not _UUID_RE.match(stem):
        return None
    return cache_dir() / f"{stem}.png"


# ── Eviction ──────────────────────────────────────────────────────────────────


def _evict_if_over_budget() -> None:
    """Drop oldest PNGs until under both ceilings.

    Uses ``st_mtime`` as the LRU proxy. We touch each file on read in
    ``read_png`` so a recently-served image survives even if the cache
    has been around for a while.
    """
    d = cache_dir()
    try:
        entries = sorted(
            (p for p in d.glob("*.png") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError as exc:
        log.warning("image_cache.list_failed", extra={"error": str(exc)})
        return

    total = sum(p.stat().st_size for p in entries if p.exists())
    over_count = len(entries) > MAX_ENTRIES
    over_size = total > MAX_TOTAL_BYTES
    if not (over_count or over_size):
        return

    # Evict oldest first until both budgets satisfied.
    removed = 0
    for victim in entries:
        if not (over_count or over_size):
            break
        try:
            size = victim.stat().st_size
            victim.unlink()
            removed += 1
            total -= size
        except OSError as exc:
            log.debug(
                "image_cache.evict_failed",
                extra={"path": str(victim), "error": str(exc)},
            )
            continue
        over_count = (len(entries) - removed) > MAX_ENTRIES
        over_size = total > MAX_TOTAL_BYTES
    if removed:
        log.info(
            "image_cache.evicted",
            extra={"removed": removed, "remaining": len(entries) - removed, "bytes": total},
        )


# ── Public API ────────────────────────────────────────────────────────────────


def write_png(png_bytes: bytes) -> str:
    """Write ``png_bytes`` to the cache, return the bare uuid stem.

    Caller assembles the final URL (``/api/images/cache/<stem>.png``) — the
    route prefix isn't this module's concern.

    Triggers an LRU eviction pass *after* the new file lands so we never
    bounce a fresh write because we were over-budget.
    """
    name = uuid.uuid4().hex
    out = cache_dir() / f"{name}.png"
    # Atomic write so a crash mid-flight can't leave a half-PNG that
    # serves as a black box later.
    tmp = out.with_suffix(".png.part")
    try:
        with open(tmp, "wb") as f:
            f.write(png_bytes)
        os.replace(tmp, out)
    except OSError:
        # Best-effort cleanup of the .part file; re-raise the original.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    _evict_if_over_budget()
    return name


def read_png(name: str) -> bytes | None:
    """Read a cached PNG by name (with or without .png suffix).

    Returns None if the name doesn't pass safety check or the file isn't
    on disk. Touches ``st_mtime`` so this entry counts as "recently used"
    for the next eviction pass.
    """
    p = _png_path(name)
    if p is None or not p.exists():
        return None
    try:
        # Bump mtime so LRU treats this as recently-used.
        os.utime(p, None)
        return p.read_bytes()
    except OSError as exc:
        log.warning("image_cache.read_failed", extra={"path": str(p), "error": str(exc)})
        return None


__all__ = [
    "MAX_ENTRIES",
    "MAX_TOTAL_BYTES",
    "cache_dir",
    "read_png",
    "write_png",
]
