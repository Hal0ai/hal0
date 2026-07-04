"""DEPRECATED shim — ``merge_flags`` now lives in :mod:`hal0.slots.argv`.

The merge logic was folded into ``hal0.slots.argv`` so it reuses argv's shared
tokenizer + short/long alias table (``-b`` ⟷ ``--batch-size`` now dedup against
each other, which this module's ``--``-only tokenizer could not do). The
APPEND-list flag set is likewise unified there (``hal0.slots.argv.APPEND_FLAGS``).

This module re-exports the canonical implementations for existing importers
(``hal0.providers.llama_server`` — a provider retired in a later wave). New code
should import from ``hal0.slots.argv`` directly.
"""

from __future__ import annotations

from hal0.slots.argv import APPEND_FLAGS as _APPEND_LIST_FLAGS
from hal0.slots.argv import merge_flags

# ``_APPEND_LIST_FLAGS`` is re-exported (aliased to the unified
# ``hal0.slots.argv.APPEND_FLAGS``) for any legacy importer that still reaches
# for it from this module; there is now a single append-flag definition.
__all__ = ["_APPEND_LIST_FLAGS", "merge_flags"]
