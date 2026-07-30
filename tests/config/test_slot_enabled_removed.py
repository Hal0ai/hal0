"""Guards for the #1369 removal of ``SlotConfig.enabled``.

A slot is activated by binding a model, not by a second boolean: a
non-empty ``[model].default`` IS the activation signal. Boot autostart
never read ``enabled`` (that is the Quadlet ``[Install] WantedBy=`` stanza
plus ``SlotManager.load()``'s empty-model guard), and every routability
check that consulted it was immediately followed by a model-presence gate.

``SlotConfig`` is ``extra="allow"``, so a TOML written by an older hal0
still validates — the stale key round-trips as an extra rather than
failing the load. The one-shot migration
(:mod:`hal0.config.migrations.slot_enabled_removal`) sweeps it off disk.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config.schema import SlotConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEEDED_SLOTS_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"


def test_slot_config_has_no_enabled_field() -> None:
    """The field is gone from the schema — not merely defaulted or deprecated."""
    assert "enabled" not in SlotConfig.model_fields


def test_stale_enabled_key_still_loads() -> None:
    """An older TOML carrying ``enabled = false`` must not fail validation.

    Operators upgrade in place; a hard validation error here would brick
    every pre-#1369 slot file until the migration ran, and the migration
    itself has to load the file first.
    """
    raw = tomllib.loads(
        'name = "legacy"\nport = 8081\ntype = "llm"\nenabled = false\n'
        '\n[model]\ndefault = "qwen3-4b"\n'
    )
    slot = SlotConfig.model_validate(raw)
    assert slot.name == "legacy"
    assert slot.model.default == "qwen3-4b"
    # extra="allow" keeps the key so a save round-trips it rather than
    # silently rewriting the operator's file mid-read.
    assert slot.model_dump().get("enabled") is False


@pytest.mark.parametrize("name", sorted(p.stem for p in _SEEDED_SLOTS_DIR.glob("*.toml")))
def test_no_seed_toml_declares_enabled(name: str) -> None:
    """Shipped seeds express activation only through ``[model].default``."""
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / f"{name}.toml").read_text(encoding="utf-8"))
    assert "enabled" not in raw.get("slot", raw)
