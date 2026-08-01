"""hal0 upstream subcommands — thin HTTP client to the hal0 API.

Provides ``hal0 upstream {list,show,create,update,delete,test}`` plus the
``hal0 upstream credentials set`` noun-subgroup.

Request/response shapes mirror ``hal0.api.routes.providers`` exactly:
create/update bodies are ``extra="forbid"`` on the server, so every flag
here maps 1:1 onto a real body field. Credentials go through the separate
``/api/providers/{name}/credentials`` route (``{key, value}``) so secrets
never transit the CRUD surface; ``create --api-key`` chains the two calls.

CLI consolidation (2026-07): ``hal0 upstream set-credentials`` is renamed to
``hal0 upstream credentials set`` — every other multi-facet resource in this
cluster (``agent bootstrap``, ``mcp catalog``, ``memory graph``) uses a
noun-subgroup, and the flat verb left no home for a future ``credentials
show``/``credentials clear``. ``set-credentials`` remains as a hidden
deprecated alias.
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


def _filters_summary(filters: Any) -> str:
    if not isinstance(filters, dict):
        return "all"
    parts = []
    for key in ("models", "include", "exclude"):
        vals = filters.get(key) or []
        if vals:
            parts.append(f"{key}:{len(vals)}")
    return ", ".join(parts) if parts else "all"


def _print_json(data: Any) -> None:
    console.print(
        Syntax(
            jsonlib.dumps(data, indent=2),
            "json",
            theme="ansi_dark",
            background_color="default",
        )
    )


# ── list ──────────────────────────────────────────────────────────────────


@app.command("list")
def list_upstreams(
    json_out: bool = typer.Option(False, "--json", help="Output raw JSON instead of a table."),
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
    table.add_column("Advertise")
    table.add_column("Auth")
    table.add_column("Filters")

    for u in ups:
        if u.get("auth_key_present"):
            auth = "✓ key set"
        elif u.get("auth_value_env"):
            auth = f"✗ {u['auth_value_env']} unset"
        else:
            auth = "—"
        table.add_row(
            u.get("name", "—"),
            u.get("kind", "—"),
            u.get("url", "—"),
            "✓" if u.get("enabled", True) else "✗",
            "✓" if u.get("advertise_models", True) else "✗",
            auth,
            _filters_summary(u.get("model_filters")),
        )
    console.print(table)


# ── advertise ──────────────────────────────────────────────────────────────

_ADVERTISE_ON = {"on", "true", "yes", "1", "enable", "show"}
_ADVERTISE_OFF = {"off", "false", "no", "0", "disable", "hide"}


@app.command("advertise")
def advertise_upstream(
    name: str = typer.Argument(..., help="Upstream name."),
    state: str = typer.Argument(..., help="on | off — toggle /v1/models visibility."),
) -> None:
    """Flip an upstream's ``advertise_models`` flag live (no hal0-api restart).

    Advertising controls catalog visibility only — dispatch/routing to the
    upstream by explicit id is unaffected. The change takes effect on the next
    ``/v1/models`` request (the API punches the composite catalog cache).
    """
    key = state.strip().lower()
    if key in _ADVERTISE_ON:
        value = True
    elif key in _ADVERTISE_OFF:
        value = False
    else:
        die(f"Invalid state {state!r} — expected 'on' or 'off'.")
        return

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    try:
        result = api_patch(f"/api/upstreams/{name}", json={"advertise_models": value})
    except CliApiError as exc:
        die(str(exc))
        return

    now_on = bool(result.get("advertise_models", value)) if isinstance(result, dict) else value
    label = "advertised" if now_on else "hidden"
    mark = "✓" if now_on else "✗"
    console.print(
        f"[green]{mark}[/green] Upstream [bold]{name}[/bold] is now [bold]{label}[/bold] "
        f"in /v1/models (advertise_models={str(now_on).lower()})."
    )


# ── show ──────────────────────────────────────────────────────────────────


@app.command("show")
def show_upstream(
    name: str = typer.Argument(..., help="Upstream name."),
    json_out: bool = typer.Option(False, "--json", help="Output raw JSON."),
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
    catalog: str = typer.Option(
        None,
        "--catalog",
        "-c",
        help="Prefill url/auth from the provider catalog (e.g. openrouter, anthropic).",
    ),
    base_url: str = typer.Option(
        None,
        "--url",
        "--base-url",
        help="OpenAI-compatible base URL (required unless --catalog supplies one).",
    ),
    auth_style: str = typer.Option(
        None,
        "--auth-style",
        help="Auth style: bearer | anthropic | header | none.",
    ),
    auth_header: str = typer.Option(
        None,
        "--auth-header",
        help="Header NAME when --auth-style header (e.g. X-Api-Key).",
    ),
    auth_env: str = typer.Option(
        None,
        "--auth-env",
        help="Env-var name holding the API key (e.g. OPENROUTER_API_KEY).",
    ),
    timeout: float = typer.Option(
        None,
        "--timeout",
        help="Request timeout in seconds (default 300).",
    ),
    api_key: str = typer.Option(
        None,
        "--api-key",
        "-k",
        help="API key value — written via the credentials route after create.",
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
    """Register a new upstream LLM provider (always kind=remote)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    body: dict[str, Any] = {
        "name": name,
        "advertise_models": advertise_models,
        "enabled": enabled,
    }
    if catalog is not None:
        body["catalog_id"] = catalog
    if base_url is not None:
        body["url"] = base_url.rstrip("/")
    if auth_style is not None:
        body["auth_style"] = auth_style
    if auth_header is not None:
        body["auth_header"] = auth_header
    if auth_env is not None:
        body["auth_value_env"] = auth_env
    if timeout is not None:
        body["timeout_seconds"] = timeout

    try:
        result = api_post("/api/upstreams", json=body)
    except CliApiError as exc:
        die(str(exc))
        return

    console.print(f"[green]✓[/green] Created upstream [bold]{name}[/bold]")
    _print_json(result)

    env_name = result.get("auth_value_env") or ""
    if api_key is not None:
        if not env_name:
            die(f"upstream {name!r} declares no auth_value_env; cannot store a key")
            return
        try:
            api_post(
                f"/api/providers/{name}/credentials",
                json={"key": env_name, "value": api_key},
            )
        except CliApiError as exc:
            die(f"upstream created, but storing the key failed: {exc}")
            return
        console.print(f"[green]✓[/green] Credential stored in api.env as [bold]{env_name}[/bold]")
        console.print(f"[dim]Try it: hal0 upstream test {name}[/dim]")
    elif hint := result.get("hint"):
        console.print(f"[dim]{hint}[/dim]")


