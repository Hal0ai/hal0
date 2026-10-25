"""hal0 comfyui subcommands — image models and the files they put on disk (ADR-0037).

Thin clients over ``/api/comfyui/*``: the daemon owns the catalog, the downloads and
the models tree. ``models install`` starts each download as a daemon job and follows it,
one model at a time, so Ctrl-C stops the CLI, not the install in flight.
"""

from __future__ import annotations

import json as jsonlib
import time
from collections.abc import Sequence
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
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

app = typer.Typer(help="ComfyUI image models and files.")
models_app = typer.Typer(help="Install, list and remove image models.")
app.add_typer(models_app, name="models")
console = Console()

#: Seconds between polls of ``/api/comfyui/jobs`` while an install runs.
_POLL_INTERVAL_S = 0.5
#: Job states after which a job no longer changes.
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_ACTIVE = frozenset({"queued", "running"})
#: A progress line is printed each time the percentage crosses a multiple of this.
_PROGRESS_STEP_PCT = 5


def _require_api() -> None:
    if _api_unreachable(_api_base()):
        raise typer.Exit(1)


#: Bytes in a GiB. Every size this CLI prints is binary, to match the GiB the API rows carry.
_GIB = 2**30


def _fmt_bytes(n: int | float | None) -> str:
    """Binary units, to match the GiB the API's ``approx_gb`` and ``missing_gb`` report."""
    n = float(n or 0)
    if n >= _GIB:
        return f"{n / _GIB:.1f} GiB"
    if n >= 2**20:
        return f"{n / 2**20:.1f} MiB"
    return f"{n / 2**10:.1f} KiB"


@models_app.command("list")
def models_list(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/comfyui/models JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """List the image models: install state, and how much each still has to download."""
    _require_api()
    try:
        data = api_get("/api/comfyui/models")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(data, indent=2))
        return
    models = data.get("models", []) if isinstance(data, dict) else []
    if not models:
        console.print("[dim]No image models in the catalog.[/dim]")
        return
    table = Table(title=f"Image models ({len(models)})")
    table.add_column("")  # recommended marker, unlabeled like `hal0 model list`'s default marker
    table.add_column("ID", style="bold")
    table.add_column("Task")
    table.add_column("State")
    table.add_column("GiB to download", justify="right")
    for m in models:
        job = m.get("job") or {}
        state = "installing" if job.get("state") in _ACTIVE else m.get("state", "—")
        missing_gb = m.get("missing_gb") or 0
        table.add_row(
            "[green]*[/green]" if m.get("recommended") else "",
            m.get("id", "—"),
            m.get("task", "—"),
            state,
            f"{missing_gb:.1f}" if missing_gb else "—",
        )
    if any(m.get("recommended") for m in models):
        table.caption = "* recommended for this box"
    console.print(table)


@models_app.command("install")
def models_install(
    ids: list[str] = typer.Argument(
        ...,
        metavar="ID...",
        help="Image model ids to install (see `hal0 comfyui models list`).",
    ),
) -> None:
    """Download image models from Hugging Face, one at a time, following each to the end.

    Starts a model's install as a background job on the daemon, polls
    ``/api/comfyui/jobs`` every 500 ms and prints progress until that job is
    terminal, then moves to the next model. They never run side by side: models
    share files, and two 40 GiB downloads at once help nobody. A model that is
    already installed counts as done; one whose install is already running is
    followed. A failed, cancelled or refused model (unknown id, no disk space)
    does not stop the ones after it. Exits 1 if any did not install. Ctrl-C stops
    following, not the install in flight; models after it are not started.
    """
    _require_api()
    queue = list(dict.fromkeys(ids))
    failed = False
    while queue:
        model_id = queue.pop(0)
        try:
            following = _start_install(model_id)
        except KeyboardInterrupt:
            _exit_interrupted(queue)
        if following is None or (following and _follow([model_id], not_started=queue)):
            failed = True
    if failed:
        raise typer.Exit(1)


def _start_install(model_id: str) -> bool | None:
    """Ask the daemon to install ``model_id``.

    ``True`` when there is a job to follow (a new one, or one already running), ``False``
    when the model is already installed, ``None`` when the daemon refused.
    """
    from hal0.comfyui.catalog import install_path

    try:
        api_post(install_path(model_id))
    except CliApiError as exc:
        if exc.code == "comfyui.model_installed":
            console.print(f"{escape(model_id)} is already installed.")
            return False
        if exc.code == "comfyui.install_running":
            console.print(f"{escape(model_id)} is already installing; following it.")
            return True
        console.print(f"[red]failed[/red] {escape(model_id)}: {escape(str(exc))}")
        return None
    console.print(f"Installing [bold]{escape(model_id)}[/bold]…")
    return True


def _follow(model_ids: list[str], *, not_started: Sequence[str] = ()) -> bool:
    """Poll the install jobs of ``model_ids`` until each is terminal.

    Returns ``True`` if any ended other than ``completed``. A job missing from the
    daemon's list counts as failed: the daemon forgets its jobs when it restarts.
    ``not_started`` names the models queued behind these, for the Ctrl-C message.
    """
    pending = list(model_ids)
    last_step: dict[str, int] = {}
    unfinished = False
    try:
        while pending:
            try:
                data = api_get("/api/comfyui/jobs")
            except CliApiError as exc:
                die(str(exc))
                return True
            listed = data.get("jobs", []) if isinstance(data, dict) else []
            jobs = {job.get("model_id"): job for job in listed}
            for model_id in list(pending):
                job = jobs.get(model_id)
                if job is not None and job.get("state") not in _TERMINAL:
                    _print_progress(model_id, job, last_step)
                    continue
                pending.remove(model_id)
                if job is None:
                    console.print(
                        f"[red]failed[/red] {escape(model_id)}: the install job is gone "
                        "(did hal0-api restart?)"
                    )
                    unfinished = True
                elif not _print_outcome(model_id, job):
                    unfinished = True
            if pending:
                time.sleep(_POLL_INTERVAL_S)
    except KeyboardInterrupt:
        _exit_interrupted(not_started)
    return unfinished


