"""Release-time drift guard for the OpenWebUI image pin.

OpenWebUI is pinned by sha256 manifest-list digest in two hand-edited sites
(bumped together per release, #79):

* ``installer/install.sh`` — ``OPENWEBUI_IMAGE`` (1 occurrence), and
* ``packaging/systemd/hal0-openwebui.service`` — the ExecStartPre pull + the
  ExecStart run (2 occurrences).

Because they are edited by hand, they can drift. This test fails loudly the
moment they disagree, so a half-applied bump can't ship. It is the pytest
counterpart to ``scripts/check-owui-digest.sh`` and mirrors how
``manifest.json`` toolbox digests are guarded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.openwebui import image_pin

# tests/openwebui/<file> → repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"
_UNIT = _REPO_ROOT / "packaging" / "systemd" / "hal0-openwebui.service"


def _digests(path: Path) -> list[str]:
    return image_pin.find_owui_digests(path.read_text(encoding="utf-8"))


@pytest.mark.skipif(not _INSTALL_SH.is_file(), reason="install.sh not in this checkout")
def test_install_sh_pins_exactly_one_digest() -> None:
    assert len(_digests(_INSTALL_SH)) == 1, f"expected exactly one OWUI pin in {_INSTALL_SH}"


@pytest.mark.skipif(not _UNIT.is_file(), reason="packaging unit not in this checkout")
def test_unit_pins_the_digest_twice() -> None:
    # ExecStartPre pull + ExecStart run — both must reference the same image.
    got = _digests(_UNIT)
    assert len(got) == 2, f"expected two OWUI pins (pull + run) in {_UNIT}, got {len(got)}"
    assert got[0] == got[1], f"the two pins in {_UNIT} disagree: {got}"


@pytest.mark.skipif(
    not (_INSTALL_SH.is_file() and _UNIT.is_file()),
    reason="pin sites not both present in this checkout",
)
def test_all_owui_pins_agree() -> None:
    all_digests = set(_digests(_INSTALL_SH)) | set(_digests(_UNIT))
    assert len(all_digests) == 1, (
        "OpenWebUI image pin has drifted across sites — bump install.sh + the "
        f"systemd unit together (#79). Distinct digests seen: {sorted(all_digests)}"
    )
