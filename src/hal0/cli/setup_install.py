"""Hybrid apply step for ``hal0 setup`` (spec §11, Task 4.1).

Both the ``--auto`` (install.sh) path and the interactive TUI funnel their
:class:`~hal0.install.orchestrate.Selections` through :func:`run_install`,
which picks an execution mode:

* **in_process** — hal0-api is not running (install time). We build offline
  deps mirroring the API lifespan, call ``apply_setup`` directly, then stream
  the planned model pulls with a rich progress display.
* **api** — hal0-api IS running. We POST the selections to the tier-less
  ``/api/install/apply-selections`` endpoint so the *live* service registers
  the slots itself (roster coherence — a post-install ``hal0 setup`` on a
  running box must not drift the in-memory slot roster).

The mode decision is unit-tested via :func:`choose_apply_mode`; the progress
rendering drives the real :class:`~hal0.registry.pull.PullJob` API (no progress
callback exists — ``run_pull`` mutates ``job.state`` / ``job.bytes_downloaded``
/ ``job.bytes_total`` in place — so we poll those fields in a ``rich.progress``
loop while the pulls run concurrently).
"""

from __future__ import annotations

import asyncio
import dataclasses

import httpx
import typer

from hal0.cli._shared import _api_base

# Imported as a MODULE ATTRIBUTE (not `from ... import _api_reachable` used at
# call-site only) so tests can monkeypatch ``hal0.cli.setup_install._api_reachable``.
from hal0.cli.setup_command import _api_reachable

_DASHBOARD_URL = "https://hal0.thinmint.dev"


def choose_apply_mode() -> str:
    """Return ``"api"`` when hal0-api is reachable, else ``"in_process"``."""
    return "api" if _api_reachable() else "in_process"


async def run_install(sel, hw, *, no_pull: bool = False) -> None:
    """Apply ``sel`` via the live API when it is up, else in-process.

    *no_pull* is threaded to :func:`_apply_in_process`; the API path
    always defers pulls to BackgroundTasks and ignores this flag.
    """
    if choose_apply_mode() == "api":
        await _apply_via_api(sel)
    else:
        await _apply_in_process(sel, hw, no_pull=no_pull)


async def _apply_in_process(sel, hw, *, no_pull: bool = False) -> None:
    """Install-time path: orchestrate offline, then stream the pulls.

    When *no_pull* is ``True`` the slot configs + first-run sentinel are
    written but model downloads are skipped entirely.  The operator runs
    ``hal0 setup`` (interactive) or ``hal0 model pull`` later to fetch
    the models.
    """
    from hal0.cli import setup_command  # imported lazily so monkeypatch lands
    from hal0.install.orchestrate import apply_setup

    slot_manager, registry = setup_command._build_offline_deps()
    result = await apply_setup(
        sel,
        hardware=hw,
        slot_manager=slot_manager,
        registry=registry,
        jobs={},
        write_sentinel=True,
    )

    if no_pull:
        n_slots = sum(1 for s in result.slots if getattr(s, "created", False))
        typer.echo(
            f"Seeded {n_slots} slot(s); run `hal0 setup` or `hal0 model pull` to download models."
        )
        return

    await _run_pulls_with_progress(result.pulls)

    n_slots = sum(1 for s in result.slots if getattr(s, "created", False))
    typer.echo(
        f"hal0 setup complete: {len(result.model_ids)} model(s), {n_slots} slot(s). "
        f"Dashboard: {_DASHBOARD_URL}"
    )


async def _run_pulls_with_progress(pulls) -> None:
    """Drive ``run_pull`` for each plan while rendering one rich bar per model.

    ``run_pull`` takes no progress callback — it mutates the ``PullJob`` in
    place (``job.state``, ``job.bytes_downloaded``, ``job.bytes_total``). So we
    launch all pulls as a single ``gather`` task and poll the job objects in a
    :class:`rich.progress.Progress` loop until the gather completes, updating
    each bar's total/completed from the live job fields.
    """
    if not pulls:
        return

    from hal0.registry.pull import run_pull

    gather_task = asyncio.gather(
        *(run_pull(p.job, **p.kwargs) for p in pulls),
        return_exceptions=True,
    )

    try:
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeRemainingColumn,
        )
    except Exception:
        # rich unavailable — fall back to a plain await + line prints.
        for p in pulls:
            typer.echo(f"downloading {p.model_id}...")
        results = await gather_task
        for p, res in zip(pulls, results, strict=False):
            if isinstance(res, BaseException):
                typer.echo(f"  failed {p.model_id}: {res}")
            else:
                typer.echo(f"  done {p.model_id}")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TimeRemainingColumn(),
        transient=False,
    ) as progress:
        bars = {}
        for p in pulls:
            bars[id(p)] = progress.add_task(p.model_id, total=None)

        # Poll the live job fields until every pull has finished. The gather
        # task drives the actual downloads concurrently.
        while not gather_task.done():
            for p in pulls:
                job = p.job
                tid = bars[id(p)]
                total = job.bytes_total or None
                progress.update(tid, total=total, completed=job.bytes_downloaded)
            await asyncio.sleep(0.1)

        # Final paint — settle each bar to its terminal state.
        results = await gather_task
        for p, res in zip(pulls, results, strict=False):
            job = p.job
            tid = bars[id(p)]
            total = job.bytes_total or job.bytes_downloaded or None
            progress.update(tid, total=total, completed=job.bytes_downloaded)
            if isinstance(res, BaseException) or job.state == "failed":
                err = res if isinstance(res, BaseException) else job.error
                progress.update(tid, description=f"{p.model_id} [red]FAILED[/red]")
                typer.echo(f"pull failed for {p.model_id}: {err}", err=True)


async def _apply_via_api(sel) -> None:
    """API-up path: POST the selections to the live service."""
    payload = dataclasses.asdict(sel)
    url = f"{_api_base()}/api/install/apply-selections"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    n_models = len(data.get("model_ids", []))
    n_slots = len(data.get("slots", []))
    typer.echo(
        f"hal0 setup applied via API: {n_models} model(s), {n_slots} slot(s) "
        f"(downloads run on the service). Dashboard: {_DASHBOARD_URL}"
    )
