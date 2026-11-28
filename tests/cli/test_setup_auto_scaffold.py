"""``hal0 setup --auto`` scaffolds only the slots a fresh install needs."""

from __future__ import annotations

import textwrap

from hal0.cli import setup_command
from hal0.cli.setup_command import (
    _BRAIN_SLOT,
    _SCAFFOLD_CAPS,
    _SETUP_SLOTS,
    build_auto_selections,
)
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.answers import load_answers
from hal0.install.static_seeds import FRESH_INSTALL_SEEDS, SEED_TEMPLATES

_STORAGE = "/var/lib/hal0/models"


def _hw() -> HardwareInfo:
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=96 * 1024,
        ram_available_mb=96 * 1024,
        unified_memory_mb=96 * 1024,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
        npu=NPUInfo(present=True),
    )


def _shape(sel) -> set[tuple[str, str, int, str | None]]:
    return {(s.capability, s.slot_name, s.port, s.model_id) for s in sel.slots}


def test_auto_scaffolds_agent_and_brain_only():
    for with_ext in (True, False):
        sel = build_auto_selections(_hw(), storage_dir=_STORAGE, with_extensions=with_ext)
        assert {s.slot_name for s in sel.slots} == {"agent", "brain"}
        assert all(s.model_id is None for s in sel.slots)


def test_auto_never_touches_existing_slot_files(tmp_path, monkeypatch):
    slots_dir = tmp_path / "slots"
    slots_dir.mkdir()
    names = sorted(SEED_TEMPLATES)
    assert len(names) == 10
    before = {}
    for n in names:
        f = slots_dir / f"{n}.toml"
        f.write_bytes(f'# operator edit of {n}\nname = "{n}"\n'.encode())
        before[f.name] = f.read_bytes()
    monkeypatch.setattr(setup_command.paths, "slots_config_dir", lambda: slots_dir)

    existing = setup_command._existing_slot_names()
    assert existing == frozenset(names)
    sel = build_auto_selections(_hw(), storage_dir=_STORAGE, existing_slots=existing)

    assert sel.slots == []
    assert sorted(p.name for p in slots_dir.iterdir()) == sorted(before)
    assert {p.name: p.read_bytes() for p in slots_dir.iterdir()} == before


def test_answers_slots_entries_still_scaffolded(tmp_path):
    doc = tmp_path / "answers.yaml"
    doc.write_text(
        textwrap.dedent(
            """
            version: 1
            model_store: { path: /var/lib/hal0/models }
            slots:
              - { capability: chat, name: agent, port: 8081, model_id: null }
              - { capability: coder, name: coder, port: 8082, model_id: null }
            """
        )
    )
    sel = load_answers(str(doc), _hw())
    assert _shape(sel) == {
        ("chat", "agent", 8081, None),
        ("coder", "coder", 8082, None),
    }


def test_answers_without_slots_matches_auto_default(tmp_path):
    doc = tmp_path / "answers.yaml"
    doc.write_text("version: 1\nmodel_store: { path: /var/lib/hal0/models }\n")
    loaded = load_answers(str(doc), _hw())
    auto = {s.slot_name for s in build_auto_selections(_hw(), storage_dir=_STORAGE).slots}
    # No ``slots`` section means no scaffold entries of its own (as before):
    # the fresh set then comes from the seed files, the same agent/brain/memory
    # that --auto ends with. Nothing outside the --auto default is created.
    assert {s.slot_name for s in loaded.slots} <= auto
    assert {s.slot_name for s in loaded.slots} <= set(FRESH_INSTALL_SEEDS)


def test_agent_extension_alone_does_not_create_coder():
    sel = build_auto_selections(_hw(), storage_dir=_STORAGE, with_extensions=True)
    assert sel.extensions["hermes"] is True
    assert "coder" not in {s.slot_name for s in sel.slots}


def test_scaffold_names_are_within_fresh_list_or_selected():
    """Lockstep: a name added to the scaffold list must be in the fresh list."""
    scaffolded = {_SETUP_SLOTS[cap][0] for cap in _SCAFFOLD_CAPS} | {_BRAIN_SLOT[0]}
    assert scaffolded <= set(FRESH_INSTALL_SEEDS), (
        f"--auto scaffolds {sorted(scaffolded - set(FRESH_INSTALL_SEEDS))} "
        "which are not in FRESH_INSTALL_SEEDS"
    )
    built = {s.slot_name for s in build_auto_selections(_hw(), storage_dir=_STORAGE).slots}
    assert built <= set(FRESH_INSTALL_SEEDS)
