"""``hal0 memory migrate`` — Cognee→Hindsight dry-run (legacy) + bank unify.

The bare ``hal0 memory migrate`` command is unchanged from before this file
existed (P2-4, dry-run only) — it now lives in a Typer sub-app's default
callback so ``migrate unify`` (cross-bank unify) can nest under the same
top-level name without colliding with it.

``migrate unify`` folds one or more source Hindsight banks into a target
bank by tag, so multiple per-agent private banks can be consolidated under
a shared/unified bank without losing which agent a fact came from.

Upstream mechanics (source-verified against the hindsight-api v0.8.4 tag —
the live instance this CLI was built against is still 0.7.2, which lacks
all of this; ``--apply`` gates on ``/version``'s ``features.document_export_api``/
``document_import_api`` flags so it fails loud rather than guessing):

* ``GET .../document-transfer?include_observations=`` exports a source
  bank's documents (optionally their observations) as a ZIP.
  ``include_observations=true`` combined with a document-id subset is a
  400 upstream — whole-bank export only when observations are included,
  which is what this command always does.
* ``POST .../document-transfer`` (multipart ``file=<zip>``,
  ``?on_conflict=skip|replace|new-id``, upstream default ``skip``) starts
  an async import into the target bank → ``202 {operation_id}``. Poll the
  existing ``operations/{id}`` passthrough for ``result_metadata``
  (documents_imported / facts_imported / observations_imported /
  skipped, if present — schema not runtime-verified since 0.8.x isn't
  live here).
* There is no tag-rewrite-during-transfer option, and no per-memory tag
  field either (Hindsight's ``UpdateMemoryRequest`` has none) — the only
  tag-edit surface is document-level ``PATCH .../documents/{id}`` with a
  full-replace ``{"tags": [...]}`` body that propagates to every memory
  unit under the doc and queues re-consolidation. So ``--add-tag``/the
  derived ``agent:``/``visibility:`` tags are applied as a *separate
  post-import pass*: diff the target bank's document-id set before/after
  each source's import to find the docs that source just added, then
  read-merge-write each one's tags (bounded concurrency, see
  ``_retag_documents``). This means retagging N docs queues N
  re-consolidation passes — slow on a local LLM, so the CLI warns and
  suggests running off-hours before it starts.
"""

from __future__ import annotations

import json as jsonlib
from concurrent.futures import ThreadPoolExecutor, as_completed

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_get,
    api_get_bytes,
    api_patch,
    api_post,
    die,
)
from hal0.memory.migrate import migrate_cognee_to_hindsight_dryrun

app = typer.Typer(
    help="Migrate memory stores (legacy Cognee→Hindsight dry-run; bank unify).",
    invoke_without_command=True,
)
console = Console()
# Progress/warning chatter during --apply goes to stderr so it never lands
# in --json's stdout payload (mirrors hal0.cli._shared's error console).
_progress = Console(stderr=True)

_DEFAULT_COGNEE_DIR = "/var/lib/hal0/memory/cognee"
_ON_CONFLICT_CHOICES = ("skip", "replace", "new-id")
_TERMINAL_OP_STATUSES = ("completed", "failed", "cancelled")
_POLL_INTERVAL_S = 2.0
_POLL_TIMEOUT_S = 900.0  # 15min — document-transfer + retain is LLM-bound
_RETAG_MAX_WORKERS = 4


@app.callback()
def migrate_default(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        True,
        "--dry-run",
        help="Report the migration plan without writing. Dry-run only — apply/write mode is not yet implemented.",
    ),
    cognee_dir: str = typer.Option(
        _DEFAULT_COGNEE_DIR,
        "--cognee-dir",
        help="Path to the Cognee data directory (contains hal0_memory_index.sqlite).",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit raw JSON instead of the human-readable panel.",
    ),
) -> None:
    """Migrate Cognee memory store → Hindsight banks (dry-run only, P2-4)."""
    if ctx.invoked_subcommand is not None:
        return
    if not dry_run:
        die("--apply is not yet implemented; dry-run only.")
        return
    report = migrate_cognee_to_hindsight_dryrun(cognee_dir=cognee_dir)
    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))
        return
    noop_label = (
        "[dim]yes — nothing to migrate[/dim]" if report["noop"] else "[bold yellow]no[/bold yellow]"
    )
    t = Table.grid(padding=(0, 2))
    t.add_column("k", style="dim")
    t.add_column("v")
    t.add_row("Rows total", str(report["rows_total"]))
    t.add_row("Rows mapped", str(report["rows_mapped"]))
    t.add_row("Rows unmapped", str(report["rows_unmapped"]))
    t.add_row("No-op", noop_label)
    console.print(Panel(t, title="memory · migrate (dry-run)", border_style="dim"))


