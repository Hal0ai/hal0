"""Retired bundled agent skills are pruned; nothing else is touched."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.agents import bundled_skills
from hal0.agents.bundled_skills import (
    RETIRED_BUNDLED_FILES,
    RETIRED_BUNDLED_SKILLS,
    main,
    prune_retired_skills,
)
from hal0.agents.hermes_provision import _mirror_bundled_skills

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def box(tmp_path: Path) -> dict[str, Path]:
    ship = tmp_path / "usr-share-skills"
    mirror = tmp_path / "etc-agent-skills"
    for name in ("hal0-quantize", "hal0-bench", "my-own-skill"):
        (ship / name).mkdir(parents=True)
        (ship / name / "SKILL.md").write_text("x", encoding="utf-8")
    mirror.mkdir()
    for name in ("hal0-quantize", "hal0-bench"):
        (mirror / name).symlink_to(ship / name)
    return {"ship": ship, "mirror": mirror}


def test_retired_list_is_not_shipped() -> None:
    shipped = {p.name for p in (_REPO_ROOT / "installer" / "agent-skills").iterdir() if p.is_dir()}
    assert "hal0-quantize" in RETIRED_BUNDLED_SKILLS
    assert not (RETIRED_BUNDLED_SKILLS & shipped)


def test_prune_removes_only_retired_names(box: dict[str, Path]) -> None:
    removed = prune_retired_skills(ship_root=box["ship"], mirror=box["mirror"])
    assert sorted(Path(p).name for p in removed) == ["hal0-quantize", "hal0-quantize"]
    assert not (box["ship"] / "hal0-quantize").exists()
    assert not (box["mirror"] / "hal0-quantize").is_symlink()
    assert (box["ship"] / "hal0-bench").is_dir()
    assert (box["ship"] / "my-own-skill").is_dir()
    assert (box["mirror"] / "hal0-bench").is_symlink()


def test_retired_files_are_not_shipped_and_are_pruned(tmp_path: Path) -> None:
    for rel in RETIRED_BUNDLED_FILES:
        assert not (_REPO_ROOT / "installer" / "agent-skills" / rel).exists()
    ship = tmp_path / "ship"
    stale = ship / "hal0-service-management" / "scripts" / "check-service-env.sh"
    stale.parent.mkdir(parents=True)
    stale.write_text("x", encoding="utf-8")
    keep = ship / "hal0-service-management" / "SKILL.md"
    keep.write_text("x", encoding="utf-8")
    sibling = stale.parent / "other.sh"
    sibling.write_text("x", encoding="utf-8")
    removed = prune_retired_skills(ship_root=ship, mirror=tmp_path / "mirror")
    assert removed == [str(stale)]
    assert not stale.exists()
    assert keep.is_file()
    assert sibling.is_file()
    # A second call with the file already gone removes nothing.
    assert prune_retired_skills(ship_root=ship, mirror=tmp_path / "mirror") == []


def test_a_symlink_at_the_retired_file_is_kept(tmp_path: Path) -> None:
    ship = tmp_path / "ship"
    target = tmp_path / "target.sh"
    target.write_text("x", encoding="utf-8")
    stale = ship / "hal0-service-management" / "scripts" / "check-service-env.sh"
    stale.parent.mkdir(parents=True)
    stale.symlink_to(target)
    assert prune_retired_skills(ship_root=ship, mirror=tmp_path / "mirror") == []
    assert stale.is_symlink()
    assert target.is_file()


def test_a_directory_at_the_retired_file_is_left_alone(tmp_path: Path) -> None:
    ship = tmp_path / "ship"
    stale = ship / "hal0-service-management" / "scripts" / "check-service-env.sh"
    stale.mkdir(parents=True)
    inner = stale / "keep.txt"
    inner.write_text("x", encoding="utf-8")
    assert prune_retired_skills(ship_root=ship, mirror=tmp_path / "mirror") == []
    assert stale.is_dir()
    assert inner.read_text(encoding="utf-8") == "x"


def test_a_parent_symlink_out_of_the_ship_root_is_not_followed(tmp_path: Path) -> None:
    ship = tmp_path / "ship"
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "check-service-env.sh"
    victim.write_text("x", encoding="utf-8")
    (ship / "hal0-service-management").mkdir(parents=True)
    (ship / "hal0-service-management" / "scripts").symlink_to(outside, target_is_directory=True)
    assert prune_retired_skills(ship_root=ship, mirror=tmp_path / "mirror") == []
    assert victim.is_file()


def test_a_name_still_shipped_is_kept(box: dict[str, Path]) -> None:
    assert (
        prune_retired_skills(ship_root=box["ship"], mirror=box["mirror"], shipped=["hal0-quantize"])
        == []
    )
    assert (box["ship"] / "hal0-quantize").is_dir()


def test_without_ship_root_only_the_mirror_link_goes(box: dict[str, Path]) -> None:
    prune_retired_skills(ship_root=None, mirror=box["mirror"])
    assert not (box["mirror"] / "hal0-quantize").is_symlink()
    assert (box["ship"] / "hal0-quantize").is_dir()


def test_a_real_directory_in_the_mirror_is_never_removed(tmp_path: Path) -> None:
    mirror = tmp_path / "mirror"
    (mirror / "hal0-quantize").mkdir(parents=True)
    prune_retired_skills(ship_root=None, mirror=mirror)
    assert (mirror / "hal0-quantize").is_dir()


def test_provisioner_never_relinks_a_retired_skill(box: dict[str, Path]) -> None:
    result = _mirror_bundled_skills(box["ship"], box["mirror"])
    assert "hal0-quantize" not in result.linked
    assert not (box["mirror"] / "hal0-quantize").exists()
    assert (box["mirror"] / "hal0-bench").is_symlink()


def test_cli_reads_the_shipped_set(box: dict[str, Path], tmp_path: Path, capsys) -> None:
    src = tmp_path / "installer-agent-skills"
    (src / "hal0-bench").mkdir(parents=True)
    assert (
        main(
            [
                "--ship-root",
                str(box["ship"]),
                "--mirror-root",
                str(box["mirror"]),
                "--shipped-from",
                str(src),
            ]
        )
        == 0
    )
    assert "hal0-quantize" in capsys.readouterr().out
    assert not (box["ship"] / "hal0-quantize").exists()


def test_default_mirror_follows_hal0_home(tmp_hal0_home: str) -> None:
    assert bundled_skills.mirror_root() == Path(tmp_hal0_home) / "etc" / "hal0" / "agent-skills"
