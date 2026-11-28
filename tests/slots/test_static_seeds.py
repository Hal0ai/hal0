"""3-way sync test for SEED_TEMPLATES.

Per docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md §5.5:
static_seeds.py tuple + install.sh loop + setup_command._SETUP_SLOTS must
agree on the 10 static seed slot names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hal0.install.static_seeds import FRESH_INSTALL_SEEDS, SEED_TEMPLATES

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "installer/install.sh"
SLOTS_DIR = REPO_ROOT / "installer/etc-hal0/slots"

EXPECTED_10 = frozenset(
    {
        "flm",
        "tts",
        "rerank",
        "memory",
        "img",
        "agent",
        "brain",
        "qwen3tts",
        "coder",
        "embed",
    }
)


def test_static_seed_slots_tuple_is_10() -> None:
    """SEED_TEMPLATES tuple is exactly the 10 expected seed names."""
    assert frozenset(SEED_TEMPLATES) == EXPECTED_10, (
        f"SEED_TEMPLATES = {sorted(SEED_TEMPLATES)}, expected {sorted(EXPECTED_10)}"
    )


def test_install_sh_loop_matches_fresh_install_seeds() -> None:
    """installer/install.sh's seed for-loop iterates over FRESH_INSTALL_SEEDS."""
    content = INSTALL_SH.read_text()
    match = re.search(r"for seed_slot in ([a-z0-9 _]+); do", content)
    assert match is not None, "could not find 'for seed_slot in ...' line in install.sh"
    bash_names = set(match.group(1).split())
    assert bash_names == set(FRESH_INSTALL_SEEDS), (
        f"install.sh loop = {sorted(bash_names)}, expected {sorted(FRESH_INSTALL_SEEDS)}"
    )


@pytest.mark.parametrize("seed_name", sorted(EXPECTED_10))
def test_every_static_seed_has_slot_toml(seed_name: str) -> None:
    """Every entry in SEED_TEMPLATES has a corresponding <name>.toml file."""
    assert (SLOTS_DIR / f"{seed_name}.toml").is_file(), (
        f"missing slot TOML for static seed '{seed_name}'"
    )


def test_no_drift_files_in_slots_dir() -> None:
    """No extra *.toml files in slots/ that aren't in SEED_TEMPLATES.

    Catches the qwen3tts.toml drift pattern: file on disk but missing from
    the registry means it never gets copied to /etc/hal0/slots/.
    """
    on_disk = {p.stem for p in SLOTS_DIR.glob("*.toml")}
    extra = on_disk - EXPECTED_10
    assert not extra, f"drift: slot TOML files not in SEED_TEMPLATES: {sorted(extra)}"


def test_no_extra_static_seeds() -> None:
    """No SEED_TEMPLATES entries that don't have a slot TOML."""
    extra = set(SEED_TEMPLATES) - {p.stem for p in SLOTS_DIR.glob("*.toml")}
    assert not extra, f"SEED_TEMPLATES entries without slot TOML: {sorted(extra)}"
