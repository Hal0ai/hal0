"""``hal0 memory bank`` — Hindsight bank admin CLI.

Thin HTTP client over the allowlisted passthrough in
:mod:`hal0.api.routes.memory_admin` (``/api/memory/banks/...``), which
forwards verbatim to the Hindsight REST API (``/v1/default/banks/...``).

Field-shape note (checked against a live 0.7.2 instance's ``/openapi.json``):
Hindsight splits "profile" across two upstream resources that this CLI's
``bank profile`` command presents as one surface:

  - ``PUT .../profile`` only accepts ``{"disposition": {skepticism,
    literalism, empathy}}`` (``UpdateDispositionRequest``) — all three
    traits are required, so ``profile set`` fetches the current profile
    and merges in only the traits the caller passed.
  - ``retain_mission`` / ``observations_mission`` / ``reflect_mission``
    are bank *config* overrides (``BankTemplateConfig``), set via
    ``PATCH .../config`` with ``{"updates": {...}}`` — not the profile
    endpoint at all, despite reading like profile fields.

``bank profile set`` routes each flag to the correct upstream call and
only sends the fields the caller actually passed.
"""

from __future__ import annotations

import json as jsonlib
import pathlib
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
    api_put,
    die,
)

app = typer.Typer(help="Manage Hindsight banks (list, stats, profile, export/import, consolidate).")
console = Console()

_DISPOSITION_FIELDS = ("skepticism", "literalism", "empathy")
_CONFIG_MISSION_FIELDS = {
    "retain_mission": "retain_mission",
    "observations_mission": "observations_mission",
    "reflect_mission": "reflect_mission",
}


def _require_api() -> str:
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    return url


def _emit(payload: Any, json_out: bool, *, title: str, render: Any) -> None:
    if json_out:
        typer.echo(jsonlib.dumps(payload, indent=2, sort_keys=True))
        return
    render(payload)


# ── ``hal0 memory bank list`` ───────────────────────────────────────────────


@app.command("list")
def bank_list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a table."),
) -> None:
    """List banks with fact counts and last-activity timestamps."""
    _require_api()
    try:
        result = api_get("/api/memory/banks")
    except CliApiError as exc:
        die(str(exc))
        return
    banks = (result or {}).get("banks", []) if isinstance(result, dict) else []

    def render(_payload: Any) -> None:
        if not banks:
            console.print("[dim]No banks.[/dim]")
            return
        t = Table(title="memory · banks")
        t.add_column("bank_id", style="bold")
        t.add_column("name")
        t.add_column("facts", justify="right")
        t.add_column("last activity")
        t.add_column("created")
        for b in banks:
            t.add_row(
                str(b.get("bank_id", "—")),
                str(b.get("name", "—")),
                str(b.get("fact_count", 0)),
                str(b.get("last_document_at") or "[dim]never[/dim]"),
                str(b.get("created_at", "—")),
            )
        console.print(t)

    _emit(result, json_out, title="memory · banks", render=render)


# ── ``hal0 memory bank stats`` ──────────────────────────────────────────────


