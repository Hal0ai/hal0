"""hal0 agent subcommands — bundled-agent lifecycle + approval queue.

Mirrors :mod:`hal0.cli.slot_commands` shape (Typer sub-app + thin HTTP
client). The lifecycle subcommands hit the routes in
:mod:`hal0.api.routes.agents`; the ``approvals`` sub-sub-app hits the
MCP-backend's approval queue at ``/api/agent/approvals`` (shape per
ADR-0004 §5 "Pending items").
"""

from __future__ import annotations

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

app = typer.Typer(help="Manage bundled agents (Phase 8 — pi-coder / Hermes-Agent).")
console = Console()

# Approvals lives as a sub-sub-app so ``hal0 agent approvals list``
# renders correctly in --help. Same pattern as the slot sub-app.
approvals_app = typer.Typer(help="Manage agent approval requests (gated destructives).")
app.add_typer(approvals_app, name="approvals")


# ── Lifecycle ────────────────────────────────────────────────────────────────


@app.command("install")
def agent_install(
    name: str = typer.Argument(..., help="Bundled agent name (pi-coder | hermes)."),
    switch: bool = typer.Option(
        False,
        "--switch",
        help=(
            "If another agent is already installed, atomically uninstall it before "
            "installing this one (single-pick enforced; ADR-0004 §2)."
        ),
    ),
) -> None:
    """Install a bundled agent."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    payload: dict[str, object] = {"name": name, "switch": switch}
    try:
        rec = api_post("/api/agents/install", json=payload)
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        Panel(
            f"[bold green]Installed[/bold green] {rec.get('name', name)}  "
            f"[dim](data: {rec.get('data_dir', '?')})[/dim]",
            border_style="green",
        )
    )


@app.command("uninstall")
def agent_uninstall(
    name: str = typer.Argument(..., help="Bundled agent name."),
) -> None:
    """Uninstall a bundled agent."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_delete(f"/api/agents/{name}")
    except CliApiError as exc:
        die(str(exc))
        return
    status = (result or {}).get("status", "uninstalled")
    if status == "not_installed":
        console.print(f"[dim]{name} was not installed.[/dim]")
    else:
        console.print(f"[bold]Uninstalled[/bold] {name}.")


@app.command("list")
def agent_list() -> None:
    """List installed bundled agents."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/agents")
    except CliApiError as exc:
        die(str(exc))
        return
    agents = data.get("agents", []) if isinstance(data, dict) else data
    if not agents:
        console.print("[dim]No bundled agents installed.[/dim]")
        return
    table = Table(title=f"Bundled agents ({len(agents)})")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Installed at")
    table.add_column("Data dir")
    for a in agents:
        table.add_row(
            a.get("name", "—"),
            a.get("status", "—"),
            a.get("installed_at", "—"),
            a.get("data_dir", "—"),
        )
    console.print(table)


# ── Approvals (MCP-backend owns the route shape; CLI assumes ADR-0004 §5) ────


@approvals_app.command("list")
def approvals_list() -> None:
    """List pending agent approval requests."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/agent/approvals")
    except CliApiError as exc:
        die(str(exc))
        return
    items = data.get("approvals", []) if isinstance(data, dict) else data
    if not items:
        console.print("[dim]No pending approvals.[/dim]")
        return
    table = Table(title=f"Pending approvals ({len(items)})")
    table.add_column("ID", style="bold")
    table.add_column("Tool")
    table.add_column("Agent")
    table.add_column("Requested at")
    table.add_column("Summary")
    for it in items:
        table.add_row(
            str(it.get("id", "—")),
            str(it.get("tool", "—")),
            str(it.get("agent", "—")),
            str(it.get("requested_at", "—")),
            str(it.get("summary", "—"))[:60],
        )
    console.print(table)


@approvals_app.command("approve")
def approvals_approve(
    approval_id: str = typer.Argument(..., help="Approval request ID."),
) -> None:
    """Approve a pending agent action."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_post(f"/api/agent/approvals/{approval_id}/approve")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"[bold green]Approved[/bold green] {approval_id}.")


@approvals_app.command("deny")
def approvals_deny(
    approval_id: str = typer.Argument(..., help="Approval request ID."),
) -> None:
    """Deny a pending agent action."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_post(f"/api/agent/approvals/{approval_id}/deny")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"[bold]Denied[/bold] {approval_id}.")
