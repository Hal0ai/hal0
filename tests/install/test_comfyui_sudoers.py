"""ComfyUI sudoers contract for hardened non-root hal0-api installs."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
SUDOERS = REPO / "packaging" / "sudoers" / "hal0-comfyui"
INSTALL_SH = REPO / "installer" / "install.sh"


def _sudoers_commands() -> set[str]:
    body = "\n".join(
        line for line in SUDOERS.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    body = body.replace("\\\n", " ")
    match = re.search(r"NOPASSWD:\s*(?P<commands>.+)", body, flags=re.S)
    assert match, "sudoers file must contain a NOPASSWD grant"
    return {cmd.strip() for cmd in match.group("commands").split(",") if cmd.strip()}


def test_install_sh_installs_comfyui_sudoers_file() -> None:
    content = INSTALL_SH.read_text()
    assert "install -m0440" in content
    assert "packaging/sudoers/hal0-comfyui" in content
    assert "/etc/sudoers.d/hal0-comfyui" in content


def test_sudoers_grants_only_route_invoked_comfyui_commands() -> None:
    from hal0.api.routes import installer

    expected = f"{installer._SYSTEMCTL} restart {installer._COMFYUI_SLOT_UNIT}"
    assert _sudoers_commands() == {expected}


def test_sudoers_grants_no_wildcards() -> None:
    assert "*" not in "\n".join(_sudoers_commands())


def test_sudoers_visudo_check_passes_when_available() -> None:
    visudo = shutil.which("visudo")
    if visudo is None:
        return
    result = subprocess.run(
        [visudo, "-cf", str(SUDOERS)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr or result.stdout
