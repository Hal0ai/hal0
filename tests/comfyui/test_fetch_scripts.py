"""
TDD: Task 2.3 — vendor fetch scripts + 2 new (get_sdxl.sh, get_esrgan.sh).

Tests:
  1. Every script passes bash -n (syntax check).
  2. set_extra_paths.sh output YAML has exactly 8 model-type keys + base_path /mnt/ai-models/comfyui/models.
  3. get_sdxl.sh --dry-run lists expected target subdirs (checkpoints, loras, vae).
  4. get_esrgan.sh --dry-run lists expected target subdir (upscale_models).
"""

import subprocess
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "installer" / "comfyui" / "scripts"
MODEL_STORE = "/mnt/ai-models/comfyui/models"

EXPECTED_SCRIPTS = [
    "set_extra_paths.sh",
    "get_qwen_image.sh",
    "get_wan22.sh",
    "get_hunyuan15.sh",
    "get_ltx2.sh",
    "get_sdxl.sh",
    "get_esrgan.sh",
]

EXPECTED_YAML_KEYS = {
    "text_encoders",
    "vae",
    "checkpoints",
    "diffusion_models",
    "unet",
    "loras",
    "latent_upscale_models",
    "clip_vision",
}


@pytest.mark.parametrize("name", EXPECTED_SCRIPTS)
def test_script_exists(name):
    assert (SCRIPTS_DIR / name).exists(), f"Missing: {SCRIPTS_DIR / name}"


@pytest.mark.parametrize("name", EXPECTED_SCRIPTS)
def test_script_bash_syntax(name):
    path = SCRIPTS_DIR / name
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, f"bash -n failed for {name}:\n{result.stderr}"


@pytest.mark.parametrize("name", EXPECTED_SCRIPTS)
def test_script_executable(name):
    path = SCRIPTS_DIR / name
    assert path.stat().st_mode & 0o111, f"Not executable: {name}"


def test_set_extra_paths_yaml_keys(tmp_path):
    """set_extra_paths.sh must write YAML with 8 model-type keys + correct base_path."""
    fake_comfy = tmp_path / "ComfyUI"
    fake_comfy.mkdir()
    yaml_file = fake_comfy / "extra_model_paths.yaml"
    model_store = tmp_path / "model-store" / "comfyui" / "models"

    script = SCRIPTS_DIR / "set_extra_paths.sh"
    env = {
        "CONFY_DIR": str(fake_comfy),
        "MODEL_DIR": str(model_store),
        "HOME": str(tmp_path),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    result = subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"set_extra_paths.sh failed:\n{result.stderr}\n{result.stdout}"
    assert yaml_file.exists(), "YAML file not written"

    data = yaml.safe_load(yaml_file.read_text())
    comfy_section = data.get("comfyui", {})

    assert comfy_section.get("base_path") == str(model_store), (
        f"base_path wrong: {comfy_section.get('base_path')!r} != {str(model_store)!r}"
    )

    actual_keys = set(comfy_section.keys()) - {"base_path"}
    assert actual_keys == EXPECTED_YAML_KEYS, (
        f"YAML keys mismatch.\n  expected: {sorted(EXPECTED_YAML_KEYS)}\n  actual:   {sorted(actual_keys)}"
    )


def test_get_sdxl_dry_run(tmp_path):
    """get_sdxl.sh --dry-run must mention checkpoints, loras, and vae subdirs."""
    script = SCRIPTS_DIR / "get_sdxl.sh"
    result = subprocess.run(
        ["bash", str(script), "--dry-run"],
        capture_output=True,
        text=True,
        env={
            "MODEL_DIR": str(tmp_path),
            "HOME": str(tmp_path),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
    )
    assert result.returncode == 0, f"get_sdxl.sh --dry-run failed:\n{result.stderr}"
    output = result.stdout
    for subdir in ("checkpoints", "loras", "vae"):
        assert subdir in output, f"--dry-run output missing '{subdir}'"


def test_get_esrgan_dry_run(tmp_path):
    """get_esrgan.sh --dry-run must mention upscale_models subdir."""
    script = SCRIPTS_DIR / "get_esrgan.sh"
    result = subprocess.run(
        ["bash", str(script), "--dry-run"],
        capture_output=True,
        text=True,
        env={
            "MODEL_DIR": str(tmp_path),
            "HOME": str(tmp_path),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
    )
    assert result.returncode == 0, f"get_esrgan.sh --dry-run failed:\n{result.stderr}"
    assert "upscale_models" in result.stdout, "--dry-run output missing 'upscale_models'"
