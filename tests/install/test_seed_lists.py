"""The two seed lists: templates that exist vs slots a fresh install creates."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hal0.install import static_seeds
from hal0.install.static_seeds import (
    FRESH_INSTALL_SEEDS,
    SEED_TEMPLATES,
    read_seed_tombstones,
    seed_static_slots,
)
from hal0.slots.manager import SlotManager

REPO_ROOT = Path(__file__).resolve().parents[2]
TEN = ("flm", "tts", "rerank", "memory", "img", "agent", "brain", "qwen3tts", "coder", "embed")


def _fake_installer_root(tmp_path: Path) -> Path:
    root = tmp_path / "installer-root"
    src_dir = root / "installer" / "etc-hal0" / "slots"
    src_dir.mkdir(parents=True)
    for name in SEED_TEMPLATES:
        (src_dir / f"{name}.toml").write_text(f'name = "{name}"\nport = 9000\n', encoding="utf-8")
    return root


def test_fresh_list_is_three_and_templates_are_ten() -> None:
    assert FRESH_INSTALL_SEEDS == ("agent", "brain", "memory")
    assert SEED_TEMPLATES == TEN
    assert not hasattr(static_seeds, "STATIC_SEED_SLOTS")


def test_installer_loop_matches_fresh_list() -> None:
    text = (REPO_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    loops = re.findall(r"^for seed_slot in ([^;]+); do\s*$", text, flags=re.MULTILINE)
    assert len(loops) == 1
    assert set(loops[0].split()) == set(FRESH_INSTALL_SEEDS)


def test_default_seeding_creates_only_the_fresh_three(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    seeded = seed_static_slots(installer_root=_fake_installer_root(tmp_path), slots_dir=dest)
    assert set(seeded) == set(FRESH_INSTALL_SEEDS)
    assert sorted(p.name for p in dest.glob("*.toml")) == [
        "agent.toml",
        "brain.toml",
        "memory.toml",
    ]


def test_existing_ten_untouched(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    before = {}
    for name in TEN:
        body = f'name = "{name}"\nport = 4242\n# operator edit for {name}\n'
        (dest / f"{name}.toml").write_text(body, encoding="utf-8")
        before[name] = (dest / f"{name}.toml").read_bytes()
    seeded = seed_static_slots(installer_root=_fake_installer_root(tmp_path), slots_dir=dest)
    assert seeded == []
    assert {p.name for p in dest.glob("*.toml")} == {f"{n}.toml" for n in TEN}
    for name in TEN:
        assert (dest / f"{name}.toml").read_bytes() == before[name]


@pytest.mark.parametrize("tombstoned", [False, True])
def test_deleted_seed_not_recreated_at_boot(tmp_path: Path, tombstoned: bool) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    for name in TEN:
        if name != "tts":
            (dest / f"{name}.toml").write_text(f'name = "{name}"\n', encoding="utf-8")
    if tombstoned:
        static_seeds.add_seed_tombstone("tts", slots_dir=dest)
    seed_static_slots(installer_root=_fake_installer_root(tmp_path), slots_dir=dest)
    assert not (dest / "tts.toml").exists()


def test_unknown_name_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="utility"):
        seed_static_slots(
            installer_root=_fake_installer_root(tmp_path),
            slots_dir=tmp_path / "slots",
            names=["agent", "utility"],
        )
    assert not (tmp_path / "slots").exists()


def test_explicit_names_can_seed_a_non_fresh_template(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    seeded = seed_static_slots(
        installer_root=_fake_installer_root(tmp_path), slots_dir=dest, names=["tts"]
    )
    assert seeded == ["tts"]


async def test_delete_of_non_fresh_template_still_writes_tombstone(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "tts" not in FRESH_INSTALL_SEEDS
    monkeypatch.setattr("hal0.slots.routing.shutil.which", lambda name: None)
    sm = SlotManager()
    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "tts.toml").write_text(
        'name = "tts"\nport = 8084\nprovider = "llama-server"\n[model]\ndefault = "x"\n'
    )
    await sm.delete("tts")
    assert "tts" in read_seed_tombstones()


def test_is_seed_true_for_all_ten() -> None:
    assert set(SlotManager.seeded_slots(include_npu=False)) == set(TEN)
    assert set(SlotManager.BUILTIN_SLOTS) == set(TEN)
