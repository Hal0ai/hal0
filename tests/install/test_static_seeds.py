"""Unit tests for :mod:`hal0.install.static_seeds`.

Closes the same fresh-install-only gap the persona startup seed does
(tests/api/test_startup_persona_seed.py): install.sh's slot-TOML copy
loop never re-runs on ``hal0 update``, so these tests pin the
copy-if-absent contract independently of the lifespan wiring.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.install.brain_model import BRAIN_MODEL_PORTABLE
from hal0.install.static_seeds import STATIC_SEED_SLOTS, seed_static_slots
from hal0.providers.container import _resolve_context_size


def _fake_installer_root(tmp_path: Path, names: tuple[str, ...] = STATIC_SEED_SLOTS) -> Path:
    root = tmp_path / "installer-root"
    src_dir = root / "installer" / "etc-hal0" / "slots"
    src_dir.mkdir(parents=True)
    for name in names:
        (src_dir / f"{name}.toml").write_text(f'name = "{name}"\nport = 9000\n', encoding="utf-8")
    return root


def test_seed_static_slots_copies_all_missing(tmp_path: Path) -> None:
    installer_root = _fake_installer_root(tmp_path)
    dest = tmp_path / "slots"
    seeded = seed_static_slots(installer_root=installer_root, slots_dir=dest)
    assert set(seeded) == set(STATIC_SEED_SLOTS)
    for name in STATIC_SEED_SLOTS:
        assert (dest / f"{name}.toml").exists()


def test_seed_static_slots_skips_existing(tmp_path: Path) -> None:
    installer_root = _fake_installer_root(tmp_path)
    dest = tmp_path / "slots"
    dest.mkdir()
    (dest / "agent.toml").write_text('name = "agent"\nport = 1234\n', encoding="utf-8")
    seeded = seed_static_slots(installer_root=installer_root, slots_dir=dest)
    assert "agent" not in seeded
    assert "brain" in seeded
    # Operator/prior file untouched.
    assert "port = 1234" in (dest / "agent.toml").read_text(encoding="utf-8")


def test_seed_static_slots_idempotent_second_run_noop(tmp_path: Path) -> None:
    installer_root = _fake_installer_root(tmp_path)
    dest = tmp_path / "slots"
    first = seed_static_slots(installer_root=installer_root, slots_dir=dest)
    assert first  # something was seeded
    second = seed_static_slots(installer_root=installer_root, slots_dir=dest)
    assert second == []


def test_seed_static_slots_missing_source_logs_and_continues(tmp_path: Path) -> None:
    """A partially-shipped installer tree (missing one seed source) must
    not abort seeding the rest."""
    names = tuple(n for n in STATIC_SEED_SLOTS if n != "brain")
    installer_root = _fake_installer_root(tmp_path, names=names)
    dest = tmp_path / "slots"
    seeded = seed_static_slots(installer_root=installer_root, slots_dir=dest)
    assert "brain" not in seeded
    assert set(seeded) == set(names)


def test_seed_static_slots_creates_dest_dir(tmp_path: Path) -> None:
    installer_root = _fake_installer_root(tmp_path)
    dest = tmp_path / "nested" / "slots"
    assert not dest.exists()
    seed_static_slots(installer_root=installer_root, slots_dir=dest)
    assert dest.is_dir()


def test_static_seed_slots_matches_shipped_files() -> None:
    """Every name in STATIC_SEED_SLOTS must have a real TOML in
    installer/etc-hal0/slots/ — catches "added the name, forgot the
    file" drift (install.sh's list is the sibling that must also stay
    in sync, checked only by review since it's bash)."""
    repo_root = Path(__file__).resolve().parents[2]
    src_dir = repo_root / "installer" / "etc-hal0" / "slots"
    for name in STATIC_SEED_SLOTS:
        assert (src_dir / f"{name}.toml").is_file(), f"missing seed source for {name!r}"


