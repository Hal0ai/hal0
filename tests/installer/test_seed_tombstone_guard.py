"""install.sh's curated-slot seed loop must honour ``.seed-tombstones`` (#1650).

#1631's contract: deleting a static seed slot (``hal0 slot delete <name>``)
writes the name into ``/etc/hal0/slots/.seed-tombstones``
(``hal0.install.static_seeds.add_seed_tombstone``, called from
``SlotManager.delete``), and the boot-time seeding pass
(``hal0.install.static_seeds.seed_static_slots``) honours it instead of
resurrecting the slot. Only that Python path checked the file — the bash
seed loop in ``install.sh`` gated solely on ``slot_name_exists()`` (filename
+ ``name =`` grep) and never read the tombstone file, so re-running the
installer (the documented repair/upgrade-in-place path, see
``updater.py``/``privileged.py``) silently re-created a slot the operator
had deliberately deleted.

Same extraction technique ``test_seed_slot_guard_id_keyed.py`` uses: pull the
real bash function out of ``install.sh`` and drive it against a fixture tree,
so this pins the actual shipped logic rather than a re-implementation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"


def _extract_function(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index(f"{name}()")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _tombstoned(tmp_path: Path, slot: str) -> bool:
    """Run the real ``slot_seed_tombstoned`` from install.sh against a fixture tree."""
    script = tmp_path / "drive.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'ETC_DIR="{tmp_path / "etc"}"\n'
        f"{_extract_function('slot_seed_tombstoned')}\n"
        f'if slot_seed_tombstoned "{slot}"; then echo TOMBSTONED; else echo LIVE; fi\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return "TOMBSTONED" in proc.stdout


def _slots(tmp_path: Path) -> Path:
    d = tmp_path / "etc" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── the regression ────────────────────────────────────────────────────────────


def test_tombstoned_name_is_recognised(tmp_path: Path) -> None:
    """THE bug. Before the fix nothing consulted this file at all."""
    (_slots(tmp_path) / ".seed-tombstones").write_text("coder\n", encoding="utf-8")
    assert _tombstoned(tmp_path, "coder") is True


def test_every_curated_seed_name_can_be_tombstoned(tmp_path: Path) -> None:
    curated = [
        "flm",
        "tts",
        "rerank",
        "utility",
        "img",
        "agent",
        "brain",
        "qwen3tts",
        "coder",
        "embed",
    ]
    (_slots(tmp_path) / ".seed-tombstones").write_text(
        "".join(f"{n}\n" for n in curated), encoding="utf-8"
    )
    for name in curated:
        assert _tombstoned(tmp_path, name) is True, name


def test_non_tombstoned_name_is_live(tmp_path: Path) -> None:
    (_slots(tmp_path) / ".seed-tombstones").write_text("coder\n", encoding="utf-8")
    assert _tombstoned(tmp_path, "brain") is False


def test_absent_tombstone_file_means_everything_live(tmp_path: Path) -> None:
    _slots(tmp_path)
    assert _tombstoned(tmp_path, "agent") is False


def test_missing_slots_dir_is_live_not_an_error(tmp_path: Path) -> None:
    """A fresh install has no /etc/hal0/slots yet — must not trip `set -e`."""
    assert _tombstoned(tmp_path, "agent") is False


def test_multiple_tombstones_each_match_exactly(tmp_path: Path) -> None:
    (_slots(tmp_path) / ".seed-tombstones").write_text("agent\ncoder\nembed\n", encoding="utf-8")
    assert _tombstoned(tmp_path, "agent") is True
    assert _tombstoned(tmp_path, "coder") is True
    assert _tombstoned(tmp_path, "embed") is True
    assert _tombstoned(tmp_path, "brain") is False


def test_a_substring_name_does_not_match(tmp_path: Path) -> None:
    """`agent` must not be satisfied by a tombstoned `agent-2`."""
    (_slots(tmp_path) / ".seed-tombstones").write_text("agent-2\n", encoding="utf-8")
    assert _tombstoned(tmp_path, "agent") is False


# ── the call site actually uses it, and gates before the copy ─────────────────


def test_the_seed_loop_checks_tombstones_before_copying() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "slot_seed_tombstoned" in text
    tombstone_check_pos = text.index('if slot_seed_tombstoned "${seed_slot}"; then')
    loop_pos = text.index(
        "for seed_slot in flm tts rerank utility img agent brain qwen3tts coder embed; do"
    )
    cp_pos = text.index('cp "${SLOT_SRC}" "${SLOT_TOML}"')
    assert loop_pos < tombstone_check_pos < cp_pos


def test_tombstone_file_name_matches_the_python_side() -> None:
    """Bash and Python must agree on the tombstone filename or they diverge silently."""
    static_seeds_py = (
        INSTALL_SH.resolve().parents[1] / "src" / "hal0" / "install" / "static_seeds.py"
    )
    py_text = static_seeds_py.read_text(encoding="utf-8")
    assert '_TOMBSTONE_FILE = ".seed-tombstones"' in py_text
    assert ".seed-tombstones" in INSTALL_SH.read_text(encoding="utf-8")
