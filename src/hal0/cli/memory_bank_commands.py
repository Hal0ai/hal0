"""``hal0 memory bank`` — Hindsight bank admin CLI.

Thin HTTP client over the allowlisted passthrough in
:mod:`hal0.api.routes.memory_admin` (``/api/memory/banks/...``), which
forwards verbatim to the Hindsight REST API (``/v1/default/banks/...``).

Field-shape note, updated for a live 0.8.4 instance: ``GET``/``PUT
.../profile`` is now **deprecated** upstream — disposition-only, no
mission fields, and PUT there stopped being the way to change anything
that matters (confirmed live: setting ``retain_mission``/
``observations_mission`` on the ``shared`` bank only took effect via
``PATCH .../config``; ``GET /profile`` kept reporting an empty
``mission`` the whole time). ``GET .../config`` turns out to carry
*everything* — its fully-resolved ``config`` dict includes
``disposition_skepticism``/``disposition_literalism``/
``disposition_empathy`` alongside ``retain_mission``/
``observations_mission``/``reflect_mission`` — so both ``bank profile
get`` and ``bank profile set`` now go through ``/config`` alone:

  - ``get`` merges ``GET .../profile`` (just for ``name``) with
    ``GET .../config`` (disposition + all three missions).
  - ``set`` is a single ``PATCH .../config`` with ``{"updates": {...}}``
    carrying whichever of the six fields the caller passed — config
    updates are a partial merge server-side, so no more read-merge-write
    dance for disposition like the old profile-PUT path needed.

CLI flags are unchanged; only what they call moved.
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
    die,
)

app = typer.Typer(help="Manage Hindsight banks (list, stats, profile, export/import, consolidate).")
console = Console()

_DISPOSITION_FIELDS = ("skepticism", "literalism", "empathy")
#: CLI flag name -> bank-config field name (Python field format, matches
#: BankConfigUpdate's ``updates`` dict and what GET .../config's resolved
#: ``config`` dict reports back).
_CONFIG_FIELD_MAP = {
    "retain_mission": "retain_mission",
    "observations_mission": "observations_mission",
    "reflect_mission": "reflect_mission",
    "skepticism": "disposition_skepticism",
    "literalism": "disposition_literalism",
    "empathy": "disposition_empathy",
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


def _fetch_merged_profile(bank: str) -> dict[str, Any]:
    """Merge ``GET .../profile`` (name) with ``GET .../config`` (disposition + missions).

    ``/profile``'s own ``disposition``/``mission`` fields are stale on a
    live 0.8.4 bank whose disposition/missions were set via ``/config``
    (confirmed against the ``shared`` bank) — ``/config``'s fully-resolved
    ``config`` dict is the actual source of truth for both, so it wins.
    """
    profile = api_get(f"/api/memory/banks/{bank}/profile") or {}
    config = (api_get(f"/api/memory/banks/{bank}/config") or {}).get("config") or {}
    return {
        "bank_id": bank,
        "name": profile.get("name"),
        "disposition": {
            "skepticism": config.get("disposition_skepticism"),
            "literalism": config.get("disposition_literalism"),
            "empathy": config.get("disposition_empathy"),
        },
        "retain_mission": config.get("retain_mission"),
        "observations_mission": config.get("observations_mission"),
        "reflect_mission": config.get("reflect_mission"),
    }


def _render_profile(payload: dict[str, Any], bank: str) -> None:
    t = Table.grid(padding=(0, 2))
    t.add_column("k", style="dim")
    t.add_column("v")
    t.add_row("Name", str(payload.get("name") or "—"))
    disp = payload.get("disposition") or {}
    t.add_row(
        "Disposition",
        ", ".join(f"{k}={disp.get(k)}" for k in _DISPOSITION_FIELDS if disp.get(k) is not None)
        or "—",
    )
    t.add_row("Retain mission", str(payload.get("retain_mission") or "[dim]none[/dim]"))
    t.add_row("Observations mission", str(payload.get("observations_mission") or "[dim]none[/dim]"))
    t.add_row("Reflect mission", str(payload.get("reflect_mission") or "[dim]none[/dim]"))
    console.print(Panel(t, title=f"memory · bank {bank} · profile", border_style="dim"))


@profile_app.command("get")
def profile_get_cmd(
    bank: str = typer.Argument(..., help="Bank id."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Show a bank's profile (name, disposition traits, missions — via /config)."""
    _require_api()
    try:
        merged = _fetch_merged_profile(bank)
    except CliApiError as exc:
        die(str(exc))
        return
    _emit(merged, json_out, title="profile", render=lambda payload: _render_profile(payload, bank))


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
    """Set a bank's profile — a single ``PATCH .../config`` call.

    All six fields (--retain-mission/--observations-mission/
    --reflect-mission/--skepticism/--literalism/--empathy) are bank-config
    overrides now (``/profile`` PUT is deprecated upstream, disposition-only,
    and doesn't actually apply — see module docstring). Config updates are
    a partial merge server-side, so only the fields you pass change.
    """
    _require_api()

    updates = {
        _CONFIG_FIELD_MAP[flag]: value
        for flag, value in (
            ("retain_mission", retain_mission),
            ("observations_mission", observations_mission),
            ("reflect_mission", reflect_mission),
            ("skepticism", skepticism),
            ("literalism", literalism),
            ("empathy", empathy),
        )
        if value is not None
    }

    if not updates:
        die(
            "pass at least one of --retain-mission/--observations-mission/--reflect-mission "
            "/--skepticism/--literalism/--empathy"
        )
        return

    try:
        api_patch(f"/api/memory/banks/{bank}/config", json={"updates": updates})
        merged = _fetch_merged_profile(bank)
    except CliApiError as exc:
        die(str(exc))
        return

    _emit(merged, json_out, title="profile", render=lambda payload: _render_profile(payload, bank))


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
