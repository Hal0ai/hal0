"""``hal0 memory mm`` — mental-model admin (list / refresh / history)."""

from __future__ import annotations

import json as jsonlib
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, api_post, die

app = typer.Typer(help="Manage Hindsight mental models.")
console = Console()


def _require_api() -> str:
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    return url


@app.command("list")
def mm_list_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """List a bank's mental models."""
    _require_api()
    try:
        result = api_get(f"/api/memory/banks/{bank}/mental-models")
    except CliApiError as exc:
        die(str(exc))
        return
    items = result.get("items") if isinstance(result, dict) and "items" in result else result
    items = items if isinstance(items, list) else []

    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    if not items:
        console.print("[dim]No mental models.[/dim]")
        return
    t = Table(title=f"memory · bank {bank} · mental models")
    t.add_column("id")
    t.add_column("name")
    t.add_column("updated_at")
    for m in items:
        t.add_row(str(m.get("id", "—")), str(m.get("name", "—")), str(m.get("updated_at", "—")))
    console.print(t)


@app.command("refresh")
def mm_refresh_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    model_id: str = typer.Argument(..., help="Mental model id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Trigger a refresh of a mental model (async)."""
    _require_api()
    try:
        result = api_post(f"/api/memory/banks/{bank}/mental-models/{model_id}/refresh")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    console.print(Panel(f"[bold green]Refresh queued[/bold green]\n{result}", border_style="green"))


@app.command("history")
def mm_history_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    model_id: str = typer.Argument(..., help="Mental model id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """Show a mental model's revision history."""
    _require_api()
    try:
        result = api_get(f"/api/memory/banks/{bank}/mental-models/{model_id}/history")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    entries: Any = result.get("items") if isinstance(result, dict) and "items" in result else result
    entries = entries if isinstance(entries, list) else []
    if not entries:
        console.print("[dim]No history.[/dim]")
        return
    t = Table(title=f"memory · mental model {model_id} · history")
    t.add_column("at")
    t.add_column("summary")
    for e in entries:
        if isinstance(e, dict):
            t.add_row(
                str(e.get("updated_at") or e.get("created_at") or "—"), str(e.get("content") or e)
            )
        else:
            t.add_row("—", str(e))
    console.print(t)


__all__ = ["app"]
