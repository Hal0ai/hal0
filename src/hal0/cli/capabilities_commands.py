"""``hal0 capabilities`` subcommands.

Operator tooling that touches ``/etc/hal0/capabilities.toml`` directly,
bypassing the running API. Used during upgrades and for repair after
a manual edit goes wrong.

Currently exposes::

    hal0 capabilities migrate
        Rewrite illegal (backend, model) pairs against the live catalog.

    hal0 capabilities migrate-to-lemonade
        v0.2 (ADR-0006 §7): schema_version=1 → 2 migration that renames
        ``backend`` → ``device`` per selection and stamps the new
        version. Idempotent on v2 files. Pairs with ``--apply`` and
        ``--revert`` for the round-trip.

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

import difflib
import os
import tomllib
from pathlib import Path

import tomli_w
import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from hal0.capabilities.catalog import models_for_capability
from hal0.capabilities.config import (
    CAPABILITIES_SCHEMA_VERSION_CURRENT,
    CapabilityConfig,
    CapabilitySelection,
    capabilities_toml_path,
    capabilities_v1_backup_path,
    load_capabilities_config,
    migrate_capabilities_v1_to_v2,
    read_schema_version,
    save_capabilities_config,
)
from hal0.capabilities.orchestrator import _CHILD_TO_CAPABILITY
from hal0.config.loader import write_toml_atomic
from hal0.lemonade.server_models_gen import (
    generate_server_models,
    write_server_models,
)
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


# ── migrate-to-lemonade (v0.2 schema_version=1 → 2) ───────────────────────────


def _dump_toml(data: dict[str, object]) -> str:
    """Stringify a TOML payload exactly the way ``write_toml_atomic`` would.

    ``tomli_w.dumps`` is the round-trip-correct stringifier — we use it
    so the diff the user sees matches the bytes the migration would
    write. Sort behaviour matches tomli_w defaults so two equivalent
    dicts produce identical output.
    """
    return tomli_w.dumps(data)


def _read_raw_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


@app.command("migrate-to-lemonade")
def migrate_to_lemonade(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Perform the migration. Without this flag, only the diff is printed.",
    ),
    revert: bool = typer.Option(
        False,
        "--revert",
        help=(
            "Restore the pre-migration backup (``capabilities.toml.v1.bak``) "
            "back to ``capabilities.toml``. Mutually exclusive with --apply."
        ),
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        help=(
            "Override the capabilities.toml path. Defaults to "
            "/etc/hal0/capabilities.toml (HAL0_HOME-aware). Useful for "
            "off-host migration of a backup file."
        ),
    ),
) -> None:
    """Migrate capabilities.toml from schema_version=1 (v0.1.x) to 2 (v0.2).

    The on-boot auto-migration in ``hal0-api`` already runs this when a
    legacy file is detected, but this command exposes the same logic for
    manual reruns: previewing the diff, replaying after a revert, or
    migrating a backup file in another location.

    Output:
      - With no flags: prints the unified diff and exits 0.
      - With ``--apply``: performs the migration (atomic-rename backup +
        rewrite). Idempotent on already-v2 files (exits 0, no changes).
      - With ``--revert``: restores ``<path>.v1.bak`` over ``<path>``.

    See ADR-0006 §7 for the field rename + value mapping (vulkan →
    gpu-vulkan, rocm → gpu-rocm, flm → npu, moonshine/kokoro → cpu,
    cpu → cpu).
    """
    if apply and revert:
        console.print("[red]error[/red]: --apply and --revert are mutually exclusive.")
        raise typer.Exit(2)

    target = Path(path) if path is not None else capabilities_toml_path()
    backup = capabilities_v1_backup_path(target)

    # ── --revert ──────────────────────────────────────────────────────────
    if revert:
        if not backup.exists():
            console.print(f"[red]error[/red]: no v1 backup found at {backup}.\nNothing to revert.")
            raise typer.Exit(1)
        os.replace(backup, target)
        console.print(f"[green]reverted[/green]: restored {target} from {backup.name}.")
        raise typer.Exit(0)

    # ── read live + compute migration target ──────────────────────────────
    if not target.exists():
        console.print(f"[yellow]nothing to do[/yellow]: {target} does not exist.")
        raise typer.Exit(0)

    before = _read_raw_toml(target)
    before_version = read_schema_version(before)
    after = migrate_capabilities_v1_to_v2(before)

    if before_version >= CAPABILITIES_SCHEMA_VERSION_CURRENT:
        console.print(
            f"[green]already v{CAPABILITIES_SCHEMA_VERSION_CURRENT}[/green]: "
            f"{target} is at schema_version={before_version}, nothing to migrate."
        )
        raise typer.Exit(0)

    # Validate the migration output BEFORE printing the diff so we never
    # advertise an invalid result.
    CapabilityConfig.model_validate(after)

    before_text = _dump_toml(before)
    after_text = _dump_toml(after)
    diff = list(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=str(target),
            tofile=f"{target} (post-migration)",
        )
    )

    if not diff:
        # Schema_version mismatch but no other diffs — still write the
        # version stamp so subsequent boots don't re-trigger migration.
        if apply:
            write_toml_atomic(target, after)
            console.print(
                f"[green]stamped[/green]: bumped {target} to schema_version="
                f"{CAPABILITIES_SCHEMA_VERSION_CURRENT}."
            )
        else:
            console.print(
                f"[yellow]version-only stamp pending[/yellow]: "
                f"{target} is at schema_version={before_version}; run with "
                f"--apply to bump to {CAPABILITIES_SCHEMA_VERSION_CURRENT}."
            )
        raise typer.Exit(0)

    console.print(
        Syntax(
            "".join(diff),
            "diff",
            theme="ansi_dark",
            background_color="default",
        )
    )

    if not apply:
        console.print(
            f"\n[yellow]dry-run[/yellow] — re-run with --apply to write "
            f"{target} (v{before_version} → v{CAPABILITIES_SCHEMA_VERSION_CURRENT})."
        )
        raise typer.Exit(0)

    # ── --apply ──────────────────────────────────────────────────────────
    if backup.exists():
        console.print(
            f"[yellow]warning[/yellow]: {backup} already exists. "
            f"Leaving it in place; the live file will be rewritten without "
            f"a fresh backup. Move or delete the old backup if you want a "
            f"new one captured."
        )
    else:
        os.replace(target, backup)
    write_toml_atomic(target, after)
    console.print(
        f"[green]migrated[/green]: {target} "
        f"v{before_version} → v{CAPABILITIES_SCHEMA_VERSION_CURRENT} "
        f"(backup at {backup.name})."
    )


# ── sync (regenerate server_models.json from registry — issue #141) ──────────

# Default paths for the sync command. Override-able for tests + dev installs.
_DEFAULT_REGISTRY_PATH = Path("/var/lib/hal0/registry/registry.toml")
_DEFAULT_SERVER_MODELS_PATH = Path("/opt/lemonade/resources/server_models.json")


@app.command()
def sync(
    registry: Path = typer.Option(
        _DEFAULT_REGISTRY_PATH,
        "--registry",
        help="Path to hal0 registry.toml.",
    ),
    output: Path = typer.Option(
        _DEFAULT_SERVER_MODELS_PATH,
        "--output",
        help="Path to Lemonade's server_models.json.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show entry count and recipe summary without writing the file.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Exit non-zero if the on-disk server_models.json differs from what the "
        "registry would generate (no write). For cron/healthcheck drift detection.",
    ),
) -> None:
    """Regenerate Lemonade's ``server_models.json`` from hal0's registry.

    This is the runtime entry point for issue #141 (the install-time hook
    runs the same code path via ``python -m hal0.lemonade.server_models_gen``).
    Lemonade re-scans ``resources/server_models.json`` on its next probe,
    so a sync does NOT require restarting ``lemond.service`` — the next
    ``/v1/load`` or ``/v1/models`` call sees the new entries.

    Idempotent: re-running with an unchanged registry produces an identical
    byte stream. Safe to invoke from cron, post-pull hooks, or operators.
    """
    catalog = generate_server_models(registry)

    if check:
        # Drift detection: compare the on-disk catalog against what the registry
        # would generate, byte-for-byte (same format as write_server_models).
        # No write; exit 1 on drift or a missing file so cron/healthchecks fail loud.
        import json as _json

        want = _json.dumps(catalog, indent=4, sort_keys=False) + "\n"
        try:
            have = output.read_text(encoding="utf-8")
        except FileNotFoundError:
            console.print(f"[red]drift[/red] — {output} does not exist.")
            raise typer.Exit(1) from None
        if have != want:
            console.print(f"[red]drift[/red] — {output} is stale; run `hal0 capabilities sync`.")
            raise typer.Exit(1)
        console.print(f"[green]in sync[/green] — {output} matches the registry.")
        raise typer.Exit(0)

    if not catalog:
        console.print(
            f"[yellow]warning[/yellow] — no models in {registry}; would write an empty catalog."
        )

    # Summary table: model id + recipe + first label (the Lemonade type driver).
    table = Table(title=f"server_models.json ({len(catalog)} entries)")
    table.add_column("model_id", style="bold")
    table.add_column("recipe")
    table.add_column("labels", overflow="fold")
    table.add_column("checkpoint", overflow="fold")
    for mid, entry in catalog.items():
        labels = entry.get("labels") or []
        table.add_row(
            mid,
            entry.get("recipe", "—"),
            ", ".join(labels) or "(llm)",
            entry.get("checkpoint", "—"),
        )
    console.print(table)

    if dry_run:
        console.print(f"\n[yellow]--dry-run[/yellow] — not writing {output}.")
        raise typer.Exit(0)

    write_server_models(registry, output)
    console.print(f"\n[green]wrote[/green] {len(catalog)} entries to {output}.")
