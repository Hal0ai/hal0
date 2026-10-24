"""hal0.comfyui.paths: the one owner of every ComfyUI data path (ADR-0036)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.comfyui import paths as comfyui_paths
from hal0.config import paths as config_paths


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A model store under tmp_path, with no data-root override in effect."""
    monkeypatch.delenv("HAL0_COMFYUI_DATA_ROOT", raising=False)
    root = tmp_path / "store"
    monkeypatch.setattr(config_paths, "model_store_root", lambda: str(root))
    return root


def test_paths_follow_model_store(store: Path) -> None:
    assert comfyui_paths.data_root() == store / "comfyui"
    assert comfyui_paths.models_root() == store / "comfyui" / "models"
    assert comfyui_paths.workflows_dir() == store / "comfyui" / "workflows"
    assert comfyui_paths.user_dir() == store / "comfyui" / "user"
    assert comfyui_paths.custom_nodes_dir() == store / "comfyui" / "custom_nodes"
    assert comfyui_paths.extra_model_paths_file() == store / "comfyui" / "extra_model_paths.yaml"


def test_data_root_env_override(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "x"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(override))
    assert comfyui_paths.data_root() == override
    assert comfyui_paths.models_root() == override / "models"
    assert comfyui_paths.workflows_dir() == override / "workflows"
    assert comfyui_paths.user_dir() == override / "user"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_data_root_env_falls_back_to_model_store(
    store: Path, monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", blank)
    assert comfyui_paths.data_root() == store / "comfyui"


def test_data_root_env_value_is_stripped(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", f"  {tmp_path / 'padded'}  ")
    assert comfyui_paths.data_root() == tmp_path / "padded"


def test_removed_dir_env_vars_are_not_read(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMFYUI_MODELS_DIR", str(tmp_path / "old-models"))
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(tmp_path / "old-workflows"))
    monkeypatch.setenv("COMFYUI_DATA_DIR", str(tmp_path / "old-data"))
    assert comfyui_paths.models_root() == store / "comfyui" / "models"
    assert comfyui_paths.workflows_dir() == store / "comfyui" / "workflows"
    assert comfyui_paths.data_root() == store / "comfyui"


def test_subdirs_cover_every_component_kind_and_the_extra_dirs() -> None:
    kind_subdirs = {
        "checkpoints",
        "diffusion_models",
        "text_encoders",
        "vae",
        "loras",
        "upscale_models",
        "clip_vision",
        "latent_upscale_models",
    }
    extra_subdirs = {"unet", "clip", "controlnet", "embeddings", "style_models"}
    assert set(comfyui_paths.SUBDIRS) == kind_subdirs | extra_subdirs
    # name -> directory name under models_root(), so a YAML renderer can use it as is
    assert all(key == value for key, value in comfyui_paths.SUBDIRS.items())


def test_is_under_models_root_accepts_files_and_the_root_itself(store: Path) -> None:
    models = comfyui_paths.models_root()
    (models / "checkpoints").mkdir(parents=True)
    weights = models / "checkpoints" / "a.safetensors"
    weights.touch()
    assert comfyui_paths.is_under_models_root(weights)
    assert comfyui_paths.is_under_models_root(models)
    # a path that does not exist yet still resolves lexically under the root
    assert comfyui_paths.is_under_models_root(models / "vae" / "future.safetensors")


def test_is_under_models_root_rejects_unrelated_paths(store: Path, tmp_path: Path) -> None:
    comfyui_paths.models_root().mkdir(parents=True)
    assert not comfyui_paths.is_under_models_root(tmp_path / "elsewhere" / "a.gguf")
    assert not comfyui_paths.is_under_models_root(comfyui_paths.workflows_dir() / "w.json")
    assert not comfyui_paths.is_under_models_root(Path("/"))


def test_is_under_models_root_resolves_symlinks(store: Path, tmp_path: Path) -> None:
    models = comfyui_paths.models_root()
    (models / "loras").mkdir(parents=True)
    target = models / "loras" / "a.safetensors"
    target.touch()

    # A link outside the root that points into it is under the root.
    outside = tmp_path / "outside"
    outside.mkdir()
    link = outside / "link.safetensors"
    link.symlink_to(target)
    assert comfyui_paths.is_under_models_root(link)

    # A link inside the root that points out of it is not.
    escape = models / "loras" / "escape.safetensors"
    escape.symlink_to(outside / "not-a-model.gguf")
    assert not comfyui_paths.is_under_models_root(escape)

    # ".." segments are resolved before the comparison.
    assert not comfyui_paths.is_under_models_root(models / ".." / "workflows" / "w.json")


def test_is_under_models_root_never_matches_substrings(store: Path) -> None:
    models = comfyui_paths.models_root()
    models.mkdir(parents=True)
    # Sibling directories whose names merely start with the root's name or path.
    lookalikes = [
        store / "comfyui-models-backup" / "a.safetensors",
        store / "comfyui" / "models-backup" / "a.safetensors",
        Path(f"{models}-old") / "a.safetensors",
    ]
    for path in lookalikes:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        assert not comfyui_paths.is_under_models_root(path), path
