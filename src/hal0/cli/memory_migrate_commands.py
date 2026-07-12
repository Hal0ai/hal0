"""``hal0 memory migrate`` — Cognee→Hindsight dry-run (legacy) + bank unify.

The bare ``hal0 memory migrate`` command is unchanged from before this file
existed (P2-4, dry-run only) — it now lives in a Typer sub-app's default
callback so ``migrate unify`` (P?-cross-bank-unify) can nest under the same
top-level name without colliding with it.

``migrate unify`` folds one or more source Hindsight banks into a target
bank by tag, so multiple per-agent private banks can be consolidated under
a shared/unified bank without losing which agent a fact came from.

Checked against a live Hindsight 0.7.2 instance (``/openapi.json``, full
path dump): there is no cross-bank document *transfer* endpoint in 0.7.2 —
only per-bank CRUD. ``export``/``import`` exist but round-trip a whole bank
template (disposition, mission, config) with no per-document tag rewrite,
so faking "unify" through export→import would silently drop the
``agent:<name>``/``visibility:`` provenance tags this command exists to
add. Rather than fake it, ``--apply`` refuses below hindsight-api 0.8.0
with a clear error; ``--dry-run`` works on any version since it only reads
bank stats.
"""

from __future__ import annotations

import json as jsonlib

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, die
from hal0.memory.migrate import migrate_cognee_to_hindsight_dryrun

app = typer.Typer(
    help="Migrate memory stores (legacy Cognee→Hindsight dry-run; bank unify).",
    invoke_without_command=True,
)
console = Console()

_DEFAULT_COGNEE_DIR = "/var/lib/hal0/memory/cognee"
_MIN_UNIFY_VERSION = (0, 8, 0)


@app.callback()
def migrate_default(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        True,
        "--dry-run",
        help="Report the migration plan without writing. Dry-run only — apply/write mode is not yet implemented.",
    ),
    cognee_dir: str = typer.Option(
        _DEFAULT_COGNEE_DIR,
        "--cognee-dir",
        help="Path to the Cognee data directory (contains hal0_memory_index.sqlite).",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit raw JSON instead of the human-readable panel.",
    ),
) -> None:
    """Migrate Cognee memory store → Hindsight banks (dry-run only, P2-4)."""
    if ctx.invoked_subcommand is not None:
        return
    if not dry_run:
        die("--apply is not yet implemented; dry-run only.")
        return
    report = migrate_cognee_to_hindsight_dryrun(cognee_dir=cognee_dir)
    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))
        return
    noop_label = (
        "[dim]yes — nothing to migrate[/dim]" if report["noop"] else "[bold yellow]no[/bold yellow]"
    )
    t = Table.grid(padding=(0, 2))
    t.add_column("k", style="dim")
    t.add_column("v")
    t.add_row("Rows total", str(report["rows_total"]))
    t.add_row("Rows mapped", str(report["rows_mapped"]))
    t.add_row("Rows unmapped", str(report["rows_unmapped"]))
    t.add_row("No-op", noop_label)
    console.print(Panel(t, title="memory · migrate (dry-run)", border_style="dim"))


def _parse_version(v: str | None) -> tuple[int, int, int] | None:
    if not v:
        return None
    parts = v.split(".")[:3]
    try:
        nums = [int("".join(c for c in p if c.isdigit()) or "0") for p in parts]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


def _derived_tags(bank_id: str, add_tags: list[str]) -> list[str]:
    tags = list(add_tags)
    if bank_id.startswith("private__"):
        agent = bank_id[len("private__") :]
        tags = [f"agent:{agent}", "visibility:private", *tags]
    return tags


@app.command("unify")
def migrate_unify_cmd(
    source: list[str] = typer.Option(
        [], "--source", help="Source bank id to fold into --target (repeatable, required)."
    ),
    target: str = typer.Option(
        "shared", "--target", help="Target bank id sources are unified into."
    ),
    apply: bool = typer.Option(
        False,
        "--apply/--dry-run",
        help="Execute the migration. Default is --dry-run: report the plan without writing.",
    ),
    add_tag: list[str] = typer.Option(
        [], "--add-tag", help="Extra tag to attach to every migrated fact (repeatable)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Fold one or more source banks into a target bank, tagging provenance.

    Never deletes source banks — that is always a separate, explicit
    ``hal0 memory bank delete <bank> --confirm <bank>``.
    """
    if not source:
        die("pass at least one --source <bank_id>")
        return
    if target in source:
        die(f"--target {target!r} cannot also be a --source")
        return

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    try:
        engine = api_get("/api/memory/engine")
        banks_resp = api_get("/api/memory/banks")
    except CliApiError as exc:
        die(str(exc))
        return

    version_str = (engine or {}).get("version")
    version = _parse_version(version_str)
    banks_by_id = {b["bank_id"]: b for b in (banks_resp or {}).get("banks", []) if "bank_id" in b}

    plan = []
    for src in source:
        b = banks_by_id.get(src)
        if b is None:
            die(f"unknown source bank: {src!r} (not in 'hal0 memory bank list')")
            return
        plan.append(
            {
                "source": src,
                "fact_count": b.get("fact_count", 0),
                "last_document_at": b.get("last_document_at"),
                "tags_to_add": _derived_tags(src, add_tag),
            }
        )
    target_info = banks_by_id.get(target, {"bank_id": target, "fact_count": 0})

    report = {
        "hindsight_version": version_str,
        "target": target,
        "target_fact_count_before": target_info.get("fact_count", 0),
        "sources": plan,
        "applied": False,
    }

    if apply:
        if version is None or version < _MIN_UNIFY_VERSION:
            die(
                f"migrate unify --apply requires hindsight-api>=0.8.0 "
                f"(current: {version_str or 'unknown'}); run the Hindsight upgrade first."
            )
            return
        # 0.8.0+: no confirmed cross-bank document-transfer endpoint exists yet
        # (none in the 0.7.2 openapi.json this command was built against, and
        # none documented for 0.8.x at the time of writing). Refuse rather than
        # silently no-op or fake success via export/import (which would drop
        # the provenance tags this command exists to add).
        die(
            f"hindsight-api {version_str} is >=0.8.0 but hal0 has no confirmed cross-bank "
            "transfer endpoint wired yet — 'bank export'/'bank import' round-trip whole "
            "bank templates and cannot rewrite tags per-document, so --apply refuses "
            "rather than fake a migration. Re-check this command once memory_admin.py "
            "gains a transfer passthrough."
        )
        return

    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))
        return

    t = Table(title=f"memory · migrate unify → {target} (dry-run)")
    t.add_column("source")
    t.add_column("facts", justify="right")
    t.add_column("last activity")
    t.add_column("tags to add")
    for row in plan:
        t.add_row(
            row["source"],
            str(row["fact_count"]),
            str(row["last_document_at"] or "[dim]never[/dim]"),
            ", ".join(row["tags_to_add"]) or "[dim]none[/dim]",
        )
    console.print(t)
    console.print(
        Panel(
            f"Target [bold]{target}[/bold]: {target_info.get('fact_count', 0)} facts before migration.\n"
            f"Hindsight version: {version_str or 'unknown'}\n"
            "[dim]This is a plan only — nothing was written. Re-run with --apply once "
            "hindsight-api>=0.8.0 is live and the transfer endpoint is confirmed.[/dim]",
            border_style="dim",
        )
    )


__all__ = ["app"]
