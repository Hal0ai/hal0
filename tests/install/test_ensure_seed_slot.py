"""``ensure_seed_slot``: create any one of the ten seeds on request."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config.schema import HardwareInfo
from hal0.install.static_seeds import (
    SEED_TEMPLATES,
    SeedResult,
    add_seed_tombstone,
    ensure_seed_slot,
    read_seed_tombstones,
    seed_static_slots,
)


@pytest.fixture(autouse=True)
def _no_gpu_lanes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hal0.install.profile_derive.rocm_lane_present", lambda *a, **k: False)
    monkeypatch.setattr(
        "hal0.install.profile_derive.default_image_serves_vulkan_lane", lambda *a, **k: True
    )


def _root(tmp_path: Path) -> Path:
    """A fake installer tree whose seeds ship the reference ``gpu-rocm`` device."""
    src_dir = tmp_path / "installer-root" / "installer" / "etc-hal0" / "slots"
    src_dir.mkdir(parents=True)
    for name in SEED_TEMPLATES:
        (src_dir / f"{name}.toml").write_text(
            f'name = "{name}"\ndevice = "gpu-rocm"\n\n[model]\nport = 9000\n', encoding="utf-8"
        )
    return tmp_path / "installer-root"


def _device(path: Path) -> str:
    return tomllib.loads(path.read_text(encoding="utf-8"))["device"]


def test_creates_missing_seed(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    result = ensure_seed_slot(
        "rerank", installer_root=_root(tmp_path), slots_dir=dest, hw=HardwareInfo(gpus=[])
    )
    assert result is SeedResult.CREATED
    assert (dest / "rerank.toml").is_file()


def test_existing_file_untouched(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    (dest / "embed.toml").write_bytes(b'name = "embed"\ndevice = "gpu-rocm"\n# operator edit\n')
    before = (dest / "embed.toml").read_bytes()
    result = ensure_seed_slot(
        "embed", installer_root=_root(tmp_path), slots_dir=dest, hw=HardwareInfo(gpus=[])
    )
    assert result is SeedResult.EXISTS
    assert (dest / "embed.toml").read_bytes() == before


def test_explicit_ask_clears_tombstone_and_creates(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    add_seed_tombstone("tts", slots_dir=dest)
    add_seed_tombstone("brain", slots_dir=dest)
    result = ensure_seed_slot(
        "tts", installer_root=_root(tmp_path), slots_dir=dest, hw=HardwareInfo(gpus=[])
    )
    assert result is SeedResult.CREATED
    assert (dest / "tts.toml").is_file()
    assert read_seed_tombstones(slots_dir=dest) == frozenset({"brain"})


def test_respects_tombstone_when_told(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    add_seed_tombstone("tts", slots_dir=dest)
    result = ensure_seed_slot(
        "tts",
        installer_root=_root(tmp_path),
        slots_dir=dest,
        hw=HardwareInfo(gpus=[]),
        clear_tombstone=False,
    )
    assert result is SeedResult.TOMBSTONED
    assert not (dest / "tts.toml").exists()
    assert "tts" in read_seed_tombstones(slots_dir=dest)


def test_default_path_never_brings_back_a_tombstoned_fresh_seed(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    root = _root(tmp_path)
    add_seed_tombstone("brain", slots_dir=dest)
    seeded = seed_static_slots(installer_root=root, slots_dir=dest, hw=HardwareInfo(gpus=[]))
    assert "brain" not in seeded
    assert not (dest / "brain.toml").exists()
    assert "brain" in read_seed_tombstones(slots_dir=dest)
    # An explicit ask is the only way back.
    result = ensure_seed_slot(
        "brain", installer_root=root, slots_dir=dest, hw=HardwareInfo(gpus=[])
    )
    assert result is SeedResult.CREATED
    assert read_seed_tombstones(slots_dir=dest) == frozenset()


@pytest.mark.parametrize("name", ["embed", "rerank"])
def test_cpu_only_hardware_gets_cpu_device(tmp_path: Path, name: str) -> None:
    dest = tmp_path / "slots"
    ensure_seed_slot(name, installer_root=_root(tmp_path), slots_dir=dest, hw=HardwareInfo(gpus=[]))
    assert _device(dest / f"{name}.toml") == "cpu"


def test_non_llama_seed_keeps_its_template_device(tmp_path: Path) -> None:
    root = _root(tmp_path)
    tmpl = root / "installer/etc-hal0/slots/tts.toml"
    tmpl.write_text('name = "tts"\ndevice = "cpu"\n', encoding="utf-8")
    dest = tmp_path / "slots"
    ensure_seed_slot("tts", installer_root=root, slots_dir=dest, hw=HardwareInfo(gpus=[]))
    assert (dest / "tts.toml").read_bytes() == tmpl.read_bytes()


def test_unknown_name_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="utility"):
        ensure_seed_slot(
            "utility",
            installer_root=_root(tmp_path),
            slots_dir=tmp_path / "slots",
            hw=HardwareInfo(gpus=[]),
        )


def test_seed_static_slots_uses_the_same_copy_path(tmp_path: Path) -> None:
    root = _root(tmp_path)
    hw = HardwareInfo(gpus=[])
    one, many = tmp_path / "one", tmp_path / "many"
    ensure_seed_slot("memory", installer_root=root, slots_dir=one, hw=hw)
    seed_static_slots(installer_root=root, slots_dir=many, hw=hw, names=["memory"])
    assert (one / "memory.toml").read_bytes() == (many / "memory.toml").read_bytes()
    assert _device(one / "memory.toml") == "cpu"


def test_file_created_before_publish_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    dest = tmp_path / "slots"
    root = _root(tmp_path)
    real_link = os.link

    def racing_link(src: object, dst: object, *a: object, **k: object) -> None:
        Path(str(dst)).write_bytes(b"winner\n")  # a concurrent creator got there first
        real_link(src, dst, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr("hal0.install.static_seeds.os.link", racing_link)
    result = ensure_seed_slot(
        "embed", installer_root=root, slots_dir=dest, hw=HardwareInfo(gpus=[])
    )
    assert result is SeedResult.EXISTS
    assert (dest / "embed.toml").read_bytes() == b"winner\n"
    assert [p.name for p in dest.iterdir()] == ["embed.toml"]


def test_published_file_already_carries_the_derived_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    dest = tmp_path / "slots"
    seen: list[str] = []
    real_link = os.link

    def spying_link(src: object, dst: object, *a: object, **k: object) -> None:
        seen.append(_device(Path(str(src))))
        real_link(src, dst, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr("hal0.install.static_seeds.os.link", spying_link)
    ensure_seed_slot(
        "embed", installer_root=_root(tmp_path), slots_dir=dest, hw=HardwareInfo(gpus=[])
    )
    assert seen == ["cpu"]  # derived before publication, never the template's gpu-rocm
    assert _device(dest / "embed.toml") == "cpu"


def test_no_temp_file_left_after_success(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    ensure_seed_slot(
        "embed", installer_root=_root(tmp_path), slots_dir=dest, hw=HardwareInfo(gpus=[])
    )
    assert [p.name for p in dest.iterdir()] == ["embed.toml"]


def test_no_temp_file_left_after_derive_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "slots"

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("derive failed")

    monkeypatch.setattr("hal0.install.static_seeds.apply_derived_seed_devices", boom)
    with pytest.raises(RuntimeError, match="derive failed"):
        ensure_seed_slot(
            "embed", installer_root=_root(tmp_path), slots_dir=dest, hw=HardwareInfo(gpus=[])
        )
    assert list(dest.iterdir()) == []
