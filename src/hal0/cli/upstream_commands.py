"""hal0 upstream subcommands — thin HTTP client to the hal0 API.

Provides ``hal0 upstream {list,show,create,update,delete,test,set-credentials}``.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
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

app = typer.Typer(help="Manage upstream LLM providers.")
console = Console()


# ── list ──────────────────────────────────────────────────────────────────


@app.command("list")
def list_upstreams(
    json_out: bool = typer.Option(
        False, "--json", help="Output raw JSON instead of a table."
    ),
) -> None:
    """List every configured upstream provider."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        ups = api_get("/api/upstreams")
    except CliApiError as exc:
        die(str(exc))
        return

    if json_out:
        console.print(jsonlib.dumps(ups, indent=2))
        return

    if not ups:
        console.print("[dim]No upstream providers configured.[/dim]")
        return

    table = Table(title="Upstream Providers")
    table.add_column("Name", style="bold")
    table.add_column("Kind")
    table.add_column("URL")
    table.add_column("Enabled")
    table.add_column("Models")

    for u in ups:
        enabled = "✓" if u.get("enabled", True) else "✗"
        filters = u.get("model_filters")
        models = ""
        if isinstance(filters, dict):
            allow = filters.get("allow") or []
            deny = filters.get("deny") or []
            has_filters = allow or deny
            if has_filters:
                parts = []
                if allow:
                    parts.append(f"allow:{len(allow)}")
                if deny:
                    parts.append(f"deny:{len(deny)}")
                models = ", ".join(parts)
            else:
                models = "all"
        else:
            models = "all"
        table.add_row(
            u.get("name", "—"),
            u.get("kind", "—"),
            u.get("openai_base_url", "—"),
            enabled,
            models,
        )
    console.print(table)


# ── show ──────────────────────────────────────────────────────────────────


@app.command("show")
def show_upstream(
    name: str = typer.Argument(..., help="Upstream name."),
    json_out: bool = typer.Option(
        False, "--json", help="Output raw JSON."
    ),
) -> None:
    """Show full detail for one upstream provider."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        up = api_get(f"/api/upstreams/{name}")
    except CliApiError as exc:
        die(str(exc))
        return

    if json_out:
        console.print(jsonlib.dumps(up, indent=2))
        return

    console.print(
        Panel(
            Syntax(
                jsonlib.dumps(up, indent=2),
                "json",
                theme="ansi_dark",
                background_color="default",
            ),
            title=f"Upstream: {name}",
            border_style="cyan",
        )
    )


# ── create ────────────────────────────────────────────────────────────────


@app.command("create")
def create_upstream(
    name: str = typer.Argument(..., help="Upstream name (unique, lowercase)."),
    kind: str = typer.Option(
        "remote",
        "--kind",
        help="Provider kind: 'remote' (default) or 'slot'.",
    ),
    base_url: str = typer.Option(
        ...,
        "--base-url",
        help="OpenAI-compatible base URL (e.g. https://api.openai.com/v1).",
    ),
    auth_header: str = typer.Option(
        None,
        "--auth-header",
        help="HTTP header for auth (default: Authorization: Bearer <key>).",
    ),
    advertise_models: bool = typer.Option(
        True,
        "--advertise-models/--hide-models",
        help="Whether to expose this upstream's models in /v1/models.",
    ),
    enabled: bool = typer.Option(
        True,
        "--enabled/--disabled",
        help="Enable or disable this upstream (disabled = no routing).",
    ),
) -> None:
    """Register a new upstream LLM provider."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    body: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "openai_base_url": base_url.rstrip("/"),
        "advertise_models": advertise_models,
        "enabled": enabled,
    }
    if auth_header is not None:
        body["auth_header"] = auth_header

    try:
        result = api_post("/api/upstreams", json=body)
    except CliApiError as exc:
        die(str(exc))
        return

    console.print(f"[green]✓[/green] Created upstream [bold]{name}[/bold]")
    console.print(Syntax(jsonlib.dumps(result, indent=2), "json", theme="ansi_dark", background_color="default"))


# ── update ────────────────────────────────────────────────────────────────


