"""The shipped ``hal0-tune`` skill teaches only ``hal0 profile`` flags that exist (#4061).

An agent follows the skill's command lines literally, so a flag the CLI no longer has
(``--mtp``, renamed ``--speculation``) turns into exit code 2 on the operator's box.
The skill is read from the repo and every ``--flag`` it shows on a ``hal0 profile ...``
command is checked against the options of the real Typer command.
"""

from __future__ import annotations

import re
from pathlib import Path

import typer.main

from hal0.cli.profile_commands import app

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "installer" / "agent-skills" / "hal0-tune" / "SKILL.md"

_SPAN = re.compile(r"`(hal0 profile (\w[\w-]*)[^`]*)`")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def _profile_spans() -> list[tuple[str, str]]:
    """(subcommand, command text) for each inline-code ``hal0 profile <sub> ...`` span."""
    text = re.sub(r"\s+", " ", SKILL.read_text(encoding="utf-8"))
    return [(m.group(2), m.group(1)) for m in _SPAN.finditer(text)]


def _options(sub: str) -> set[str]:
    group = typer.main.get_command(app)
    command = group.commands[sub]  # type: ignore[attr-defined]
    # Hidden options (the refused ``--mtp``) are not taught, so they do not count.
    return {
        opt
        for param in command.params
        if not getattr(param, "hidden", False)
        for opt in (*param.opts, *param.secondary_opts)
    }


def test_skill_shows_profile_edit_line() -> None:
    assert any(sub == "edit" for sub, _ in _profile_spans())


def test_every_flag_on_a_profile_command_exists() -> None:
    for sub, span in _profile_spans():
        known = _options(sub)
        for flag in _FLAG.findall(span):
            assert flag in known, f"`hal0 profile {sub}` has no {flag}: {span!r}"


def test_removed_mtp_flag_is_not_taught() -> None:
    assert "--mtp" not in SKILL.read_text(encoding="utf-8")
