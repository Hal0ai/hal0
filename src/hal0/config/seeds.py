"""Loader for hal0's shipped seed data (profiles, stacks, bench, family defaults).

P3-schema (spec-p3-schema.final.md, Part A) externalizes the hardcoded
``SEED_PROFILES`` / ``SEED_STACKS`` / ``PROFILE_BENCH`` / ``FAMILY_DEFAULTS``
dicts that used to live inline in ``hal0.config.schema`` into shipped TOML
under ``hal0/config/data/``. This module owns reading, validating, and
caching that data; ``schema.py`` keeps a bottom-of-module import shim
(``SEED_PROFILES = _seeds.seed_profiles()`` etc.) so every existing
``from hal0.config.schema import SEED_PROFILES`` call site is unaffected.

Circular-import note: ``schema.py`` imports this module at the very BOTTOM
of its own file (after every pydantic model is defined), so this module must
NOT import ``hal0.config.schema`` at module level -- that would deadlock the
import (schema -> seeds -> schema, mid-execution). Every function below that
needs a schema symbol (``ProfileConfig``/``StackConfig``/the image
constants/``StackCapabilityRow``) imports it locally, inside the function
body, once schema.py has already finished executing far enough (or entirely,
for the steady-state case where some *other* module imports ``seeds`` first
-- Python's import-in-progress module is still registered in ``sys.modules``,
so a local import here just binds the partially- or fully-initialized module
object; by the time any of these functions actually RUN, schema.py's module
body has reached its own bottom-of-file import of ``seeds`` -- see spec risk
R2 and ``tests/config/test_seeds_data.py`` for the cold-import regression
test).

Image-pin sentinels: ``seed_profiles.toml`` cannot hardcode the resolved
ROCmFPX/Vulkan-fallback image digests -- that would fork the pin the
ML-runner registry (§7.1b) is about to own. Instead the TOML carries a
placeholder string prefixed with ``@`` (e.g. ``"@DEFAULT_ROCMFPX_IMAGE"``)
and :func:`_resolve_image_sentinel` substitutes the live value from the
still-in-schema.py constant at read time. When ML-runner lands
``RUNNER_IMAGES``, the resolver's lookup table below is the one place that
changes (schema.* -> runners.RUNNER_IMAGES[...]).
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib.resources import files
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hal0.config.schema import StackConfig

_DATA_PACKAGE = "hal0.config.data"
_SENTINEL_PREFIX = "@"

#: Marker used in seed_stacks.toml for the slot that carries the shared
#: embed+rerank capability pair (see :func:`_embed_rerank_rows`).
_EMBED_RERANK_MARKER = "@embed_rerank"


def _read_toml(filename: str) -> dict[str, Any]:
    """Read one shipped data file via importlib.resources (works from an
    editable checkout and an installed wheel identically -- see spec §A.1)."""
    text = files(_DATA_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
    return tomllib.loads(text)


def _resolve_image_sentinel(value: str) -> str:
    """Resolve an ``@NAME`` sentinel to the live ``hal0.config.schema`` constant.

    Non-sentinel values (literal image refs -- flm/kokoro/qwen3tts/the
    upstream CUDA image/the comfyui digest) pass through unchanged.
    """
    if not isinstance(value, str) or not value.startswith(_SENTINEL_PREFIX):
        return value
    name = value[len(_SENTINEL_PREFIX) :]
    from hal0.config import schema  # local import: breaks the schema<->seeds cycle

    try:
        resolved = getattr(schema, name)
    except AttributeError as exc:
        raise ValueError(
            f"seed_profiles.toml references unknown image sentinel {value!r} "
            f"(no such constant hal0.config.schema.{name})"
        ) from exc
    if not isinstance(resolved, str):
        raise ValueError(
            f"seed_profiles.toml image sentinel {value!r} resolved to a "
            f"non-string constant ({resolved!r})"
        )
    return resolved


def _embed_rerank_rows(device: str = "gpu-rocm") -> list[dict[str, Any]]:
    """The shared embed + rerank capability pair every seed stack ships with.

    Kept in Python (not TOML) per spec §A.2(a) so the two hardcoded
    capability model ids (``qwen3-embedding-0-6b-q8-0``,
    ``bge-reranker-v2-m3-q4_k_m``) live in exactly one place. Returns plain
    dicts (the shape ``StackCapabilityRow.model_validate`` expects) rather
    than model instances, matching what :func:`seed_stacks` feeds into
    ``StackConfig.model_validate``.
    """
    return [
        {
            "child": "embed",
            "device": device,
            "provider": "llama-server",
            "model": "qwen3-embedding-0-6b-q8-0",
            "enabled": True,
        },
        {
            "child": "rerank",
            "device": device,
            "provider": "llama-server",
            "model": "bge-reranker-v2-m3-q4_k_m",
            "enabled": True,
        },
    ]


@lru_cache(maxsize=1)
def seed_profiles() -> dict[str, dict[str, Any]]:
    """Raw seed-profile dicts (image sentinels resolved), keyed by slug.

    Same shape as the old module-level ``SEED_PROFILES`` dict this replaces:
    ``ProfileConfig.model_validate`` is called by callers (the loader, the
    catalog), not here -- callers that mutate the returned dict per-entry
    (e.g. ``loader.load_profiles_config``) expect fresh dicts, so each call
    into a *cached* top-level dict still yields distinct per-profile dicts
    the caller may treat as read-only data (mirroring the previous
    module-constant semantics: one shared dict, refreshed only via
    :func:`reset_cache`).
    """
    raw = _read_toml("seed_profiles.toml")
    profiles = raw.get("profile", {})
    out: dict[str, dict[str, Any]] = {}
    for name, entry in profiles.items():
        entry = dict(entry)
        if "image" in entry:
            entry["image"] = _resolve_image_sentinel(entry["image"])
        out[name] = entry
    return out


@lru_cache(maxsize=1)
def seed_stacks() -> dict[str, StackConfig]:
    """Built-in seed stacks, validated into :class:`StackConfig` instances."""
    from hal0.config.schema import StackConfig  # local import: breaks the cycle

    raw = _read_toml("seed_stacks.toml")
    stacks = raw.get("stack", {})
    out: dict[str, StackConfig] = {}
    for slug, entry in stacks.items():
        entry = dict(entry)
        slots = []
        for slot in entry.get("slots", []):
            slot = dict(slot)
            if slot.get("capabilities") == _EMBED_RERANK_MARKER:
                slot["capabilities"] = _embed_rerank_rows(device=slot.get("device", "gpu-rocm"))
            slots.append(slot)
        entry["slots"] = slots
        out[slug] = StackConfig.model_validate(entry)
    return out


@lru_cache(maxsize=1)
def profile_bench() -> dict[str, dict[str, float]]:
    """Static bench numbers for seed profiles (card hero metric)."""
    raw = _read_toml("profile_bench.toml")
    return {name: dict(vals) for name, vals in raw.get("bench", {}).items()}


@lru_cache(maxsize=1)
def family_defaults() -> dict[str, str]:
    """Per-family llama-server flag overrides (the arch-quirks layer)."""
    raw = _read_toml("family_defaults.toml")
    return dict(raw.get("family", {}))


def reset_cache() -> None:
    """Clear every ``lru_cache`` above.

    Tests that monkeypatch the data package or otherwise need a fresh read
    (rather than the process-lifetime-cached values every other caller gets)
    should call this after making their change.
    """
    seed_profiles.cache_clear()
    seed_stacks.cache_clear()
    profile_bench.cache_clear()
    family_defaults.cache_clear()
