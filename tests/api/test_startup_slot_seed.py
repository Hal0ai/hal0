"""Startup slot seeding — the lifespan hook that ships agent/brain/etc.

install.sh's static slot-TOML copy loop only runs on a fresh install; it
never re-runs on ``hal0 update``, so a box upgrading past a release that
added a new seed (``brain``, the dashboard steward's slot) never grew the
file. The lifespan now copies any missing seed on every API start
(copy-if-absent), mirroring the persona startup seed
(tests/api/test_startup_persona_seed.py).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.install.static_seeds import STATIC_SEED_SLOTS


def _slots_dir(tmp_hal0_home: str) -> Path:
    # Mirrors paths.slots_config_dir() under HAL0_HOME.
    return Path(tmp_hal0_home) / "etc" / "hal0" / "slots"


def test_lifespan_seeds_static_slots(tmp_hal0_home: str) -> None:
    """A blank box grows every static seed at startup."""
    slots_dir = _slots_dir(tmp_hal0_home)
    assert not slots_dir.exists()

    with TestClient(create_app()):
        pass

    names = {p.stem for p in slots_dir.glob("*.toml")}
    assert set(STATIC_SEED_SLOTS) <= names
    assert "brain" in names
    assert "agent" in names


def test_lifespan_slot_seed_converges_old_box_without_touching_edits(
    tmp_hal0_home: str,
) -> None:
    """The upgrade case: an old box has agent (operator-edited), no
    brain — startup adds the missing seed and leaves the edit alone."""
    slots_dir = _slots_dir(tmp_hal0_home)
    slots_dir.mkdir(parents=True)
    (slots_dir / "agent.toml").write_text(
        'name = "agent"\nport = 9999\nenabled = true\n', encoding="utf-8"
    )

    with TestClient(create_app()):
        pass

    assert (slots_dir / "brain.toml").exists()
    # Operator edit survives untouched.
    edited = (slots_dir / "agent.toml").read_text(encoding="utf-8")
    assert "port = 9999" in edited
    assert "enabled = true" in edited
