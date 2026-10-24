"""The LLM registry never scans, detects or lists ComfyUI files.

ComfyUI's model tree (``hal0.comfyui.paths.models_root()``) belongs to the image
catalog. Discovery, scan preview, scan commit, add-by-path and reconcile all
decide "is this a ComfyUI file" with ``is_under_models_root`` and leave those
files alone.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hal0.comfyui import paths as comfyui_paths
from hal0.config.schema import ModelsConfig
from hal0.registry.detect import detect
from hal0.registry.discover import (
    find_candidates,
    is_skippable,
    reconcile_skipped_auto_scan,
    scan_and_register,
)
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.services import models_service as svc

_EXTS = [".gguf", ".safetensors"]


def _write(path: Path, size: int = 64) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A model store whose ComfyUI tree sits at ``<store>/comfyui``."""
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root / "comfyui"))
    return root


@pytest.fixture
def registry(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_dir=tmp_path / "registry")


@pytest.fixture
def no_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.registry import discover

    monkeypatch.setattr(discover, "_collect_referenced_model_ids", lambda: (set(), True))


def _names(roots: list[Path]) -> set[str]:
    cands = find_candidates(roots=list(roots), extensions=_EXTS, known_paths=set())
    return {c.path.name for c in cands}


def test_discover_skips_comfyui_tree(store: Path) -> None:
    models = comfyui_paths.models_root()
    _write(models / "checkpoints" / "a.safetensors")
    _write(models / "unet" / "b.safetensors")
    _write(models / "latent_upscale_models" / "c.safetensors")
    # A complete shard set and a GGUF with its projector sidecar take the
    # shard and mmproj branches, which ``continue`` before the per-file checks.
    _write(models / "checkpoints" / "big-00001-of-00002.safetensors")
    _write(models / "checkpoints" / "big-00002-of-00002.safetensors")
    _write(models / "unet" / "vision.gguf")
    _write(models / "unet" / "mmproj-vision.gguf")
    _write(store / "qwen3-4b-instruct-q4_k_m.gguf")

    assert _names([store]) == {"qwen3-4b-instruct-q4_k_m.gguf"}


def test_discover_skips_a_scan_root_inside_the_comfyui_tree(store: Path) -> None:
    models = comfyui_paths.models_root()
    _write(models / "checkpoints" / "a.safetensors")

    assert _names([models / "checkpoints"]) == set()


@pytest.mark.parametrize("via", ["data_root_symlink", "scan_root_symlink", "file_symlink"])
def test_discover_skips_symlinked_comfyui_tree(
    via: str, store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tree is recognised by where it resolves, however it is reached."""
    _write(store / "comfyui" / "models" / "checkpoints" / "a.safetensors")
    _write(store / "chat-q4.gguf")
    scan_root = store
    if via == "data_root_symlink":
        link = tmp_path / "comfy-link"
        link.symlink_to(store / "comfyui")
        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(link))
    elif via == "scan_root_symlink":
        scan_root = tmp_path / "store-link"
        scan_root.symlink_to(store)
    else:
        farm = store / "farm"
        farm.mkdir()
        (farm / "linked.safetensors").symlink_to(
            store / "comfyui" / "models" / "checkpoints" / "a.safetensors"
        )
        for idx in (1, 2):  # a symlinked shard set takes the shard branch
            name = f"m-0000{idx}-of-00002.safetensors"
            (farm / name).symlink_to(_write(store / "comfyui" / "models" / "unet" / name))

    assert _names([scan_root]) == {"chat-q4.gguf"}


def test_discover_keeps_a_store_next_to_the_comfyui_tree(store: Path) -> None:
    """Only the ``models`` subtree is ComfyUI's; a sibling ``models-backup`` is not."""
    _write(store / "comfyui" / "models-backup" / "kept-q4.gguf")

    assert _names([store]) == {"kept-q4.gguf"}


def test_is_skippable_flags_a_comfyui_path(store: Path) -> None:
    inside = comfyui_paths.models_root() / "checkpoints" / "a.safetensors"

    assert is_skippable(inside)
    assert not is_skippable(store / "chat-q4.gguf")


def test_detect_has_no_comfyui_special_case(store: Path) -> None:
    """``detect()`` stays a pure file classifier; the refusal lives where a path enters."""
    f = _write(comfyui_paths.models_root() / "checkpoints" / "sdxl-turbo.safetensors")

    result = detect(f)

    assert "image" not in result.suggested_capabilities
    assert "comfyui" not in result.suggested_backends
    assert result.raw_hints.get("source") != "comfyui-tree"


def test_scan_commit_skips_comfyui_path(store: Path, registry: ModelRegistry) -> None:
    image = _write(comfyui_paths.models_root() / "checkpoints" / "sdxl-turbo.safetensors")
    chat = _write(store / "chat-q4.gguf")

    result = asyncio.run(
        svc.commit_scan_rows([{"path": str(image)}, {"path": str(chat)}], registry, None)
    )

    assert result["added"] == ["chat-q4"]
    assert {"path": str(image.resolve()), "reason": "image_asset"} in result["skipped"]
    assert registry.has("chat-q4")
    assert not registry.has("sdxl-turbo")


def test_reconcile_drops_row_under_relocated_comfyui_tree(
    tmp_path: Path, registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch, no_refs: None
) -> None:
    """A tree moved by ``HAL0_COMFYUI_DATA_ROOT`` has no ``/comfyui/models/`` in its
    path, so only the real path rule can recognise its stale auto-scan rows."""
    data = tmp_path / "img-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(data))
    store = tmp_path / "store"
    stale = _write(data / "models" / "checkpoints" / "old.safetensors")
    stale_unet = _write(data / "models" / "unet" / "old-unet.safetensors")
    live = _write(store / "chat-q4.gguf")
    for model_id, path in (("old", stale), ("old-unet", stale_unet), ("chat-q4", live)):
        registry.add(
            Model(
                id=model_id,
                path=str(path.resolve()),
                capabilities=["chat"],
                metadata={"discovered": True, "source": "auto-scan"},
            )
        )
    registry.add(
        Model(
            id="hand-added",
            path=str(stale.resolve()),
            capabilities=["chat"],
            metadata={"discovered": True, "source": "add-from-path"},
        )
    )

    # No scan root contains the stale rows; the path rule still drops them.
    result = reconcile_skipped_auto_scan(registry, roots=[store], protected_ids={"old-unet"})

    assert result["removed"] == ["old"]
    assert result["referenced"] == ["old-unet"]
    assert registry.has("chat-q4")
    assert registry.has("hand-added")  # only auto-scan rows are ever reconciled


def test_scan_and_register_drops_stale_row_without_a_scan_root_over_it(
    tmp_path: Path, registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch, no_refs: None
) -> None:
    data = tmp_path / "img-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(data))
    store = tmp_path / "store"
    store.mkdir()
    stale = _write(data / "models" / "checkpoints" / "old.safetensors")
    registry.add(
        Model(
            id="old",
            path=str(stale.resolve()),
            capabilities=["chat"],
            metadata={"discovered": True, "source": "auto-scan"},
        )
    )

    result = scan_and_register(registry, ModelsConfig(roots=[str(store)], file_extensions=_EXTS))

    assert result["reconciled"] == ["old"]
    assert not registry.has("old")
