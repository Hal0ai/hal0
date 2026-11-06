"""``hal0 memory kb`` — knowledge-base tree/page admin (showcase consoles)."""

from __future__ import annotations

import json as jsonlib
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, api_post, die

app = typer.Typer(help="Browse and refresh a bank's Hindsight knowledge-base pages.")
console = Console()


def _require_api() -> str:
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    return url


def _nodes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    return tree.get("nodes") or tree.get("roots") or tree.get("children") or tree.get("items") or []


def _add_nodes(rich_tree: Tree, nodes: list[dict[str, Any]]) -> None:
    for n in nodes:
        kind = n.get("type", n.get("kind", "?"))
        label = f"[bold]{n.get('name', '?')}[/bold] [dim]({kind}, id={n.get('id', '?')})[/dim]"
        branch = rich_tree.add(label)
        children = n.get("children") or []
        if children:
            _add_nodes(branch, children)


@app.command("tree")
def kb_tree_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a tree view."),
) -> None:
    """Show a bank's knowledge-base folder/page tree."""
    _require_api()
    try:
        tree = api_get(f"/api/memory/banks/{bank}/knowledge-base/tree")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(tree, indent=2, sort_keys=True))
        return
    nodes = _nodes(tree if isinstance(tree, dict) else {})
    rich_tree = Tree(f"[bold]memory · bank {bank} · knowledge-base[/bold]")
    if not nodes:
        rich_tree.add("[dim](empty)[/dim]")
    else:
        _add_nodes(rich_tree, nodes)
    console.print(rich_tree)


@app.command("show")
def kb_show_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    page_id: str = typer.Argument(..., help="Knowledge-base page id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Show one knowledge-base page's content and metadata."""
    _require_api()
    try:
        page = api_get(f"/api/memory/banks/{bank}/knowledge-base/pages/{page_id}")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(page, indent=2, sort_keys=True))
        return
    name = page.get("name", page_id) if isinstance(page, dict) else page_id
    body = page.get("content") or page.get("body") or "" if isinstance(page, dict) else ""
    console.print(Panel(str(body) or "[dim](no content)[/dim]", title=f"memory · kb page {name}"))


@app.command("refresh")
def kb_refresh_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    page_id: str = typer.Argument(..., help="Knowledge-base page id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Trigger an async refresh of one knowledge-base page."""
    _require_api()
    try:
        result = api_post(f"/api/memory/banks/{bank}/knowledge-base/pages/{page_id}/refresh")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return
    console.print(Panel(f"[bold green]Refresh queued[/bold green]\n{result}", border_style="green"))


__all__ = ["app"]
