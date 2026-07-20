"""halo143 end-to-end: seed → fold → migrate → re-boot must NOT split-brain.

Reproduces the production incident (halo143): a name-keyed static seed slot
folded into the identity store, then migrated to id-keyed on disk, must survive
a re-boot's seed+reconcile+fold pass WITHOUT the seeder re-materialising a
``<name>.toml`` beside the migrated ``<id>.toml`` and WITHOUT the fold minting a
bogus digit-named identity row.

Runs entirely on the filesystem + injected SQLite stores (no podman/systemd);
``RecordingSlotArtifactOps`` swallows the unit/container renames.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config import paths
from hal0.install.static_seeds import STATIC_SEED_SLOTS, seed_static_slots
from hal0.ports.authority import PortAuthority
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.manager import SlotManager
from hal0.slots.migrate_id_keying import (
    RecordingSlotArtifactOps,
    migrate_slot_id_keying,
)
from hal0.slots.state import SlotState

# Keep the run fast + deterministic: a representative subset incl. the halo143
# slot 'brain'. Distinct ports so the fold's port-claim seeding never conflicts.
_SEEDS: tuple[str, ...] = ("brain", "agent", "flm")


@pytest.fixture
def hal0_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    return tmp_path


def _fake_installer_root(tmp_path: Path) -> Path:
    root = tmp_path / "installer-root"
    src_dir = root / "installer" / "etc-hal0" / "slots"
    src_dir.mkdir(parents=True)
    for i, name in enumerate(STATIC_SEED_SLOTS):
        (src_dir / f"{name}.toml").write_text(
            f'name = "{name}"\nport = {9000 + i}\ntype = "llm"\n',
            encoding="utf-8",
        )
    return root


def _manager(home: Path) -> SlotManager:
    db = home / "hal0.db"
    identity = SlotIdentityStore(db_path=db)
    authority = PortAuthority(pool=(9000, 9300), db_path=db)
    return SlotManager(identity_store=identity, port_authority=authority)


async def test_halo143_reboot_no_split_brain(hal0_home: Path, tmp_path: Path) -> None:
    installer_root = _fake_installer_root(tmp_path)
    slots_dir = paths.slots_config_dir()

    # ── 1. Fresh box: seed name-keyed + fold identity ─────────────────────────
    first = seed_static_slots(installer_root=installer_root, slots_dir=slots_dir)
    assert set(_SEEDS).issubset(set(first))
    for name in _SEEDS:
        assert (slots_dir / f"{name}.toml").exists()

    sm1 = _manager(hal0_home)
    # give each seeded slot a state.json so the migration has one to move.
    for name in _SEEDS:
        await sm1._transition(name, SlotState.OFFLINE, port=0, force=True)
    folded = await sm1.fold_identity()
    assert folded >= len(_SEEDS)
    ids_by_name = {
        name: sm1._identity.get_by_name(name).id  # type: ignore[union-attr]
        for name in _SEEDS
    }

    # ── 2. Operator runs the id-keying migration (downtime window) ────────────
    ops = RecordingSlotArtifactOps()
    report = migrate_slot_id_keying(
        identity=sm1._identity,
        config_dir=slots_dir,
        data_dir=paths.var_lib() / "slots",
        ops=ops,
        seeded_names=set(STATIC_SEED_SLOTS),
    )
    migrated_names = {m.name for m in report.migrations}
    assert set(_SEEDS).issubset(migrated_names)
    # after migration: <id>.toml exists, <name>.toml gone.
    for name, sid in ids_by_name.items():
        assert (slots_dir / f"{sid}.toml").exists()
        assert not (slots_dir / f"{name}.toml").exists()

    # ── 3. Re-boot: fresh manager, re-run reconcile → fold → seed ─────────────
    sm2 = _manager(hal0_home)
    await sm2.reconcile_unconfigured_slots()
    refolded = await sm2.fold_identity()
    existing_names = sm2.identity_names()
    reseeded = seed_static_slots(
        installer_root=installer_root,
        slots_dir=slots_dir,
        existing_names=existing_names,
    )

    # ── (a) NO <name>.toml recreated for any migrated slot ────────────────────
    for name in _SEEDS:
        assert name not in reseeded, f"{name} was wrongly re-seeded"
        assert not (slots_dir / f"{name}.toml").exists(), f"{name}.toml split-brain"

    # ── (b) exactly one identity row per slot; no bogus digit rows ────────────
    rows = sm2._identity.list_all()
    names = [r.name for r in rows]
    assert not any(n.isdigit() for n in names), f"digit-named identity row: {names}"
    for name in _SEEDS:
        assert names.count(name) == 1, f"{name} has {names.count(name)} rows"
    assert refolded == len(sm2._all_configured_slot_names())

    # ── (c) iter_configs is id-keyed with names intact ────────────────────────
    cfgs = await sm2.iter_configs()
    by_name = {c["name"] for c in cfgs}
    assert set(_SEEDS).issubset(by_name)
    assert not any(str(c["name"]).isdigit() for c in cfgs)
    # the config on disk is addressed by id.
    for name in _SEEDS:
        assert sm2._config_file_for(name) == slots_dir / f"{ids_by_name[name]}.toml"

    # ── (d) states hydrate from <id>/state.json ───────────────────────────────
    for name, sid in ids_by_name.items():
        assert sm2._state_file_for(name) == paths.slot_data_dir(str(sid)) / "state.json"
        snap = await sm2.status(name)
        assert snap.name == name
