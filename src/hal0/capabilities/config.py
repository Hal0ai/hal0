"""Pydantic models + atomic read/write for ``/etc/hal0/capabilities.toml``.

The overlay configuration carries one :class:`CapabilitySelection` per
(slot, child) tuple. The on-disk shape is intentionally flat / nested
TOML so operators can hand-edit the file::

    schema_version = 2

    [selections.embed.embed]
    device   = "gpu-rocm"
    provider = "llama-server"
    model    = "nomic-embed-text-v1.5"
    enabled  = true

    [selections.voice.tts]
    device   = "cpu"
    provider = "kokoro"
    model    = "kokoro-v1"
    enabled  = false

The full file is rewritten atomically on every change via
:func:`hal0.config.loader.write_toml_atomic` so an interrupted write
leaves the prior file intact.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from hal0.config import paths
from hal0.config.loader import write_toml_atomic
from hal0.config.schema import (
    CAPABILITIES_SCHEMA_VERSION_CURRENT,
    map_backend_to_device,
)

log = logging.getLogger(__name__)


class CapabilitySelection(BaseModel):
    """One operator-facing selection for a (slot, child) tuple.

    Mirrors the dashboard's per-child editor card: which device the
    user picked, which provider runs on it, which model id they bound,
    and whether the child is currently active.

    v0.2 rename: the ``backend`` field is now :attr:`device`. P2-device
    makes ``device`` the sole persisted truth — ``backend`` is no longer a
    model field, but a TOML carrying only a legacy ``backend`` key still
    auto-promotes via :func:`map_backend_to_device` on load (see
    :meth:`_promote_backend_to_device`); the key is then dropped rather
    than round-tripped.
    """

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}

    device: str = Field(
        default="",
        description=(
            "v0.2 hardware-preference id: 'gpu-rocm' | 'gpu-vulkan' | "
            "'npu' | 'cpu'. Empty == unset (matches the dashboard's "
            "blank-picker UX)."
        ),
    )
    provider: str = Field(
        default="",
        description="Provider name: 'llama-server' | 'flm' | 'moonshine' | 'kokoro' | 'comfyui'.",
    )
    model: str = Field(
        default="",
        description="Model id from the registry. Empty when the child is unset.",
    )
    enabled: bool = Field(
        default=False,
        description="True when the underlying slot should be loaded with this model.",
    )

    @model_validator(mode="before")
    @classmethod
    def _promote_backend_to_device(cls, data: Any) -> Any:
        """Read-only promotion shim: derive ``device`` from a legacy
        on-disk ``backend`` key, then drop ``backend`` from the dict.

        ``backend`` is no longer a model field (P2-device: ``device`` is
        the sole persisted truth). This validator defends against:

          1. Hand-edited TOMLs that revert to the old shape.
          2. Programmatic call sites that still pass ``backend=...``.

        Without this, a legacy ``backend``-only selection would silently
        lose its device on load. ``backend`` is always popped when
        present (even if ``device`` was already set) so a stray key never
        round-trips — only the *promotion* is gated on ``device`` being
        absent.
        """
        if not isinstance(data, dict):
            return data
        backend_val = data.get("backend")
        if backend_val is None:
            return data
        new_data = dict(data)
        new_data.pop("backend", None)
        if not data.get("device") and backend_val:
            new_data["device"] = map_backend_to_device(str(backend_val))
        return new_data


class CapabilityConfig(BaseModel):
    """Parsed ``/etc/hal0/capabilities.toml``.

    Selections are keyed ``[selections.<slot>.<child>]`` — see the module
    docstring for the on-disk shape. Empty selections are allowed (the
    orchestrator initialises them lazily on first read).

    ``schema_version`` independently tracks the on-disk shape (separate
    from ``hal0.toml``'s ``meta.schema_version``). New writes always
    stamp the current version.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    schema_version: int = Field(
        default=CAPABILITIES_SCHEMA_VERSION_CURRENT,
        ge=1,
        description=("Capabilities-file schema version. v1 used ``backend``; v2 uses ``device``."),
    )

    selections: dict[str, dict[str, CapabilitySelection]] = Field(
        default_factory=dict,
        description="Nested map: { slot_name: { child_name: CapabilitySelection } }.",
    )


# ── Path resolution ───────────────────────────────────────────────────────────


def capabilities_toml_path() -> Path:
    """Return ``/etc/hal0/capabilities.toml`` (HAL0_HOME-aware)."""
    return paths.etc() / "capabilities.toml"


# ── Read / write ──────────────────────────────────────────────────────────────


def load_capabilities_config(path: Path | None = None) -> CapabilityConfig:
    """Load and validate ``capabilities.toml``.

    Returns an empty :class:`CapabilityConfig` when the file does not
    exist — callers (notably :meth:`CapabilityOrchestrator.initialize_if_missing`)
    detect that with :func:`exists` below and seed defaults. Read-only.
    """
    target = path if path is not None else capabilities_toml_path()
    if not Path(target).exists():
        return CapabilityConfig()
    with open(target, "rb") as f:
        raw = tomllib.load(f)
    # Retired capability lanes are dropped on read (tolerant load — the next
    # write converges the file). ``vision`` stopped being a slot-backed
    # capability: vision is a MODEL property (mmproj presence, the registry's
    # ``capabilities``; whether it loads is the profile's ``vision``) served by any llm slot, so a
    # stray ``[selections.vision]`` table from an older install is inert.
    sels = raw.get("selections")
    if isinstance(sels, dict):
        sels.pop("vision", None)
    return CapabilityConfig.model_validate(raw)


def capabilities_toml_payload(cfg: CapabilityConfig) -> dict[str, Any]:
    """Serialise a validated config into the canonical on-disk dict shape.

    Single source for both :func:`save_capabilities_config` and the
    :class:`hal0.slot_config.SlotConfigStore` ChangeSet computation
    (issue #697) so the two can never disagree on the persisted shape.

    Always stamps the current schema_version so a downgrade that wrote
    v1-shaped TOML on top of a v2 install can be detected on the next
    upgrade.
    """
    # Pydantic v2 ``model_dump`` walks the nested CapabilitySelection tables
    # into plain dicts — exactly the shape ``tomli_w.dump`` wants.
    data: dict[str, Any] = cfg.model_dump(mode="python")
    # Re-stamp the version (model defaults guarantee this is correct,
    # but a hand-constructed CapabilityConfig that overrode it would
    # leak the wrong version onto disk).
    data["schema_version"] = CAPABILITIES_SCHEMA_VERSION_CURRENT
    # Drop the deprecated ``backend`` field from each selection so the
    # canonical persisted shape is single-source-of-truth.
    sels = data.get("selections")
    if isinstance(sels, dict):
        for _slot, children in sels.items():
            if not isinstance(children, dict):
                continue
            for _child, entry in children.items():
                if isinstance(entry, dict):
                    entry.pop("backend", None)
    return data


def save_capabilities_config(cfg: CapabilityConfig, path: Path | None = None) -> None:
    """Atomically rewrite ``capabilities.toml`` from a validated config.

    NOTE(#697): the capability-apply path writes through
    ``hal0.slot_config.SlotConfigStore`` instead (one ChangeSet covering
    both files). This helper remains for the NON-apply writers whose
    semantics don't fit a selection ChangeSet: the first-boot seed
    (``CapabilityOrchestrator.initialize_if_missing``) and the
    ``hal0 capabilities migrate`` CLI. Both serialise through
    :func:`capabilities_toml_payload`,
    so the on-disk shape cannot diverge from the store's.
    """
    target = path if path is not None else capabilities_toml_path()
    write_toml_atomic(target, capabilities_toml_payload(cfg))


__all__ = [
    "CAPABILITIES_SCHEMA_VERSION_CURRENT",
    "CapabilityConfig",
    "CapabilitySelection",
    "capabilities_toml_path",
    "capabilities_toml_payload",
    "load_capabilities_config",
    "save_capabilities_config",
]
