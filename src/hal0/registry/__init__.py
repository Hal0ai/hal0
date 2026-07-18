"""hal0.registry — SQLite-backed model catalog (ML-1 pilot).

The registry is the single source of truth for "what models exist on this
host." As of ML-1 (hal0-specs/spec-ml1-sqlite.final.md), that source of
truth is a SQLite database (see ``hal0.db``) rather than the historic
atomic ``registry.toml`` — the TOML store (``TomlModelRegistry``) is kept
alive for the import/export path, but ``ModelRegistry`` now names
``SqliteModelRegistry``.

Slot configs reference model IDs from the registry.  If a model is deleted,
any slot referencing it fails to load with a structured error
({"error": {"code": "model.not_found", ...}}).

Port target: haloai lib/registry.py (split into store + model).
See PLAN.md §3, §7.5, §8 and ARCHITECTURE.md §Key boundaries.

Key exports:
    ModelRegistry       — primary entry point for all registry operations
                           (= SqliteModelRegistry).
    SqliteModelRegistry — the ML-1 SQLite-backed implementation.
    TomlModelRegistry   — the original TOML-backed implementation, kept
                           for the import/export path.
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
    TomlModelRegistry,
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
    "TomlModelRegistry",
    "detect",
    "read_gguf_header",
]
