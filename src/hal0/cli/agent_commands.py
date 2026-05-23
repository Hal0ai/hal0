"""hal0 agent subcommands — bundled-agent lifecycle + approval queue.

Mirrors :mod:`hal0.cli.slot_commands` shape (Typer sub-app + thin HTTP
client). The lifecycle subcommands hit the routes in
:mod:`hal0.api.routes.agents`; the ``approvals`` sub-sub-app hits the
MCP-backend's approval queue at ``/api/agent/approvals`` (shape per
ADR-0004 §5 "Pending items").
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    api_post,
    die,
)
from hal0.mcp.approval_queue import _PRIMARY_TARGET_ARG

app = typer.Typer(help="Manage bundled agents (Phase 8 — pi-coder / Hermes-Agent).")
console = Console()

# Approvals lives as a sub-sub-app so ``hal0 agent approvals list``
# renders correctly in --help. Same pattern as the slot sub-app.
approvals_app = typer.Typer(help="Manage agent approval requests (gated destructives).")
app.add_typer(approvals_app, name="approvals")

# Bootstrap sub-sub-app — ``hal0 agent bootstrap hermes`` runs the
# Hermes provisioning state machine (v0.3 Phase 10 stream).
bootstrap_app = typer.Typer(help="Run bundled-agent bootstrap pipelines (Phase 10).")
app.add_typer(bootstrap_app, name="bootstrap")


# ── Lifecycle ────────────────────────────────────────────────────────────────


@app.command("install")
def agent_install(
    name: str = typer.Argument(..., help="Bundled agent name (pi-coder | hermes)."),
    switch: bool = typer.Option(
        False,
        "--switch",
        help=(
            "If another agent is already installed, atomically uninstall it before "
            "installing this one (single-pick enforced; ADR-0004 §2)."
        ),
    ),
) -> None:
    """Install a bundled agent."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    payload: dict[str, object] = {"name": name, "switch": switch}
    try:
        rec = api_post("/api/agents/install", json=payload)
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        Panel(
            f"[bold green]Installed[/bold green] {rec.get('name', name)}  "
            f"[dim](data: {rec.get('data_dir', '?')})[/dim]",
            border_style="green",
        )
    )


@app.command("uninstall")
def agent_uninstall(
    name: str = typer.Argument(..., help="Bundled agent name."),
) -> None:
    """Uninstall a bundled agent."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_delete(f"/api/agents/{name}")
    except CliApiError as exc:
        die(str(exc))
        return
    status = (result or {}).get("status", "uninstalled")
    if status == "not_installed":
        console.print(f"[dim]{name} was not installed.[/dim]")
    else:
        console.print(f"[bold]Uninstalled[/bold] {name}.")


@app.command("list")
def agent_list() -> None:
    """List installed bundled agents."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/agents")
    except CliApiError as exc:
        die(str(exc))
        return
    agents = data.get("agents", []) if isinstance(data, dict) else data
    if not agents:
        console.print("[dim]No bundled agents installed.[/dim]")
        return
    table = Table(title=f"Bundled agents ({len(agents)})")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Installed at")
    table.add_column("Data dir")
    for a in agents:
        table.add_row(
            a.get("name", "—"),
            a.get("status", "—"),
            a.get("installed_at", "—"),
            a.get("data_dir", "—"),
        )
    console.print(table)


# ── Approvals (MCP-backend owns the route shape; CLI assumes ADR-0004 §5) ────


