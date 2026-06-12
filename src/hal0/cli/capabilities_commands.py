"""``hal0 capabilities`` subcommands.

Operator tooling that touches ``/etc/hal0/capabilities.toml`` directly,
bypassing the running API. Used during upgrades and for repair after
a manual edit goes wrong.

Currently exposes::

    hal0 capabilities migrate
        Rewrite illegal (backend, model) pairs against the live catalog.

(The schema_version=1 → 2 migration that used to live here as a CLI
command now runs automatically on config load — see
``hal0.capabilities.config``. The old ``sync`` command is gone too:
``registry.toml`` is the sole model catalog.)

Migration is the first reason this module exists: when the catalog
reshape landed (model-first grouped rows + per-(backend, model)
validation), any previously-persisted selection that mixed an FLM
chat tag with a GGUF backend (or vice-versa) became illegal. The
runtime orchestrator now rejects such writes, but already-on-disk
selections survive until a write touches them. ``migrate`` walks the
file, snaps illegal pairs to a legal one (or clears the selection
when the model is gone entirely), and writes back atomically.
"""

from __future__ import annotations

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
from hal0.capabilities.orchestrator import _CHILD_TO_CAPABILITY
from hal0.registry.store import ModelRegistry

app = typer.Typer(
    name="capabilities",
    help="Capability-slot configuration repair + migration.",
    no_args_is_help=True,
)

console = Console()


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
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would change without writing the file.",
    ),
) -> None:
    """Rewrite persisted selections that are illegal against the live catalog.

    Walks ``/etc/hal0/capabilities.toml`` and, for each non-empty
    selection, validates the (model, backend) pair against
    ``models_for_capability``. Selections where the backend can't serve
    the model are snapped to the model's first legal backend; selections
    whose model is no longer in the catalog are cleared (backend stays
    intact so the dashboard can still show what was previously chosen).

    Idempotent — running it twice is a no-op once everything is legal.
    """
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
                if not dry_run:
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
                if not dry_run:
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

    if dry_run:
        console.print(
            f"\n[yellow]--dry-run[/yellow] — {len(changes)} selection(s) "
            f"would be rewritten in {capabilities_toml_path()}."
        )
        raise typer.Exit(0)

    save_capabilities_config(cfg)
    console.print(
        f"\n[green]migrated[/green] {len(changes)} selection(s) in {capabilities_toml_path()}."
    )
