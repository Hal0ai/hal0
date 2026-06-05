"""hal0 model subcommands — thin HTTP client to the hal0 API."""

from __future__ import annotations

import json as jsonlib

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_delete,
    api_get,
    api_put,
    die,
)

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
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Upstream")
    table.add_column("Size", justify="right")
    if not models:
        console.print("[dim]No models available.[/dim]")
        return
    for m in models:
        table.add_row(
            m.get("id", "—"),
            m.get("name") or m.get("id", "—"),
            m.get("upstream") or m.get("owned_by") or "—",
            _fmt_size(m.get("size_bytes")),
        )
    console.print(table)


@app.command("pull")
def model_pull(
    ref: str = typer.Argument(..., help="Curated alias (e.g. qwen3-4b) or registered model id"),
) -> None:
    """Download a model from Hugging Face into the local registry.

    Starts the pull as a background job on the daemon, then polls
    ``/api/models/<id>/pull/status`` every 500ms and prints a tqdm-style
    progress bar until the job reaches a terminal state.
    """
    import time

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        from hal0.cli._shared import api_post

        start = api_post(f"/api/models/{ref}/pull")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        f"Starting pull for [bold]{ref}[/bold] "
        f"({start.get('hf_repo', '?')}/{start.get('hf_file', '?')})…"
    )

    # Poll status — keeps the CLI dependency footprint small. The HF
    # tier is well within rate budget at 500ms.
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
                    f"[green]Done.[/green] {ref} → {s.get('path')}  "
                    f"({_fmt_size(downloaded)}, sha256 {sha}…)"
                )
                return
            err = s.get("error") or "(no error message)"
            die(f"pull {state}: {err}")
            return
        time.sleep(0.5)


@app.command("register")
def model_register(
    model_id: str = typer.Argument(..., help="Model id, e.g. 'qwen3-4b-q4_k_m'"),
    path: str = typer.Option(..., "--path", "-p", help="Absolute path to the model file."),
    name: str = typer.Option("", "--name", help="Display name."),
    license_id: str = typer.Option("unknown", "--license", help="SPDX license id."),
) -> None:
    """Register a model that's already on disk into the local registry."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    payload = {
        "id": model_id,
        "path": path,
        "name": name or model_id,
        "license": license_id,
    }
    try:
        from hal0.cli._shared import api_post

        m = api_post("/api/models", json=payload)
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Registered [bold]{m.get('id', model_id)}[/bold] → {m.get('path', path)}")


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


@app.command("assign")
def model_assign(
    ref: str = typer.Argument(..., help="Model ref to assign"),
    slot: str = typer.Option(..., "--slot", "-s", help="Slot name to assign the model to"),
) -> None:
    """Assign a model to a slot's default (does not load the slot)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_put(f"/api/slots/{slot}/config", json={"model": {"default": ref}})
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"Assigned [bold]{ref}[/bold] → slot [bold]{slot}[/bold]")
