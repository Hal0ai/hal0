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
    start = text.index("if [[ -e /dev/accel/accel0 ]]; then")
    return text[start : text.index("\n    fi\n", start)]


def test_hal0_creates_its_own_cache_dirs(npu_block: str) -> None:
    """No root mkdir through hal0's HOME: hal0 makes ``.config/flm/models``."""
    assert 'runuser -u hal0 -- mkdir -p "${VAR_DIR}/.config/flm/models"' in npu_block
    assert 'mkdir -p "${FLM_CACHE_DIR}"' not in npu_block


def test_root_only_chowns_parents_that_are_still_root_owned(npu_block: str) -> None:
    """A root-owned dir can't be renamed or swapped by hal0, so chowning it is
    race-free. A symlink is never followed (``-L`` check plus ``chown -h``)."""
    assert '"$(stat -c %u "${_flm_parent}")" == 0' in npu_block
    assert '! -L "${_flm_parent}"' in npu_block
    assert 'chown -h hal0:hal0 "${_flm_parent}"' in npu_block
    # Parents are handed over before hal0 tries to mkdir under them.
    assert npu_block.index("chown -h hal0:hal0") < npu_block.index("runuser -u hal0 -- mkdir")


def test_no_root_chown_of_the_hal0_home_cache(npu_block: str) -> None:
    """The default cache may be hal0's symlink to the store; uid 1000 ownership
    comes from ``doctor perms --fix`` through no-follow fds instead."""
    assert 'chown 1000:hal0 "${FLM_CACHE_DIR}"' not in npu_block
    assert 'chown 1000:hal0 "${HAL0_FLM_MODELS_DIR}"' in npu_block


def test_doctor_perms_backstop_runs_after_the_npu_block() -> None:
    """The resolved-store repair rides ``doctor perms --fix --force``, which must
    run after the NPU block has created the dirs it reconciles."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    npu = text.index('runuser -u hal0 -- mkdir -p "${VAR_DIR}/.config/flm/models"')
    backstop = text.index('"${HAL0_BIN}" doctor perms --fix --force')
    assert npu < backstop
