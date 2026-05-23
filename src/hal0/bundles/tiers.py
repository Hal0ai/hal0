"""Bundle tier registry — locked list + loader.

The five bundle names are fixed by ADR-0010 (no additional tiers in
v0.2). Manifests ship under ``installer/manifests/omni/`` and are
copied by ``install.sh`` to ``/var/lib/hal0/models/collections/omni/``
at install time; the loader picks them up from the runtime location
first and falls back to the in-tree source for dev installs (mirrors
:func:`hal0.config.paths.manifest_json`).
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

from hal0.bundles.schema import Bundle, BundleManifest
from hal0.config import paths

# Locked bundle list — order matters: this is the picker rendering
# order (Lite → Default → Pro → Max → LMX kit).
BUNDLES: tuple[str, ...] = (
    "hal0-Lite",
    "hal0-Default",
    "hal0-Pro",
    "hal0-Max",
    "LMX-Omni-52B-Halo",
)


def _runtime_root() -> Path:
    """Return the production runtime directory for bundle manifests."""

    return paths.var_lib() / "models" / "collections" / "omni"


def _intree_root() -> Path:
    """Return the in-tree manifest directory used as a dev fallback."""

    # tiers.py → src/hal0/bundles/tiers.py → repo root is parents[3].
    return Path(__file__).resolve().parents[3] / "installer" / "manifests" / "omni"


def _candidate_roots() -> tuple[Path, ...]:
    """Resolution order for bundle manifest lookups.

    ``HAL0_BUNDLES_DIR`` is a test hook — pointing it at a temp dir lets
    tests load fixture manifests without touching disk. The production
    runtime dir takes precedence over the in-tree fallback so a freshly
    pulled hal0 install never accidentally serves stale dev manifests.
    """

    override = os.environ.get("HAL0_BUNDLES_DIR", "").strip()
    if override:
        return (Path(override),)
    return (_runtime_root(), _intree_root())


def _manifest_filename(name: str) -> str:
    """Resolve a bundle name to its on-disk filename.

    The hal0 tiers use lowercase slugs; the LMX kit keeps its mixed-case
    name verbatim because it's vendor-branded.
    """

    if name == "LMX-Omni-52B-Halo":
        return "LMX-Omni-52B-Halo.json"
    return f"{name.lower()}.json"


def _locate(name: str) -> Path:
    """Find the manifest path for ``name`` across the candidate roots."""

    filename = _manifest_filename(name)
    for root in _candidate_roots():
        candidate = root / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"bundle manifest not found for {name!r} "
        f"(searched: {[str(root) for root in _candidate_roots()]})"
    )


@cache
def _load_cached(name: str) -> BundleManifest:
    """Cached manifest load. Keyed on the bundle name only — bumping the
    on-disk file requires a process restart, which matches install.sh's
    write-once semantics.
    """

    return BundleManifest.from_path(_locate(name))


def load_bundle(name: str) -> BundleManifest:
    """Return the manifest for ``name``. Raises FileNotFoundError if
    the file is missing on every candidate root.
    """

    if name not in BUNDLES:
        raise ValueError(f"unknown bundle {name!r}; expected one of {list(BUNDLES)}")
    return _load_cached(name)


def load_all_bundles() -> list[BundleManifest]:
    """Load every bundle in :data:`BUNDLES` order.

    Tests that need to assert against the full set walk the result; the
    HTTP layer uses it to render the picker payload.
    """

    return [load_bundle(name) for name in BUNDLES]


def list_bundle_summaries() -> list[Bundle]:
    """Project the manifests onto the lightweight :class:`Bundle` shape.

    Used by ``GET /api/bundles`` — the picker doesn't need the
    ``collection.omni`` payload, just the tier metadata.
    """

    return [manifest.bundle for manifest in load_all_bundles()]


def reset_cache() -> None:
    """Clear the cached manifest loads. Used by tests + the API on a
    manual reload."""

    _load_cached.cache_clear()


__all__ = [
    "BUNDLES",
    "list_bundle_summaries",
    "load_all_bundles",
    "load_bundle",
    "reset_cache",
]
