"""hal0.comfyui.inventory.delete_file: removing a file nobody in the catalog owns (ADR-0037).

The delete is addressed by ``(subdir, relpath)`` and is the one place a client-supplied path
becomes an ``unlink``, so most of these tests are about what it must refuse, and each refusal
also checks the file it was aimed at is still there.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from hal0.comfyui import inventory, paths
from hal0.comfyui.catalog import CATALOG, component_path

_SDXL = "sdxl-lightning"
_SDXL_CHECKPOINT = "sd_xl_base_1.0.safetensors"


@pytest.fixture(autouse=True)
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "comfyui-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    assert paths.models_root().is_relative_to(tmp_path)
    return root


def _touch(subdir: str, relpath: str, size: int = 4) -> Path:
    path = paths.models_root() / subdir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_delete_file_ok() -> None:
    mine = _touch("checkpoints", "mine.safetensors")
    other = _touch("checkpoints", "other.safetensors")

    assert inventory.delete_file("checkpoints", "mine.safetensors") is None

    assert not mine.exists()
    assert other.exists()


def test_delete_file_ok_nested_leaves_the_directory() -> None:
    nested = _touch("loras", "styles/mine.safetensors")

    inventory.delete_file("loras", "styles/mine.safetensors")

    assert not nested.exists()
    assert nested.parent.is_dir()


@pytest.mark.parametrize("subdir", sorted(set(paths.SUBDIRS.values())))
def test_delete_file_accepts_every_scanned_subdir(subdir: str) -> None:
    mine = _touch(subdir, "mine.safetensors")

    inventory.delete_file(subdir, "mine.safetensors")

    assert not mine.exists()


@pytest.mark.parametrize(
    "relpath",
    [
        "../x",
        "/etc/passwd",
        "a/../../x",
        "a/..",
        "..",
        ".",
        "a/./b.safetensors",
        "",
        ".hidden.safetensors",
        ".hf_stage_sdxl/sd_xl_base_1.0.safetensors",
        "sub/.hf_stage_x/part.safetensors",
        "a\x00b.safetensors",
    ],
)
def test_delete_file_rejects_traversal(relpath: str) -> None:
    partial = _touch("checkpoints", ".hf_stage_sdxl/sd_xl_base_1.0.safetensors")
    target = _touch("checkpoints", "a/b.safetensors")
    outside = paths.models_root() / "x"
    outside.write_bytes(b"keep")

    with pytest.raises(inventory.FilePathInvalid) as exc:
        inventory.delete_file("checkpoints", relpath)

    assert exc.value.code == "comfyui.file_path_invalid"
    assert exc.value.status == 400
    assert partial.exists()
    assert target.exists()
    assert outside.read_bytes() == b"keep"


def test_delete_file_traversal_cannot_reach_a_sibling_subdir() -> None:
    sibling = _touch("loras", "keep.safetensors")
    _touch("checkpoints", "mine.safetensors")

    with pytest.raises(inventory.FilePathInvalid):
        inventory.delete_file("checkpoints", "../loras/keep.safetensors")

    assert sibling.exists()


@pytest.mark.parametrize("subdir", ["nope", "", "..", "../etc", "checkpoints/sub", "/checkpoints"])
def test_delete_file_rejects_an_unknown_subdir(subdir: str) -> None:
    mine = _touch("checkpoints", "mine.safetensors")

    with pytest.raises(inventory.FilePathInvalid) as exc:
        inventory.delete_file(subdir, "mine.safetensors")

    assert exc.value.code == "comfyui.file_path_invalid"
    assert mine.exists()


def test_delete_file_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "elsewhere" / "real.safetensors"
    real.parent.mkdir()
    real.write_bytes(b"real")
    link = paths.models_root() / "checkpoints" / "linked.safetensors"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    with pytest.raises(inventory.FileIsSymlink) as exc:
        inventory.delete_file("checkpoints", "linked.safetensors")

    assert exc.value.code == "comfyui.file_symlink"
    assert exc.value.status == 400
    assert link.is_symlink()
    assert real.read_bytes() == b"real"


def test_delete_file_rejects_a_dangling_symlink(tmp_path: Path) -> None:
    link = paths.models_root() / "checkpoints" / "gone.safetensors"
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "missing.safetensors")

    with pytest.raises(inventory.FileIsSymlink):
        inventory.delete_file("checkpoints", "gone.safetensors")

    assert link.is_symlink()


def test_delete_file_rejects_a_path_through_a_symlinked_directory_that_leaves_the_tree(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.safetensors"
    victim.write_bytes(b"keep")
    (paths.models_root() / "checkpoints").mkdir(parents=True)
    (paths.models_root() / "checkpoints" / "door").symlink_to(outside, target_is_directory=True)

    with pytest.raises(inventory.FilePathInvalid) as exc:
        inventory.delete_file("checkpoints", "door/victim.safetensors")

    assert exc.value.code == "comfyui.file_path_invalid"
    assert victim.read_bytes() == b"keep"


def test_delete_file_rejects_a_catalog_file_reached_through_a_symlinked_directory() -> None:
    """An alias that stays inside the subdir must not slip past the ownership check."""
    owned = _touch("checkpoints", _SDXL_CHECKPOINT)
    (paths.models_root() / "checkpoints" / "alias").symlink_to(
        paths.models_root() / "checkpoints", target_is_directory=True
    )

    with pytest.raises(inventory.FilePathInvalid):
        inventory.delete_file("checkpoints", f"alias/{_SDXL_CHECKPOINT}")

    assert owned.exists()


def test_delete_file_rejects_a_symlinked_subdir(tmp_path: Path) -> None:
    """The scan skips a subdirectory that is a link, so nothing in it was ever listed."""
    real = tmp_path / "real-loras"
    real.mkdir()
    keep = real / "keep.safetensors"
    keep.write_bytes(b"keep")
    paths.models_root().mkdir(parents=True)
    (paths.models_root() / "loras").symlink_to(real, target_is_directory=True)

    with pytest.raises(inventory.FilePathInvalid):
        inventory.delete_file("loras", "keep.safetensors")

    assert keep.exists()


def test_delete_file_409_catalog_owned() -> None:
    owned = _touch("checkpoints", _SDXL_CHECKPOINT)

    with pytest.raises(inventory.FileCatalogOwned) as exc:
        inventory.delete_file("checkpoints", _SDXL_CHECKPOINT)

    assert exc.value.code == "comfyui.file_catalog_owned"
    assert exc.value.status == 409
    assert exc.value.details == {"models": [_SDXL]}
    assert owned.exists()


def test_delete_file_names_every_owner_the_listing_shows() -> None:
    """The refusal and ``GET /files`` read one ownership map, so they cannot disagree."""
    root = paths.models_root()
    owners = inventory.owners(root)
    shared = next(
        component
        for model in CATALOG.values()
        for component in model.components
        if len(owners[component_path(component, root)]) > 1
    )
    dest = shared.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"w")
    (listed,) = [f for f in inventory.list_files() if f["relpath"] == shared.relpath]

    with pytest.raises(inventory.FileCatalogOwned) as exc:
        inventory.delete_file(shared.kind.subdir, shared.relpath)

    assert len(listed["models"]) > 1
    assert exc.value.details == {"models": listed["models"]}


def test_delete_file_403_on_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = _touch("checkpoints", "mine.safetensors")

    def denied(self: Path, *args: Any, **kwargs: Any) -> None:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "unlink", denied)

    with pytest.raises(inventory.FileDeleteDenied) as exc:
        inventory.delete_file("checkpoints", "mine.safetensors")

    assert exc.value.code == "comfyui.file_permission"
    assert exc.value.status == 403
    assert mine.exists()


def test_delete_file_403_when_the_directory_cannot_be_searched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _touch("checkpoints", "mine.safetensors")
    real_lstat = os.lstat

    def lstat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if str(path).endswith("mine.safetensors"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)

    with pytest.raises(inventory.FileDeleteDenied):
        inventory.delete_file("checkpoints", "mine.safetensors")


def test_delete_file_404() -> None:
    with pytest.raises(inventory.FileMissing) as exc:
        inventory.delete_file("checkpoints", "nope.safetensors")

    assert exc.value.code == "comfyui.file_not_found"
    assert exc.value.status == 404


def test_delete_file_404_when_the_subdir_does_not_exist_yet() -> None:
    with pytest.raises(inventory.FileMissing):
        inventory.delete_file("loras", "nope.safetensors")


def test_delete_file_404_when_the_path_is_a_directory() -> None:
    directory = _touch("checkpoints", "dir/inner.safetensors").parent

    with pytest.raises(inventory.FileMissing):
        inventory.delete_file("checkpoints", "dir")

    assert directory.is_dir()


def test_delete_file_404_when_a_parent_is_a_file() -> None:
    _touch("checkpoints", "plain.safetensors")

    with pytest.raises(inventory.FileMissing):
        inventory.delete_file("checkpoints", "plain.safetensors/child.safetensors")


def test_delete_file_404_when_it_vanishes_before_the_unlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _touch("checkpoints", "mine.safetensors")

    def gone(self: Path, *args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError(2, "No such file", str(self))

    monkeypatch.setattr(Path, "unlink", gone)

    with pytest.raises(inventory.FileMissing):
        inventory.delete_file("checkpoints", "mine.safetensors")


# ── the listing's side of the same contract ─────────────────────────────────


def test_owners_is_the_map_the_listing_reads() -> None:
    root = paths.models_root()
    _touch("checkpoints", _SDXL_CHECKPOINT)

    owners = inventory.owners(root)
    (entry,) = inventory.list_files()

    assert owners[root / "checkpoints" / _SDXL_CHECKPOINT] == entry["models"] == [_SDXL]


def test_list_files_flags_a_symlinked_weight(tmp_path: Path) -> None:
    _touch("checkpoints", "plain.safetensors")
    real = tmp_path / "elsewhere" / "real.safetensors"
    real.parent.mkdir()
    real.write_bytes(b"real")
    (paths.models_root() / "checkpoints" / "linked.safetensors").symlink_to(real)

    rows = {f["relpath"]: f for f in inventory.list_files()}

    assert rows["plain.safetensors"]["symlink"] is False
    assert rows["linked.safetensors"]["symlink"] is True


def test_ckpt_api_id_is_the_listing_id_without_the_suffix() -> None:
    assert inventory.ckpt_api_id("a.safetensors") == "ckpt/a"
    assert inventory.ckpt_api_id("sub/a.safetensors") == "ckpt/sub/a"
