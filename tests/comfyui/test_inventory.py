"""hal0.comfyui.inventory: the weight files on disk, and which catalog models own them (ADR-0036)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.comfyui import inventory, paths
from hal0.comfyui.catalog import CATALOG, ComponentKind, models_using

# What ``/api/comfyui/status`` has always reported under ``inventory``.
_STATUS_KEYS = {
    "checkpoints",
    "diffusion",
    "loras",
    "vae",
    "controlnet",
    "upscale",
    "text_encoders",
}


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


def _by_path(files: list[dict]) -> dict[str, dict]:
    return {f"{f['subdir']}/{f['relpath']}": f for f in files}


def test_list_files_reports_kind_subdir_relpath_size_and_owners() -> None:
    _touch("checkpoints", "sd_xl_base_1.0.safetensors", size=7)

    files = inventory.list_files()

    assert files == [
        {
            "kind": "checkpoint",
            "subdir": "checkpoints",
            "relpath": "sd_xl_base_1.0.safetensors",
            "size_bytes": 7,
            "models": ["sdxl-lightning"],
        }
    ]


def test_list_files_walks_every_component_kind_subdir() -> None:
    for kind in ComponentKind:
        _touch(kind.subdir, "weights.safetensors")

    files = inventory.list_files()

    assert {(f["kind"], f["subdir"]) for f in files} == {(k.value, k.subdir) for k in ComponentKind}


@pytest.mark.parametrize("subdir", ["unet", "clip", "controlnet", "embeddings", "style_models"])
def test_list_files_reports_the_hand_filled_dirs_with_the_subdir_as_kind(subdir: str) -> None:
    _touch(subdir, "mine.safetensors")

    (entry,) = inventory.list_files()

    assert entry["kind"] == subdir
    assert entry["subdir"] == subdir
    assert entry["models"] == []


def test_list_files_walks_nested_directories() -> None:
    relpath = "Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V2.0/high_noise_model.safetensors"
    _touch("loras", relpath)

    (entry,) = inventory.list_files()

    assert entry["relpath"] == relpath  # posix separators, relative to the subdir
    assert entry["models"], "the Wan LoRA belongs to a catalog model"


def test_a_stray_file_has_no_owner() -> None:
    _touch("checkpoints", "my_finetune.safetensors")

    (entry,) = inventory.list_files()

    assert entry["models"] == []


def test_a_shared_component_lists_every_model_that_loads_it() -> None:
    component = next(c for m in CATALOG.values() for c in m.components if len(models_using(c)) > 1)
    _touch(component.kind.subdir, component.relpath)

    (entry,) = inventory.list_files()

    assert entry["models"] == models_using(component)
    assert len(entry["models"]) > 1


def test_owners_are_matched_by_full_path_not_basename() -> None:
    """A same-named file in another directory is not the catalog's file."""
    component = CATALOG["sdxl-lightning"].components[0]
    _touch("loras", component.relpath)  # right name, wrong directory

    (entry,) = inventory.list_files()

    assert entry["subdir"] == "loras"
    assert entry["models"] == []


def test_list_files_skips_dotfiles_and_dot_directories() -> None:
    _touch("checkpoints", "kept.safetensors")
    _touch("checkpoints", ".hidden.safetensors")
    _touch("checkpoints", ".cache/huggingface/blob.safetensors")
    _touch("loras", ".hf_stage_wan22/partial.safetensors")
    _touch("loras", "nested/.hf_stage_x/partial.safetensors")

    assert list(_by_path(inventory.list_files())) == ["checkpoints/kept.safetensors"]


def test_list_files_ignores_staging_dirs_at_the_models_root() -> None:
    """The old installer staged under ``models/.hf_stage_*``; those are not a model subdir."""
    _touch(".hf_stage_qwen", "split_files/model.safetensors")
    _touch("checkpoints", "kept.safetensors")

    assert list(_by_path(inventory.list_files())) == ["checkpoints/kept.safetensors"]


def test_list_files_ignores_directories_that_are_not_model_subdirs() -> None:
    _touch("vae_approx", "taesd.safetensors")
    _touch("somewhere_else", "x.safetensors")

    assert inventory.list_files() == []


