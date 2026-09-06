"""hal0 oauth subcommands — thin HTTP client to the hal0 API.

Provides ``hal0 oauth {list,connect,disconnect,status,set-client-secret}``
for the agent-driven OAuth passthrough (study 3.3): a Hermes skill that
needs OAuth (Google Calendar, Spotify, GitHub, ...) is connected by sending
the operator a consent link rather than having them copy-paste an
authorization code. This is also the CLI Hermes's persona addendum walks
the operator through when driving the flow itself (see the OAuth section
appended to ``src/hal0/agents/hermes_templates/SOUL.md.j2``).

Request/response shapes mirror ``hal0.api.routes.oauth`` exactly — see
that module's docstring for the full endpoint list.
"""

from __future__ import annotations

import json as jsonlib

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_delete, api_get, api_post

app = typer.Typer(
    help="Connect/disconnect OAuth for Hermes skills (Google Calendar, Spotify, ...)."
)
console = Console()


def _die(message: str) -> None:
    console.print(f"[red]✗[/red] {message}")


@app.command("list")
def list_providers(
    json_out: bool = typer.Option(False, "--json", help="Output raw JSON instead of a table."),
) -> None:
    """List every registered OAuth provider and its connection status."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/oauth/providers")
    except CliApiError as exc:
        _die(str(exc))
        raise typer.Exit(1) from None

    providers = data.get("providers", [])
    if json_out:
        console.print(jsonlib.dumps(providers, indent=2))
        return
    if not providers:
        console.print("[dim]No OAuth providers registered.[/dim]")
        return

    table = Table(title="OAuth Providers")
    table.add_column("Provider", style="bold")
    table.add_column("Skill")
    table.add_column("Configured")
    table.add_column("Connected")
    table.add_column("Expires")

    for p in providers:
        expires = p.get("expires_at")
        expires_str = "—"
        if expires:
            import datetime

            expires_str = datetime.datetime.fromtimestamp(expires, tz=datetime.UTC).isoformat()
            if p.get("expired"):
                expires_str += " [red](expired)[/red]"
        table.add_row(
            p.get("id", "—"),
            p.get("skill_id", "—"),
            "✓" if p.get("configured") else "✗ (set client_id / secret)",
            "✓" if p.get("connected") else "✗",
            expires_str,
        )
    console.print(table)


@app.command("connect")
def connect(
    provider: str = typer.Argument(..., help="Provider id (see `hal0 oauth list`)."),
) -> None:
    """Print the consent URL to authorize a provider.

    Send the printed URL to the operator (a markdown link, if you're the
    agent). Once they authorize in their browser, the provider redirects
    straight back to hal0 — poll `hal0 oauth status <provider>` until
    `connected` flips true rather than asking for a code.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_post(f"/api/oauth/{provider}/start")
    except CliApiError as exc:
        _die(str(exc))
        raise typer.Exit(1) from None

    console.print(f"[green]✓[/green] Authorize [bold]{provider}[/bold] at:")
    console.print(result.get("authorize_url", ""))
    console.print(f"[dim]Then check: hal0 oauth status {provider}[/dim]")


@app.command("disconnect")
def disconnect(
    provider: str = typer.Argument(..., help="Provider id to disconnect."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
) -> None:
    """Disconnect a provider (best-effort revoke at the provider, then forget the token)."""
    if not force:
        confirm = typer.confirm(
            f"Disconnect {provider!r}? The skill will lose access until reconnected."
        )
        if not confirm:
            console.print("Aborted.")
            raise typer.Exit(0)

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_delete(f"/api/oauth/{provider}")
    except CliApiError as exc:
        _die(str(exc))
        raise typer.Exit(1) from None
    console.print(f"[green]✓[/green] Disconnected [bold]{provider}[/bold]")


@app.command("status")
def status(
    provider: str = typer.Argument(..., help="Provider id."),
    json_out: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Show one provider's connection status."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_get(f"/api/oauth/{provider}/status")
    except CliApiError as exc:
        _die(str(exc))
        raise typer.Exit(1) from None

    if json_out:
        console.print(jsonlib.dumps(result, indent=2))
        return

    if result.get("connected"):
        state = "[green]connected[/green]"
        if result.get("expired"):
            state += " [red](token expired)[/red]"
    else:
        state = "[yellow]not connected[/yellow]"
    console.print(f"{provider}: {state}")


@app.command("set-client-secret")
def set_client_secret(
    provider: str = typer.Argument(..., help="Provider id."),
    value: str = typer.Option(
        ...,
        "--value",
        prompt="Client secret",
        hide_input=True,
        help="Client secret VALUE (secret; prompted securely if omitted).",
    ),
) -> None:
    """Store a provider's OAuth client secret (through the secrets store, never in TOML)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_post(f"/api/oauth/{provider}/client-secret", json={"value": value})
    except CliApiError as exc:
        _die(str(exc))
        raise typer.Exit(1) from None
    console.print(f"[green]✓[/green] Client secret stored for [bold]{provider}[/bold]")
