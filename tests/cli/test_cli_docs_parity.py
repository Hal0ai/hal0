"""Guard: docs/reference/cli.mdx must document every real CLI command.

Issue #501 reconciled ``cli.mdx`` with the real Typer surface (~40
commands) and triaged the phantom references that used to send new users
into ``typer`` "no such command" errors. This test stops that drift from
silently coming back:

* Every full command path the live Typer app exposes (including hidden
  deprecated aliases like ``slot add``) must appear somewhere in
  ``cli.mdx``. A new command that ships without a doc line fails here.
* The handful of *intentionally* undocumented-in-the-grouped-sections
  helpers are listed in ``ALLOWED_MISSING`` with a reason, so the
  exemption is explicit rather than a silent gap.

It deliberately does NOT assert the reverse (doc-only commands) beyond
the curated "Planned" section, because the doc legitimately mentions
planned-but-unimplemented verbs under a clearly-labelled heading.
"""

from __future__ import annotations

from pathlib import Path

import typer

from hal0.cli.main import app

# Repo root: tests/cli/this_file.py -> parents[2] is the repo root.
_CLI_MDX = Path(__file__).resolve().parents[2] / "docs" / "reference" / "cli.mdx"

# Commands that are real but intentionally not given their own line in the
# grouped command sections. Each entry needs a reason so the exemption is a
# decision, not an oversight.
ALLOWED_MISSING: dict[str, str] = {
    # Hidden deprecated aliases: documented as a *concept* (the
    # "slot add / slot remove still work as deprecated aliases" note)
    # rather than as their own command lines.
    "slot add": "hidden deprecated alias of `slot create` (documented as a note)",
    "slot remove": "hidden deprecated alias of `slot delete` (documented as a note)",
}


def _walk(t: typer.Typer, prefix: str = "") -> list[str]:
    """Return every full command path the Typer app exposes.

    e.g. ``["status", "slot list", "agent personas activate", ...]``.
    Hidden commands are included on purpose — a deprecated alias is still
    part of the surface a user can hit, so the doc has to account for it
    (either a line or an ``ALLOWED_MISSING`` exemption).
    """
    paths: list[str] = []
    for cmd in t.registered_commands:
        name = cmd.name
        if name is None and cmd.callback is not None:
            name = cmd.callback.__name__.replace("_", "-")
        if name:
            paths.append(f"{prefix} {name}".strip())
    for group in t.registered_groups:
        sub = group.typer_instance
        if sub is None or group.name is None:
            continue
        paths.extend(_walk(sub, f"{prefix} {group.name}".strip()))
    return paths


def test_cli_mdx_documents_every_command() -> None:
    text = _CLI_MDX.read_text(encoding="utf-8")
    missing: list[str] = []
    for path in sorted(set(_walk(app))):
        if path in ALLOWED_MISSING:
            continue
        # A command is "documented" if its full path appears verbatim in
        # the mdx. The grouped command blocks print them as
        # ``hal0 slot list`` etc., so match on the bare path too.
        if path not in text and f"hal0 {path}" not in text:
            missing.append(path)
    assert not missing, (
        "docs/reference/cli.mdx is missing these real CLI commands: "
        + ", ".join(missing)
        + ". Add a line under the right section (or, for an intentional "
        "omission, add it to ALLOWED_MISSING with a reason)."
    )


def test_allowed_missing_are_still_real_commands() -> None:
    """Keep ``ALLOWED_MISSING`` honest: every entry must still exist.

    If a deprecated alias is finally deleted from the CLI, this fails so
    the stale exemption gets cleaned up instead of masking a future gap.
    """
    real = set(_walk(app))
    stale = [c for c in ALLOWED_MISSING if c not in real]
    assert not stale, (
        "ALLOWED_MISSING lists commands that no longer exist in the CLI: "
        + ", ".join(stale)
        + ". Remove them from the exemption list."
    )
