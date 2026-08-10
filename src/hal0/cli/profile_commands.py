"""``hal0 profile`` subcommands — thin CLI over ``/api/profiles``.

Before this file existed there was no CLI surface for profiles at all
(#1796): 17 server-side profiles with full CRUD live at ``/api/profiles``
(+``/generate``, ``/import``, ``/export``), and every slot TOML references
one by name, but ``hal0 profile`` returned "No such command". ``list`` /
``show`` cover the minimum read surface an operator needs to answer "what
profiles exist" and "what does this one resolve to" without a REST client —
mirroring the read half of the dashboard's Profiles page and
``GET /api/profiles`` / ``GET /api/profiles/{name}``. Write operations
(create/update/delete/generate/import/export) stay API/dashboard-only for
now; add them here the same way if operators ask for scriptable writes.
"""

from __future__ import annotations

import json as jsonlib

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, die

app = typer.Typer(
    name="profile",
    help="Inspect launch-flag profiles (read-only; full CRUD lives in the dashboard/API).",
    no_args_is_help=True,
)

console = Console()


@app.command("list")
def profile_list(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/profiles JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """List every profile in the catalog (GET /api/profiles)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        profiles = api_get("/api/profiles")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(profiles, indent=2))
        return
    if not isinstance(profiles, list) or not profiles:
        console.print("[dim]No profiles available.[/dim]")
        return
    table = Table(title=f"Profiles ({len(profiles)})")
    table.add_column("Name", style="bold")
    table.add_column("Device")
    table.add_column("Backend")
    table.add_column("MTP")
    table.add_column("Intent")
    table.add_column("tok/s", justify="right")
    table.add_column("Used by")
    for p in profiles:
        if not isinstance(p, dict):
            continue
        tps = p.get("tps")
        used_by = p.get("used_by") or []
        table.add_row(
            p.get("name", "—"),
            p.get("device_class") or "—",
            p.get("backend") or "—",
            "yes" if p.get("mtp") else "",
            p.get("intent") or "—",
            f"{tps:.1f}" if isinstance(tps, (int, float)) else "—",
            ", ".join(used_by) if used_by else "—",
        )
    console.print(table)


@app.command("show")
def profile_show(
    name: str = typer.Argument(..., help="Profile name to inspect"),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/profiles/{name} JSON for CI/pipe use (no Rich panel).",
    ),
) -> None:
    """Resolve a single profile by name (GET /api/profiles/{name})."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        profile = api_get(f"/api/profiles/{name}")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(profile, indent=2))
        return
    if not isinstance(profile, dict):
        die(f"unexpected response for profile {name!r}")
        return
    for key in (
        "name",
        "device_class",
        "backend",
        "mtp",
        "intent",
        "quant",
        "tps",
        "rtf",
    ):
        if key in profile:
            console.print(f"[bold]{key}[/bold]: {profile[key]}")
    used_by = profile.get("used_by") or []
    console.print(f"[bold]used_by[/bold]: {', '.join(used_by) if used_by else '—'}")
    flags = profile.get("resolved_flags") or profile.get("flags")
    if flags:
        console.print(f"[bold]flags[/bold]: {flags}")


__all__ = ["app"]
