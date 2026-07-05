"""hal0 mcp subcommands — thin HTTP client to /api/mcp/*.

Issue #504: ``hal0 mcp {list,status,install,uninstall,restart,catalog list}``
backing the existing API routes in :mod:`hal0.api.routes.mcp`.  ADR-0015 +
the Hermes ``MCP-CLIENTS.md.j2`` template already promise this surface.

Endpoints hit
-------------

    hal0 mcp list              → GET  /api/mcp/servers
    hal0 mcp status <id>       → GET  /api/mcp/servers (filter by id)
    hal0 mcp install <url>     → POST /api/mcp/install
    hal0 mcp uninstall <id>    → DELETE /api/mcp/{id}
    hal0 mcp restart <id>      → POST /api/mcp/{id}/restart
    hal0 mcp catalog list      → GET  /api/mcp/catalog
    hal0 mcp catalog refresh   → GET  /api/mcp/catalog (force-refresh stub)

PLAN.md §13 ("CLI is a thin client") — every command hits 127.0.0.1:8080.
"""

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

app = typer.Typer(help="Manage MCP servers.")
console = Console()

# Sub-app for ``hal0 mcp catalog``
catalog_app = typer.Typer(help="Browse the installable-MCP catalog.")
app.add_typer(catalog_app, name="catalog")


# ── helpers ──────────────────────────────────────────────────────────────────


def _find_server(servers: list[dict[str, Any]], server_id: str) -> dict[str, Any] | None:
    """Case-insensitive server-id lookup in the servers list."""
    for s in servers:
        if s.get("id", "").lower() == server_id.lower():
            return s
    return None


def _state_style(state: str) -> str:
    """Rich colour for a server state."""
    if state == "running":
        return "[green]running[/green]"
    if state == "stopped":
        return "[dim]stopped[/dim]"
    return f"[yellow]{state}[/yellow]"


# ── `hal0 mcp list` ──────────────────────────────────────────────────────────


@app.command("list")
def list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of the table."),
) -> None:
    """List all MCP servers (bundled + installed)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_get("/api/mcp/servers")
    except CliApiError as exc:
        die(str(exc))
        return

    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return

    servers: list[dict[str, Any]] = result.get("servers", [])
    if not servers:
        console.print("[dim]No MCP servers found.[/dim]")
        return

    table = Table(title="MCP Servers")
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("State")
    table.add_column("Tools", justify="right")
    table.add_column("Provider")
    table.add_column("Bundled", justify="center")

    for s in servers:
        table.add_row(
            s.get("id", "—"),
            s.get("name", "—"),
            _state_style(s.get("state", "unknown")),
            str(s.get("tools", "—")),
            s.get("provider", "—"),
            "✓" if s.get("bundled") else "",
        )

    console.print(table)


# ── `hal0 mcp status` ────────────────────────────────────────────────────────


@app.command("status")
def status_cmd(
    server_id: str = typer.Argument(..., help="MCP server id (e.g. 'hal0-admin')."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of the panel."),
) -> None:
    """Show detail for one MCP server."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_get("/api/mcp/servers")
    except CliApiError as exc:
        die(str(exc))
        return

    servers: list[dict[str, Any]] = result.get("servers", [])
    server = _find_server(servers, server_id)
    if server is None:
        die(f"no MCP server matching '{server_id}'")
        return

    if json_out:
        typer.echo(jsonlib.dumps(server, indent=2, sort_keys=True))
        return

    # Basic fields
    lines: list[str] = [
        f"[bold]{server.get('name', server_id)}[/bold]",
        f"  id:       {server.get('id', '—')}",
        f"  state:    {_state_style(server.get('state', 'unknown'))}",
        f"  tools:    {server.get('tools', '—')}",
        f"  prompts:  {server.get('prompts', '—')}",
        f"  provider: {server.get('provider', '—')}",
        f"  bundled:  {server.get('bundled')}",
    ]
    if server.get("description"):
        lines.append(f"  desc:     {server['description']}")
    if server.get("pid"):
        lines.append(f"  pid:      {server['pid']}")
    if server.get("version"):
        lines.append(f"  version:  {server['version']}")
    if server.get("connect_url"):
        lines.append(f"  connect:  [dim]{server['connect_url']}[/dim]")
    if server.get("transport"):
        lines.append(f"  transport:{server['transport']}")

    # Activity
    activity = server.get("activity", {})
    rpm = activity.get("rpm", 0)
    lines.append(f"  activity: {rpm} rpm")

    # Connected clients
    connected = server.get("connected") or []
    if connected:
        lines.append(f"  clients:  {', '.join(connected)}")

    # Env overrides (installed servers)
    env_block = server.get("env")
    if env_block:
        lines.append("  env:")
        for k, v in sorted(env_block.items()):
            masked = v if not v else "***"
            lines.append(f"    {k} = {masked}")

    console.print(Panel("\n".join(lines), title="mcp · status", border_style="dim"))

    # Tool details
    tool_details = server.get("tool_details") or []
    if tool_details:
        tool_table = Table(title="Tools")
        tool_table.add_column("Name", style="bold")
        tool_table.add_column("Description")
        tool_table.add_column("Gated", justify="center")
        for td in tool_details:
            tool_table.add_row(
                td.get("name", "—"),
                (td.get("description") or "")[:80],
                "✓" if td.get("gated") else "",
            )
        console.print(tool_table)


