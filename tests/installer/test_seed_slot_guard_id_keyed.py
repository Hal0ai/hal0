"""install.sh's curated-slot seed guard must be keying-layout agnostic (#1421).

**Cross-stream note:** the seed loop this exercises is Stream A's block. The
guard is fixed here because the defect is a live-config-destroying chain that
Stream B's convergence work is directly responsible for not shipping, and
Stream A is closed. The change is one helper plus one call site.

The bug: the guard was ``[[ -f "${ETC_DIR}/slots/<name>.toml" ]]``, a test for a
NAME-keyed file. On a box that has run ``hal0 slot migrate-id-keying`` the same
slot lives at ``<id>.toml`` with ``name = "<name>"`` inside, so the test misses
and the installer re-seeds all ten curated names as name-keyed duplicates of
slots that already exist. Chained with #1422 and ``migrate_slot_id_keying``'s
glob order, the live ``<id>.toml`` is then overwritten from the seed content:
a configured `agent` slot (port 8082, profile ``saber-fpx``, a 35B model)
reverts to the seed shape (port 8081, profile ``chadrock-moe``, no model).

Driven by extracting the helper out of install.sh, the same technique
``test_migration_step_containment.py`` uses.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"

_ID_KEYED_AGENT = """\
id = 1
name = "agent"
type = "llm"
device = "gpu-vulkan"
port = 8082
profile = "saber-fpx"

[model]
default = "Qwopus3.6-35B-A3B-Coder-MTP-Q6"
"""


def _extract_function(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index(f"{name}()")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _exists(tmp_path: Path, slot: str) -> bool:
    """Run the real ``slot_name_exists`` from install.sh against a fixture tree."""
    script = tmp_path / "drive.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'ETC_DIR="{tmp_path / "etc"}"\n'
        f"{_extract_function('slot_name_exists')}\n"
        f'if slot_name_exists "{slot}"; then echo FOUND; else echo MISSING; fi\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return "FOUND" in proc.stdout


def _slots(tmp_path: Path) -> Path:
    d = tmp_path / "etc" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── the regression ────────────────────────────────────────────────────────────


def test_id_keyed_slot_is_recognised_by_name(tmp_path: Path) -> None:
    """THE bug. Before the fix this returned MISSING and re-seeded a duplicate."""
    (_slots(tmp_path) / "1.toml").write_text(_ID_KEYED_AGENT, encoding="utf-8")
    assert _exists(tmp_path, "agent") is True


def test_every_curated_seed_name_is_seen_on_a_fully_id_keyed_box(tmp_path: Path) -> None:
    """The observed blast radius: all ten curated names, not just `agent`."""
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
    d = _slots(tmp_path)
    for slot_id, name in enumerate(curated, start=1):
        (d / f"{slot_id}.toml").write_text(
            f'id = {slot_id}\nname = "{name}"\nport = {8080 + slot_id}\n', encoding="utf-8"
        )
    for name in curated:
        assert _exists(tmp_path, name) is True, name


# ── still correct on the layouts that already worked ──────────────────────────


def test_name_keyed_slot_is_still_recognised(tmp_path: Path) -> None:
    (_slots(tmp_path) / "agent.toml").write_text('name = "agent"\n', encoding="utf-8")
    assert _exists(tmp_path, "agent") is True


def test_name_keyed_file_without_a_name_field_still_counts(tmp_path: Path) -> None:
    """The filename test stays first — an old seed may carry no `name =` key."""
    (_slots(tmp_path) / "agent.toml").write_text("port = 8081\n", encoding="utf-8")
    assert _exists(tmp_path, "agent") is True


def test_absent_slot_is_reported_missing(tmp_path: Path) -> None:
    (_slots(tmp_path) / "1.toml").write_text(_ID_KEYED_AGENT, encoding="utf-8")
    assert _exists(tmp_path, "brain") is False


def test_empty_and_missing_slot_dirs_are_missing_not_errors(tmp_path: Path) -> None:
    """A fresh install has no /etc/hal0/slots yet — this must not trip `set -e`."""
    assert _exists(tmp_path, "agent") is False
    _slots(tmp_path)
    assert _exists(tmp_path, "agent") is False


def test_a_substring_name_does_not_match(tmp_path: Path) -> None:
    """`agent` must not be satisfied by a slot named `agent-2`.

    Otherwise the fix trades a re-seed bug for a never-seed bug.
    """
    (_slots(tmp_path) / "1.toml").write_text('id = 1\nname = "agent-2"\n', encoding="utf-8")
    assert _exists(tmp_path, "agent") is False


def test_a_name_in_a_comment_or_value_does_not_match(tmp_path: Path) -> None:
    (_slots(tmp_path) / "1.toml").write_text(
        'id = 1\nname = "coder"\n# name = "agent"\nprofile = "agent"\n', encoding="utf-8"
    )
    assert _exists(tmp_path, "agent") is False
    assert _exists(tmp_path, "coder") is True


def test_single_quoted_toml_names_match(tmp_path: Path) -> None:
    (_slots(tmp_path) / "1.toml").write_text("id = 1\nname = 'agent'\n", encoding="utf-8")
    assert _exists(tmp_path, "agent") is True


# ── the call site actually uses it ────────────────────────────────────────────


def test_the_seed_loop_no_longer_gates_on_the_filename_alone() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert 'if slot_name_exists "${seed_slot}"; then' in text
    assert 'if [[ -f "${SLOT_TOML}" ]]; then' not in text