def test_seed_static_slots_skips_identity_known_name(tmp_path: Path) -> None:
    """P3-runtime-db inc3 (the direct halo143 guard): a slot the identity store
    already tracks is SKIPPED even when no ``<name>.toml`` exists on disk —
    the id-keyed layout where the file lives at ``<id>.toml``. Without this the
    seeder re-materialises a stale ``brain.toml`` beside the migrated
    ``143.toml`` (the split-brain)."""
    installer_root = _fake_installer_root(tmp_path)
    dest = tmp_path / "slots"
    dest.mkdir()
    # 'brain' is known to identity but has NO brain.toml (it's id-keyed as
    # <id>.toml) — the seeder must not recreate brain.toml.
    seeded = seed_static_slots(
        installer_root=installer_root, slots_dir=dest, existing_names={"brain"}
    )
    assert "brain" not in seeded
    assert not (dest / "brain.toml").exists()
    # every OTHER seed (not identity-known, no file) is still copied.
    assert "agent" in seeded


def test_seed_static_slots_existing_names_default_empty_unchanged(tmp_path: Path) -> None:
    """Omitting existing_names keeps the pure file-existence behaviour."""
    installer_root = _fake_installer_root(tmp_path)
    dest = tmp_path / "slots"
    seeded = seed_static_slots(installer_root=installer_root, slots_dir=dest)
    assert set(seeded) == set(STATIC_SEED_SLOTS)


def test_seed_static_slots_default_args_seed_real_tree(tmp_path: Path) -> None:
    """Default args (no installer_root/slots_dir override) resolve the
    real repo tree in this dev/editable checkout and seed every slot."""
    dest = tmp_path / "slots"
    seeded = seed_static_slots(slots_dir=dest)
    assert set(seeded) == set(STATIC_SEED_SLOTS)


# ── #1827: the shipped seeds must actually deliver what they ask for ──────────
#
# The seeds are only half the contract; providers.container._resolve_context_size
# decides what a slot LAUNCHES with. rc.5 shipped three seeds asking for 65536
# that all launched at 32768, because the derived-window path applied a blanket
# dense cap underneath the seeded ceiling. 32768 is below Hermes' hard 64,000
# floor, so the bundled agent could not start on a fresh install at all.
#
# This walks the real shipped TOMLs through the real resolver with the model
# shape a fresh install actually produces (installer-pulled brain: a 131072
# native window, no explicit defaults.context_size), so either half drifting
# back down fails here rather than on a box.

#: ``agent/model_metadata.py::MINIMUM_CONTEXT_LENGTH`` in the bundled Hermes.
_HERMES_MIN_CONTEXT = 64_000

#: Seeds the bundled Hermes agent can be pointed at — ``hal0/agent`` resolves to
#: the ``agent`` slot and falls back to ``brain``; ``hal0/utility`` is the third
#: chat surface an operator is told to try.
_HERMES_REACHABLE_SEEDS = ("agent", "brain", "utility")


def _seed_ceiling(name: str) -> int | None:
    repo_root = Path(__file__).resolve().parents[2]
    raw = (repo_root / "installer" / "etc-hal0" / "slots" / f"{name}.toml").read_bytes()
    return tomllib.loads(raw.decode("utf-8")).get("model", {}).get("context_size")


@pytest.mark.parametrize("slot_name", _HERMES_REACHABLE_SEEDS)
def test_shipped_seed_resolves_above_the_hermes_floor(slot_name: str) -> None:
    ceiling = _seed_ceiling(slot_name)
    assert ceiling is not None, f"{slot_name}.toml lost its [model].context_size"
    assert ceiling >= _HERMES_MIN_CONTEXT, (
        f"{slot_name}.toml asks for {ceiling}, below Hermes' {_HERMES_MIN_CONTEXT} floor"
    )
    model_info = {
        "_model_key": BRAIN_MODEL_PORTABLE,
        "metadata": {"context_length": 131072},
        "defaults": {"context_size": None},
    }
    effective = _resolve_context_size(ceiling, model_info, slot_name=slot_name)
    assert effective == ceiling
    assert effective >= _HERMES_MIN_CONTEXT
