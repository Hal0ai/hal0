"""hal0 auth subcommands — thin HTTP client to /api/auth/*.

§5.2 of the R5 sync assessment: the CLI shipped zero verbs for the R3/R4
auth surface (rotation, posture toggle, status probe) even though the
underlying API routes have existed since KB-1. On-box `rotate` is the
lockout recovery path when a bearer is lost or compromised; `require`
flips the `[security].require_auth` posture live; `status` mirrors what
the dashboard's own auth gate reads.

Endpoints hit
-------------

    hal0 auth status                → GET  /api/auth/status
    hal0 auth rotate <admin|client> → POST /api/auth/rotate
    hal0 auth require <on|off>      → PUT  /api/auth/require
    sudo hal0 auth reset-key        → POST /api/auth/rotate (falls back to a
                                      local api.env write when the API is down)

All three go through the shared api_* helpers in `_shared`, so they carry
`_auth_headers()` on auth-enabled boxes exactly like every other CLI
surface — never a bespoke unauthenticated client.
"""

from __future__ import annotations

import json as jsonlib
import os
from enum import StrEnum

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_get,
    api_post,
    api_put,
    die,
)

app = typer.Typer(help="Manage hal0 API authentication (status, key rotation, enforcement).")
console = Console()


class KeyTier(StrEnum):
    admin = "admin"
    client = "client"


class RequireToggle(StrEnum):
    on = "on"
    off = "off"


@app.command("status")
def auth_status(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw /api/auth/status JSON for CI/pipe use."
    ),
) -> None:
    """Show the current auth posture (GET /api/auth/status)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/auth/status")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(data, indent=2))
        return
    table = Table(show_header=False, title="hal0 auth status")
    table.add_row("auth_required", str(data.get("auth_required")))
    table.add_row("has_admin_key", str(data.get("has_admin_key")))
    table.add_row("tier (this caller)", str(data.get("tier")))
    console.print(table)


@app.command("rotate")
def auth_rotate(
    tier: KeyTier = typer.Argument(KeyTier.admin, help="Which box key to rotate: admin | client."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip the confirmation prompt."),
) -> None:
    """Rotate the admin or client box key (POST /api/auth/rotate).

    Mints a fresh key on the daemon and writes it to /etc/hal0/api.env —
    the value is never returned, printed, or logged here; retrieve it
    on-box afterwards. Existing bearer/API callers on the OLD key stop
    working immediately; the operator's own browser session survives
    (see the route docstring for details).
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    if not force:
        typer.confirm(
            f"Rotate the {tier.value} key? Callers using the OLD {tier.value} key will "
            "stop working immediately.",
            abort=True,
        )
    try:
        result = api_post("/api/auth/rotate", json={"tier": tier.value})
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        f"[green]Rotated[/green] {tier.value} key — fingerprint {result.get('fingerprint')}"
    )
    console.print(f"[dim]{result.get('note', '')}[/dim]")


@app.command("require")
def auth_require(
    toggle: RequireToggle = typer.Argument(
        ..., help="Enable or disable auth enforcement: on | off."
    ),
) -> None:
    """Persist the [security].require_auth posture (PUT /api/auth/require)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_put("/api/auth/require", json={"require_auth": toggle == RequireToggle.on})
    except CliApiError as exc:
        die(str(exc))
        return
    state = "ON" if result.get("require_auth") else "OFF"
    console.print(f"auth enforcement is now [bold]{state}[/bold] (applies live, no restart).")


@app.command("reset-key")
def auth_reset_key(
    force: bool = typer.Option(False, "--force", "-f", help="Skip the confirmation prompt."),
) -> None:
    """Mint a new admin key and print it once — the lost-key recovery path.

    Must run on the box as root: it reads and writes /etc/hal0/api.env, the
    root-only file the key lives in. When hal0-api is up the rotation goes
    through it (authenticated with the box's own key from api.env), so the
    new key applies live. When the API is down it is written straight to
    api.env and takes effect on the next ``systemctl restart hal0-api``.
    Callers on the old key (scripts, bearer clients) stop working; an
    already signed-in browser session stays signed in.

    On a box with no admin key yet this mints the first one, which arms the
    LAN admin gate (:func:`hal0.api.auth._lan_admin_gate`) on a box bound past
    loopback: ADMIN-class routes then need the key from other devices even
    with ``require_auth`` off. The prompt says so.
    """
    if os.geteuid() != 0:
        die("reset-key needs root to read /etc/hal0/api.env — run: sudo hal0 auth reset-key")
        return
    if not force:
        typer.confirm(
            "Mint a new admin key? Anything still using the old key will stop working, "
            "and while a key is set, admin pages and changes need it from other "
            "devices whenever this box is reachable from your network.",
            abort=True,
        )

    from hal0.service_identity import keys_from_api_env, rotate_api_env_key

    applied_live = False
    url = _api_base()
    if not _api_unreachable(url):
        try:
            api_post("/api/auth/rotate", json={"tier": "admin"})
            applied_live = True
        except CliApiError as exc:
            console.print(
                f"[yellow]API rotation failed ({exc}); writing api.env directly.[/yellow]"
            )
    if not applied_live:
        rotate_api_env_key("admin")

    key = keys_from_api_env().get("HAL0_ADMIN_KEY")
    if not key:
        die("rotation reported success but no HAL0_ADMIN_KEY is readable in /etc/hal0/api.env")
        return
    console.print("\n[bold]New hal0 admin key[/bold] (store it in a password manager):\n")
    # Plain print, not rich: the key must come out byte-exact for copy/paste.
    typer.echo(f"    {key}\n")
    if applied_live:
        console.print("[dim]Applied live — no restart needed.[/dim]")
    else:
        console.print(
            "[yellow]The running hal0-api did not take the new key; it applies after "
            "`sudo systemctl restart hal0-api`.[/yellow]"
        )


__all__ = ["app"]
