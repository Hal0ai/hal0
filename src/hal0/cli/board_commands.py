"""hal0 board subcommands — thin HTTP client to /api/board/* (§5.2).

A small operator-facing slice of the board surface: list what's on it,
show one task, add one, move it between lanes. Full CRUD (comments,
links, bulk ops, board switching, orchestration knobs) stays
dashboard-only for now — this is the "thin over /api/board" cut the R5
sync assessment calls for, not a board admin client.

Endpoints hit
-------------

    hal0 board list          → GET  /api/board/board   (lanes → tasks)
    hal0 board show <id>     → GET  /api/board/tasks/{id}
    hal0 board add <title>   → POST /api/board/tasks
    hal0 board move <id> <status> → PATCH /api/board/tasks/{id}
"""

from __future__ import annotations

import json as jsonlib

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_get,
    api_patch,
    api_post,
    die,
)

app = typer.Typer(help="Operator board — list, show, add, and move tasks.")
console = Console()


@app.command("list")
def board_list(
    board: str | None = typer.Option(
        None, "--board", help="Board slug (omit for the current board)."
    ),
    include_archived: bool = typer.Option(
        False, "--include-archived", help="Include the archived lane."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw /api/board/board JSON for CI/pipe use."
    ),
) -> None:
    """List tasks by lane (GET /api/board/board)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    params: dict[str, object] = {}
    if board:
        params["board"] = board
    if include_archived:
        params["include_archived"] = "true"
    try:
        data = api_get("/api/board/board", params=params or None)
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(data, indent=2))
        return
    lanes = data.get("lanes") or {}
    if not lanes:
        console.print("[dim]No lanes.[/dim]")
        return
    for status, tasks in lanes.items():
        table = Table(title=f"{status} ({len(tasks)})")
        table.add_column("ID", style="bold")
        table.add_column("Title")
        table.add_column("Assignee")
        if not tasks:
            console.print(f"[dim]{status}: — no tasks —[/dim]")
            continue
        for t in tasks:
            table.add_row(str(t.get("id", "—")), t.get("title") or "—", t.get("assignee") or "—")
        console.print(table)


@app.command("show")
def board_show(
    task_id: str = typer.Argument(..., help="Task id to inspect"),
    json_out: bool = typer.Option(False, "--json", help="Emit the raw task JSON for CI/pipe use."),
) -> None:
    """Show one task (GET /api/board/tasks/{id})."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        task = api_get(f"/api/board/tasks/{task_id}")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(task, indent=2))
        return
    table = Table(show_header=False, title=f"task: {task_id}")
    for k, v in task.items():
        table.add_row(k, str(v))
    console.print(table)


@app.command("add")
def board_add(
    title: str = typer.Argument(..., help="Task title"),
    body: str = typer.Option("", "--body", help="Task body/description"),
    assignee: str = typer.Option("", "--assignee", help="Assignee/profile to route to"),
    status: str = typer.Option("triage", "--status", help="Initial lane (default: triage)"),
    board: str | None = typer.Option(
        None, "--board", help="Board slug (omit for the current board)."
    ),
    priority: int = typer.Option(0, "--priority", help="Priority (higher sorts first)"),
) -> None:
    """Create a task (POST /api/board/tasks)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    payload: dict[str, object] = {"title": title, "status": status, "priority": priority}
    if body:
        payload["body"] = body
    if assignee:
        payload["assignee"] = assignee
    params = {"board": board} if board else None
    try:
        result = api_post("/api/board/tasks", json=payload, params=params)
    except CliApiError as exc:
        die(str(exc))
        return
    task = result.get("task") if isinstance(result, dict) else None
    tid = task.get("id") if isinstance(task, dict) else "?"
    console.print(f"Created task [bold]{tid}[/bold]: {title}")


@app.command("move")
def board_move(
    task_id: str = typer.Argument(..., help="Task id to move"),
    status: str = typer.Argument(..., help="Target lane (e.g. triage, running, done, blocked)"),
) -> None:
    """Move a task to a different lane (PATCH /api/board/tasks/{id})."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_patch(f"/api/board/tasks/{task_id}", json={"status": status})
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Moved [bold]{task_id}[/bold] → {result.get('status', status)}")


__all__ = ["app"]
