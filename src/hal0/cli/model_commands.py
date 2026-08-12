"""hal0 model subcommands — thin HTTP client to the hal0 API."""

from __future__ import annotations

import json as jsonlib
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_delete,
    api_get,
    api_post,
    api_put,
    die,
)
from hal0.cli.registry_commands import DEFAULT_REGISTRY_PATH, _do_import_backup
from hal0.registry.import_toml import import_toml_to_sqlite

app = typer.Typer(help="Manage the local model registry.")
console = Console()


def _fmt_size(b: int | None) -> str:
    if not b:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(b)
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f}{units[i]}"


@app.command("list")
def model_list(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/models JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """List all models in the local registry and from upstreams."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/models")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(data, indent=2))
        return
    models = data.get("models", []) if isinstance(data, dict) else data
    table = Table(title=f"Models ({len(models)})")
    table.add_column("")  # default-model marker (#1796); unlabeled like `git branch`'s `*`
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Upstream")
    table.add_column("Size", justify="right")
    if not models:
        console.print("[dim]No models available.[/dim]")
        return
    for m in models:
        table.add_row(
            "[green]*[/green]" if m.get("default") else "",
            m.get("id", "—"),
            m.get("name") or m.get("id", "—"),
            m.get("upstream") or m.get("owned_by") or "—",
            _fmt_size(m.get("size_bytes")),
        )
    console.print(table)


def _poll_pull_progress(ref: str, *, done_verb: str = "Done") -> None:
    """Poll ``/api/models/<ref>/pull/status`` every 500ms until terminal.

    Shared by ``model pull`` and ``model update`` — both start a background
    pull job under the same ``model_id`` key and want the identical
    tqdm-style progress bar / terminal-state handling.
    """
    import time

    last_pct = -1
    while True:
        try:
            s = api_get(f"/api/models/{ref}/pull/status")
        except CliApiError as exc:
            die(str(exc))
            return
        state = s.get("state")
        downloaded = int(s.get("bytes_downloaded") or 0)
        total = int(s.get("bytes_total") or 0)
        pct = int(downloaded * 100 / total) if total > 0 else 0
        if pct != last_pct or state != "running":
            bar = "#" * (pct // 4) + "-" * (25 - pct // 4)
            console.print(
                f"  [{bar}] {pct:3d}%  "
                f"{_fmt_size(downloaded)} / {_fmt_size(total) if total else '?'}  "
                f"[dim]{state}[/dim]",
                end="\r",
            )
            last_pct = pct
        if state in ("completed", "failed", "cancelled"):
            console.print()
            if state == "completed":
                sha = (s.get("sha256") or "?")[:12]
                console.print(
                    f"[green]{done_verb}.[/green] {ref} → {s.get('path')}  "
                    f"({_fmt_size(downloaded)}, sha256 {sha}…)"
                )
                return
            err = s.get("error") or "(no error message)"
            die(f"{state}: {err}")
            return
        time.sleep(0.5)


@app.command("pull")
def model_pull(
    ref: str = typer.Argument(..., help="Curated alias (e.g. qwen3-4b) or registered model id"),
    cancel: bool = typer.Option(
        False, "--cancel", help="Cancel an in-flight pull instead of starting one."
    ),
) -> None:
    """Download a model from Hugging Face into the local registry.

    Starts the pull as a background job on the daemon, then polls
    ``/api/models/<id>/pull/status`` every 500ms and prints a tqdm-style
    progress bar until the job reaches a terminal state. ``--cancel``
    instead requests cancellation of whatever pull job is currently
    tracked under this ref (POST /api/models/<id>/pull/cancel) — a no-op
    if the job already finished.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    if cancel:
        try:
            job = api_post(f"/api/models/{ref}/pull/cancel")
        except CliApiError as exc:
            die(str(exc))
            return
        console.print(f"Cancel requested for [bold]{ref}[/bold] → state={job.get('state', '—')}")
        return

    try:
        start = api_post(f"/api/models/{ref}/pull")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        f"Starting pull for [bold]{ref}[/bold] "
        f"({start.get('hf_repo', '?')}/{start.get('hf_file', '?')})…"
    )
    _poll_pull_progress(ref)


