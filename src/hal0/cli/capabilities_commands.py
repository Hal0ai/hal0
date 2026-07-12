"""``hal0 capabilities`` subcommands.

Operator tooling for the capability-slot surface (embed/voice/img/vision).
Two kinds of command live here:

* ``list`` / ``set`` — thin clients over the live API
  (``GET /api/capabilities``, ``POST /api/capabilities/{slot}/{child}``),
  the same endpoints the dashboard's Capability slots section and the
  hal0-admin MCP (``capability_list``/``capability_set``) already use.
  These need a *running* ``hal0-api`` — they go through the orchestrator
  so slot lifecycle (start/stop/reconcile) stays consistent.
* ``migrate`` — repair tooling that touches
  ``/etc/hal0/capabilities.toml`` directly, bypassing the running API.
  Used during upgrades and for repair after a manual edit goes wrong.

(The schema_version=1 → 2 migration that used to live here as a CLI
command now runs automatically on config load — see
``hal0.capabilities.config``. The old ``sync`` command is gone too:
``registry.toml`` is the sole model catalog.)

Migration is the reason ``migrate`` exists: when the catalog reshape
landed (model-first grouped rows + per-(backend, model) validation), any
previously-persisted selection that mixed an FLM chat tag with a GGUF
backend (or vice-versa) became illegal. The runtime orchestrator now
rejects such writes, but already-on-disk selections survive until a
write touches them. ``migrate`` walks the file, snaps illegal pairs to a
legal one (or clears the selection when the model is gone entirely), and
writes back atomically. Default is dry-run (matching
``hal0 migrate model-layout``'s contract) — pass ``--apply`` to write.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from hal0.capabilities.catalog import models_for_capability
from hal0.capabilities.config import (
    CapabilitySelection,
    capabilities_toml_path,
    load_capabilities_config,
    save_capabilities_config,
)
from hal0.capabilities.orchestrator import _CHILD_TO_CAPABILITY, LEGAL_SLOTS, legal_children
from hal0.cli._shared import CliApiError, _api_base, _api_unreachable, api_get, api_post, die
from hal0.config.locking import file_lock
from hal0.registry.store import ModelRegistry

app = typer.Typer(
    name="capabilities",
    help="Capability-slot configuration: list, set, repair + migration.",
    no_args_is_help=True,
)

console = Console()


# ── `hal0 capabilities list` — GET /api/capabilities ────────────────────────


@app.command("list")
def list_capabilities() -> None:
    """List capability-slot selections (embed/voice/img/vision) from the live API.

    Thin client over ``GET /api/capabilities`` — the same payload the
    dashboard's Capability slots section renders. Shows the persisted
    (backend, provider, model, enabled) selection for every (slot, child)
    pair the orchestrator knows about.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/capabilities")
    except CliApiError as exc:
        die(str(exc))
        return

    selections = data.get("selections") if isinstance(data, dict) else None
    if not isinstance(selections, dict) or not selections:
        console.print("[dim]no capability selections configured.[/dim]")
        return

    table = Table(title="Capability selections")
    table.add_column("Slot", style="bold")
    table.add_column("Child")
    table.add_column("Backend")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Enabled")
    for slot, children in sorted(selections.items()):
        if not isinstance(children, dict):
            continue
        for child, sel in sorted(children.items()):
            if not isinstance(sel, dict):
                continue
            enabled = sel.get("enabled")
            table.add_row(
                slot,
                child,
                str(sel.get("backend") or "—"),
                str(sel.get("provider") or "—"),
                str(sel.get("model") or "—"),
                "[green]yes[/green]" if enabled else "[dim]no[/dim]",
            )
    console.print(table)


# ── `hal0 capabilities set` — POST /api/capabilities/{slot}/{child} ─────────


@app.command("set")
def set_capability(
    slot: str = typer.Argument(..., help=f"Capability slot: {', '.join(LEGAL_SLOTS)}."),
    child: str = typer.Argument(..., help="Child within the slot (e.g. 'default')."),
    model: str | None = typer.Option(None, "--model", help="Model id to select."),
    backend: str | None = typer.Option(None, "--backend", help="Backend id to select."),
    provider: str | None = typer.Option(None, "--provider", help="Provider tag for the backend."),
    enabled: bool | None = typer.Option(
        None,
        "--enabled/--disabled",
        help="Enable or disable this (slot, child) selection.",
    ),
) -> None:
    """Apply a partial capability selection update.

    Thin client over ``POST /api/capabilities/{slot}/{child}`` — reconciles
    slot lifecycle via the running ``CapabilityOrchestrator`` (starts/stops/
    swaps the backing slot as needed), the same path the dashboard uses.
    Only the flags you pass are sent; omitted fields keep their current value.
    """
    if slot not in LEGAL_SLOTS:
        die(f"unknown capability slot {slot!r} — legal: {', '.join(LEGAL_SLOTS)}")
        return
    legal = legal_children(slot)
    if child not in legal:
        die(f"child {child!r} not valid for slot {slot!r} — legal: {', '.join(legal)}")
        return

    body: dict[str, Any] = {}
    if model is not None:
        body["model"] = model
    if backend is not None:
        body["backend"] = backend
    if provider is not None:
        body["provider"] = provider
    if enabled is not None:
        body["enabled"] = enabled
    if not body:
        die("nothing to set — pass at least one of --model/--backend/--provider/--enabled.")
        return

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_post(f"/api/capabilities/{slot}/{child}", json=body)
    except CliApiError as exc:
        die(str(exc))
        return

    selection = result.get("selection") if isinstance(result, dict) else None
    console.print(f"[green]✓[/green]  {slot}/{child} → {selection or body}")


