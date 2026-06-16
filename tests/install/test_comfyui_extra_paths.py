"""TDD — Task 3.2: extra_model_paths.yaml template shipped.

Assertions:
  (a) installer/comfyui/extra_model_paths.yaml exists.
  (b) Parses as valid YAML.
  (c) comfyui.base_path == "/root/comfy-models".
  (d) Required keys present: checkpoints, loras, vae, upscale_models.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
YAML_PATH = REPO / "installer" / "comfyui" / "extra_model_paths.yaml"


def test_extra_model_paths_file_exists():
    assert YAML_PATH.exists(), f"Missing: {YAML_PATH}"


def test_extra_model_paths_parses_as_yaml():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(YAML_PATH.read_text())
    assert isinstance(data, dict), "YAML root must be a mapping"


def test_extra_model_paths_base_path():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(YAML_PATH.read_text())
    assert data["comfyui"]["base_path"] == "/root/comfy-models"


def test_extra_model_paths_required_keys():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(YAML_PATH.read_text())
    section = data["comfyui"]
    for key in ("checkpoints", "loras", "vae", "upscale_models"):
        assert key in section, f"Missing key: {key}"