# ── update ────────────────────────────────────────────────────────────────


@app.command("update")
def update_upstream(
    name: str = typer.Argument(..., help="Upstream name to update."),
    base_url: str = typer.Option(None, "--url", "--base-url", help="New base URL."),
    auth_style: str = typer.Option(
        None,
        "--auth-style",
        help="Auth style: bearer | anthropic | header | none.",
    ),
    auth_header: str = typer.Option(
        None, "--auth-header", help="Header NAME when auth-style is 'header'."
    ),
    auth_env: str = typer.Option(None, "--auth-env", help="Env-var name holding the API key."),
    timeout: float = typer.Option(None, "--timeout", help="Request timeout in seconds."),
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
    models: list[str] | None = typer.Option(
        None,
        "--model",
        help="Exact model id to allowlist (repeatable).",
    ),
    include: list[str] | None = typer.Option(
        None,
        "--include",
        help="Glob of model ids to advertise (repeatable, e.g. --include 'anthropic/*').",
    ),
    exclude: list[str] | None = typer.Option(
        None,
        "--exclude",
        help="Glob of model ids to hide (repeatable, e.g. --exclude '*:free'). Wins over include.",
    ),
    clear_filters: bool = typer.Option(
        False,
        "--clear-filters",
        help="Clear all model filters (advertise everything again).",
    ),
) -> None:
    """Update an upstream provider's settings (partial update)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    body: dict[str, Any] = {}
    if base_url is not None:
        body["url"] = base_url.rstrip("/")
    if auth_style is not None:
        body["auth_style"] = auth_style
    if auth_header is not None:
        body["auth_header"] = auth_header
    if auth_env is not None:
        body["auth_value_env"] = auth_env
    if timeout is not None:
        body["timeout_seconds"] = timeout
    if advertise_models is not None:
        body["advertise_models"] = advertise_models
    if enabled is not None:
        body["enabled"] = enabled
    if clear_filters:
        # All-empty filters ≡ clear on the API side.
        body["model_filters"] = {"models": [], "include": [], "exclude": []}
    elif models is not None or include is not None or exclude is not None:
        body["model_filters"] = {
            "models": models or [],
            "include": include or [],
            "exclude": exclude or [],
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
    _print_json(result)


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
    """Remove an upstream provider (its api.env credential is retained)."""
    if not force:
        confirm = typer.confirm(f"Delete upstream {name!r}? This cannot be undone.")
        if not confirm:
            console.print("Aborted.")
            raise typer.Exit(0)

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    try:
        result = api_delete(f"/api/upstreams/{name}")
    except CliApiError as exc:
        die(str(exc))
        return

    console.print(f"[green]✓[/green] Deleted upstream [bold]{name}[/bold]")
    if isinstance(result, dict) and (hint := result.get("hint")):
        console.print(f"[dim]{hint}[/dim]")


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
    if ok:
        latency = result.get("latency_ms")
        count = result.get("models_count")
        detail = []
        if latency is not None:
            detail.append(f"{latency:.0f} ms")
        if count is not None:
            detail.append(f"{count} models")
        suffix = f" ({', '.join(detail)})" if detail else ""
        console.print(f"[green]✓[/green] Upstream [bold]{name}[/bold] is reachable{suffix}")
    else:
        err = result.get("error") or result.get("status", "unknown")
        console.print(f"[red]✗[/red] Upstream [bold]{name}[/bold] is unreachable: {err}")
        raise typer.Exit(1)


# ── credentials ──────────────────────────────────────────────────────────

credentials_app = typer.Typer(help="Manage upstream provider credentials.")
app.add_typer(credentials_app, name="credentials")


def _do_set_credentials(name: str, key: str, env_var: str | None) -> None:
    """Shared implementation behind ``upstream credentials set`` and the
    deprecated ``upstream set-credentials`` alias."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    if env_var is None:
        # The credentials route binds the key to the upstream's declared
        # env-var — resolve it so the caller doesn't have to look it up.
        try:
            up = api_get(f"/api/upstreams/{name}")
        except CliApiError as exc:
            die(str(exc))
            return
        env_var = up.get("auth_value_env") or ""
        if not env_var:
            die(
                f"upstream {name!r} declares no auth_value_env; "
                "pass --env-var or set one via `hal0 upstream update --auth-env`"
            )
            return

    try:
        api_post(
            f"/api/providers/{name}/credentials",
            json={"key": env_var, "value": key},
        )
    except CliApiError as exc:
        die(str(exc))
        return

    console.print(
        f"[green]✓[/green] Credential stored for [bold]{name}[/bold] "
        f"in api.env as [bold]{env_var}[/bold]"
    )


