"""TDD: test _comfyui_models_dir uses model_store_root() instead of hardcoded /var/lib/hal0."""

from hal0.config import paths
from hal0.registry import pull


def test_comfyui_models_dir_uses_store_root(monkeypatch, tmp_path):
    """_comfyui_models_dir must return model_store_root()/comfyui/models/<subdir>."""
    # Monkeypatch model_store_root to return tmp_path
    monkeypatch.setattr(paths, "model_store_root", lambda: str(tmp_path))

    result = pull._comfyui_models_dir("loras")
    expected = tmp_path / "comfyui" / "models" / "loras"

    assert result == expected
