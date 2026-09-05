"""hal0 mcp subcommands — thin HTTP client to /api/mcp/*.

Issue #504: ``hal0 mcp {list,status,install,uninstall,restart,catalog list}``
backing the existing API routes in :mod:`hal0.api.routes.mcp`.  The Hermes
``MCP-CLIENTS.md.j2`` template already promises this surface.

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
    api_patch,
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


# ── `hal0 mcp add` (ADR-0015 §Decision 4 — alias of install) ────────────────


@app.command("add")
def add_cmd(
    url_spec: str = typer.Argument(..., help="MCP server URL or spec (oci://…, npm:…, etc.)."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of the panel."),
) -> None:
    """Alias of ``hal0 mcp install``."""
    install_cmd(url_spec, json_out)


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


# ── `hal0 mcp remove` (ADR-0015 §Decision 4 — alias of uninstall) ───────────


@app.command("remove")
def remove_cmd(
    server_id: str = typer.Argument(..., help="MCP server id to remove."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
) -> None:
    """Alias of ``hal0 mcp uninstall``."""
    uninstall_cmd(server_id, force)


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


# ── `hal0 mcp test` (ADR-0015) ───────────────────────────────────────────────


@app.command("test")
def test_cmd(
    server_id: str = typer.Argument(..., help="MCP server id to probe."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of the table."),
) -> None:
    """Probe an installed server and show each tool's allow/gated/blocked verdict."""
    api_url = _api_base()
    if _api_unreachable(api_url):
        raise typer.Exit(1)
    try:
        result = api_post(f"/api/mcp/{server_id}/test")
    except CliApiError as exc:
        die(str(exc))
        return

    if json_out:
        typer.echo(jsonlib.dumps(result, indent=2, sort_keys=True))
        return

    probe = result.get("probe", {})
    if not probe.get("ok"):
        console.print(
            Panel(
                f"[bold red]✗ unreachable[/bold red]  {probe.get('error', 'unknown error')}",
                border_style="red",
            )
        )
        return

    verdicts: dict[str, str] = result.get("verdicts", {})
    verdict_style = {
        "allow": "[green]allow[/green]",
        "gated": "[yellow]gated[/yellow]",
        "blocked": "[red]blocked[/red]",
        "unknown_tool": "[dim]unknown_tool[/dim]",
        "unknown_server": "[dim]unknown_server[/dim]",
    }
    table = Table(title=f"mcp test · {server_id}")
    table.add_column("Tool", style="bold")
    table.add_column("Verdict")
    for tool in probe.get("tools", []):
        table.add_row(tool, verdict_style.get(verdicts.get(tool, ""), verdicts.get(tool, "—")))
    console.print(table)


# ── `hal0 mcp allow|gate|block` (ADR-0015) ───────────────────────────────────


def _move_tool(server_id: str, tool: str, target: str) -> None:
    """Fetch the current [tools] policy, move ``tool`` into ``target``, PATCH."""
    api_url = _api_base()
    if _api_unreachable(api_url):
        raise typer.Exit(1)
    try:
        servers = api_get("/api/mcp/servers").get("servers", [])
        server = _find_server(servers, server_id)
        if server is None:
            die(f"no MCP server matching '{server_id}'")
            return
        policy = dict(server.get("tools_policy") or {"allow": [], "gated": [], "blocked": []})
        for tier in ("allow", "gated", "blocked"):
            policy[tier] = [t for t in policy.get(tier, []) if t != tool]
        policy[target] = [*policy.get(target, []), tool]
        result = api_patch(f"/api/mcp/{server_id}/tools", json=policy)
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        Panel(
            f"[bold]{tool}[/bold] → [bold]{target}[/bold] on {server_id}",
            border_style="green",
        )
    )
    if not result.get("hermes_sync", {}).get("errors"):
        return
    console.print(f"[yellow]![/yellow] [dim]hermes sync: {result['hermes_sync']['errors']}[/dim]")


@app.command("allow")
def allow_cmd(
    server_id: str = typer.Argument(..., help="MCP server id."),
    tool: str = typer.Argument(..., help="Tool name to allow autonomously."),
) -> None:
    """Move a tool onto the allow list (autonomous calls)."""
    _move_tool(server_id, tool, "allow")


@app.command("gate")
def gate_cmd(
    server_id: str = typer.Argument(..., help="MCP server id."),
    tool: str = typer.Argument(..., help="Tool name to gate behind approval."),
) -> None:
    """Move a tool onto the gated list (each call enqueues an approval)."""
    _move_tool(server_id, tool, "gated")


@app.command("block")
def block_cmd(
    server_id: str = typer.Argument(..., help="MCP server id."),
    tool: str = typer.Argument(..., help="Tool name to hard-block."),
) -> None:
    """Move a tool onto the blocked list (hard-rejected at the client)."""
    _move_tool(server_id, tool, "blocked")


# ── `hal0 mcp expose` (ADR-0015) ─────────────────────────────────────────────


@app.command("expose")
def expose_cmd(
    server_id: str = typer.Argument(..., help="MCP server id."),
    hermes: bool = typer.Option(None, "--hermes/--no-hermes", help="Join into Hermes's config."),
    brain: bool = typer.Option(
        None, "--brain/--no-brain", help="Join into the hal0-brain profile."
    ),
) -> None:
    """Flip which consumers can see an installed server."""
    if hermes is None and brain is None:
        die("pass at least one of --hermes/--no-hermes or --brain/--no-brain")
        return
    api_url = _api_base()
    if _api_unreachable(api_url):
        raise typer.Exit(1)
    body: dict[str, Any] = {}
    if hermes is not None:
        body["hermes"] = hermes
    if brain is not None:
        body["brain"] = brain
    try:
        result = api_patch(f"/api/mcp/{server_id}/exposure", json=body)
    except CliApiError as exc:
        die(str(exc))
        return
    exposure = result.get("server", {}).get("exposure", {})
    console.print(
        Panel(
            f"[bold]{server_id}[/bold] exposure: "
            f"hermes={exposure.get('hermes')} brain={exposure.get('brain')}",
            border_style="green",
        )
    )


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
    advisory = result.get("advisory")

    # The Tools/Stars columns are gone (#1468): both were invented numbers
    # carried over from the v0.3-alpha dashboard mock, and a popularity figure
    # an operator might weigh a trust decision on cannot be fabricated. What
    # replaces them is the one fact hal0 can stand behind — the install spec.
    table = Table(title="MCP Catalog")
    table.add_column("ID", style="bold")
    table.add_column("Author")
    table.add_column("Category")
    table.add_column("Spec")
    table.add_column("Publisher", justify="center")

    for item in items:
        table.add_row(
            item.get("id", "—"),
            item.get("author", "—"),
            item.get("category", "—"),
            item.get("spec", "—"),
            "first-party" if item.get("verified") else "community",
        )

    console.print(table)
    if categories:
        console.print(f"[dim]categories:[/dim] {', '.join(categories)}")
    if advisory:
        console.print(f"[yellow]![/yellow] [dim]{advisory}[/dim]")


# ── `hal0 mcp catalog refresh` ───────────────────────────────────────────────


@catalog_app.command("refresh")
def catalog_refresh_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of the table."),
) -> None:
    """Refresh the installable-MCP catalog (re-fetches from upstream).

    Currently a stub — re-fetches the static catalog.  The backend will
    grow a live registry probe; when that lands this command
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