def _exit_interrupted(not_started: Sequence[str] = ()) -> NoReturn:
    """Ctrl-C: stop the CLI, not the download the daemon already started.

    ``not_started`` are the models still queued behind it; installs run one at a time,
    so they were never requested.
    """
    console.print(
        "\nStopped. Installs already started continue on the daemon; "
        "check them with: [bold]hal0 comfyui models list[/bold]"
    )
    if not_started:
        console.print(f"Not started: {escape(', '.join(not_started))}")
    raise typer.Exit(130) from None


def _print_progress(model_id: str, job: dict[str, Any], last_step: dict[str, int]) -> None:
    """One ``<id>: <pct>% (<done>/<total> GiB)`` line each time ``job`` crosses a step."""
    done = int(job.get("bytes_downloaded") or 0)
    total = int(job.get("bytes_total") or 0)
    if job.get("state") != "running" or total <= 0:
        return
    pct = int(done * 100 / total)
    step = pct // _PROGRESS_STEP_PCT
    if last_step.get(model_id) == step:
        return
    last_step[model_id] = step
    console.print(
        f"  {model_id}: {pct}% ({done / _GIB:.1f}/{total / _GIB:.1f} GiB)",
        markup=False,
        highlight=False,
    )


def _print_outcome(model_id: str, job: dict[str, Any]) -> bool:
    """Print how ``job`` ended; ``True`` when it completed."""
    state = job.get("state")
    if state == "completed":
        console.print(f"[green]installed[/green] {escape(model_id)}")
        return True
    if state == "cancelled":
        console.print(f"[yellow]cancelled[/yellow] {escape(model_id)}")
        return False
    error = job.get("error") or "(no error message)"
    console.print(f"[red]failed[/red] {escape(model_id)}: {escape(str(error))}")
    return False


@models_app.command("remove")
def models_remove(
    model_id: str = typer.Argument(..., metavar="ID", help="Image model id to remove."),
    include_shared: bool = typer.Option(
        False,
        "--include-shared",
        help="Also delete weight files other installed models load; those models stop working.",
    ),
) -> None:
    """Delete an image model's weight files and workflow.

    A weight file that another installed model also loads stays on disk, and is listed
    with the models that hold it; `--include-shared` deletes it too and names the models
    that are no longer installed. Refused while an install of the model, or of a model
    sharing its files, is running.
    """
    _require_api()
    try:
        if include_shared:
            data = api_delete(f"/api/comfyui/models/{model_id}", params={"include_shared": "true"})
        else:
            data = api_delete(f"/api/comfyui/models/{model_id}")
    except CliApiError as exc:
        die(str(exc))
        return
    body = data if isinstance(data, dict) else {}
    removed = body.get("removed", [])
    kept = body.get("kept", [])
    affected = body.get("affected", [])
    if not removed and not kept:
        console.print(f"Nothing to remove for {escape(model_id)}: none of its files are on disk.")
        return
    if removed:
        console.print(f"Removed {len(removed)} file(s) of [bold]{escape(model_id)}[/bold]:")
        for relpath in removed:
            console.print(f"  {relpath}", markup=False, highlight=False)
    else:
        console.print(f"Removed no files of [bold]{escape(model_id)}[/bold].")
    for entry in kept:
        holders = ", ".join(entry.get("models") or [])
        console.print(
            f"  kept {entry.get('relpath', '—')} (also used by {holders})",
            markup=False,
            highlight=False,
        )
    if kept:
        console.print(
            "Rerun with --include-shared to delete the kept files too.",
            markup=False,
            highlight=False,
        )
    if affected:
        console.print(f"No longer installed: {escape(', '.join(affected))}")


@app.command("files")
def files() -> None:
    """List the weight files in the ComfyUI models tree, grouped by kind.

    Each file shows its size and the catalog models that load it; a file no catalog
    model owns (your own checkpoint, for example) shows none.
    """
    _require_api()
    try:
        data = api_get("/api/comfyui/files")
    except CliApiError as exc:
        die(str(exc))
        return
    root = data.get("root", "") if isinstance(data, dict) else ""
    listed = data.get("files", []) if isinstance(data, dict) else []
    if not listed:
        console.print(f"[dim]No model files under {escape(str(root))}.[/dim]")
        return
    console.print(f"Models root: {escape(str(root))}")
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for entry in listed:
        by_kind.setdefault(str(entry.get("kind", "other")), []).append(entry)
    for kind, entries in by_kind.items():
        table = Table(title=f"{kind} ({len(entries)})", title_justify="left")
        table.add_column("File", style="bold", overflow="fold")
        table.add_column("Size", justify="right")
        table.add_column("Models")
        for entry in entries:
            table.add_row(
                escape(str(entry.get("relpath", "—"))),
                _fmt_bytes(entry.get("size_bytes")),
                ", ".join(entry.get("models") or []) or "—",
            )
        console.print(table)


__all__ = ["app"]
