"""hal0 bridge subcommands — thin HTTP client to /api/bridge/clients (ADR-0043).

The agent bridge lets a remote coding agent (Claude Code, Codex, ...) reach
hal0's MCP servers with a per-client key instead of the box admin key. These
verbs mint, list, revoke, rotate and re-scope those clients, and list the runs
they have delegated to Hermes; hal0-api owns the store, so the CLI never opens
``bridge.db`` itself.

Endpoints hit
-------------

    hal0 bridge add <name>              → POST   /api/bridge/clients
    hal0 bridge list                    → GET    /api/bridge/clients
    hal0 bridge revoke <name>           → DELETE /api/bridge/clients/{name}
    hal0 bridge rotate <name>           → POST   /api/bridge/clients/{name}/rotate
    hal0 bridge scopes <name> <scope>…  → PATCH  /api/bridge/clients/{name}
    hal0 bridge runs                    → GET    /api/bridge/runs

``add`` and ``rotate`` print the new key once, then one ``claude mcp add`` line
per granted scope exactly as the API built it; hal0 keeps only a hash of the
key, so it cannot be shown again. Exit codes: 0 ok, 1 API error, 2 usage.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import typer
from rich import box
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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

app = typer.Typer(help="Manage agent-bridge clients: keys for remote agents to reach hal0's MCP.")
console = Console()

_CLIENTS = "/api/bridge/clients"
_RUNS = "/api/bridge/runs"
_RUNS = "/api/bridge/runs"

_DISABLED_CODE = "bridge.disabled"
_DISABLED_MESSAGE = (
    "The agent bridge is disabled: set [bridge] enabled = true in /etc/hal0/hal0.toml "
    "and restart hal0-api."
)
_ADMIN_SERVER = "hal0-admin"
_ADMIN_WARNING = (
    "The admin scope reaches hal0's admin MCP tools. Treat this key like an operator credential."
)


def _call(request: Callable[[], Any]) -> Any:
    """Run one API call, turning every failure into a printed message and exit 1.

    ``request`` is a closure so the ``api_*`` names resolve in this module at
    call time. A ``bridge.disabled`` envelope gets the operator remedy rather
    than the raw HTTP error; any other :class:`CliApiError` is printed as the
    other ``hal0`` verbs print theirs (the server's ``error.message`` is inside).
    """
    if _api_unreachable(_api_base()):
        raise typer.Exit(1)
    try:
        return request()
    except CliApiError as exc:
        # escape(): the remedy contains "[bridge]", which rich would read as a style tag.
        die(escape(_DISABLED_MESSAGE if exc.code == _DISABLED_CODE else str(exc)))
        raise typer.Exit(1) from exc


def _fmt_ts(value: Any, *, with_time: bool = False) -> str:
    """Format an API timestamp (epoch seconds or ISO 8601) in UTC.

    ``YYYY-MM-DD``, or ``YYYY-MM-DD HH:MM`` with ``with_time``. The list table
    keeps most columns date-only so scopes and note stay readable at 80 columns.
    """
    fmt = "%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d"
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC).strftime(fmt)
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime(fmt)


def _age(value: Any) -> str:
    """How long ago an API timestamp (epoch seconds) was, as ``45s``, ``5m``, ``2h`` or ``3d``.

    Runs last minutes, so the runs table shows ages: a date would say nothing
    and a full date-time would not fit next to a 36-character run id.
    """
    if not isinstance(value, int | float):
        return "-"
    seconds = max(0, int(time.time() - value))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _print_minted(result: dict[str, Any]) -> None:
    """Show a freshly minted key once, then its per-server ``claude mcp add`` lines."""
    name = str(result["client"]["name"])
    body = Group(
        Text(str(result["key"]), style="bold"),
        Text(""),
        Text("Shown once — hal0 stores only a hash.", style="dim"),
    )
    console.print(Panel(body, title=Text(name, style="bold")))
    lines: list[dict[str, Any]] = result.get("onboarding", {}).get("lines", [])
    if lines:
        console.print()
        console.print("Run these on the machine that will use the key:")
    for line in lines:
        # A "# label" comment keeps the whole block pasteable into a shell, and
        # soft_wrap keeps each command on one physical line (rich would otherwise
        # insert newlines at the terminal width and break the pasted command), and
        # emoji=False keeps an IPv6 host intact: rich reads ":100:" in
        # "[2001:db8:100::5]" as an emoji code.
        console.print(Text(f"# {line.get('server', '')}"), highlight=False)
        console.print(
            str(line["claude_mcp_add"]),
            soft_wrap=True,
            markup=False,
            highlight=False,
            emoji=False,
        )
    if any(line.get("server") == _ADMIN_SERVER for line in lines):
        console.print()
        console.print(_ADMIN_WARNING, style="yellow", soft_wrap=True)


@app.command("add")
def add_cmd(
    name: str = typer.Argument(..., help="Client name: lowercase letters, digits, '-' or '_'."),
    note: str = typer.Option("", "--note", help="Free-text note, shown in 'hal0 bridge list'."),
    scope: list[str] | None = typer.Option(
        None,
        "--scope",
        help="Scope to grant: agents, memory or admin. Repeat for several. "
        "Default: agents and memory.",
    ),
) -> None:
    """Mint a key for a remote agent and print how to connect it."""
    # No --scope leaves ``scopes`` out, so the server's default set applies
    # (hal0.bridge.keys.DEFAULT_SCOPES) and this command never restates it.
    body: dict[str, Any] = {"name": name, "note": note}
    if scope:
        body["scopes"] = list(scope)
    _print_minted(_call(lambda: api_post(_CLIENTS, json=body)))


@app.command("list")
def list_cmd() -> None:
    """List bridge clients, including revoked ones."""
    items: list[dict[str, Any]] = _call(lambda: api_get(_CLIENTS)).get("items", [])
    if not items:
        console.print("No bridge clients yet. Add one with: hal0 bridge add <name>")
        return
    table = Table(title="hal0 bridge clients", box=box.SIMPLE_HEAD)
    table.add_column("name", no_wrap=True)
    table.add_column("scopes", no_wrap=True)
    table.add_column("note", overflow="fold")
    table.add_column("created", no_wrap=True)
    table.add_column("last seen", no_wrap=True)
    table.add_column("revoked", no_wrap=True)
    for client in items:
        revoked_at = client.get("revoked_at")
        last_seen = client.get("last_seen_at")
        table.add_row(
            Text(str(client.get("name", ""))),
            Text(", ".join(client.get("scopes") or [])),
            Text(str(client.get("note") or "")),
            _fmt_ts(client["created_at"]) if client.get("created_at") is not None else "-",
            _fmt_ts(last_seen, with_time=True) if last_seen is not None else "never",
            _fmt_ts(revoked_at) if revoked_at is not None else "no",
        )
    console.print(table)


@app.command("revoke")
def revoke_cmd(name: str = typer.Argument(..., help="Client to revoke.")) -> None:
    """Revoke a client's key. The client stays listed, marked revoked."""
    _call(lambda: api_delete(f"{_CLIENTS}/{name}"))
    console.print(Text(f"Revoked bridge client {name}; its key no longer works."))


@app.command("rotate")
def rotate_cmd(name: str = typer.Argument(..., help="Client whose key to replace.")) -> None:
    """Replace a client's key. The old key stops working at once."""
    result = _call(lambda: api_post(f"{_CLIENTS}/{name}/rotate"))
    _print_minted(result)
    console.print()
    console.print("The previous key no longer works.")


@app.command("scopes")
def scopes_cmd(
    name: str = typer.Argument(..., help="Client to change."),
    scopes: list[str] = typer.Argument(..., help="New scopes: agents, memory, admin."),
) -> None:
    """Replace a client's scopes. 'agents' is always kept."""
    result = _call(lambda: api_patch(f"{_CLIENTS}/{name}", json={"scopes": list(scopes)}))
    granted: list[str] = result["client"]["scopes"]
    console.print(Text(f"Scopes for {name}: {', '.join(granted)}"))
    if "admin" in granted:
        console.print(_ADMIN_WARNING, style="yellow", soft_wrap=True)


@app.command("runs")
def runs_cmd(
    client: str | None = typer.Option(
        None, "--client", help="Only this client's runs (as admin you see every client's)."
    ),
    status: str | None = typer.Option(
        None, "--status", help="Only runs in this status, for example running or completed."
    ),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Most runs to show."),
) -> None:
    """List the runs bridge clients delegated to Hermes, newest first.

    started and updated are ages: 45s, 5m, 2h, 3d.
    """
    params: dict[str, Any] = {"limit": limit}
    if client:
        params["client"] = client
    if status:
        params["status"] = status
    items: list[dict[str, Any]] = _call(lambda: api_get(_RUNS, params=params)).get("items", [])
    if not items:
        filtered = bool(client or status)
        console.print("No runs match." if filtered else "No delegated runs yet.")
        return
    table = Table(
        title="hal0 bridge runs",
        box=None,
        pad_edge=False,
        collapse_padding=True,
        header_style="bold",
    )
    # The run id is what 'agent_run_status' takes, so it is never wrapped or cut.
    table.add_column("run_id", no_wrap=True)
    table.add_column("client", overflow="fold")
    table.add_column("agent", overflow="fold")
    table.add_column("status", overflow="fold")
    table.add_column("started", no_wrap=True)
    table.add_column("updated", no_wrap=True)
    for run in items:
        table.add_row(
            Text(str(run.get("run_id", ""))),
            Text(str(run.get("client", ""))),
            Text(str(run.get("agent", ""))),
            Text(str(run.get("status", ""))),
            _age(run.get("created_at")),
            _age(run.get("updated_at")),
        )
    console.print(table)
