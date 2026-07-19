"""``hal0 ports`` — a CLI view over the PortAuthority claim map (§5.2).

Plain top-level command (no verbs of its own), mirroring `system-info` /
`chat`. Backed by ``GET /api/ports`` (:mod:`hal0.api.routes.ports`), which
recomputes port ownership from live truth every call — slot-config,
slot-runtime, reserved, listener, and (when a PortAuthority is wired) its
issued claims. Surfaces conflicts up front since those are the actionable
signal an operator reaches for this command to find.
"""

from __future__ import annotations

import json as jsonlib

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, die

console = Console()


def ports_cmd(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw /api/ports JSON for CI/pipe use (no Rich table)."
    ),
) -> None:
    """Show the port-claim map: who owns which port, and any conflicts."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        report = api_get("/api/ports")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2))
        return

    pool = report.get("pool") or {}
    console.print(
        f"Pool: [bold]{pool.get('start', '?')}-{pool.get('end', '?')}[/bold]  "
        f"next free: [bold]{report.get('next_free', '—')}[/bold]"
    )

    claims = report.get("claims") or []
    table = Table(title="Port claims")
    table.add_column("Port", justify="right")
    table.add_column("Owner", style="bold")
    table.add_column("Source")
    table.add_column("Group", style="dim")
    if not claims:
        console.print("[dim]No claims in the pool.[/dim]")
    else:
        for c in sorted(claims, key=lambda c: c.get("port", 0)):
            table.add_row(
                str(c.get("port", "—")),
                c.get("owner", "—"),
                c.get("source", "—"),
                c.get("group") or "—",
            )
        console.print(table)

    conflicts = report.get("conflicts") or []
    if conflicts:
        console.print(f"[bold red]{len(conflicts)} conflict(s):[/bold red]")
        for c in conflicts:
            owners = ", ".join(c.get("owners", []) or [])
            console.print(f"  port {c.get('port')}: {owners}")
    else:
        console.print("[green]No conflicts.[/green]")


__all__ = ["ports_cmd"]
