"""#2446: the installer must not leave flm's HOME-side parents root-owned.

flm hardcodes ``$HOME/.config/flm/models``. When ``[models].flm_store`` points
elsewhere, hal0-api (``User=hal0``) replaces that path with a symlink to the
store before each pull, which needs write access on ``.config/flm``. The NPU
block's ``mkdir -p`` runs as root, so it must hand ``.config`` and
``.config/flm`` to hal0 afterwards.

Static-text assertions against ``installer/install.sh``, same technique as
``test_subuid_allocation.py``: exercising chown needs root, which the
black-box harness owns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"


@pytest.fixture(scope="module")
def npu_block() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("if [[ -e /dev/accel/accel0 ]]; then\n        FLM_CACHE_DIR=")
    return text[start : text.index("\n    fi\n", start)]


def test_npu_block_hands_config_parents_to_hal0(npu_block: str) -> None:
    assert 'mkdir -p "${VAR_DIR}/.config/flm"' in npu_block
    assert 'chown hal0:hal0 "${VAR_DIR}/.config" "${VAR_DIR}/.config/flm"' in npu_block


def test_parents_are_chowned_after_the_root_mkdir(npu_block: str) -> None:
    mkdir_at = npu_block.index('mkdir -p "${FLM_CACHE_DIR}"')
    chown_at = npu_block.index('chown hal0:hal0 "${VAR_DIR}/.config"')
    assert mkdir_at < chown_at


def test_doctor_perms_backstop_runs_after_the_npu_block() -> None:
    """The resolved-store repair rides ``doctor perms --fix --force``, which must
    run after the NPU block has created the dirs it reconciles."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    npu = text.index("FLM_CACHE_DIR=")
    backstop = text.index('"${HAL0_BIN}" doctor perms --fix --force')
    assert npu < backstop
