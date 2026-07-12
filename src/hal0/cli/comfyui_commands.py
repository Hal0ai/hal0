"""hal0 comfyui subcommands — ComfyUI model provisioning helpers.

Currently exposes ``orchestrate-models`` (#1199): pull the curated ComfyUI model
set (the default variant of every capability) in one sequence, logging each step
and writing an operator-readable log.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="ComfyUI model provisioning.")
console = Console()


@app.command("orchestrate-models")
def orchestrate_models_cmd(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="List the curated families and their scripts without downloading.",
    ),
    log_dir: str = typer.Option(
        "",
        "--log-dir",
        help="Directory for the run log (default: <model-store>/comfyui/logs).",
    ),
) -> None:
    """Pull the curated ComfyUI model set end-to-end, logging each step.

    Runs the default variant of every capability (SDXL/Qwen/Wan/LTX-2/Hunyuan/
    ESRGAN) in sequence via the vendored ``get_*.sh`` scripts. A failed *optional*
    asset (e.g. the ESRGAN 4x-UltraSharp mirror) does not block the other
    families; the command exits non-zero only if a *required* family fails.
    Already-present files are skipped, so it is safe to re-run.
    """
    from pathlib import Path

    from hal0.comfyui.orchestrate import OPTIONAL_FAMILIES, curated_set, orchestrate_models

    pairs = curated_set()

    if dry_run:
        table = Table(title="Curated ComfyUI model set")
        table.add_column("Capability")
        table.add_column("Family")
        table.add_column("Script")
        table.add_column("Steps", justify="right")
        table.add_column("Kind")
        for cap_id, variant in pairs:
            kind = "optional" if variant.family in OPTIONAL_FAMILIES else "required"
            table.add_row(
                cap_id,
                variant.family,
                variant.fetch_script,
                str(len(variant.fetch_steps or ((),))),
                kind,
            )
        console.print(table)
        return

    console.print("[bold]Pulling curated ComfyUI model set…[/bold]")
    result = orchestrate_models(
        pairs,
        log_dir=Path(log_dir) if log_dir else None,
        on_line=lambda line: console.print(line, highlight=False),
    )

    console.print()
    console.print(f"[bold]landed[/bold]: {', '.join(result.landed) or '—'}")
    if result.failed_optional:
        console.print(
            f"[yellow]failed (optional, skipped)[/yellow]: {', '.join(result.failed_optional)}"
        )
    if result.failed_required:
        console.print(f"[red]failed (required)[/red]: {', '.join(result.failed_required)}")
    console.print(f"log written to: {result.log_path}")

    if not result.ok:
        raise typer.Exit(1)


__all__ = ["app"]