def _derived_tags(bank_id: str, add_tags: list[str]) -> list[str]:
    tags = list(add_tags)
    if bank_id.startswith("private__"):
        agent = bank_id[len("private__") :]
        tags = [f"agent:{agent}", "visibility:private", *tags]
    return tags


def _document_ids(bank: str) -> set[str]:
    """Best-effort set of document ids currently in ``bank``.

    ``GET .../documents`` response-key convention isn't runtime-verified
    against a live 0.8.x instance (only 0.7.2 is up here) — try the
    documented-by-convention keys before falling back to "the payload
    itself is the list", so this degrades to an empty diff (no retagging)
    rather than crashing if the shape is slightly different.
    """
    result = api_get(f"/api/memory/banks/{bank}/documents", params={"limit": 1000})
    items = result
    if isinstance(result, dict):
        for key in ("documents", "items", "results"):
            if key in result:
                items = result[key]
                break
    if not isinstance(items, list):
        return set()
    return {str(d["id"]) for d in items if isinstance(d, dict) and "id" in d}


def _poll_operation(bank: str, operation_id: str) -> dict:
    import time

    deadline = time.monotonic() + _POLL_TIMEOUT_S
    last: dict = {}
    while time.monotonic() < deadline:
        last = api_get(f"/api/memory/banks/{bank}/operations/{operation_id}") or {}
        if last.get("status") in _TERMINAL_OP_STATUSES:
            return last
        time.sleep(_POLL_INTERVAL_S)
    last.setdefault("status", "timed_out")
    return last


def _retag_documents(bank: str, doc_ids: set[str], tags: list[str]) -> dict[str, str]:
    """Read-merge-write ``tags`` onto each of ``doc_ids`` in ``bank``.

    PATCH .../documents/{id} is a full-tag-replace, so each doc's current
    tags are fetched first and unioned with ``tags`` rather than
    overwritten. Bounded concurrency (4 workers) — this is still N PATCHes
    against a single-tenant Hindsight instance, each of which queues its
    own re-consolidation pass.
    """
    results: dict[str, str] = {}

    def _one(doc_id: str) -> tuple[str, str]:
        try:
            current = api_get(f"/api/memory/banks/{bank}/documents/{doc_id}") or {}
            merged = sorted(set(current.get("tags") or []) | set(tags))
            api_patch(f"/api/memory/banks/{bank}/documents/{doc_id}", json={"tags": merged})
            return doc_id, "ok"
        except CliApiError as exc:
            return doc_id, f"error: {exc}"

    with ThreadPoolExecutor(max_workers=_RETAG_MAX_WORKERS) as pool:
        futures = [pool.submit(_one, d) for d in sorted(doc_ids)]
        for fut in as_completed(futures):
            doc_id, status = fut.result()
            results[doc_id] = status
    return results