@app.command("default")
def model_default(
    ref: str = typer.Argument(
        ..., help="Model ref to promote/clear as its dispatcher-type default"
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Clear the default marker instead of setting it."
    ),
) -> None:
    """Promote (or clear) a model as its dispatcher type's default model.

    POST /api/models/<id>/default. At most one model per dispatcher type
    (llm/embedding/reranking/…) holds the default marker — promoting one
    demotes whichever model currently holds it.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        result = api_post(f"/api/models/{ref}/default", json={"default": not clear})
    except CliApiError as exc:
        die(str(exc))
        return
    if clear:
        console.print(f"Cleared default marker on [bold]{ref}[/bold].")
        return
    demoted = [m for m in (result.get("demoted") or []) if m != ref]
    console.print(f"[bold]{ref}[/bold] is now the default.")
    if demoted:
        console.print(f"  [dim]demoted:[/dim] {', '.join(demoted)}")


@app.command("update")
def model_update(
    ref: str | None = typer.Argument(
        None, help="Model id to re-pull in place over its existing bytes (omit with --check)"
    ),
    check: bool = typer.Option(
        False, "--check", help="Probe HuggingFace for available updates instead of re-pulling."
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Force a fresh HF probe, bypassing the 1h server-side cache (--check only).",
    ),
) -> None:
    """Check for, or apply, HuggingFace updates to already-pulled models.

    ``hal0 model update --check`` compares each HF-pulled model's recorded
    sha256 against the Hub's current bytes (GET /api/models/updates/check)
    without downloading anything. ``hal0 model update <ref>`` re-pulls that
    model's HF file in place over its existing path (POST
    /api/models/<id>/update), reusing the same progress-bar polling as
    ``model pull``.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    if check:
        try:
            data = api_get(
                "/api/models/updates/check", params={"refresh": "1"} if refresh else None
            )
        except CliApiError as exc:
            die(str(exc))
            return
        models = data.get("models") or {}
        if not models:
            console.print(
                "[dim]No HF-pulled models to check (or none have a recorded sha256).[/dim]"
            )
            return
        table = Table(title=f"Update check ({data.get('updates_available', 0)} available)")
        table.add_column("ID", style="bold")
        table.add_column("Update available")
        table.add_column("Reason", style="dim")
        for mid, verdict in models.items():
            table.add_row(mid, str(verdict.get("update_available")), verdict.get("reason") or "—")
        console.print(table)
        return

    if not ref:
        die("model update requires a model ref, or pass --check to scan for updates.")
        return

    try:
        start = api_post(f"/api/models/{ref}/update")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        f"Starting update for [bold]{ref}[/bold] "
        f"({start.get('hf_repo', '?')}/{start.get('hf_file', '?')})…"
    )
    _poll_pull_progress(ref, done_verb="Updated")


# HAL0-SUNSET: v1.0.0 — alias for `model add`; drop the alias.
@app.command("register", hidden=True)
def model_register(
    model_id: str = typer.Argument(..., help="Model id, e.g. 'qwen3-4b-q4_k_m'"),
    path: str = typer.Option(..., "--path", "-p", help="Absolute path to the model file."),
    name: str = typer.Option("", "--name", help="Display name."),
    license_id: str = typer.Option("unknown", "--license", help="SPDX license id."),
) -> None:
    """[DEPRECATED] alias for `model add`; use `hal0 model add <path> --id --license` instead."""
    typer.echo(
        "[deprecated] `model register` is replaced by `model add`; "
        "use `hal0 model add <path> --id <id> --license <license>`.",
        err=True,
    )
    model_add(path=path, model_id=model_id, name=name, license_id=license_id, overwrite=False)


