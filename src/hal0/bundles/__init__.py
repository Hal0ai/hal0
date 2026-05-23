"""hal0 bundles — first-run picker manifests + tier eligibility.

ADR-0010 (bundle picker, no default model stack) and Lemonade adoption
plan §8 (First-run UX) define a small, fixed set of hardware-anchored
tiers and one vendor-blessed kit. Each bundle is a
``collection.omni``-compatible manifest plus hal0-specific slot
metadata. The picker lives on the first dashboard load; this package
is the backend half.

Public surface:

- :data:`BUNDLES` — the five locked bundle names.
- :class:`Bundle`, :class:`BundleManifest` — typed shapes
  (:mod:`hal0.bundles.schema`).
- :func:`load_bundle`, :func:`load_all_bundles` — manifest loaders.
- :func:`eligible_tiers` — hardware-anchored RAM filter.
"""

from __future__ import annotations

from hal0.bundles.eligibility import eligible_tiers
from hal0.bundles.schema import Bundle, BundleManifest, ModelEntry
from hal0.bundles.tiers import BUNDLES, load_all_bundles, load_bundle

__all__ = [
    "BUNDLES",
    "Bundle",
    "BundleManifest",
    "ModelEntry",
    "eligible_tiers",
    "load_all_bundles",
    "load_bundle",
]