@app.command("unify")
def migrate_unify_cmd(
    source: list[str] = typer.Option(
        [], "--source", help="Source bank id to fold into --target (repeatable, required)."
    ),
    target: str = typer.Option(
        "shared", "--target", help="Target bank id sources are unified into."
    ),
    apply: bool = typer.Option(
        False,
        "--apply/--dry-run",
        help="Execute the migration. Default is --dry-run: report the plan without writing.",
    ),
    add_tag: list[str] = typer.Option(
        [], "--add-tag", help="Extra tag to attach to every migrated document (repeatable)."
    ),
    on_conflict: str = typer.Option(
        "skip",
        "--on-conflict",
        help="What to do when an imported document id collides in the target bank: "
        "skip (upstream default) | replace | new-id.",
    ),
    skip_observations: bool = typer.Option(
        False,
        "--skip-observations",
        help="Export documents only, without their derived observations (default: include them).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Fold one or more source banks into a target bank, tagging provenance.

    Never deletes source banks — that is always a separate, explicit
    ``hal0 memory bank delete <bank> --confirm <bank>``.
    """
    if not source:
        die("pass at least one --source <bank_id>")
        return
    if target in source:
        die(f"--target {target!r} cannot also be a --source")
        return
    if on_conflict not in _ON_CONFLICT_CHOICES:
        die(f"--on-conflict must be one of {_ON_CONFLICT_CHOICES}, got {on_conflict!r}")
        return

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    try:
        engine = api_get("/api/memory/engine")
        banks_resp = api_get("/api/memory/banks")
    except CliApiError as exc:
        die(str(exc))
        return

    version_str = (engine or {}).get("version")
    features = (engine or {}).get("features") or {}
    banks_by_id = {b["bank_id"]: b for b in (banks_resp or {}).get("banks", []) if "bank_id" in b}

    plan = []
    for src in source:
        b = banks_by_id.get(src)
        if b is None:
            die(f"unknown source bank: {src!r} (not in 'hal0 memory bank list')")
            return
        plan.append(
            {
                "source": src,
                "fact_count": b.get("fact_count", 0),
                "last_document_at": b.get("last_document_at"),
                "tags_to_add": _derived_tags(src, add_tag),
            }
        )
    target_info = banks_by_id.get(target, {"bank_id": target, "fact_count": 0})

    report: dict = {
        "hindsight_version": version_str,
        "target": target,
        "target_fact_count_before": target_info.get("fact_count", 0),
        "sources": plan,
        "applied": False,
    }

    if apply:
        has_export = bool(features.get("document_export_api"))
        has_import = bool(features.get("document_import_api"))
        if not (has_export and has_import):
            die(
                "migrate unify --apply requires hindsight-api's document-transfer API "
                f"(features.document_export_api + document_import_api on /version; got "
                f"{features!r} on version {version_str or 'unknown'}); upgrade Hindsight first."
            )
            return

        include_observations = not skip_observations
        transfer_results = []
        for row in plan:
            src = row["source"]
            tags = row["tags_to_add"]
            _progress.print(f"[dim]exporting {src} → importing into {target}…[/dim]")
            # Only bother listing the target bank's documents (an extra
            # network round-trip) when there are tags to apply — the
            # before/after diff is how a document-transfer with no
            # tag-rewrite option gets retagged after the fact.
            ids_before = _document_ids(target) if tags else None
            try:
                zip_bytes, _ct = api_get_bytes(
                    f"/api/memory/banks/{src}/document-transfer",
                    params={"include_observations": str(include_observations).lower()},
                )
                submit = api_post(
                    f"/api/memory/banks/{target}/document-transfer",
                    params={"on_conflict": on_conflict},
                    files={"file": (f"{src}.zip", zip_bytes, "application/zip")},
                )
            except CliApiError as exc:
                die(f"transfer {src} → {target} failed: {exc}")
                return
            op = _poll_operation(target, str(submit.get("operation_id")))
            if op.get("status") != "completed":
                die(
                    f"transfer {src} → {target} did not complete "
                    f"(status={op.get('status')}, error={op.get('error_message')})"
                )
                return
            result_metadata = op.get("result_metadata") or {}

            retagged: dict[str, str] = {}
            if tags:
                ids_after = _document_ids(target)
                new_ids = ids_after - (ids_before or set())
                if new_ids:
                    _progress.print(
                        f"[yellow]retagging {len(new_ids)} document(s) from {src} → "
                        f"queues {len(new_ids)} re-consolidation pass(es); slow on a local "
                        "LLM, consider running off-hours.[/yellow]"
                    )
                    retagged = _retag_documents(target, new_ids, tags)

            transfer_results.append(
                {
                    "source": src,
                    "operation_id": op.get("operation_id") or submit.get("operation_id"),
                    "result_metadata": result_metadata,
                    "tags_applied": tags,
                    "retagged_documents": retagged,
                }
            )

        report["applied"] = True
        report["on_conflict"] = on_conflict
        report["include_observations"] = include_observations
        report["transfers"] = transfer_results

        if json_out:
            typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))
            return
        for tr in transfer_results:
            console.print(
                Panel(
                    f"[bold green]{tr['source']} → {target}[/bold green]\n"
                    f"operation_id = {tr['operation_id']}\n"
                    f"result: {tr['result_metadata']}\n"
                    f"tags applied: {', '.join(tr['tags_applied']) or '[dim]none[/dim]'}"
                    + (
                        f" ({len(tr['retagged_documents'])} documents)"
                        if tr["tags_applied"]
                        else ""
                    ),
                    border_style="green",
                )
            )
        return

    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))
        return

    t = Table(title=f"memory · migrate unify → {target} (dry-run)")
    t.add_column("source")
    t.add_column("facts", justify="right")
    t.add_column("last activity")
    t.add_column("tags to add")
    for row in plan:
        t.add_row(
            row["source"],
            str(row["fact_count"]),
            str(row["last_document_at"] or "[dim]never[/dim]"),
            ", ".join(row["tags_to_add"]) or "[dim]none[/dim]",
        )
    console.print(t)
    console.print(
        Panel(
            f"Target [bold]{target}[/bold]: {target_info.get('fact_count', 0)} facts before migration.\n"
            f"Hindsight version: {version_str or 'unknown'}\n"
            "[dim]This is a plan only — nothing was written. Re-run with --apply once "
            "hindsight-api's document-transfer API is enabled "
            "(features.document_export_api/document_import_api on /version).[/dim]",
            border_style="dim",
        )
    )


__all__ = ["app"]
