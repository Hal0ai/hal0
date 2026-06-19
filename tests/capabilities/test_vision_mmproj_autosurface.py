"""Auto-surface `vision` from an associated mmproj sidecar (#901).

Before #901 a model only advertised `vision` if a human hand-added a
`vision` tag. This pins the automatic path: a registry model that carries
an `mmproj` sidecar (the #899 association) advertises `vision` as a
secondary capability — keeping its primary `chat` — and therefore appears
under the `vision` capability without any hand-added tag.
"""

from __future__ import annotations

from pathlib import Path

from hal0.capabilities.catalog import _model_capabilities, models_for_capability
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry


def _reg(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_dir=tmp_path / "registry")


def test_mmproj_model_advertises_vision_keeping_chat(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.add(
        Model(
            id="chat-vlm",
            path="/mnt/ai-models/qwopus/qwopus.gguf",
            capabilities=["chat"],
            backends=["gpu-rocm"],
            mmproj="/mnt/ai-models/qwopus/mmproj-F32.mmproj",
        )
    )
    caps = _model_capabilities(reg.get("chat-vlm"))
    assert "chat" in caps, f"primary chat capability must remain: {caps}"
    assert "vision" in caps, f"vision must be auto-surfaced from the sidecar: {caps}"


def test_mmproj_model_listed_under_vision_capability(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.add(
        Model(
            id="chat-vlm",
            path="/mnt/ai-models/qwopus/qwopus.gguf",
            capabilities=["chat"],
            backends=["gpu-rocm"],
            mmproj="/mnt/ai-models/qwopus/mmproj-F32.mmproj",
        )
    )
    rows = models_for_capability("vision", registry=reg)
    assert "chat-vlm" in {r["id"] for r in rows}, (
        f"sidecar-bearing model missing from vision capability: {[r['id'] for r in rows]}"
    )


def test_model_without_sidecar_does_not_advertise_vision(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.add(
        Model(
            id="plain-chat",
            path="/mnt/ai-models/plain/plain.gguf",
            capabilities=["chat"],
            backends=["gpu-rocm"],
        )
    )
    caps = _model_capabilities(reg.get("plain-chat"))
    assert "vision" not in caps, f"no sidecar → no vision (no false positive): {caps}"
