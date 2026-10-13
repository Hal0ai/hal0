"""Guards for the #1369 removal of ``SlotConfig.enabled``.

A slot is activated by binding a model, not by a second boolean: a
non-empty ``[model].default`` IS the activation signal. Boot autostart
never read ``enabled`` (that is the Quadlet ``[Install] WantedBy=`` stanza
plus ``SlotManager.load()``'s empty-model guard), and every routability
check that consulted it was immediately followed by a model-presence gate.

``SlotConfig`` is ``extra="forbid"`` (ADR-0029 §5), so a TOML written by an
older hal0 that still carries the key fails its typed load with the
replacement named; the profile-centric conversion strips the key.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from hal0.config.schema import SlotConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEEDED_SLOTS_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"


def test_slot_config_has_no_enabled_field() -> None:
    """The field is gone from the schema — not merely defaulted or deprecated."""
    assert "enabled" not in SlotConfig.model_fields


def test_stale_enabled_key_is_refused_with_its_replacement() -> None:
    """A file still carrying ``enabled`` fails the typed load, pointing at
    the replacement (model-presence), rather than carrying a dead key."""
    from hal0.config.loader import ConfigParseError, load_slot_config

    raw = (
        'name = "legacy"\nport = 8081\ntype = "llm"\nenabled = false\n'
        '\n[model]\ndefault = "qwen3-4b"\n'
    )
    with pytest.raises(ValidationError) as exc:
        SlotConfig.model_validate(tomllib.loads(raw))
    assert exc.value.errors()[0]["loc"] == ("enabled",)

    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "legacy.toml"
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(ConfigParseError) as parse:
            load_slot_config("legacy", path=path)
    assert "'enabled'" in str(parse.value)
    assert "model-presence" in str(parse.value)


def test_the_conversion_strips_enabled() -> None:
    from hal0.config.migrations import profile_centric as pc

    assert "enabled" in pc.SLOT_DROP_KEYS


@pytest.mark.parametrize("name", sorted(p.stem for p in _SEEDED_SLOTS_DIR.glob("*.toml")))
def test_no_seed_toml_declares_enabled(name: str) -> None:
    """Shipped seeds express activation only through ``[model].default``."""
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / f"{name}.toml").read_text(encoding="utf-8"))
    assert "enabled" not in raw.get("slot", raw)
