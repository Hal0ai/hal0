"""``hal0 memory migrate`` — Hindsight bank unify and platform-bank move.

``migrate platform`` (ADR-0052) clones the platform bank into its configured
name and folds the retired ``agents`` bank into it; see its docstring.

``migrate unify`` folds one or more source Hindsight banks into a target
bank by tag, so multiple per-agent private banks can be consolidated under
a shared/unified bank without losing which agent a fact came from.

Upstream mechanics (source-verified against the hindsight-api v0.8.4 tag and
re-checked against the 0.9.2 wheels — 0.9.2's import replays the archive
deterministically without LLM calls, so a unify stays cheap; ``--apply``
gates on ``/version``'s ``features.document_export_api``/
``document_import_api`` flags so it fails loud rather than guessing):

* ``GET .../document-transfer?include_observations=`` exports a source
  bank's documents (optionally their observations) as a ZIP. On 0.9.x the
  engine's sync export is a 410 tombstone (#2155); hal0-api's GET hides
  that by running the engine's async export (submit, poll, download)
  server-side, so this call can block for the whole export — hence the
  long read timeout on it.
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

app = typer.Typer(
    help="Migrate memory stores (bank unify, platform bank).",
    invoke_without_command=True,
)
console = Console()
# Progress/warning chatter during --apply goes to stderr so it never lands
# in --json's stdout payload (mirrors hal0.cli._shared's error console).
_progress = Console(stderr=True)

_ON_CONFLICT_CHOICES = ("skip", "replace", "new-id")
_TERMINAL_OP_STATUSES = ("completed", "failed", "cancelled")
_POLL_INTERVAL_S = 2.0
_POLL_TIMEOUT_S = 900.0  # 15min — document-transfer + retain is LLM-bound
# Export GET read budget: the server-side async-export poll (same 15min) plus
# slack for the archive download itself (#2155).
_EXPORT_READ_TIMEOUT_S = _POLL_TIMEOUT_S + 60.0
_RETAG_MAX_WORKERS = 4


@app.callback()
def migrate_default(ctx: typer.Context) -> None:
    """Migrate memory stores (``unify`` folds banks, ``platform`` moves the platform bank)."""
    if ctx.invoked_subcommand is None:
        die("use 'migrate unify' or 'migrate platform' (see 'hal0 memory migrate --help').")


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


def _transfer_documents(
    src: str, target: str, *, on_conflict: str, include_observations: bool
) -> tuple[dict, dict]:
    """Export ``src`` and import it into ``target``; return ``(submit, operation)``.

    ``operation`` is the polled terminal state of the import operation.
    Raises :class:`CliApiError` when either HTTP leg fails; the caller checks
    ``operation["status"]``.
    """
    zip_bytes, _ct = api_get_bytes(
        f"/api/memory/banks/{src}/document-transfer",
        params={"include_observations": str(include_observations).lower()},
        # hal0-api polls the engine's async export for up to
        # memory_admin._EXPORT_POLL_TIMEOUT_S before answering.
        timeout=_EXPORT_READ_TIMEOUT_S,
    )
    submit = api_post(
        f"/api/memory/banks/{target}/document-transfer",
        params={"on_conflict": on_conflict},
        files={"file": (f"{src}.zip", zip_bytes, "application/zip")},
    )
    return submit, _poll_operation(target, str(submit.get("operation_id")))


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
            # Only bother listing documents (an extra network round-trip)
            # when there are tags to apply — the before/after diff is how a
            # document-transfer with no tag-rewrite option gets retagged
            # after the fact. We also capture the *source* bank's own
            # document-id set so the retag can be scoped to docs this
            # migration actually transferred (see below), not every id that
            # appeared in the target during the transfer window.
            ids_before = _document_ids(target) if tags else None
            source_doc_ids = _document_ids(src) if tags else None
            try:
                submit, op = _transfer_documents(
                    src, target, on_conflict=on_conflict, include_observations=include_observations
                )
            except CliApiError as exc:
                die(f"transfer {src} → {target} failed: {exc}")
                return
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
                # Scope the retag to docs THIS migration transferred. The
                # document-transfer import returns only counts
                # (result_metadata), never the ids it created, so the
                # tightest scoping available without an upstream change is
                # to intersect the target's before/after diff with the
                # source bank's own document ids. A doc another writer lands
                # in the target during the transfer window carries a
                # foreign id (not in the source bank) and is thus excluded,
                # instead of being mis-attributed to this source.
                new_ids = (ids_after - (ids_before or set())) & (source_doc_ids or set())
                if new_ids:
                    _progress.print(
                        "[yellow]-- retag is scoped to documents whose ids match the "
                        "source bank; under --on-conflict new-id transferred docs receive "
                        "fresh ids and are not retagged, and a concurrent writer reusing a "
                        "source id could still be mis-attributed.[/yellow]"
                    )
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


def _configured_platform_bank() -> str | None:
    """``[memory] bank`` from the loaded hal0 config, ``None`` if unreadable."""
    try:
        from hal0.config.loader import load_hal0_config

        return str(load_hal0_config().memory.bank)
    except Exception:
        return None


def _bank_facts(banks_by_id: dict[str, dict], bank: str) -> int:
    return int((banks_by_id.get(bank) or {}).get("fact_count") or 0)


@app.command("platform")
def migrate_platform_cmd(
    target: str = typer.Option(
        "",
        "--target",
        help="Platform bank to create (default: [memory] bank from hal0.toml).",
    ),
    source: str = typer.Option(
        "shared", "--source", help="Bank to clone from (the pre-ADR-0052 platform bank)."
    ),
    agents_bank: str = typer.Option(
        "agents",
        "--agents-bank",
        help="Retired identity-card bank folded into the platform bank (ADR-0005). "
        "Skipped when it does not exist.",
    ),
    skip_clone: bool = typer.Option(
        False,
        "--skip-clone",
        help="Do not clone: --target already holds the cloned data. Only fold and verify.",
    ),
    skip_bank_config: bool = typer.Option(
        False,
        "--skip-bank-config",
        help="Clone documents/facts only, not the source bank's config overrides, "
        "directives and webhooks (the clone's webhooks call the source's endpoints).",
    ),
    apply: bool = typer.Option(
        False,
        "--apply/--dry-run",
        help="Execute the migration. Default is --dry-run: report the plan without writing.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a panel."),
) -> None:
    """Clone the platform bank into its configured name (ADR-0052).

    Step 1 clones ``--source`` (default ``shared``) into ``--target`` with the
    engine's own ``POST .../clone`` — in-process export+import, no LLM call.
    Step 2 folds the retired ``agents`` bank (Hal0 Brain identity cards tagged
    ``agent-identity``) into the target. Step 3 compares fact counts and exits
    non-zero if the target is short of the source.

    Additive only: the source bank is never touched. Run it BEFORE setting
    ``[memory] bank`` and restarting hal0-api, then delete the source
    separately (``hal0 memory bank delete``) once the counts are verified.
    """
    target = target or _configured_platform_bank() or ""
    if not target:
        die("pass --target <bank_id> (could not read [memory] bank from hal0.toml)")
        return
    if target == source:
        die(
            f"--target and --source are both {target!r}: [memory] bank is still the "
            "default. Set it to the new platform bank name first (or pass --target)."
        )
        return
    if agents_bank in (source, target):
        die(f"--agents-bank {agents_bank!r} cannot also be the source or the target")
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
    features = (engine or {}).get("features") or {}
    banks_by_id = {b["bank_id"]: b for b in (banks_resp or {}).get("banks", []) if "bank_id" in b}

    if source not in banks_by_id:
        die(f"unknown source bank: {source!r} (not in 'hal0 memory bank list')")
        return
    target_exists = target in banks_by_id
    if target_exists and not skip_clone:
        die(
            f"target bank {target!r} already exists ({_bank_facts(banks_by_id, target)} facts) "
            "and the engine's clone refuses an existing bank. If it already holds the "
            "cloned data, re-run with --skip-clone to fold and verify; if hal0-api was "
            "pointed at it before cloning, review it first — nothing here deletes banks."
        )
        return
    if skip_clone and not target_exists:
        die(f"--skip-clone given but target bank {target!r} does not exist")
        return
    fold = agents_bank in banks_by_id

    report: dict = {
        "source": source,
        "target": target,
        "source_facts": _bank_facts(banks_by_id, source),
        "target_facts_before": _bank_facts(banks_by_id, target),
        "agents_bank": agents_bank if fold else None,
        "agents_facts": _bank_facts(banks_by_id, agents_bank) if fold else 0,
        "clone": not skip_clone,
        "applied": False,
    }

    if apply:
        needs = ("document_export_api", "document_import_api")
        if not all(features.get(f) for f in needs):
            die(
                "migrate platform --apply needs hindsight-api's export+import APIs "
                f"(features.{' + features.'.join(needs)} on /version; got {features!r}); "
                "enable them or upgrade Hindsight first."
            )
            return
        if not skip_clone:
            _progress.print(f"[dim]cloning {source} → {target}…[/dim]")
            try:
                submit = api_post(
                    f"/api/memory/banks/{source}/clone",
                    params={
                        "target_bank_id": target,
                        "include_bank_config": str(not skip_bank_config).lower(),
                    },
                )
            except CliApiError as exc:
                die(f"clone {source} → {target} failed: {exc}")
                return
            # The engine records the clone operation against the SOURCE bank
            # (the target does not exist yet).
            op = _poll_operation(source, str(submit.get("operation_id")))
            if op.get("status") != "completed":
                die(
                    f"clone {source} → {target} did not complete "
                    f"(status={op.get('status')}, error={op.get('error_message')})"
                )
                return
            report["clone_operation"] = {
                "operation_id": submit.get("operation_id"),
                "result_metadata": op.get("result_metadata") or {},
            }
        if fold:
            _progress.print(f"[dim]folding {agents_bank} → {target}…[/dim]")
            try:
                submit, op = _transfer_documents(
                    agents_bank, target, on_conflict="skip", include_observations=True
                )
            except CliApiError as exc:
                die(f"fold {agents_bank} → {target} failed: {exc}")
                return
            if op.get("status") != "completed":
                die(
                    f"fold {agents_bank} → {target} did not complete "
                    f"(status={op.get('status')}, error={op.get('error_message')})"
                )
                return
            report["fold_operation"] = {
                "operation_id": submit.get("operation_id"),
                "result_metadata": op.get("result_metadata") or {},
            }
        try:
            after = {
                b["bank_id"]: b
                for b in (api_get("/api/memory/banks") or {}).get("banks", [])
                if "bank_id" in b
            }
        except CliApiError as exc:
            die(f"could not re-read bank counts to verify: {exc}")
            return
        expected = report["source_facts"] + (report["agents_facts"] if fold else 0)
        got = _bank_facts(after, target)
        report.update(
            applied=True,
            target_facts_after=got,
            expected_facts_at_least=expected,
            verified=got >= expected,
        )

    if json_out:
        typer.echo(jsonlib.dumps(report, indent=2, sort_keys=True))
    else:
        t = Table(
            title=f"memory · migrate platform → {target} ({'applied' if apply else 'dry-run'})"
        )
        t.add_column("step")
        t.add_column("from")
        t.add_column("facts", justify="right")
        t.add_row(
            "clone", source if not skip_clone else "[dim]skipped[/dim]", str(report["source_facts"])
        )
        t.add_row(
            "fold", agents_bank if fold else "[dim]no such bank[/dim]", str(report["agents_facts"])
        )
        console.print(t)
        if apply:
            console.print(
                Panel(
                    f"{target}: {report['target_facts_after']} facts "
                    f"(expected at least {report['expected_facts_at_least']}) — "
                    + ("[green]verified[/green]" if report["verified"] else "[red]SHORT[/red]")
                    + f'\nNext: set [memory] bank = "{target}" in hal0.toml, restart hal0-api. '
                    f"{source} and {agents_bank} are untouched.",
                    border_style="green" if report["verified"] else "red",
                )
            )
        else:
            console.print(
                Panel(
                    f"[dim]Plan only — nothing was written. Re-run with --apply. {source} and "
                    f"{agents_bank} are never modified.[/dim]",
                    border_style="dim",
                )
            )
    if apply and not report["verified"]:
        raise typer.Exit(1)


__all__ = ["app"]
