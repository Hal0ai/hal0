"""TDD — Task 3.1: Ship ComfyUI control scripts.

Three assertions:
  (a) All shipped scripts exist in installer/comfyui/scripts/ and are bash -n clean.
  (b) Every /opt/comfyui/*.sh path referenced in src/hal0/api/routes/comfyui.py
      has a matching shipped script in installer/comfyui/scripts/.
  (c) installer/install.sh contains the /opt/comfyui placement block.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Repo root = three levels up from tests/install/
REPO = Path(__file__).parent.parent.parent
SCRIPTS_DIR = REPO / "installer" / "comfyui" / "scripts"
CUSTOM_NODES_DIR = REPO / "installer" / "comfyui" / "custom_nodes"
COMFYUI_PY = REPO / "src" / "hal0" / "api" / "routes" / "comfyui.py"
INSTALL_SH = REPO / "installer" / "install.sh"

EXPECTED_SCRIPTS = [
    "comfy-up.sh",
    "comfy-down.sh",
    "comfy-logs.sh",
    "comfy-postinstall.sh",
]


# ── (a) scripts exist and are bash -n clean ──────────────────────────────────


def test_all_scripts_exist():
    missing = [s for s in EXPECTED_SCRIPTS if not (SCRIPTS_DIR / s).exists()]
    assert not missing, f"Missing scripts in {SCRIPTS_DIR}: {missing}"


def test_all_scripts_are_executable():
    import stat

    not_exec = [s for s in EXPECTED_SCRIPTS if not (SCRIPTS_DIR / s).stat().st_mode & stat.S_IXUSR]
    assert not not_exec, f"Scripts not executable: {not_exec}"


def test_all_scripts_bash_syntax_clean():
    errors = []
    for name in EXPECTED_SCRIPTS:
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


# ── (b) comfyui.py /opt/comfyui/*.sh references covered ─────────────────────


def test_comfyui_py_script_refs_all_shipped():
    """Every /opt/comfyui/<name>.sh referenced in comfyui.py must be shipped.

    NOTE: comfyui.py currently does NOT shell out (the API comment explicitly
    states the scripts are for manual ops only).  This test will pass with an
    empty reference set, but will catch regressions if someone adds a
    subprocess call to a script that isn't shipped.
    """
    source = COMFYUI_PY.read_text()
    # Match any string literal containing /opt/comfyui/<something>.sh
    refs = re.findall(r"/opt/comfyui/([\w.-]+\.sh)", source)
    shipped = {s for s in EXPECTED_SCRIPTS}
    missing = [r for r in refs if r not in shipped]
    assert not missing, f"comfyui.py references /opt/comfyui/ scripts not in shipped set: {missing}"


# ── (c) install.sh places scripts at /opt/comfyui ────────────────────────────


def test_install_sh_contains_opt_comfyui_placement():
    content = INSTALL_SH.read_text()
    assert "/opt/comfyui" in content, "installer/install.sh has no /opt/comfyui placement block"
    # More specific: must use install command to copy the scripts
    assert "installer/comfyui/scripts" in content or "comfyui/scripts" in content, (
        "installer/install.sh does not reference installer/comfyui/scripts"
    )


def test_install_sh_uses_install_command_for_scripts():
    content = INSTALL_SH.read_text()
    # Should contain something like: install -m0755 .../comfyui/scripts/*.sh /opt/comfyui/
    # We look for the pattern of install + comfyui scripts + /opt/comfyui
    has_install_block = (
        "install -d /opt/comfyui" in content
        or 'install -d "${PREFIX}/opt/comfyui"' in content
        or 'install -d "${PREFIX}/opt/comfyui"' in content
        or "COMFYUI_DIR" in content
    )
    assert has_install_block, "install.sh does not contain 'install -d /opt/comfyui' or equivalent"


def test_install_sh_places_comfyui_custom_nodes():
    content = INSTALL_SH.read_text()
    assert (CUSTOM_NODES_DIR / "hal0_gpu_gate.py").exists()
    assert "installer/comfyui/custom_nodes" in content or "comfyui/custom_nodes" in content
    assert "COMFYUI_MODELS_ROOT}/custom_nodes" in content
