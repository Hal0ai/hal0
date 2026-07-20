"""3-way sync test for STATIC_SEED_SLOTS.

Per docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md §5.5:
static_seeds.py tuple + install.sh loop + setup_command._SETUP_SLOTS must
agree on the 10 static seed slot names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hal0.install.static_seeds import STATIC_SEED_SLOTS

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "installer/install.sh"
SLOTS_DIR = REPO_ROOT / "installer/etc-hal0/slots"

EXPECTED_10 = frozenset({
    "flm", "tts", "rerank", "utility", "img", "agent", "brain",
    "qwen3tts", "coder", "embed",
})


def test_static_seed_slots_tuple_is_10() -> None:
    """STATIC_SEED_SLOTS tuple is exactly the 10 expected seed names."""
    assert frozenset(STATIC_SEED_SLOTS) == EXPECTED_10, (
        f"STATIC_SEED_SLOTS = {sorted(STATIC_SEED_SLOTS)}, "
        f"expected {sorted(EXPECTED_10)}"
    )


def test_install_sh_loop_matches_tuple() -> None:
    """installer/install.sh:1666 for-loop iterates over the same 10 names."""
    content = INSTALL_SH.read_text()
    match = re.search(r"for seed_slot in ([a-z0-9 _]+); do", content)
    assert match is not None, "could not find 'for seed_slot in ...' line in install.sh"
    bash_names = set(match.group(1).split())
    assert bash_names == EXPECTED_10, (
        f"install.sh loop = {sorted(bash_names)}, expected {sorted(EXPECTED_10)}"
    )


@pytest.mark.parametrize("seed_name", sorted(EXPECTED_10))
def test_every_static_seed_has_slot_toml(seed_name: str) -> None:
    """Every entry in STATIC_SEED_SLOTS has a corresponding <name>.toml file."""
    assert (SLOTS_DIR / f"{seed_name}.toml").is_file(), (
        f"missing slot TOML for static seed '{seed_name}'"
    )


def test_no_drift_files_in_slots_dir() -> None:
    """No extra *.toml files in slots/ that aren't in STATIC_SEED_SLOTS.

    Catches the qwen3tts.toml drift pattern: file on disk but missing from
    the registry means it never gets copied to /etc/hal0/slots/.
    """
    on_disk = {p.stem for p in SLOTS_DIR.glob("*.toml")}
    extra = on_disk - EXPECTED_10
    assert not extra, f"drift: slot TOML files not in STATIC_SEED_SLOTS: {sorted(extra)}"


def test_no_extra_static_seeds() -> None:
    """No STATIC_SEED_SLOTS entries that don't have a slot TOML."""
    extra = set(STATIC_SEED_SLOTS) - {p.stem for p in SLOTS_DIR.glob("*.toml")}
    assert not extra, f"STATIC_SEED_SLOTS entries without slot TOML: {sorted(extra)}"
