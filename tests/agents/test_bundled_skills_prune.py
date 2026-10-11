"""Retired bundled agent skills are pruned; nothing else is touched."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.agents import bundled_skills
from hal0.agents.bundled_skills import RETIRED_BUNDLED_SKILLS, main, prune_retired_skills
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