@app.command("stats")
def bank_stats_cmd(
    bank: str = typer.Argument(..., help="Bank id (e.g. 'shared', 'private__hermes')."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Show detailed stats for a single bank (nodes, links, operations, ...)."""
    _require_api()
    try:
        s = api_get(f"/api/memory/banks/{bank}/stats")
    except CliApiError as exc:
        die(str(exc))
        return

    def render(payload: dict[str, Any]) -> None:
        t = Table.grid(padding=(0, 2))
        t.add_column("k", style="dim")
        t.add_column("v")
        t.add_row("Total nodes", str(payload.get("total_nodes", 0)))
        t.add_row("Total links", str(payload.get("total_links", 0)))
        t.add_row("Total documents", str(payload.get("total_documents", 0)))
        t.add_row("Pending operations", str(payload.get("pending_operations", 0)))
        t.add_row("Failed operations", str(payload.get("failed_operations", 0)))
        last_c = payload.get("last_consolidated_at") or "[dim]never[/dim]"
        t.add_row("Last consolidated", str(last_c))
        by_status = payload.get("operations_by_status") or {}
        if by_status:
            t.add_row("Ops by status", ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
        console.print(Panel(t, title=f"memory · bank {bank} · stats", border_style="dim"))

    _emit(s, json_out, title="stats", render=render)


# ── ``hal0 memory bank profile get/set`` ────────────────────────────────────

profile_app = typer.Typer(help="Bank profile (disposition + mission).")
app.add_typer(profile_app, name="profile")


def _render_profile(payload: dict[str, Any], bank: str) -> None:
    t = Table.grid(padding=(0, 2))
    t.add_column("k", style="dim")
    t.add_column("v")
    t.add_row("Name", str(payload.get("name", "—")))
    t.add_row("Mission", str(payload.get("mission") or "[dim]none[/dim]"))
    disp = payload.get("disposition") or {}
    t.add_row(
        "Disposition",
        ", ".join(f"{k}={disp.get(k)}" for k in _DISPOSITION_FIELDS if k in disp) or "—",
    )
    if payload.get("background"):
        t.add_row("Background (deprecated)", str(payload["background"]))
    console.print(Panel(t, title=f"memory · bank {bank} · profile", border_style="dim"))


@profile_app.command("get")
def profile_get_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Show a bank's profile (name, mission, disposition traits)."""
    _require_api()
    try:
        p = api_get(f"/api/memory/banks/{bank}/profile")
    except CliApiError as exc:
        die(str(exc))
        return
    _emit(p, json_out, title="profile", render=lambda payload: _render_profile(payload, bank))


@profile_app.command("set")
def profile_set_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    retain_mission: str | None = typer.Option(
        None,
        "--retain-mission",
        help="Steers what gets extracted during retain (bank config override).",
    ),
    observations_mission: str | None = typer.Option(
        None,
        "--observations-mission",
        help="Controls what gets synthesised into observations (bank config override).",
    ),
    reflect_mission: str | None = typer.Option(
        None,
        "--reflect-mission",
        help="Mission/context for reflect operations (bank config override).",
    ),
    skepticism: int | None = typer.Option(
        None, "--skepticism", min=1, max=5, help="1=trusting .. 5=skeptical (disposition trait)."
    ),
    literalism: int | None = typer.Option(
        None, "--literalism", min=1, max=5, help="1=flexible .. 5=literal (disposition trait)."
    ),
    empathy: int | None = typer.Option(
        None, "--empathy", min=1, max=5, help="1=detached .. 5=empathetic (disposition trait)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Read-modify-write a bank's profile.

    Disposition traits (--skepticism/--literalism/--empathy) go through
    ``PUT .../profile``; mission fields (--retain-mission/
    --observations-mission/--reflect-mission) go through
    ``PATCH .../config`` — see module docstring for why these are split.
    Only the fields you pass are changed; everything else is left alone.
    """
    _require_api()

    config_updates = {
        flag: value
        for flag, value in (
            ("retain_mission", retain_mission),
            ("observations_mission", observations_mission),
            ("reflect_mission", reflect_mission),
        )
        if value is not None
    }
    disposition_updates = {
        flag: value
        for flag, value in (
            ("skepticism", skepticism),
            ("literalism", literalism),
            ("empathy", empathy),
        )
        if value is not None
    }

    if not config_updates and not disposition_updates:
        die(
            "pass at least one of --retain-mission/--observations-mission/--reflect-mission "
            "/--skepticism/--literalism/--empathy"
        )
        return

    try:
        if disposition_updates:
            current = api_get(f"/api/memory/banks/{bank}/profile")
            disp = dict((current or {}).get("disposition") or {})
            disp.update(disposition_updates)
            # DispositionTraits requires all three fields on every PUT.
            for field in _DISPOSITION_FIELDS:
                disp.setdefault(field, 3)
            api_put(f"/api/memory/banks/{bank}/profile", json={"disposition": disp})
        if config_updates:
            api_patch(f"/api/memory/banks/{bank}/config", json={"updates": config_updates})
        final = api_get(f"/api/memory/banks/{bank}/profile")
    except CliApiError as exc:
        die(str(exc))
        return

    _emit(final, json_out, title="profile", render=lambda payload: _render_profile(payload, bank))


# ── ``hal0 memory bank export`` / ``import`` ────────────────────────────────


@app.command("export")
def bank_export_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    out: str | None = typer.Option(
        None, "--out", help="Write the export manifest to this path (default: print to stdout)."
    ),
) -> None:
    """Export a bank as a portable template manifest (synchronous, no polling needed)."""
    _require_api()
    try:
        manifest = api_get(f"/api/memory/banks/{bank}/export")
    except CliApiError as exc:
        die(str(exc))
        return
    text = jsonlib.dumps(manifest, indent=2, sort_keys=True)
    if out:
        pathlib.Path(out).write_text(text + "\n")
        console.print(f"[green]Wrote {out}[/green]")
    else:
        typer.echo(text)


@app.command("import")
def bank_import_cmd(
    bank: str = typer.Argument(..., help="Bank id to import into."),
    file: str = typer.Option(
        ..., "--file", help="Path to a bank template manifest (from 'bank export')."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate the manifest without writing (upstream ?dry_run=true)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Import a bank template manifest (synchronous, no polling needed)."""
    _require_api()
    path = pathlib.Path(file)
    if not path.is_file():
        die(f"no such file: {file}")
        return
    try:
        manifest = jsonlib.loads(path.read_text())
    except ValueError as exc:
        die(f"{file} is not valid JSON: {exc}")
        return
    try:
        result = api_post(
            f"/api/memory/banks/{bank}/import",
            json=manifest,
            params={"dry_run": "true"} if dry_run else None,
        )
    except CliApiError as exc:
        die(str(exc))
        return

    def render(payload: Any) -> None:
        label = "would import" if dry_run else "imported"
        console.print(
            Panel(
                f"[bold green]{label}[/bold green] into bank [bold]{bank}[/bold]\n{payload}",
                border_style="green",
            )
        )

    _emit(result, json_out, title="import", render=render)


# ── ``hal0 memory bank delete`` ─────────────────────────────────────────────


@app.command("delete")
def bank_delete_cmd(
    bank: str = typer.Argument(..., help="Bank id to delete."),
    confirm: str = typer.Option(
        ..., "--confirm", help="Must exactly match the bank id — refuses otherwise."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Irreversibly delete a bank (drops every memory/document/entity in it).

    Requires ``--confirm <bank>`` to exactly match the target bank id;
    the server enforces this too (``?confirm=``), but the CLI refuses
    before making the call so a typo doesn't even reach the wire.
    """
    if confirm != bank:
        die(f"--confirm must exactly match the bank id ({bank!r}, got {confirm!r}); refusing.")
        return
    _require_api()
    try:
        result = api_delete(f"/api/memory/banks/{bank}", params={"confirm": bank})
    except CliApiError as exc:
        die(str(exc))
        return

    def render(payload: Any) -> None:
        console.print(
            Panel(f"[bold red]Deleted bank {bank}[/bold red]\n{payload}", border_style="red")
        )

    _emit(result, json_out, title="delete", render=render)


# ── ``hal0 memory bank consolidate`` ────────────────────────────────────────


@app.command("consolidate")
def bank_consolidate_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    scope: list[str] = typer.Option(
        [],
        "--scope",
        help="Tag to scope consolidation to (repeatable). All given tags form a single "
        "scope; only memories with every tag in that scope are processed. "
        "Omit to consolidate every unconsolidated memory in the bank.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Trigger consolidation for a bank (async — returns an operation id)."""
    _require_api()
    body: dict[str, Any] | None = {"observation_scopes": [list(scope)]} if scope else None
    try:
        result = api_post(f"/api/memory/banks/{bank}/consolidate", json=body)
    except CliApiError as exc:
        die(str(exc))
        return

    def render(payload: dict[str, Any]) -> None:
        dedup = (
            " [dim](reused an existing pending operation)[/dim]"
            if payload.get("deduplicated")
            else ""
        )
        console.print(
            Panel(
                f"[bold green]Consolidation queued[/bold green]\n"
                f"operation_id = [bold]{payload.get('operation_id')}[/bold]{dedup}",
                border_style="green",
            )
        )

    _emit(result, json_out, title="consolidate", render=render)


__all__ = ["app"]
