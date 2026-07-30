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
* ``hal0 bench`` is an argparse passthrough (design §5), invisible to
  ``_walk()`` beyond the single top-level ``bench`` command — its own
  verbs get a dedicated parity check against ``hal0.bench.cli``'s
  argparse subparsers (GH #1474).

Matching is word-boundary, not substring: a short command name like
``bench`` must not be satisfied by it appearing inside a longer word like
``bench-tuned`` (that gap is what let ``hal0 bench`` ship fully
undocumented while this test still passed — GH #1474).

Issue #1462 added a second, narrower guard: the "Key options" cells of
the "## Top-level" command table must only ever name real flags. The
original bug (a documented ``--source {release|git}`` flag that had been
deleted from ``hal0 update``) would have gone undetected forever by the
command-path check above, since ``update`` itself is a real command —
only one of its documented *flags* was phantom.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import typer

from hal0.bench.cli import build_parser as _bench_build_parser
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


def _bench_verbs() -> list[str]:
    """Return every ``hal0 bench <verb>`` the argparse subparsers expose.

    ``_walk()`` only sees the single typer-registered ``bench`` passthrough
    command — everything past that is argparse (design §5) and needs its
    own walk over ``build_parser()``'s subparsers action.
    """
    parser = _bench_build_parser()
    for action in parser._subparsers._group_actions:  # argparse has no public API for this
        if hasattr(action, "choices"):
            return sorted(action.choices)
    return []


def _documented(term: str, text: str) -> bool:
    """Word-boundary containment: ``term`` must appear as its own token.

    A plain ``term in text`` substring check is satisfied by ``bench``
    appearing inside ``bench-tuned`` — this treats ``-`` (and word chars)
    as part of the boundary so a short command name can't be satisfied by
    a longer word that merely contains it.
    """
    pattern = re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])")
    return bool(pattern.search(text))


def test_cli_mdx_documents_every_command() -> None:
    text = _CLI_MDX.read_text(encoding="utf-8")
    missing: list[str] = []
    for path in sorted(set(_walk(app))):
        if path in ALLOWED_MISSING:
            continue
        # A command is "documented" if its full path appears, word-boundary,
        # in the mdx. The grouped command blocks print them as
        # ``hal0 slot list`` etc., so match on the bare path too.
        if not _documented(path, text) and not _documented(f"hal0 {path}", text):
            missing.append(path)
    assert not missing, (
        "docs/reference/cli.mdx is missing these real CLI commands: "
        + ", ".join(missing)
        + ". Add a line under the right section (or, for an intentional "
        "omission, add it to ALLOWED_MISSING with a reason)."
    )


def test_cli_mdx_documents_every_bench_verb() -> None:
    """Parity walk over the argparse-based ``hal0 bench`` surface (GH #1474).

    ``_walk()`` can't see past the single top-level ``bench`` typer
    command, so the ~11 real verbs (plan/run/status/worker/results/
    history/reindex/devices/publish/eval/import-v1) need their own check
    against the doc, or a new verb can ship silently undocumented the same
    way ``bench`` itself did.
    """
    text = _CLI_MDX.read_text(encoding="utf-8")
    verbs = _bench_verbs()
    assert verbs, "hal0.bench.cli.build_parser() exposed no verbs — parser wiring changed?"
    missing = [v for v in verbs if not _documented(f"bench {v}", text)]
    assert not missing, (
        "docs/reference/cli.mdx is missing these `hal0 bench` verbs: "
        + ", ".join(missing)
        + ". Add a row under the `hal0 bench` section."
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


# --- "## Top-level" table flag-drift guard (issue #1462) -------------------

# Matches the command cell of a "## Top-level" table row, e.g. the
# ``update`` in ``| `hal0 update` | ... |`` or ``update owui`` in
# ``| `hal0 update owui` | ... |``. Rows whose first token after ``hal0``
# isn't a plain command word (e.g. the ``` `hal0 --version` / `-V` ``` row)
# don't match on purpose — that row documents a root eager-option, not a
# subcommand, and has no Typer command to check flags against.
_ROW_COMMAND_RE = re.compile(r"^\|\s*`hal0\s+([a-zA-Z][a-zA-Z0-9-]*(?:\s+[a-zA-Z][a-zA-Z0-9-]*)*)`")

# A documented flag token: `--foo-bar` or `-x`.
_FLAG_RE = re.compile(r"`(--[a-zA-Z][a-zA-Z0-9-]*|-[a-zA-Z])")


def _split_table_row(line: str) -> list[str]:
    """Split a markdown table row into cells, treating ``\\|`` as a literal pipe.

    Several "Key options" cells embed an escaped pipe inside a choice list
    (e.g. ``` `--channel {stable\\|preview\\|nightly}` ```) — a naive
    ``line.split("|")`` would slice that cell in two. Protect escaped pipes
    before splitting, then restore them.
    """
    protected = line.replace("\\|", "\x00")
    cells = [c.strip() for c in protected.strip().strip("|").split("|")]
    return [c.replace("\x00", "\\|") for c in cells]


def _top_level_table_rows(text: str) -> list[tuple[str, str]]:
    """Return ``(command path, key-options cell)`` for each row under "## Top-level"."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "## Top-level")
    rows: list[tuple[str, str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        match = _ROW_COMMAND_RE.match(stripped)
        if not match:
            continue
        cells = _split_table_row(stripped)
        assert len(cells) == 3, f"unexpected column count in Top-level row: {stripped!r}"
        rows.append((match.group(1), cells[2]))
    return rows


def _resolve_click_command(path: str) -> click.Command:
    """Walk the live Click command tree along a space-separated path.

    e.g. ``"update owui"`` -> the ``owui`` subcommand of the ``update`` group.
    """
    node: click.Command = typer.main.get_command(app)
    for part in path.split():
        assert isinstance(node, click.Group) and part in node.commands, (
            f"cli.mdx documents `hal0 {path}`, but `{part}` is not a real "
            "subcommand at that point in the live CLI."
        )
        node = node.commands[part]
    return node


def test_cli_mdx_options_table_has_no_phantom_flags() -> None:
    """Every flag in the "## Top-level" table's "Key options" cells must be real.

    Issue #1462: the ``hal0 update`` row documented a ``--source
    {release|git}`` flag years after the git-based update path (and the
    flag itself) had been deleted from the CLI. The command-path check in
    ``test_cli_mdx_documents_every_command`` didn't catch it because
    ``update`` is still a real command — the drift was at the flag level.
    """
    text = _CLI_MDX.read_text(encoding="utf-8")
    rows = _top_level_table_rows(text)
    assert rows, "expected at least one row in the '## Top-level' table"

    phantom: list[str] = []
    for path, options_cell in rows:
        flags = _FLAG_RE.findall(options_cell)
        if not flags:
            continue
        command = _resolve_click_command(path)
        real_opts: set[str] = set()
        for param in command.params:
            if isinstance(param, click.Option):
                real_opts.update(param.opts)
        for flag in flags:
            if flag not in real_opts:
                phantom.append(f"`hal0 {path}` documents `{flag}`, which is not a real option")

    assert not phantom, (
        "docs/reference/cli.mdx 'Top-level' table documents flags that don't "
        "exist on the live CLI:\n" + "\n".join(phantom)
    )