@app.command("rm")
def model_rm(
    ref: str = typer.Argument(..., help="Model ref to remove from the registry"),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Remove a model from the local registry."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    if not force:
        typer.confirm(f"Remove model {ref!r} from the registry?", abort=True)
    try:
        api_delete(f"/api/models/{ref}")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Removed model [bold]{ref}[/bold] from the registry.")


@app.command("show")
def model_show(
    ref: str = typer.Argument(..., help="Model ref to inspect"),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw model metadata JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """Show a model's metadata."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        m = api_get(f"/api/models/{ref}")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(m, indent=2))
        return
    table = Table(show_header=False, title=m.get("id", ref))
    for k, v in m.items():
        table.add_row(k, str(v))
    console.print(table)


# HAL0-SUNSET: v1.0.0 — alias for `slot edit --model`; drop the alias.
@app.command("assign", hidden=True)
def model_assign(
    ref: str = typer.Argument(..., help="Model ref to assign"),
    slot: str = typer.Option(..., "--slot", "-s", help="Slot name to assign the model to"),
) -> None:
    """[DEPRECATED] alias for `slot edit --model`; use `hal0 slot edit <slot> --model` instead."""
    typer.echo(
        "[deprecated] `model assign` is replaced by `slot edit --model`; "
        "use `hal0 slot edit <slot> --model <ref>`.",
        err=True,
    )
    from hal0.cli.slot_commands import slot_edit

    slot_edit(
        name=slot,
        model=ref,
        port=None,
        ctx_size=None,
        provider=None,
        hardware=None,
        backend=None,
    )


@app.command("scan")
def model_scan() -> None:
    """Walk the configured model roots + store and register new files.

    Use after hand-placing GGUF/safetensors files (or mounting a drive of
    them) so they show up in ``hal0 model list`` without an api restart.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        from hal0.cli._shared import api_post

        result = api_post("/api/models/scan")
    except CliApiError as exc:
        die(str(exc))
        return
    added = result.get("added", []) or []
    # scan_and_register() (and the /api/models/scan route wrapping it) returns
    # "skipped" as a *list* of skipped entries, matching "added" — not a
    # count. Printing it raw rendered "4 added, [] skipped" (#1796); take the
    # length like the boot-time auto-scan log line already does.
    skipped_raw = result.get("skipped", []) or []
    skipped = len(skipped_raw) if isinstance(skipped_raw, list) else skipped_raw
    roots = result.get("scanned_roots", []) or []
    console.print(f"Scanned: {', '.join(roots) or '—'}")
    for mid in added:
        console.print(f"  [green]+[/green] {mid}")
    console.print(f"[bold]{len(added)}[/bold] added, {skipped} skipped.")
    if not added:
        console.print(
            "[dim]Nothing new. If your files live elsewhere, check the scanned "
            "roots above against the actual directory — `hal0 model store` shows "
            "and moves the store path; `hal0 model add <path>` registers a single "
            "file from anywhere.[/dim]"
        )


@app.command("add")
def model_add(
    path: str = typer.Argument(..., help="Absolute path to a model file (.gguf/.safetensors)"),
    model_id: str = typer.Option("", "--id", help="Explicit registry id (default: derived)"),
    name: str = typer.Option("", "--name", help="Display name."),
    license_id: str = typer.Option(
        "", "--license", help="SPDX license id (optional; default: 'unknown')."
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing entry."),
) -> None:
    """Register an already-downloaded model file — capabilities auto-detected.

    Reads the file header to derive id, capabilities and backends; the
    file stays where it is (no copy). When the header doesn't carry enough
    signal (e.g. no ``pooling_type``), capabilities fall back to a filename
    guess — watch for the confidence/warning line this command prints, and
    fix a wrong guess with ``PUT /api/models/<id>``
    (``{"capabilities": [...]}``). Folds in the explicit-metadata flags
    (``--id``, ``--name``, ``--license``) that the old ``model register``
    command exposed, so this command now covers both cases.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    payload: dict[str, object] = {"path": path, "overwrite": overwrite}
    if model_id:
        payload["id"] = model_id
    if name:
        payload["name"] = name
    try:
        from hal0.cli._shared import api_post

        m = api_post("/api/models/add-from-path", json=payload)
    except CliApiError as exc:
        die(str(exc))
        return
    mid = m.get("id", model_id or "?")
    if license_id:
        try:
            m = api_put(f"/api/models/{mid}", json={"license": license_id})
        except CliApiError as exc:
            die(f"registered {mid}, but setting --license failed: {exc}")
            return
    console.print(f"Registered [bold]{mid}[/bold] → {m.get('path', path)}")
    caps = ", ".join(m.get("capabilities", []) or []) or "—"
    console.print(f"  capabilities: {caps}")
    # #1838: detect() may have guessed capabilities/backends from the
    # filename alone (confidence != "high") or failed to read a valid GGUF
    # header at all — both cases used to print the same confident success
    # line as a header-derived "high" hit, which reads like a fact when
    # it's a guess.
    meta = m.get("metadata") or {}
    confidence = meta.get("detection_confidence")
    warning = meta.get("detection_warning")
    if warning:
        console.print(f"  [yellow]warning:[/yellow] {warning}")
    elif confidence and confidence != "high":
        # #1838 (codex review): a medium result can come from a filename
        # guess OR from a header signal (attention.causal=False) that
        # contradicts the "chat" default — don't claim it's always the
        # filename, just that it's not a confident header read.
        console.print(
            f"  [yellow]confidence: {confidence}[/yellow] "
            "(capabilities/backends could not be reliably read from the file header)"
        )
    console.print(
        f"[dim]Next: hal0 model run {mid}   (or: hal0 slot edit <slot> --model {mid})[/dim]"
    )


@app.command("store")
def model_store(
    path: str | None = typer.Argument(
        None, help="New store directory (omit to show the current state)"
    ),
    migrate: bool = typer.Option(
        False, "--migrate", help="Move existing model files into the new store."
    ),
) -> None:
    """Show or change the model store — the ONE directory hal0 pulls to,
    scans, and bind-mounts into slot containers.

    A mismatch here is the classic "the file exists but the slot says No
    such file or directory" failure: weights on one path, registry/slot
    pointing at another.
    """
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    from hal0.cli._shared import api_post

    if path is None:
        try:
            state = api_get("/api/settings/models/store")
        except CliApiError as exc:
            die(str(exc))
            return
        eff = state.get("effective", "?")
        if state.get("fallback_active"):
            console.print(
                f"store: [yellow]unset[/yellow] → effective [bold]{eff}[/bold] (fallback)"
            )
        else:
            console.print(f"store: [bold]{eff}[/bold]")
        for s in state.get("suggestions", []) or []:
            console.print(f"  [dim]candidate:[/dim] {s.get('path')}  {s.get('note', '')}")
        return

    try:
        result = api_post(
            "/api/settings/models/store",
            json={"path": path, "migrate": migrate},
        )
    except CliApiError as exc:
        die(str(exc))
        return
    if result.get("status") == "needs_migration":
        plan = result.get("plan", {})
        console.print(
            f"[yellow]Files exist at the current store.[/yellow] "
            f"Re-run with [bold]--migrate[/bold] to move them "
            f"({len(plan.get('files', []) or [])} file(s))."
        )
        raise typer.Exit(1)
    console.print(f"[green]Store set →[/green] [bold]{path}[/bold]")
    mig = result.get("migration")
    if mig:
        console.print(
            f"  moved {len(mig.get('moved', []))} file(s), {len(mig.get('failed', []))} failed"
        )
    scan = result.get("scan") or {}
    added = scan.get("added", []) or []
    if added:
        console.print(f"  registered {len(added)} model(s) found at the new path:")
        for mid in added:
            console.print(f"    [green]+[/green] {mid}")
    console.print("[dim]Running slots pick up the new mount on their next restart.[/dim]")


@app.command("run")
def model_run(
    ref: str = typer.Argument(..., help="Model ref (registered id or curated alias)"),
    slot: str = typer.Option(
        "", "--slot", "-s", help="Slot to run on (default: the first compatible slot)"
    ),
    timeout_s: int = typer.Option(
        300, "--timeout", help="Seconds to wait for the slot to become ready."
    ),
) -> None:
    """Get a model serving: pull if needed, assign to a slot, load, wait ready.

    The one-command first-run path::

        hal0 model pull qwen3-4b     # or drop a file + `hal0 model add`
        hal0 model run  qwen3-4b
    """
    import time

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    from hal0.cli._shared import api_post

    # 1. Model must exist (registered local file or an installed FLM tag).
    try:
        api_get(f"/api/models/{ref}")
    except CliApiError:
        die(
            f"model {ref!r} is not registered. Pull it first (hal0 model pull {ref}), "
            f"register a local file (hal0 model add /path/to/file.gguf), or rescan "
            f"(hal0 model scan)."
        )
        return

    # 2. Resolve the target slot.
    target = slot
    if not target:
        try:
            slots = api_get("/api/slots")
        except CliApiError as exc:
            die(str(exc))
            return
        rows = slots.get("slots", slots) if isinstance(slots, dict) else slots
        names = [r.get("name", "") for r in rows if isinstance(r, dict)]
        if not names:
            die(
                "no slots configured — create one first: "
                "hal0 slot create chat  (then re-run this command)"
            )
            return
        target = "chat" if "chat" in names else names[0]
        console.print(f"[dim]No --slot given; using slot [bold]{target}[/bold].[/dim]")

    # 3. Load with the model assigned.
    try:
        api_post(f"/api/slots/{target}/load", json={"model_id": ref})
    except CliApiError as exc:
        die(f"{exc}\nCheck compatibility with: hal0 slot show {target}")
        return

    # 4. Poll until ready (or failed/timeout).
    deadline = time.monotonic() + max(timeout_s, 1)
    state = "unknown"
    while time.monotonic() < deadline:
        try:
            snap = api_get(f"/api/slots/{target}")
        except CliApiError as exc:
            die(str(exc))
            return
        state = str(snap.get("status") or snap.get("state") or "unknown")
        if state.lower() in ("ready", "running", "loaded"):
            port = snap.get("port")
            console.print(f"[green]Ready.[/green] {ref} serving on slot [bold]{target}[/bold]")
            if port:
                console.print(
                    f"  Try it: curl -s http://127.0.0.1:{port}/v1/chat/completions "
                    f'-H "Content-Type: application/json" '
                    f'-d \'{{"model":"{ref}","messages":[{{"role":"user","content":"hi"}}]}}\''
                )
            return
        if state.lower() in ("failed", "error", "dead"):
            die(f"slot {target} entered state {state!r} — inspect with: hal0 slot logs {target}")
            return
        console.print(f"  [dim]{state}…[/dim]", end="\r")
        time.sleep(2.0)
    die(f"timed out after {timeout_s}s waiting for slot {target} (last state: {state})")


@app.command("import-backup")
def model_import_backup(
    path: Path = typer.Argument(
        ...,
        help="Path to hal0-v0.1-backup-YYYY-MM-DD.tar.gz produced by the "
        "v0.1.x backup instructions in install.sh.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite an existing registry.toml at the destination. "
        "Without this, the command refuses to clobber a registry that "
        "may already hold v0.2 selections.",
    ),
    dest: Path = typer.Option(
        DEFAULT_REGISTRY_PATH,
        "--dest",
        help="Destination registry.toml path. Test/dev escape hatch — "
        "production always uses /var/lib/hal0/registry/registry.toml.",
    ),
) -> None:
    """Restore ``registry.toml`` from a v0.1.x disaster-recovery backup.

    v0.1.x → v0.2 disaster recovery command (formerly ``hal0 registry
    import``, moved here because ``hal0 model`` already owns registry
    CRUD). Slot selections, ``capabilities.toml``, and per-slot TOML files
    are NOT restored — v0.1.x → v0.2 is a clean break; redo slot selection
    via the bundle picker or ``hal0 slot create`` after importing.

    Post-ML-1, ``registry.toml`` is a derived snapshot — the SQLite
    registry is what every runtime reader consults, and it ignores a
    non-empty on-disk TOML (:mod:`hal0.registry.sqlite_store` §161-163).
    Restoring the TOML alone would silently do nothing on any box that has
    already cut over. Chain the same idempotent ``INSERT OR IGNORE`` import
    ``hal0 registry import-sqlite`` uses so the restored entries actually
    become visible to ``hal0 model list`` — never overwrites a model id
    already present in SQLite.
    """
    _do_import_backup(path, force, dest)
    report = import_toml_to_sqlite(registry_file=dest)
    console.print(
        f"[green]sqlite sync[/green]: {report.imported} imported, "
        f"{report.skipped_existing} already present, "
        f"{report.skipped_invalid} invalid (skipped)"
    )