@credentials_app.command("set")
def credentials_set(
    name: str = typer.Argument(..., help="Provider name."),
    key: str = typer.Option(
        ...,
        "--key",
        prompt="API key",
        hide_input=True,
        help="API key VALUE (secret; prompted securely if omitted).",
    ),
    env_var: str = typer.Option(
        None,
        "--env-var",
        help="Env-var name to write (defaults to the upstream's declared auth_value_env).",
    ),
) -> None:
    """Set an API key for a provider (writes to api.env)."""
    _do_set_credentials(name, key, env_var)


# HAL0-SUNSET: v1.0.0 — alias for `upstream credentials set`; drop the alias.
@app.command("set-credentials", hidden=True)
def set_credentials(
    name: str = typer.Argument(..., help="Provider name."),
    key: str = typer.Option(
        ...,
        "--key",
        prompt="API key",
        hide_input=True,
        help="API key VALUE (secret; prompted securely if omitted).",
    ),
    env_var: str = typer.Option(
        None,
        "--env-var",
        help="Env-var name to write (defaults to the upstream's declared auth_value_env).",
    ),
) -> None:
    """[DEPRECATED] alias for `upstream credentials set`; use that instead."""
    typer.echo(
        "[deprecated] `upstream set-credentials` is renamed to "
        "`upstream credentials set`; use `hal0 upstream credentials set`.",
        err=True,
    )
    _do_set_credentials(name, key, env_var)