def _classify_pair(
    capability: str,
    model: str,
    backend: str,
    registry: ModelRegistry | None,
) -> tuple[str, list[str]]:
    """Return ``(verdict, legal_backends)`` for one (capability, model, backend) tuple.

    Verdict is one of:

    - ``"empty"`` — no model selected; nothing to migrate.
    - ``"ok"``    — model is in the catalog and backend is in its
                    ``backends`` list. Legal as-is.
    - ``"unknown_model"`` — model id isn't advertised for this capability
                            (and the registry doesn't carry it). The
                            migration will clear the selection.
    - ``"illegal_backend"`` — model exists, but the persisted backend
                              can't actually serve it. The migration
                              will snap the backend to the model's first
                              legal option.

    ``legal_backends`` is the model's full ``backends`` list (id-only)
    when the model exists, or ``[]`` otherwise.
    """
    if not model:
        return "empty", []
    rows = models_for_capability(capability, registry=registry)
    match = next((row for row in rows if row["id"] == model), None)
    if match is None:
        return "unknown_model", []
    legal = [b["id"] for b in match.get("backends", [])]
    if backend and backend in legal:
        return "ok", legal
    return "illegal_backend", legal


@app.command()
def migrate(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the migration. Without this, the command is a dry-run.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        hidden=True,
        help="Deprecated no-op — dry-run is now the default; pass --apply to write.",
    ),
) -> None:
    """Rewrite persisted selections that are illegal against the live catalog.

    Walks ``/etc/hal0/capabilities.toml`` and, for each non-empty
    selection, validates the (model, backend) pair against
    ``models_for_capability``. Selections where the backend can't serve
    the model are snapped to the model's first legal backend; selections
    whose model is no longer in the catalog are cleared (backend stays
    intact so the dashboard can still show what was previously chosen).

    Default is dry-run (no flags = safe preview); pass ``--apply`` to
    write, matching ``hal0 migrate model-layout --apply``'s contract.
    Idempotent — running ``--apply`` twice is a no-op once everything is
    legal.
    """
    del dry_run  # deprecated hidden flag — dry-run is the unconditional default now
    # SC-10: the load → diff → save is one read-modify-write. Hold the same
    # capabilities.toml advisory lock the running API uses so a ``migrate``
    # invoked while hal0-api is applying a selection cannot read a stale copy
    # and clobber the API's concurrent write (or vice-versa). The lock spans
    # the whole span — locking only the ``save`` would leave the stale read
    # unguarded. Dry-run holds it too (cheap) so the preview reflects a
    # consistent snapshot.
    with file_lock(capabilities_toml_path()):
        cfg = load_capabilities_config()
        registry = ModelRegistry()

        changes: list[dict[str, str]] = []
        for slot, children in cfg.selections.items():
            for child, sel in children.items():
                capability = _CHILD_TO_CAPABILITY.get((slot, child))
                if capability is None:
                    continue
                verdict, legal = _classify_pair(capability, sel.model, sel.backend, registry)
                if verdict in {"empty", "ok"}:
                    continue
                if verdict == "illegal_backend":
                    new_backend = legal[0] if legal else ""
                    # Re-resolve provider against the matching row so the
                    # slot rewrite uses the right runtime tag.
                    rows = models_for_capability(capability, registry=registry)
                    row = next((r for r in rows if r["id"] == sel.model), None)
                    new_provider = ""
                    if row is not None:
                        backend_meta = next(
                            (b for b in row.get("backends", []) if b["id"] == new_backend),
                            None,
                        )
                        if backend_meta is not None:
                            new_provider = backend_meta.get("provider", "") or ""
                    changes.append(
                        {
                            "slot": slot,
                            "child": child,
                            "model": sel.model,
                            "before": f"{sel.backend or '—'} / {sel.provider or '—'}",
                            "after": f"{new_backend or '—'} / {new_provider or '—'}",
                            "reason": "backend cannot serve model",
                        }
                    )
                    if apply:
                        children[child] = CapabilitySelection(
                            backend=new_backend,
                            provider=new_provider,
                            model=sel.model,
                            enabled=sel.enabled,
                        )
                elif verdict == "unknown_model":
                    changes.append(
                        {
                            "slot": slot,
                            "child": child,
                            "model": sel.model,
                            "before": f"{sel.backend or '—'} / {sel.provider or '—'}",
                            "after": "(cleared)",
                            "reason": "model not in catalog",
                        }
                    )
                    if apply:
                        children[child] = CapabilitySelection(
                            backend=sel.backend,
                            provider=sel.provider,
                            model="",
                            enabled=False,
                        )

        if not changes:
            console.print(
                f"[green]nothing to migrate[/green] — every selection in "
                f"{capabilities_toml_path()} is legal against the current catalog."
            )
            raise typer.Exit(0)

        table = Table(title="capabilities migrate")
        table.add_column("slot", style="bold")
        table.add_column("child")
        table.add_column("model")
        table.add_column("before")
        table.add_column("after")
        table.add_column("reason")
        for c in changes:
            table.add_row(c["slot"], c["child"], c["model"], c["before"], c["after"], c["reason"])
        console.print(table)

        if not apply:
            console.print(
                f"\n[yellow]--dry-run[/yellow] — would rewrite {len(changes)} selection(s) "
                f"in {capabilities_toml_path()}; pass --apply to write."
            )
            raise typer.Exit(0)

        save_capabilities_config(cfg)
        console.print(
            f"\n[green]migrated[/green] {len(changes)} selection(s) in {capabilities_toml_path()}."
        )
