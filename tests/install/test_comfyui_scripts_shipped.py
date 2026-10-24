"""TDD — #984: Retire docker comfy-up/down/logs/postinstall scripts.

The podman ``img`` slot (``hal0-slot@img.service``) is the sole lifecycle owner
of ComfyUI on :8188.  The four standalone docker control scripts have been
retired to eliminate the :8188 port conflict described in #984 / #874.

Assertions:
  (a) The retired docker scripts do NOT exist in installer/comfyui/scripts/
      (the directory itself is gone: downloads run in hal0, not in shell scripts).
  (b) installer/install.sh contains NO reference to ``comfy-up`` or the
      ``installer/comfyui/scripts`` source path (the install block is gone).
  (c) installer/install.sh contains NO comfy-ui sudoers block
      (``packaging/sudoers/hal0-comfyui`` is retired).
  (d) install.sh no longer touches the ComfyUI tree at all: the provider creates
      every directory at launch (ADR-0037), under the configured model store.
  (e) No Python source file shells out to any of the retired scripts.
"""

from __future__ import annotations

import re
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


# ── (a) retired scripts do not exist ─────────────────────────────────────────


def test_retired_docker_scripts_removed():
    """The four docker control scripts must not exist after #984."""
    still_present = [s for s in RETIRED_SCRIPTS if (SCRIPTS_DIR / s).exists()]
    assert not still_present, (
        f"Retired docker scripts still present in {SCRIPTS_DIR}: {still_present}"
    )


def test_download_scripts_are_gone():
    """Model downloads run in hal0 (hal0.comfyui.install); no get_*.sh ships with the installer."""
    assert not SCRIPTS_DIR.exists()
    assert not list((REPO / "installer").rglob("get_*.sh"))


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
    assert "hal0-comfyui" not in content, "install.sh still references hal0-comfyui sudoers"


# ── (d) install.sh does not create the ComfyUI tree ──────────────────────────


def test_install_sh_no_longer_touches_the_comfyui_tree():
    """The provider creates the data root under the configured store; install.sh must not.

    The old block hardcoded ``/mnt/ai-models/comfyui``, ignored ``[models].store`` and
    ``HAL0_COMFYUI_DATA_ROOT``, and made root-owned directories hal0 could not write.
    """
    content = INSTALL_SH.read_text()
    for gone in ("COMFYUI_MODELS_ROOT", "/mnt/ai-models/comfyui", "HAL0_SKIP_COMFYUI"):
        assert gone not in content, f"install.sh still references {gone}"
    assert "ComfyUI model share" not in content


# ── (e) no Python src file shells out to retired scripts ─────────────────────


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