# ── `hal0 mcp install` ───────────────────────────────────────────────────────


@app.command("install")
def install_cmd(
    url_spec: str = typer.Argument(..., help="MCP server URL or spec (oci://…, npm:…, etc.)."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of the panel."),
) -> None:
    """Install a user MCP server from a URL / spec."""
    api_url = _api_base()
    if _api_unreachable(api_url):
        raise typer.Exit(1)
    try:
        result = api_post("/api/mcp/install", json={"url": url_spec})
    except CliApiError as exc:
        die(str(exc))
        return

    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return

    installed = result.get("installed", {})
    sid = installed.get("id", "?")
    console.print(
        Panel(
            f"[bold green]✓ installed[/bold green] [bold]{sid}[/bold]\n"
            f"  name:  {installed.get('name', '—')}\n"
            f"  tools: {installed.get('tools', '—')}\n"
            f"  spec:  [dim]{installed.get('spec', '—')}[/dim]",
            border_style="green",
        )
    )


# ── `hal0 mcp uninstall` ─────────────────────────────────────────────────────


@app.command("uninstall")
def uninstall_cmd(
    server_id: str = typer.Argument(..., help="MCP server id to remove."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
) -> None:
    """Uninstall a user-installed MCP server. Bundled servers reject 409."""
    if not force:
        typer.confirm(
            f"Uninstall MCP server '{server_id}'? This only affects user-installed servers.",
            abort=True,
        )

    api_url = _api_base()
    if _api_unreachable(api_url):
        raise typer.Exit(1)
    try:
        result = api_delete(f"/api/mcp/{server_id}")
    except CliApiError as exc:
        die(str(exc))
        return

    console.print(
        Panel(
            f"[bold]Uninstalled[/bold] {result.get('uninstalled', server_id)}",
            border_style="yellow",
        )
    )


# ── `hal0 mcp restart` ───────────────────────────────────────────────────────


@app.command("restart")
def restart_cmd(
    server_id: str = typer.Argument(..., help="MCP server id to restart."),
) -> None:
    """Restart an MCP server (bundled or installed)."""
    api_url = _api_base()
    if _api_unreachable(api_url):
        raise typer.Exit(1)
    try:
        result = api_post(f"/api/mcp/{server_id}/restart")
    except CliApiError as exc:
        die(str(exc))
        return

    # Expecting a 501 for now (supervisor not implemented), but handle gracefully
    console.print(f"[bold yellow]restart {server_id}:[/bold yellow] {result}")


# ── `hal0 mcp catalog list` ──────────────────────────────────────────────────


@catalog_app.command("list")
def catalog_list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of the table."),
) -> None:
    """List installable MCP servers from the catalog."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_get("/api/mcp/catalog")
    except CliApiError as exc:
        die(str(exc))
        return

    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return

    items = result.get("items", [])
    categories = result.get("categories", [])

    table = Table(title="MCP Catalog")
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Author")
    table.add_column("Tools", justify="right")
    table.add_column("Stars", justify="right")
    table.add_column("Category")
    table.add_column("Verified", justify="center")

    for item in items:
        table.add_row(
            item.get("id", "—"),
            item.get("name", "—"),
            item.get("author", "—"),
            str(item.get("tools", "—")),
            str(item.get("stars", "—")),
            item.get("category", "—"),
            "✓" if item.get("verified") else "",
        )

    console.print(table)
    if categories:
        console.print(f"[dim]categories:[/dim] {', '.join(categories)}")


# ── `hal0 mcp catalog refresh` ───────────────────────────────────────────────


@catalog_app.command("refresh")
def catalog_refresh_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of the table."),
) -> None:
    """Refresh the installable-MCP catalog (re-fetches from upstream).

    Currently a stub — re-fetches the static catalog.  The backend will
    grow a live registry probe per ADR-0013; when that lands this command
    will force a re-index instead of returning the same static payload.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_get("/api/mcp/catalog")
    except CliApiError as exc:
        die(str(exc))
        return

    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return

    items = result.get("items", [])
    console.print(
        Panel(
            f"[bold green]Catalog refreshed[/bold green]  [dim]{len(items)} entries.[/dim]",
            border_style="green",
        )
    )


__all__ = ["app", "catalog_app"]
