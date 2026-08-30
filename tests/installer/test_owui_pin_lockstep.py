"""OPENWEBUI_IMAGE_PIN stays in lockstep with the shipped pin sites.

Three declaration sites by design (spec §2): the python constant (read by
the converge arm), packaging/systemd/hal0-openwebui.service (the shipped
unit — pins the digest twice: ExecStartPre pull + ExecStart run), and
installer/install.sh. Bump one and this fails naming the others.
"""

from __future__ import annotations

from pathlib import Path

from hal0.openwebui.image_pin import OPENWEBUI_IMAGE_PIN, find_owui_digests

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _digests_in(rel: str) -> list[str]:
    return find_owui_digests((_REPO_ROOT / rel).read_text(encoding="utf-8"))


def test_packaging_unit_matches_constant() -> None:
    digests = _digests_in("packaging/systemd/hal0-openwebui.service")
    assert digests, "packaging unit no longer pins an OWUI digest"
    assert set(digests) == {OPENWEBUI_IMAGE_PIN}, (
        f"packaging unit pins {set(digests)} but OPENWEBUI_IMAGE_PIN is "
        f"{OPENWEBUI_IMAGE_PIN!r} — update whichever side is stale"
    )


def test_install_sh_matches_constant() -> None:
    digests = _digests_in("installer/install.sh")
    assert digests, "installer/install.sh no longer pins an OWUI digest"
    assert set(digests) == {OPENWEBUI_IMAGE_PIN}
