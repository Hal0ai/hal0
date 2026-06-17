"""#872: Real (non-mock) smoke tests for flag-form fetch scripts.

Runs get_esrgan.sh --dry-run and get_sdxl.sh --precision fp16 --dry-run
to verify the argv built by fetch_model is actually valid for those scripts.
No network access; no HF tool required.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "installer" / "comfyui" / "scripts"


def _run_script(name: str, *args: str, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {
        "MODEL_DIR": str(tmp_path),
        "HOME": str(tmp_path),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    return subprocess.run(
        ["bash", str(SCRIPTS_DIR / name), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_esrgan_dryrun(tmp_path):
    """get_esrgan.sh --dry-run exits 0 and mentions upscale_models."""
    result = _run_script("get_esrgan.sh", "--dry-run", tmp_path=tmp_path)
    assert result.returncode == 0, f"get_esrgan.sh --dry-run failed:\n{result.stderr}"
    assert "upscale_models" in result.stdout


def test_sdxl_precision_dryrun(tmp_path):
    """get_sdxl.sh --precision fp16 --dry-run exits 0 and mentions checkpoints/loras/vae."""
    result = _run_script("get_sdxl.sh", "--precision", "fp16", "--dry-run", tmp_path=tmp_path)
    assert result.returncode == 0, (
        f"get_sdxl.sh --precision fp16 --dry-run failed:\n{result.stderr}"
    )
    for subdir in ("checkpoints", "loras", "vae"):
        assert subdir in result.stdout, f"dry-run output missing '{subdir}'"
