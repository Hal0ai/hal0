"""TDD — #984: Retire docker comfy-up/down/logs/postinstall scripts.

The podman ``img`` slot (``hal0-slot@img.service``) is the sole lifecycle owner
of ComfyUI on :8188.  The four standalone docker control scripts have been
retired to eliminate the :8188 port conflict described in #984 / #874.

Assertions:
  (a) The retired docker scripts do NOT exist in installer/comfyui/scripts/.
  (b) installer/install.sh contains NO reference to ``comfy-up`` or the
      ``installer/comfyui/scripts`` source path (the install block is gone).
  (c) installer/install.sh contains NO comfy-ui sudoers block
      (``packaging/sudoers/hal0-comfyui`` is retired).
  (d) The model-share setup block is still present (COMFYUI_MODELS_ROOT).
  (e) The remaining non-docker scripts (get_*.sh, set_extra_paths.sh) still
      exist and are bash -n clean.
  (f) No Python source file shells out to any of the retired scripts.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
SCRIPTS_DIR = REPO / "installer" / "comfyui" / "scripts"
INSTALL_SH = REPO / "installer" / "install.sh"
SRC_DIR = REPO / "src"

# Scripts that must NOT be present after the #984 retirement.
RETIRED_SCRIPTS = [
    "comfy-up.sh",
    "comfy-down.sh",
    "comfy-logs.sh",
    "comfy-postinstall.sh",
]

# Non-docker helper scripts that must still be present.
RETAINED_SCRIPTS = [
    "get_esrgan.sh",
    "get_hunyuan15.sh",
    "get_ltx2.sh",
    "get_qwen_image.sh",
    "get_sdxl.sh",
    "get_wan22.sh",
    "set_extra_paths.sh",
]


# ── (a) retired scripts do not exist ─────────────────────────────────────────


def test_retired_docker_scripts_removed():
    """The four docker control scripts must not exist after #984."""
    still_present = [s for s in RETIRED_SCRIPTS if (SCRIPTS_DIR / s).exists()]
    assert not still_present, (
        f"Retired docker scripts still present in {SCRIPTS_DIR}: {still_present}"
    )


# ── (b) install.sh no longer contains the docker script install block ────────


def test_install_sh_does_not_install_docker_scripts():
    content = INSTALL_SH.read_text()
    # The old block set COMFYUI_SCRIPTS_SRC and ran:
    #   install -m0755 "${COMFYUI_SCRIPTS_SRC}"/*.sh "${COMFYUI_DIR}/"
    assert "COMFYUI_SCRIPTS_SRC" not in content, (
        "install.sh still contains COMFYUI_SCRIPTS_SRC — the docker script install block was not removed"
    )
    # The old variable COMFYUI_DIR (/opt/comfyui) was only used for the scripts.
    assert "COMFYUI_DIR" not in content, (
        "install.sh still contains COMFYUI_DIR — the docker script install block was not removed"
    )


# ── (c) sudoers block removed from install.sh ────────────────────────────────


def test_install_sh_does_not_install_comfyui_sudoers():
    content = INSTALL_SH.read_text()
    assert "packaging/sudoers/hal0-comfyui" not in content, (
        "install.sh still installs packaging/sudoers/hal0-comfyui — sudoers block not removed"
    )
    assert "hal0-comfyui" not in content, (
        "install.sh still references hal0-comfyui sudoers"
    )


# ── (d) model-share setup block still present ────────────────────────────────


def test_install_sh_still_sets_up_comfyui_model_share():
    """The model-share subdirs (used by the img slot) must still be created."""
    content = INSTALL_SH.read_text()
    assert "COMFYUI_MODELS_ROOT" in content, (
        "install.sh no longer sets up the ComfyUI model-share directories"
    )
    assert "installer/comfyui/custom_nodes" in content or "COMFYUI_CUSTOM_NODES_SRC" in content, (
        "install.sh no longer deploys ComfyUI custom nodes"
    )


# ── (e) retained scripts exist and are bash-clean ────────────────────────────


def test_retained_scripts_still_exist():
    missing = [s for s in RETAINED_SCRIPTS if not (SCRIPTS_DIR / s).exists()]
    assert not missing, f"Retained scripts missing from {SCRIPTS_DIR}: {missing}"


def test_retained_scripts_bash_syntax_clean():
    errors = []
    for name in RETAINED_SCRIPTS:
        path = SCRIPTS_DIR / name
        if not path.exists():
            errors.append(f"{name}: file not found")
            continue
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            errors.append(f"{name}: {result.stderr.strip()}")
    assert not errors, "bash -n failures:\n" + "\n".join(errors)


# ── (f) no Python src file shells out to retired scripts ─────────────────────


def test_src_has_no_subprocess_calls_to_retired_scripts():
    """Verify no Python code shells out to the retired docker comfy-*.sh scripts."""
    pattern = re.compile(r"comfy-(?:up|down|logs|postinstall)\.sh")
    offenders: list[str] = []
    for py_file in SRC_DIR.rglob("*.py"):
        text = py_file.read_text(errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            # Skip pure comment lines — executable references matter.
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(f"{py_file.relative_to(REPO)}:{lineno}: {line.rstrip()}")
    assert not offenders, (
        "Python source references retired comfy scripts in non-comment code:\n"
        + "\n".join(offenders)
    )
