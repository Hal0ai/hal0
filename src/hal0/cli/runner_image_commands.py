"""CLI implementation for ``hal0 runner-images ls|sync|pull`` (#2106).

Closes the CLI-absence gap on the runner-image catalogue: the catalogue
has had a full API surface (``hal0.api.routes.runner_images``) and a
dashboard page since catalogue-v2, but no CLI verb at all — an operator on
a headless box (or scripting a provision step) had no way to list, sync,
or pull a runner image without curling the API by hand.

Talks to the **service layer directly** (:class:`RunnerImageStore`,
:func:`sync_runner_images`, :func:`run_runner_pull`) rather than through
the local API client, matching the read-verb idiom other direct-store CLI
groups use (e.g. ``hal0 registry``): ``ls`` must keep working even when
``hal0-api`` itself is down, and ``sync``/``pull`` reuse the exact same
service calls the API route handlers do, so behaviour (tag validation,
store writes) is identical either way.

``rm`` is deliberately absent — the delete story is still open (spec D2).
"""

from __future__ import annotations

import asyncio
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from hal0.providers.podman_introspect import LocalImagesDigests
from hal0.registry.runner_image import RunnerImage
from hal0.registry.runner_image_store import RunnerImageStore
from hal0.registry.runner_pull import (
    RunnerImageTagInvalid,
    RunnerPullJob,
    make_job,
    validate_pull_tag,
)

app = typer.Typer(
    name="runner-images",
    help="Manage the runner-image catalogue (GHCR toolbox/runner images).",
    no_args_is_help=True,
)

console = Console()


def _local_store() -> LocalImagesDigests | None:
    """One best-effort local-store read; ``None`` degrades to ``unknown`` rows."""
    from hal0.providers.podman_introspect import images_digests

    try:
        return images_digests()
    except Exception:
        return None


def _store_state(image: RunnerImage, local: LocalImagesDigests | None) -> str:
    """``present``/``missing``/``unknown`` for one row.

    Same rule as ``hal0.api.routes.runner_images.enrich_row``'s headline
    fallback: digest match first, then an exact ``image:tag`` match in the
    local refs. ``unknown`` when neither podman store answered at all.
    """
    if local is None:
        return "unknown"
    local_digests = set(filter(None, local.refs.values()))
    if image.digest and image.digest in local_digests:
        return "present"
    ref = f"{image.image}:{image.tag}"
    return "present" if ref in local.refs else "missing"


_STATE_BADGE = {
    "present": "[green]present[/green]",
    "missing": "[yellow]missing[/yellow]",
    "unknown": "[dim]unknown[/dim]",
}


@app.command("ls")
def ls() -> None:
    """List every catalogued runner image."""
    store = RunnerImageStore()
    images = store.list()
    local = _local_store()

    table = Table(title="Runner images")
    table.add_column("ID", style="bold")
    table.add_column("Image:Tag")
    table.add_column("Store")
    table.add_column("Tags", justify="right")
    for image in images:
        state = _store_state(image, local)
        tag_count = len(image.available_tags) if image.available_tags else 1
        table.add_row(
            image.id,
            f"{image.image}:{image.tag}",
            _STATE_BADGE[state],
            str(tag_count),
        )
    console.print(table)
    if not images:
        console.print("[dim]no runner images catalogued yet — run `hal0 runner-images sync`.[/dim]")


@app.command("sync")
def sync() -> None:
    """Run one discovery pass now (GHCR probe + images.json merge)."""
    from hal0.registry.runner_image_sync import sync_runner_images

    store = RunnerImageStore()
    result = asyncio.run(sync_runner_images(store))
    console.print(f"synced {len(result.images)} runner image(s).")
    if result.images_json_error:
        console.print(f"[yellow]![/yellow]  images.json: {result.images_json_error}")
    if result.probe_errors:
        for image_id, err in result.probe_errors.items():
            console.print(f"[yellow]![/yellow]  {image_id}: {err}")
    if not result.images and result.images_json_error:
        raise typer.Exit(1)


def _resolve_pull_tag(entry: RunnerImage, tag: str | None) -> str:
    """Validate ``--tag`` the same way ``POST /{id}/pull`` does.

    The headline tag is always allowed; anything else must already be a
    catalogued ``available_tags`` member — free-text refs stay out, the
    catalogue is the honesty boundary. Raises :class:`typer.Exit` (via
    ``console.print`` + ``raise typer.Exit(1)``) on a bad tag rather than
    a service exception, so the CLI error reads the same as every other
    verb's failure path.
    """
    if tag is None:
        return entry.tag
    try:
        validate_pull_tag(tag)
    except RunnerImageTagInvalid as exc:
        console.print(f"[red]✗[/red]  {exc}")
        raise typer.Exit(1) from exc
    if tag != entry.tag and tag not in entry.available_tags:
        available = ", ".join(entry.available_tags) or entry.tag
        console.print(
            f"[red]✗[/red]  tag {tag!r} is not a catalogued tag of {entry.id!r} "
            f"(available: {available}) — run `hal0 runner-images sync` first."
        )
        raise typer.Exit(1)
    return tag


async def _drive_pull(job: RunnerPullJob, *, store: RunnerImageStore, provider: Any) -> None:
    """Run the pull, printing ``layers_done/layers_total`` on one updating line.

    Watches ``job.progress_event`` (set on every state change by
    :func:`hal0.registry.runner_pull.run_runner_pull`) rather than polling
    on a timer, so the printed line advances exactly when the job itself
    advances.
    """
    from hal0.registry.runner_pull import run_runner_pull

    pull_task = asyncio.ensure_future(run_runner_pull(job, store=store, provider=provider))
    while not pull_task.done():
        wait_task = asyncio.ensure_future(job.progress_event.wait())
        _done, pending = await asyncio.wait(
            {pull_task, wait_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if wait_task in pending:
            wait_task.cancel()
        console.print(f"\r{job.layers_done}/{job.layers_total} layers done", end="")
    console.print()  # newline after the last updating line
    await pull_task


@app.command("pull")
def pull(
    image_id: str = typer.Argument(..., help="Catalogue id, e.g. hal0ai/hal0-toolbox-cpu."),
    tag: str | None = typer.Option(
        None, "--tag", help="Tag to pull (default: the row's headline tag)."
    ),
) -> None:
    """Pull one catalogued runner image (synchronous, with progress)."""
    from hal0.providers.container import container_provider

    store = RunnerImageStore()
    entry = store.get(image_id)
    if entry is None:
        console.print(
            f"[red]✗[/red]  runner image {image_id!r} not in catalogue — "
            "run `hal0 runner-images sync` first."
        )
        raise typer.Exit(1)

    pull_tag = _resolve_pull_tag(entry, tag)
    image_ref = f"{entry.image}:{pull_tag}"
    job = make_job(image_id, image_ref, tag=pull_tag)

    asyncio.run(_drive_pull(job, store=store, provider=container_provider()))

    if job.state == "failed":
        console.print(f"[red]✗[/red]  pull failed: {job.error}")
        raise typer.Exit(1)
    if job.state == "cancelled":
        console.print("[yellow]![/yellow]  pull cancelled.")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green]  pulled {image_ref} -> {job.local_path}")


__all__ = ["app"]
