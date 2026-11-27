"""The no-render-node hint names AMD's kernel floor for Strix Halo (#3975).

The hint used to say "kernel >= 6.14", which is below the floor AMD documents
for RDNA3.5 GPU compute: 6.18.4, or on Ubuntu 24.04 the HWE kernel
6.17.0-19.19~24.04.2 or the OEM kernel 6.14.0-1018. A plain 6.14 kernel lacks
the two KFD fixes.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEXT = (REPO / "installer" / "lib" / "preflight.sh").read_text(encoding="utf-8")


def _hint() -> str:
    start = TEXT.index("no GPU driver bound (CPU-only install?)")
    return TEXT[start : TEXT.index("return 0", start)]


def test_hint_names_amds_floor() -> None:
    hint = _hint()
    assert "6.18.4 or newer" in hint
    assert "6.17.0-19.19~24.04.2+" in hint
    assert "6.14.0-1018+" in hint


def test_hint_no_longer_says_plain_614_is_enough() -> None:
    assert "kernel >= 6.14" not in TEXT
