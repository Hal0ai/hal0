"""``hal0 memory spec`` — apply the packaged Hindsight bank spec (#showcase).

Thin CLI over :mod:`hal0.memory.spec_apply`: dry-run or apply the bank
templates + knowledge-base pages shipped under ``hal0/memory/spec/`` against
a live Hindsight API (default ``http://127.0.0.1:9177``, override with
``--api``).
"""

from __future__ import annotations

import json as jsonlib

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from hal0.memory.spec_apply import DEFAULT_API, apply_spec
from hal0.memory.write_gate import MemoryWritesPaused, require_writes_allowed

app = typer.Typer(help="Apply the packaged Hindsight bank spec (templates + knowledge-base pages).")
console = Console()


@app.command("apply")
def spec_apply_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Plan only — POST /import?dry_run=true, no writes."
    ),
    bank: str | None = typer.Option(
        None, "--bank", help="Apply only this bank (default: every bank in the spec)."
    ),
    api: str = typer.Option(DEFAULT_API, "--api", help="Hindsight API base URL."),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw JSON report instead of a table."
    ),
) -> None:
    """Apply (or dry-run) the bank spec: templates, config_extra, knowledge-base pages."""
    if not dry_run:
        # #3518: this command writes to the engine directly, so it checks the
        # write gate itself: a spec applied while `hal0 memory embedder switch`
        # runs would land in a bank that is about to be replaced.
        try:
            require_writes_allowed()
        except MemoryWritesPaused as exc:
            console.print(Panel(f"[red]{escape(exc.message)}[/red]", border_style="red"))
            raise typer.Exit(1) from exc
    report = apply_spec(api=api, dry_run=dry_run, bank_filter=bank)
    if json_out:
        typer.echo(jsonlib.dumps(report.to_dict(), indent=2, sort_keys=True))
        return

    t = Table(title=f"memory spec {'plan' if dry_run else 'apply'} · {api}")
    t.add_column("bank")
    t.add_column("template")
    t.add_column("result")
    for b in report.banks:
        if b.error:
            result = f"[red]error: {b.error}[/red]"
        elif dry_run:
            result = "[yellow]planned[/yellow]"
        else:
            bits = []
            if b.config_updated:
                bits.append(f"config={b.config_updated}")
            if b.folder_created:
                bits.append(f"folder+{b.folder_created}")
            if b.pages_created:
                bits.append(f"pages+{len(b.pages_created)}")
            if b.pages_updated:
                bits.append(f"pages~{len(b.pages_updated)}")
            result = "[green]applied[/green] " + " ".join(bits)
        t.add_row(b.bank, b.template, result)
    console.print(t)
    errors = [b for b in report.banks if b.error]
    if errors:
        console.print(Panel(f"[red]{len(errors)} bank(s) failed[/red]", border_style="red"))
        raise typer.Exit(1)


__all__ = ["app"]
