"""install.sh's ``HINDSIGHT_PIN`` stays in lockstep with the python constant.

The engine version pin has exactly two declaration sites by design:

* :data:`hal0.memory.engine_upgrade.HINDSIGHT_API_PIN` — read by the
  engine-upgrade migration pass, the doctor version check, and anything else
  python-side;
* ``HINDSIGHT_PIN="..."`` in ``installer/install.sh`` — a shell literal so the
  fresh-install branch works even when the python side is broken.

This test is the sync mechanism (the ``runner-image-pins`` pattern): bump one
side and it fails naming the exact value the other side must carry. It also
sweeps install.sh for stray hard-coded ``hindsight-api==X`` literals so a
future edit can't quietly reintroduce a third declaration site.
"""

from __future__ import annotations

import re
from pathlib import Path

from hal0.memory.engine_upgrade import HINDSIGHT_API_PIN

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"


def test_install_sh_pin_matches_python_constant() -> None:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(r'^\s*HINDSIGHT_PIN="([^"]+)"', text, flags=re.MULTILINE)
    assert match, "installer/install.sh no longer declares HINDSIGHT_PIN"
    assert match.group(1) == HINDSIGHT_API_PIN, (
        f"installer/install.sh pins HINDSIGHT_PIN={match.group(1)!r} but "
        f"hal0.memory.engine_upgrade.HINDSIGHT_API_PIN is {HINDSIGHT_API_PIN!r} — "
        "update whichever side is stale so both carry the same version"
    )


def test_install_sh_has_no_stray_hardcoded_pins() -> None:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    stray = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"hindsight-api==(?!\$\{HINDSIGHT_PIN\})", line)
    ]
    assert not stray, (
        "install.sh hard-codes a hindsight-api version instead of using "
        f"${{HINDSIGHT_PIN}}: {stray}"
    )