def test_list_files_counts_only_weight_extensions() -> None:
    for name in (
        "a.safetensors",
        "b.CKPT",
        "c.pt",
        "d.pth",
        "e.gguf",
        "f.bin",
        "g.sft",
        "config.json",
        "notes.txt",
        "weights.safetensors.part",
        "weights.safetensors.resume",
    ):
        _touch("checkpoints", name)

    names = {f["relpath"] for f in inventory.list_files()}

    assert names == {"a.safetensors", "b.CKPT", "c.pt", "d.pth", "e.gguf", "f.bin", "g.sft"}


def test_list_files_does_not_follow_a_symlinked_directory_out_of_the_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.safetensors").write_bytes(b"x")
    (paths.models_root() / "checkpoints").mkdir(parents=True)
    (paths.models_root() / "checkpoints" / "escape").symlink_to(outside, target_is_directory=True)
    _touch("checkpoints", "kept.safetensors")

    assert list(_by_path(inventory.list_files())) == ["checkpoints/kept.safetensors"]


def test_list_files_skips_a_model_subdir_that_is_itself_a_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.safetensors").write_bytes(b"x")
    paths.models_root().mkdir(parents=True)
    (paths.models_root() / "vae").symlink_to(outside, target_is_directory=True)

    assert inventory.list_files() == []


def test_list_files_lists_a_symlinked_weight_file_and_skips_a_dangling_one(
    tmp_path: Path,
) -> None:
    """ComfyUI loads a weight symlinked in from other storage, so the listing shows it."""
    real = tmp_path / "elsewhere.safetensors"
    real.write_bytes(b"x" * 9)
    (paths.models_root() / "checkpoints").mkdir(parents=True)
    (paths.models_root() / "checkpoints" / "linked.safetensors").symlink_to(real)
    (paths.models_root() / "checkpoints" / "dangling.safetensors").symlink_to(tmp_path / "gone")

    files = _by_path(inventory.list_files())

    assert list(files) == ["checkpoints/linked.safetensors"]
    assert files["checkpoints/linked.safetensors"]["size_bytes"] == 9


def test_list_files_is_empty_when_the_models_root_is_absent() -> None:
    assert not paths.models_root().exists()
    assert inventory.list_files() == []


def test_list_files_is_sorted_by_subdir_then_relpath() -> None:
    _touch("vae", "b.safetensors")
    _touch("checkpoints", "z.safetensors")
    _touch("checkpoints", "a.safetensors")

    assert list(_by_path(inventory.list_files())) == [
        "checkpoints/a.safetensors",
        "checkpoints/z.safetensors",
        "vae/b.safetensors",
    ]


# ── status counts ───────────────────────────────────────────────────────────


def test_status_counts_is_none_when_the_models_root_is_absent() -> None:
    assert inventory.status_counts([]) is None


def test_status_counts_has_exactly_todays_keys_all_zero_for_an_empty_tree() -> None:
    paths.models_root().mkdir(parents=True)

    assert inventory.status_counts(inventory.list_files()) == dict.fromkeys(_STATUS_KEYS, 0)


def test_status_counts_groups_subdirs_as_the_status_card_always_has() -> None:
    for subdir, n in {
        "checkpoints": 2,
        "diffusion_models": 3,
        "unet": 1,  # folded into diffusion
        "loras": 4,
        "vae": 5,
        "controlnet": 6,
        "upscale_models": 7,
        "text_encoders": 8,
        "clip": 2,  # folded into text_encoders
    }.items():
        for i in range(n):
            _touch(subdir, f"m{i}.safetensors")
    # Directories the card has no row for do not leak into any count.
    _touch("clip_vision", "v.safetensors")
    _touch("latent_upscale_models", "u.safetensors")
    _touch("embeddings", "e.safetensors")
    _touch("style_models", "s.safetensors")

    counts = inventory.status_counts(inventory.list_files())

    assert counts == {
        "checkpoints": 2,
        "diffusion": 4,
        "loras": 4,
        "vae": 5,
        "controlnet": 6,
        "upscale": 7,
        "text_encoders": 10,
    }


def test_status_counts_counts_nested_lora_files() -> None:
    _touch("loras", "top.safetensors")
    _touch("loras", "wan/high_noise_model.safetensors")
    _touch("loras", "wan/low_noise_model.safetensors")

    counts = inventory.status_counts(inventory.list_files())

    assert counts is not None
    assert counts["loras"] == 3
