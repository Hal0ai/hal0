"""hal0.registry — SQLite-backed model catalog.

The registry is the single source of truth for "what models exist on this
host": the ``model`` tables of the shared SQLite database (see ``hal0.db``).
A TOML-era ``registry.toml`` is converted once and renamed by
:mod:`hal0.registry.toml_conversion`; nothing reads it afterwards.

Slot configs reference model IDs from the registry.  If a model is deleted,
any slot referencing it fails to load with a structured error
({"error": {"code": "model.not_found", ...}}).

Port target: haloai lib/registry.py (split into store + model).
See PLAN.md §3, §7.5, §8 and ARCHITECTURE.md §Key boundaries.

Key exports:
    ModelRegistry       — primary entry point for all registry operations
                           (= SqliteModelRegistry).
    SqliteModelRegistry — the SQLite-backed implementation.
    Model               — pydantic model for a single registry entry.
"""

from __future__ import annotations

from hal0.registry.detect import DetectionResult, detect
from hal0.registry.gguf_header import GGUFParseError, read_gguf_header
from hal0.registry.model import Model, ModelDefaults
from hal0.registry.store import (
    ModelAlreadyExists,
    ModelNotFound,
    ModelRegistry,
    RegistryError,
    SqliteModelRegistry,
)

__all__ = [
    "DetectionResult",
    "GGUFParseError",
    "Model",
    "ModelAlreadyExists",
    "ModelDefaults",
    "ModelNotFound",
    "ModelRegistry",
    "RegistryError",
    "SqliteModelRegistry",
    "detect",
    "read_gguf_header",
]
