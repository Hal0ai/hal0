"""
TDD: Task 2.3 — vendor fetch scripts + 2 new (get_sdxl.sh, get_esrgan.sh).

Tests:
  1. Every script passes bash -n (syntax check).
  2. get_sdxl.sh --dry-run lists expected target subdirs (checkpoints, loras, vae).
  3. get_esrgan.sh --dry-run lists expected target subdir (upscale_models).
"""

import subprocess
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "installer" / "comfyui" / "scripts"
MODEL_STORE = "/mnt/ai-models/comfyui/models"

EXPECTED_SCRIPTS = [
    "get_qwen_image.sh",
    "get_wan22.sh",
    "get_hunyuan15.sh",
    "get_ltx2.sh",
    "get_sdxl.sh",
    "get_esrgan.sh",
]


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
