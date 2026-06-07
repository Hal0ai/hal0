"""hal0.config — Configuration loading, validation, and path resolution.

Submodules:
    paths.py      — FHS-aligned path resolver (respects HAL0_HOME env var)
    schema.py     — pydantic v2 models: Hal0Config, SlotConfig, ModelConfig, etc.
    loader.py     — load_hal0_config() / load_slot_config() returning validated models
    env.py        — write_env_atomic(): atomic slot env file writer (Tier 1 fix)
    migrations/   — versioned TOML migration transforms (Phase 5)

Port target: haloai lib/config.py (420 lines), lib/env_manager.py, lib/features.py,
lib/paths.py.  See PLAN.md §3, §5 Tier 1.

Key exports:
    load_hal0_config  — load and validate hal0.toml
    load_slot_config  — load and validate slots/<name>.toml
    Hal0Config        — top-level config pydantic model
    SlotConfig        — per-slot config pydantic model
    ModelConfig       — [model] section within a slot config
    paths             — the paths submodule (import as: from hal0.config import paths)
"""

from __future__ import annotations

from hal0.config import paths
from hal0.config.loader import load_hal0_config, load_slot_config
from hal0.config.schema import Hal0Config, ModelConfig, SlotConfig

__all__ = [
    "Hal0Config",
    "ModelConfig",
    "SlotConfig",
    "load_hal0_config",
    "load_slot_config",
    "paths",
]
