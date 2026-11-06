"""``hal0 memory directive`` — list/add/remove bank directives (showcase)."""

from __future__ import annotations

import json as jsonlib
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_delete,
    api_get,
    api_post,
    die,
)

app = typer.Typer(help="Manage a bank's Hindsight directives.")
console = Console()


def _require_api() -> str:
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    return url


@app.command("list")
def directive_list_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """List a bank's directives."""
    _require_api()
    try:
        result = api_get(f"/api/memory/banks/{bank}/directives")
    except CliApiError as exc:
        die(str(exc))
        return
    items: list[dict[str, Any]] = result.get("items", []) if isinstance(result, dict) else []
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    if not items:
        console.print("[dim]No directives.[/dim]")
        return
    t = Table(title=f"memory · bank {bank} · directives")
    t.add_column("id")
    t.add_column("text")
    t.add_column("active")
    for d in items:
        t.add_row(
            str(d.get("id", "—")),
            str(d.get("text", d.get("content", "—"))),
            str(d.get("active", "—")),
        )
    console.print(t)


@app.command("add")
def directive_add_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    text: str = typer.Argument(..., help="Directive text."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Create a new directive on a bank."""
    _require_api()
    try:
        result = api_post(f"/api/memory/banks/{bank}/directives", json={"text": text})
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    console.print(
        Panel(f"[bold green]Directive created[/bold green]\n{result}", border_style="green")
    )


@app.command("rm")
def directive_rm_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    directive_id: str = typer.Argument(..., help="Directive id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Delete a directive from a bank."""
    _require_api()
    try:
        result = api_delete(f"/api/memory/banks/{bank}/directives/{directive_id}")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    console.print(
        Panel(f"[bold green]Directive {directive_id} deleted[/bold green]", border_style="green")
    )


__all__ = ["app"]
