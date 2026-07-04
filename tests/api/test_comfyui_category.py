"""_comfyui_category — path-derived ComfyUI discriminator for /api/models.

The Models route self-heals rows an older pull mis-tagged (capabilities
["chat"], backends []) by deriving "this is a ComfyUI model" and its subdir
category straight from the on-disk path, so no data migration is needed.
"""

from __future__ import annotations

from hal0.api.routes.models import _comfyui_category


def test_category_from_comfyui_checkpoint_path() -> None:
    assert (
        _comfyui_category("/var/lib/hal0/comfyui/models/checkpoints/sdxl-turbo.safetensors")
        == "checkpoints"
    )


def test_category_reads_each_subdir() -> None:
    base = "/mnt/ai-models/comfyui/models"
    assert _comfyui_category(f"{base}/loras/foo.safetensors") == "loras"
    assert _comfyui_category(f"{base}/vae/ae.safetensors") == "vae"
    assert _comfyui_category(f"{base}/upscale_models/x4.pth") == "upscale_models"


def test_non_comfyui_path_is_none() -> None:
    assert _comfyui_category("/var/lib/hal0/models/qwen3-4b/model.gguf") is None
    assert _comfyui_category("") is None
    assert _comfyui_category(None) is None
    assert _comfyui_category(1234) is None
