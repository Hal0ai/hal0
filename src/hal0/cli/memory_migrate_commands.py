"""``hal0 memory migrate`` — move the platform bank (ADR-0052).

``migrate platform`` clones the platform bank into its configured name and
folds the retired ``agents`` bank into it; see its docstring. The old
``migrate unify`` is gone: ADR-0058 decision 7a moves a store's other banks
into the two-bank layout with one converter instead.

The fold of the ``agents`` bank goes through hal0-api's document-transfer
passthrough:

* ``GET .../document-transfer?include_observations=`` exports a bank's
  documents (and their observations) as a ZIP. On 0.9.x and later the
  engine's sync export is a 410 tombstone (#2155); hal0-api's GET hides that
  by running the engine's async export (submit, poll, download) server-side,
  so this call can block for the whole export, hence the long read timeout.
* ``POST .../document-transfer`` (multipart ``file=<zip>``,
  ``?on_conflict=skip|replace|new-id``) starts an async import into the
  target bank and answers ``202 {operation_id}``, polled through the
  ``operations/{id}`` passthrough.
"""

from __future__ import annotations

import json as jsonlib

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
    api_post,
    die,
)

app = typer.Typer(
    help="Migrate memory stores (platform bank).",
    invoke_without_command=True,
)
console = Console()
# Progress/warning chatter during --apply goes to stderr so it never lands
# in --json's stdout payload (mirrors hal0.cli._shared's error console).
_progress = Console(stderr=True)

_TERMINAL_OP_STATUSES = ("completed", "failed", "cancelled")
_POLL_INTERVAL_S = 2.0
_POLL_TIMEOUT_S = 900.0  # 15min — document-transfer + retain is LLM-bound
# Export GET read budget: the server-side async-export poll (same 15min) plus
# slack for the archive download itself (#2155).
_EXPORT_READ_TIMEOUT_S = _POLL_TIMEOUT_S + 60.0


@app.callback()
def migrate_default(ctx: typer.Context) -> None:
    """Migrate memory stores (``platform`` moves the platform bank)."""
    if ctx.invoked_subcommand is None:
        die("use 'migrate platform' (see 'hal0 memory migrate --help').")


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


def _configured_platform_bank() -> str | None:
    """``[memory] bank`` when the operator set it in hal0.toml, else ``None``.

    Only a value the operator chose is a target: the shipped default is the
    two-bank layout's ``hal0:shared``, which a store on its old layout
    reaches through the fold-in, not a clone
    (:func:`hal0.memory.bank_layout.legacy_platform_bank` reads the key the
    same way). ``None`` too when hal0.toml cannot be read.
    """
    try:
        from hal0.config.loader import load_hal0_config
        from hal0.memory.bank_layout import TWO_BANK_PLATFORM_BANK

        memory = load_hal0_config().memory
    except Exception:
        return None
    if "bank" not in memory.model_fields_set or memory.bank == TWO_BANK_PLATFORM_BANK:
        return None
    return str(memory.bank)


def _two_bank_name(target: str) -> bool:
    """Whether ``target`` is one of the two-bank layout's bank names."""
    from hal0.memory.bank_layout import TWO_BANK_PLATFORM_BANK, TWO_BANK_PRIVATE_BANK

    return target in (TWO_BANK_PLATFORM_BANK, TWO_BANK_PRIVATE_BANK)


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
        die("pass --target <bank_id> ([memory] bank is not set in hal0.toml)")
        return
    if _two_bank_name(target):
        die(
            f"{target!r} is a bank of the two-bank layout (ADR-0058). A store moves into "
            "it with the fold-in, which also splits private memory out; a clone would "
            "copy every agent's private memory into it. Pick another --target."
        )
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