def _fmt_enqueued_at(value: Any) -> str:
    """Project ``enqueued_at`` (epoch seconds float) to a short ISO string.

    Mirrors the dashboard's ``AgentApprovalRow.vue`` tooltip projection
    (``new Date(epoch * 1000).toISOString()``) — keep CLI + UI agreeing
    on a single representation so screenshots and CLI output read the
    same to operators.
    """
    if value in (None, "", "—"):
        return "—"
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        # Already a string-ish timestamp — pass through untouched.
        return str(value)
    return (
        datetime.fromtimestamp(epoch, tz=UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _approval_summary(entry: dict[str, Any]) -> str:
    """Build a one-line Summary from ``tool`` + primary target arg.

    Mirrors ``AgentApprovalRow.vue``'s ``primaryArg`` projection: the
    arg name comes from :data:`hal0.mcp.approval_queue._PRIMARY_TARGET_ARG`
    so the CLI and the dashboard agree on which field is the
    "distinguishing" one. When the tool has no registered primary arg
    we fall back to the first scalar in ``args`` so the operator still
    sees something more useful than the bare tool name. Truncated to
    60 chars to fit a reasonable terminal width without wrapping.
    """
    tool = str(entry.get("tool") or "—")
    args = entry.get("args")
    if not isinstance(args, dict) or not args:
        return tool[:60]

    primary_key = _PRIMARY_TARGET_ARG.get(tool)
    primary_val: Any = None
    if primary_key is not None:
        primary_val = args.get(primary_key)
    if primary_val is None:
        # No registered primary arg (or it's missing) — fall back to
        # the first scalar value, matching the Vue row's behaviour.
        for v in args.values():
            if isinstance(v, str | int | float | bool) and v != "":
                primary_val = v
                break

    if isinstance(primary_val, list | tuple):
        primary_val = ",".join(str(v) for v in primary_val)
    summary = tool if primary_val is None or primary_val == "" else f"{tool} {primary_val}"
    return summary[:60]


@approvals_app.command("list")
def approvals_list() -> None:
    """List pending agent approval requests."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/agent/approvals")
    except CliApiError as exc:
        die(str(exc))
        return
    items = data.get("approvals", []) if isinstance(data, dict) else data
    if not items:
        console.print("[dim]No pending approvals.[/dim]")
        return
    table = Table(title=f"Pending approvals ({len(items)})")
    table.add_column("ID", style="bold")
    table.add_column("Tool")
    table.add_column("Agent")
    table.add_column("Requested at")
    table.add_column("Summary")
    for it in items:
        # ApprovalEntry.as_dict() emits ``client_id`` + ``enqueued_at``
        # + ``args`` — NOT ``agent`` / ``requested_at`` / ``summary``.
        # Mirror ui/src/components/agent/AgentApprovalRow.vue's
        # projection so CLI + dashboard show the same row content.
        table.add_row(
            str(it.get("id", "—")),
            str(it.get("tool", "—")),
            str(it.get("client_id") or "—"),
            _fmt_enqueued_at(it.get("enqueued_at")),
            _approval_summary(it),
        )
    console.print(table)


@approvals_app.command("approve")
def approvals_approve(
    approval_id: str = typer.Argument(..., help="Approval request ID."),
) -> None:
    """Approve a pending agent action."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_post(f"/api/agent/approvals/{approval_id}/approve")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"[bold green]Approved[/bold green] {approval_id}.")


@approvals_app.command("deny")
def approvals_deny(
    approval_id: str = typer.Argument(..., help="Approval request ID."),
) -> None:
    """Deny a pending agent action."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_post(f"/api/agent/approvals/{approval_id}/deny")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"[bold]Denied[/bold] {approval_id}.")


# ── Bootstrap (Hermes provisioning, Phase 10) ────────────────────────────────


@bootstrap_app.command("hermes")
def bootstrap_hermes(
    repair: bool = typer.Option(
        False,
        "--repair",
        help="Re-run every phase regardless of checkpoint state (forces full rerun).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run phases but don't persist provision.json.",
    ),
    skip_phase: list[str] = typer.Option(
        [],
        "--skip-phase",
        help="Skip the named phase (may be repeated).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose phase log."),
) -> None:
    """Run the Hermes-Agent bootstrap state machine."""
    # Late import keeps the CLI startup snappy on hosts where the
    # hermes_provision module's downstream slices grow heavier deps.
    from hal0.agents.hermes_provision import bootstrap_cli

    rc = bootstrap_cli(
        repair=repair,
        dry_run=dry_run,
        skip_phases=tuple(skip_phase),
        verbose=verbose,
    )
    raise typer.Exit(rc)