@app.command("update")
def update_upstream(
    name: str = typer.Argument(..., help="Upstream name to update."),
    base_url: str = typer.Option(
        None,
        "--base-url",
        help="New base URL.",
    ),
    auth_header: str = typer.Option(
        None,
        "--auth-header",
        help="New auth header.",
    ),
    advertise_models: bool | None = typer.Option(
        None,
        "--advertise-models/--hide-models",
        help="Toggle /v1/models visibility.",
    ),
    enabled: bool | None = typer.Option(
        None,
        "--enabled/--disabled",
        help="Toggle routing enable/disable.",
    ),
    allow_models: list[str] | None = typer.Option(
        None,
        "--allow",
        help="Model allowlist glob (repeatable, e.g. --allow 'gpt-4*').",
    ),
    deny_models: list[str] | None = typer.Option(
        None,
        "--deny",
        help="Model denylist glob (repeatable, e.g. --deny '*vision*').",
    ),
    clear_filters: bool = typer.Option(
        False,
        "--clear-filters",
        help="Clear all model allow/deny filters.",
    ),
) -> None:
    """Update an upstream provider's settings (partial update)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    body: dict[str, Any] = {}
    if base_url is not None:
        body["openai_base_url"] = base_url.rstrip("/")
    if auth_header is not None:
        body["auth_header"] = auth_header
    if advertise_models is not None:
        body["advertise_models"] = advertise_models
    if enabled is not None:
        body["enabled"] = enabled
    if clear_filters:
        body["model_filters"] = {}
    elif allow_models is not None or deny_models is not None:
        body["model_filters"] = {
            "allow": allow_models or [],
            "deny": deny_models or [],
        }

    if not body:
        console.print("[yellow]Nothing to update.[/yellow]")
        raise typer.Exit(0)

    try:
        result = api_patch(f"/api/upstreams/{name}", json=body)
    except CliApiError as exc:
        die(str(exc))
        return

    console.print(f"[green]✓[/green] Updated upstream [bold]{name}[/bold]")
    console.print(Syntax(jsonlib.dumps(result, indent=2), "json", theme="ansi_dark", background_color="default"))


# ── delete ────────────────────────────────────────────────────────────────


@app.command("delete")
def delete_upstream(
    name: str = typer.Argument(..., help="Upstream name to delete."),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Remove an upstream provider."""
    if not force:
        confirm = typer.confirm(
            f"Delete upstream [bold]{name}[/bold]? This cannot be undone."
        )
        if not confirm:
            console.print("Aborted.")
            raise typer.Exit(0)

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    try:
        api_delete(f"/api/upstreams/{name}")
    except CliApiError as exc:
        die(str(exc))
        return

    console.print(f"[green]✓[/green] Deleted upstream [bold]{name}[/bold]")


# ── test ──────────────────────────────────────────────────────────────────


@app.command("test")
def test_upstream(
    name: str = typer.Argument(..., help="Upstream name to probe."),
) -> None:
    """Probe an upstream's reachability and auth."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    try:
        result = api_post(f"/api/upstreams/{name}/test")
    except CliApiError as exc:
        die(str(exc))
        return

    ok = result.get("ok", False)
    status = result.get("status", "unknown")
    if ok:
        console.print(f"[green]✓[/green] Upstream [bold]{name}[/bold] is reachable ({status})")
    else:
        console.print(f"[red]✗[/red] Upstream [bold]{name}[/bold] is unreachable: {result.get('error', status)}")


# ── set-credentials ───────────────────────────────────────────────────────


@app.command("set-credentials")
def set_credentials(
    name: str = typer.Argument(..., help="Provider name."),
    key: str = typer.Option(
        ...,
        "--key",
        prompt="API key",
        hide_input=True,
        help="API key (secret; prompted securely if omitted).",
    ),
) -> None:
    """Set an API key for a provider (writes to api.env)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    try:
        result = api_post(f"/api/providers/{name}/credentials", json={"api_key": key})
    except CliApiError as exc:
        die(str(exc))
        return

    console.print(
        f"[green]✓[/green] Credentials stored for provider [bold]{name}[/bold] "
        f"({result.get('provider')})"
    )
