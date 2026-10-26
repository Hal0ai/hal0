"""CLI implementation for ``hal0 update``.

Thin client over the /api/updates/* surface. The CLI never invokes
``Updater`` directly - it goes through the daemon so the same code path
is exercised whether you trigger an update from the dashboard or the
shell. After a successful apply the daemon try-restarts hal0-api itself
(see ``routes/updater._run_apply_job``); the CLI does not touch systemd.

Surface:
    hal0 update                 # check + apply if newer
    hal0 update --check         # check only
    hal0 update --rollback      # roll back to previous tree
    hal0 update --channel CH    # set channel (persists), then check
    hal0 update --target VER    # pin a specific version
"""

from __future__ import annotations

import sys
import time
from enum import StrEnum
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    CliApiTimeout,
    _api_base,
    _api_unreachable,
    api_get,
    api_post,
    api_put,
    die,
    run_with_progress,
)
from hal0.slot_lifecycle_budget import slot_lifecycle_timeout_s

console = Console()

#: Client read budget for ``POST /api/updates/rollback`` (#2661). The route is
#: synchronous and always re-pips the prior tree into the venv (13 s on a
#: 32-core box, 15 s end to end), so ``api_post``'s 10 s default reported a
#: failure while the server finished the rollback and flipped ``hal0.previous``.
#: 300 s matches the long-running-POST precedent of ``--restart-slots`` (#1832)
#: and leaves ~20x headroom for a slow disk, a cold pip cache or a loaded box.
_ROLLBACK_TIMEOUT_S = 300.0

#: The `hal0 update` surface. The group callback runs the hal0 self-update
#: (the historical behaviour of the bare `hal0 update`); `status` and `component`
#: are sibling verbs for the release-pinned components. Registered in main.py via
#: ``app.add_typer(update_app, name="update")``.
update_app = typer.Typer(
    help="Update hal0, or one of its components (`hal0 update component <id>`).",
    no_args_is_help=False,
)


class UpdateChannel(StrEnum):
    stable = "stable"
    preview = "preview"
    nightly = "nightly"


#: A manifest whose version is exactly "0.0.0" (optionally paired with an
#: all-zero digest) is the placeholder the release service serves for a
#: channel with nothing published yet — not a real, installable target.
#: Rendering it as "→ 0.0.0 up to date" tells the operator there is nothing
#: to install when the truth is nobody has published anything here.
_PLACEHOLDER_VERSION = "0.0.0"
_PLACEHOLDER_DIGEST = "0" * 64


def _is_placeholder_manifest(latest: str, manifest: dict) -> bool:
    if latest.strip() == _PLACEHOLDER_VERSION:
        return True
    digest = manifest.get("digest_sha256")
    return isinstance(digest, str) and digest == _PLACEHOLDER_DIGEST


def _print_override_note(override: str, channel: str) -> None:
    """Disclose an active HAL0_RELEASES_URL override next to the channel (#2128).

    The override bypasses channel resolution entirely, so any output that
    names the configured channel without this note reads exactly like a
    correctly-honoured channel setting — while the box may be pinned to one
    immutable asset forever.
    """
    if not override:
        return
    console.print(
        f"[yellow]manifest URL overridden by HAL0_RELEASES_URL={override} "
        f"(configured channel '{channel}' ignored)[/yellow]"
    )


def _print_check(body: dict) -> None:
    """Render the /api/updates/check response as a rich panel + table."""
    current = body.get("current", "?")
    latest_raw = str(body.get("latest") or "")
    channel = body.get("channel", "stable")
    available = body.get("update_available", False)
    override = str(body.get("releases_url_override") or "")
    channel_label = f"{channel} — overridden" if override else str(channel)
    manifest = body.get("manifest") or {}
    if not isinstance(manifest, dict):
        manifest = {}

    if _is_placeholder_manifest(latest_raw, manifest):
        console.print(
            Panel(
                f"[bold]hal0[/bold] {current}  ({channel_label})  "
                "[yellow]no release published on this channel[/yellow]",
                border_style="cyan",
            )
        )
        _print_override_note(override, channel)
        return

    latest = latest_raw or "—"
    if available:
        status = "[green]update available[/green]"
    elif override:
        # "up to date" cannot mean what it normally means against a pinned
        # manifest: an immutable asset reports it forever, no matter what
        # ships on the real channel (#2128).
        status = "[yellow]pinned by HAL0_RELEASES_URL[/yellow]"
    else:
        status = "[dim]up to date[/dim]"
    console.print(
        Panel(
            f"[bold]hal0[/bold] {current}  →  {latest}  ({channel_label})  {status}",
            border_style="cyan",
        )
    )
    _print_override_note(override, channel)
    if manifest:
        table = Table(show_header=False, box=None, padding=(0, 2))
        for key in ("released_at", "notes_url", "digest_sha256", "signer_identity"):
            val = manifest.get(key)
            if val:
                table.add_row(f"[dim]{key}[/dim]", str(val))
        console.print(table)


def _refuse_if_editable() -> None:
    """Die early on an editable/dev install — `hal0 update` can't swap the FHS tree.

    Mirrors the daemon-side refusal (audit 4.1) so an operator on a
    `pip install -e` checkout gets an immediate, actionable message instead of
    a background prepare job that silently fails. Detection is metadata-driven
    so an editable install cloned from git is caught too.
    """
    from hal0.updater.updater import _editable_install_path, _is_editable_install

    if not _is_editable_install():
        return
    path = _editable_install_path() or "the current source tree"
    die(
        f"hal0 is installed in editable mode from {path}. "
        "Install from release wheel with `pip install hal0`."
    )


#: How long the poll tolerates a completely unreachable API before giving up.
#: A commit restarts hal0-api underneath us, so connection-refused is the
#: EXPECTED response for a few seconds mid-update, not a failure (#1540).
#: Bounded separately from ``timeout_s`` so a genuinely dead API still fails in
#: seconds rather than sitting out the full 600s job budget.
_POLL_UNREACHABLE_BUDGET_S = 180.0


def _poll_job(
    job_id: str,
    *,
    terminal: tuple[str, ...] = ("applied", "failed"),
    timeout_s: float = 600.0,
) -> dict:
    """Poll /api/updates/status/<id> until it reaches a terminal state.

    Survives the restart window. ``commit`` bounces hal0-api as part of
    applying, so this poll is talking to a service that is deliberately
    going away and coming back. Treating the resulting connection error as
    fatal made every successful update exit 1 (#1540).

    Only *transport* failures are retried. A ``CliApiError`` raised from an
    HTTP status (a live API answering 4xx/5xx) still dies immediately —
    blanket-retrying would turn a real commit failure into a long hang.
    """
    deadline = time.monotonic() + timeout_s
    last_state: str | None = None
    unreachable_since: float | None = None
    warned_unreachable = False
    while time.monotonic() < deadline:
        try:
            job = api_get(f"/api/updates/status/{job_id}")
        except CliApiError as exc:
            if not isinstance(exc.__cause__, httpx.TransportError):
                # A live API returned an error status - that is a real
                # failure, not the restart window.
                die(str(exc))
                return {}
            now = time.monotonic()
            if unreachable_since is None:
                unreachable_since = now
            elif now - unreachable_since > _POLL_UNREACHABLE_BUDGET_S:
                die(
                    f"hal0-api stayed unreachable for "
                    f"{_POLL_UNREACHABLE_BUDGET_S:.0f}s while applying job {job_id}. "
                    "The update may have completed - check `systemctl status hal0-api` "
                    "and `hal0 --version`."
                )
                return {}
            if not warned_unreachable:
                console.print("[dim]· hal0-api restarting, waiting for it to come back…[/dim]")
                warned_unreachable = True
            time.sleep(1.0)
            continue
        unreachable_since = None
        state = job.get("state")
        if state != last_state:
            console.print(f"[dim]· job {job_id} → {state}[/dim]")
            last_state = state
        if state in terminal:
            return job
        time.sleep(0.5)
    die(f"update job {job_id} timed out after {timeout_s:.0f}s")
    return {}


def _interactive() -> bool:
    """True on an interactive TTY — the gate for the pre-commit confirm prompt.

    Factored out so headless/piped runs (cron, scripts, ``|`` pipelines) skip the
    prompt, and so tests can force either path deterministically.
    """
    return sys.stdout.isatty()


def _render_notes(notes: dict | None) -> None:
    """Render the release notes from a prepared update job.

    ``breaking`` / ``migrations`` get a loud panel; ``highlights`` a bullet
    list; the full markdown (if any) is rendered below. All fields optional —
    a release without notes renders nothing.
    """
    if not isinstance(notes, dict):
        return
    highlights = notes.get("highlights") or []
    breaking = notes.get("breaking") or []
    migrations = notes.get("migrations") or []
    markdown = (notes.get("markdown") or "").strip()

    if breaking or migrations:
        lines = [f"[red]⚠ {b}[/red]" for b in breaking]
        lines += [f"[yellow]↻ {m}[/yellow]" for m in migrations]
        console.print(Panel("\n".join(lines), title="Breaking / migrations", border_style="yellow"))
    if highlights:
        console.print("[bold]Highlights[/bold]")
        for h in highlights:
            console.print(f"  • {h}")
    if markdown:
        console.print(Markdown(markdown))


#: Human labels for the non-fatal per-pass failure events
#: run_post_activation_migrations can log (#1960 B2) — keys must match
#: hal0.updater.updater._NON_FATAL_MIGRATION_FAILURE_EVENTS exactly.
_MIGRATION_PASS_LABELS: dict[str, str] = {
    "updater.registry_toml_retire_failed": "registry.toml retirement (see hal0 doctor all)",
    "updater.vulkan_migration_failed": "gpu-vulkan slot relabel",
    "updater.image_keys_strip_failed": (
        "img slot cleanup (delete idle_restore_minutes from [image] in the img slot TOML)"
    ),
    "updater.image_keys_write_failed": (
        "img slot cleanup could not rewrite a slot TOML "
        "(delete idle_restore_minutes from its [image] table by hand)"
    ),
    "updater.image_keys_slot_unreadable": (
        "img slot cleanup could not read a slot TOML (see hal0 doctor all)"
    ),
    "updater.memory_engine_upgrade_failed": "memory engine upgrade",
    "updater.components_converge_failed": "component convergence",
    "updater.retired_skills_prune_failed": "retired bundled-skill cleanup",
    "updater.kokoro_bundle_pull_failed": "kokoro-v1 bundle pull (run: hal0 model pull kokoro-v1)",
    "components.hermes_gateway_regen_failed": (
        "hermes gateway unit regeneration (run: sudo hal0 agent install hermes)"
    ),
}


#: The packaged path of the migration backups directory, for the #2657 hint.
_BACKUPS_DIR_HINT = "/var/lib/hal0/backups"


def _is_backups_dir_denied(error: str) -> bool:
    """True when a post-swap migration failed on an unwritable backups dir (#2657)."""
    return "/backups" in error and (
        "Permission denied" in error or "Errno 13" in error or "not writable" in error
    )


def _print_convergence(convergence: dict | None) -> bool:
    """Report whether the post-update data migrations applied.

    Returns True when fully converged. A False return is what makes
    ``hal0 update`` refuse to call the run a clean success — silently handing
    back a half-migrated box is the failure mode this exists to prevent.
    """
    if not isinstance(convergence, dict):
        return True

    # #1960: post-activation migrations (schema + slot/registry cleanup
    # sweeps) now run in a subprocess AFTER the symlink swap, so a failure
    # there does NOT abort the update — the release is already active. It
    # still has to be reported, loudly, or "hal0 update" would silently hand
    # back a box whose on-disk data still reflects the previous release.
    post_migrations = convergence.get("post_activation_migrations") or {}
    if post_migrations.get("ok"):
        src, dst = post_migrations.get("from"), post_migrations.get("to")
        if src is not None and dst is not None and src != dst:
            console.print(f"[green]✓[/green] on-disk schema migrated v{src} → v{dst}")
        # A pass that swallowed its own exception into a warning is still
        # non-fatal by design (see run_post_activation_migrations) — this
        # does not flip `ok`/`converged` — but it must not be invisible
        # outside journalctl either (#1960 B2).
        for event in post_migrations.get("pass_warnings") or []:
            label = _MIGRATION_PASS_LABELS.get(event, event)
            console.print(
                f"[yellow]![/yellow] {label} logged a warning — see journalctl for hal0-api"
            )
    elif post_migrations:
        error = str(post_migrations.get("error") or "unknown error")
        retry = (
            "shape. hal0-api retries this automatically at its next start — restart the\n"
            "service now to retry immediately, or wait for the next scheduled restart."
        )
        if _is_backups_dir_denied(error):
            # #2657: a restart alone can never fix this; the retry hits the
            # same EACCES. Name the repair first.
            retry = (
                "shape. A restart alone will not fix it: hal0-api cannot write the\n"
                "migration backups directory. Repair it, then restart hal0-api:\n"
                "  sudo hal0 doctor perms --fix\n"
                f"  (or: sudo chmod 2775 {_BACKUPS_DIR_HINT})\n"
                "  sudo systemctl restart hal0-api"
            )
        console.print(
            Panel(
                "[bold red]Post-update data migrations did not apply.[/bold red]\n"
                f"{error}\n\n"
                "The new code is installed and running, but on-disk data (slot TOMLs,\n"
                "hal0.toml schema, registry rows) may still reflect the PREVIOUS release's\n"
                f"{retry}",
                title="Migration incomplete",
                border_style="red",
            )
        )

    # ADR-0029: whatever the profile-centric conversion could not convert.
    # One line each, no table: the slot file (or ``profile:<name>`` /
    # ``stack:<name>``), why, and how to fix it.
    blockers = [b for b in post_migrations.get("conversion_blockers") or [] if isinstance(b, dict)]
    for blocker in blockers:
        console.print(
            f"[yellow]![/yellow] profile conversion blocked: {blocker.get('target')} — "
            f"{blocker.get('reason') or 'cannot be converted as it is'} — fix: run "
            "`hal0 profile migrate` to see the plan, edit the named file or profile, "
            "then run `sudo -u hal0 hal0 profile migrate --apply`",
            soft_wrap=True,
        )

    # ADR-0028 §7 / #2694: a slot whose model the runner-system conversion
    # unassigned. Not a blocker (the conversion did what it must), but the
    # operator must hear it here, not only in the journal: the running
    # container keeps serving the old model until its next restart.
    for entry in post_migrations.get("unassigned_models") or []:
        if not isinstance(entry, dict):
            continue
        slot = str(entry.get("slot") or "?")
        quant = entry.get("quant")
        why = f" (a {escape(str(quant))} build no runner can load)" if quant else ""
        remedy = entry.get("remedy") or (
            f"assign a plain GGUF build: `hal0 slot edit {slot} --model <id>`"
        )
        console.print(
            f"[yellow]![/yellow] slot {escape(slot)}: model "
            f"{escape(str(entry.get('model') or '?'))}"
            f"{why} was unassigned — it keeps serving until its next restart — fix: "
            f"{escape(str(remedy))}",
            soft_wrap=True,
        )

    # #2739 / ADR-0030: the kokoro tts slot launches only from the pulled
    # store bundle, so the upgrade fetches it once. Say what happened; a
    # failed fetch is not a blocker, but the operator needs the remedy.
    for entry in post_migrations.get("model_bundles") or []:
        if not isinstance(entry, dict):
            continue
        model = escape(str(entry.get("model") or "?"))
        path = escape(str(entry.get("path") or "the model store"))
        remedy = escape(str(entry.get("remedy") or f"hal0 model pull {model}"))
        outcome = entry.get("outcome")
        if outcome == "pulled":
            console.print(f"[green]✓[/green] pulled the {model} bundle into {path}", soft_wrap=True)
            continue
        why = escape(str(entry.get("error") or "")) if outcome == "failed" else ""
        console.print(
            f"[yellow]![/yellow] {model} bundle "
            f"{'could not be pulled' if outcome == 'failed' else 'is missing'} into {path}"
            f"{f' ({why})' if why else ''} — the tts slot will not start until it is there "
            f"— fix: `{remedy}`",
            soft_wrap=True,
        )

    converged = bool(convergence.get("converged")) and not blockers
    if converged:
        console.print("[dim]on-disk config is fully converged.[/dim]")
    return converged


def _fetch_slot_drift() -> dict:
    """Return the /api/updates/slot-drift payload, or an empty summary on error.

    Best-effort: the drift banner is a courtesy on top of the update flow, so
    a probe failure must never turn a successful update into a hard error.
    """
    try:
        body = api_get("/api/updates/slot-drift")
    except CliApiError:
        return {"count": 0, "slots": []}
    return body if isinstance(body, dict) else {"count": 0, "slots": []}


def _remaining_drift_state(names: list[str]) -> str | None:
    """Progress-line state for ``--restart-slots`` (#1870): how many of the
    slots this sweep targeted are still reported drifted.

    Best-effort like :func:`_fetch_slot_drift`: a probe failure renders as no
    state suffix (elapsed-only line), never as an error — the sweep itself is
    still running regardless of whether this side probe succeeds.
    """
    drift = _fetch_slot_drift()
    still_drifted = {s.get("slot") for s in (drift.get("slots") or []) if isinstance(s, dict)}
    remaining = [n for n in names if n in still_drifted]
    if not remaining:
        return "converging"
    return f"{len(remaining)}/{len(names)} still drifted"


def _has_image_drift(slot: object) -> bool:
    """True when a slot-drift entry carries an ``image`` diff (#2096).

    THE restart policy predicate: image drift is a different build of the
    runner, not stale flags, so it is restarted automatically after an apply;
    argv-only drift stays opt-in (``--restart-slots``).
    """
    if not isinstance(slot, dict):
        return False
    return any(isinstance(d, dict) and d.get("key") == "image" for d in slot.get("diffs") or [])


def _slot_names(slots: list) -> list[str]:
    return [str(s.get("slot")) for s in slots if isinstance(s, dict) and s.get("slot")]


def _render_error(slot: object) -> str | None:
    """The reason a drifted slot's launch command cannot be rendered (#2660).

    Such a slot is never restarted by an automatic pass or by
    ``--restart-slots``: the restart would fail to render and take it down.
    The operator fixes the config first, or names the slot explicitly in
    ``POST /api/updates/restart-slots``.
    """
    if not isinstance(slot, dict):
        return None
    err = slot.get("render_error")
    return str(err) if err else None


def _render_error_lines(slots: list) -> list[str]:
    return [
        f"cannot be restarted: {escape(str(s.get('slot')))} \u2014 its launch command does not "
        f"render from the current config: {escape(err)}"
        for s in slots
        if (err := _render_error(s))
    ]


def _print_drift_banner(
    drift: dict,
    *,
    restarted: list[str] | None = None,
    skipped_busy: list[str] | None = None,
    failed: list[dict] | None = None,
) -> None:
    """Post-update slot banner: what was restarted, what still needs a restart.

    ``rerender_slot_units`` refreshes each unit file on disk but never bounces
    the running process. Slots whose drift includes an image change are
    restarted automatically after an apply (#2096), so ``restarted`` lists
    those; whatever is still in ``drift`` "needs restart" -- argv-only drift
    (opt in with ``hal0 update --restart-slots``) plus any image slot that
    could not be bounced (``skipped_busy`` / ``failed``).
    """
    restarted = restarted or []
    skipped_busy = skipped_busy or []
    failed = failed or []
    if restarted:
        console.print(
            Panel(
                f"[green]restarted {len(restarted)} slot(s) onto the new runner image:[/green] "
                f"{', '.join(restarted)}",
                border_style="green",
            )
        )
    count = int(drift.get("count") or 0)
    if count == 0:
        # This probe ran against hal0-api BEFORE it restarted onto the new
        # release (the job is "applied" before the bounce), so zero drift only
        # means the old code saw none. The new render is compared on each
        # slot's next load, which restarts a slot whose launch config changed
        # (#3044).
        console.print(
            "[dim]no slot drift seen yet. A slot whose launch config changed in this "
            "release restarts on its next load.[/dim]"
        )
        return
    slots = drift.get("slots") or []
    names = ", ".join(_slot_names(slots))
    plural = "s" if count != 1 else ""
    lines = [f"[bold yellow]{count} slot{plural} need restart[/bold yellow]", f"[dim]{names}[/dim]"]
    if skipped_busy:
        lines.append(
            "could not be restarted automatically: in use \u2014 "
            f"{', '.join(skipped_busy)} (still serving the old runner image)"
        )
    for f in failed:
        if isinstance(f, dict):
            lines.append(
                f"could not be restarted automatically: {f.get('slot')} \u2014 {f.get('error')}"
            )
    lines.extend(_render_error_lines(slots))
    if slots and all(_render_error(s) for s in slots):
        # `--restart-slots` skips every one of these (#2660); sending the
        # operator there is a dead end (#2696).
        lines.append(
            "\nThese slots are still running the pre-update launch command or image, "
            "and none can be restarted until its config renders. Fix each slot as "
            "described above, then run [bold]hal0 update --restart-slots[/bold]."
        )
    else:
        lines.append(
            "\nThese slots are still running the pre-update launch command or image. Run "
            "[bold]hal0 update --restart-slots[/bold] to bounce the drifted slots "
            "(this briefly interrupts any in-flight request on them)."
        )
    console.print(Panel("\n".join(lines), title="Slots need restart", border_style="yellow"))


def _auto_restart_image_drift(drift: dict) -> dict:
    """Restart the slots in ``drift`` that carry an image diff; skip busy ones.

    Returns the /api/updates/restart-slots body (``restarted`` / ``failed`` /
    ``skipped_busy``), or ``{}`` when nothing qualified or the call failed --
    best-effort like the rest of the banner: a failed side-restart must not
    turn an applied update into a hard error (the slot then still shows as
    drifted and the verdict says restarts are pending).
    """
    names = _slot_names(
        [s for s in drift.get("slots") or [] if _has_image_drift(s) and not _render_error(s)]
    )
    if not names:
        return {}
    timeout_s = slot_lifecycle_timeout_s(loads=1, unloads=1, slots=len(names))
    try:
        body = run_with_progress(
            lambda: api_post(
                "/api/updates/restart-slots",
                json={"slots": names, "skip_busy": True},
                timeout=timeout_s,
            ),
            console=console,
            label=f"Restarting {len(names)} slot(s) on a new runner image",
            timeout_s=timeout_s,
            poll_state=lambda: _remaining_drift_state(names),
        )
    except (CliApiError, CliApiTimeout) as exc:
        console.print(f"[yellow]automatic slot restart did not complete:[/yellow] {exc}")
        return {}
    return body if isinstance(body, dict) else {}


def _restart_drifted_slots() -> None:
    """POST /api/updates/restart-slots and report the outcome.

    Clean-path message when nothing is drifted; otherwise restarts only the
    drifted slots and lists successes + per-slot failures.
    """
    drift = _fetch_slot_drift()
    count = int(drift.get("count") or 0)
    if count == 0:
        console.print(Panel("[green]no slots need restart.[/green]", border_style="green"))
        return
    # restart_drifted_slots loops `await sm.restart(name)` over every drifted
    # slot inside ONE request, so the client budget scales with the sweep
    # (#1832). At api_post's generic 10s default this reported failure while
    # the server went on bouncing every slot unattended.
    #
    # Send the slots we counted rather than letting the route recompute drift:
    # otherwise a slot that drifts between the GET and the POST widens the
    # server's sweep past the budget derived from that count.
    slots = drift.get("slots") or []
    for line in _render_error_lines(slots):
        console.print(f"[yellow]{line}[/yellow]")
    names = _slot_names([s for s in slots if not _render_error(s)])
    if not names and any(_render_error(s) for s in slots):
        return
    payload = {"slots": names} if names else None
    timeout_s = slot_lifecycle_timeout_s(loads=1, unloads=1, slots=max(len(names), count))
    try:
        body = run_with_progress(
            lambda: api_post("/api/updates/restart-slots", json=payload, timeout=timeout_s),
            console=console,
            label=f"Restarting {len(names)} drifted slot(s)",
            timeout_s=timeout_s,
            poll_state=lambda: _remaining_drift_state(names),
        )
    except CliApiTimeout as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        console.print(
            "[dim]still converging? check `hal0 slot list` or `journalctl -u 'hal0-slot@*'`.[/dim]"
        )
        raise typer.Exit(1) from exc
    except CliApiError as exc:
        die(str(exc))
        return
    restarted = body.get("restarted") or []
    failed = body.get("failed") or []
    if restarted:
        console.print(
            Panel(
                f"[green]restarted {len(restarted)} slot(s):[/green] {', '.join(restarted)}",
                border_style="green",
            )
        )
    for f in failed:
        if isinstance(f, dict):
            console.print(f"[yellow]could not restart {f.get('slot')}:[/yellow] {f.get('error')}")


def _running_api_version() -> str | None:
    """Version the live hal0-api process reports on ``/api/health``, or None.

    Fallback for a daemon whose rollback response predates ``running_version``.
    Deliberately NOT the CLI's own in-process ``hal0.__version__``: after the
    swap the CLI binary already resolves through the rolled-back tree, so a
    local read would compare the rolled-back version against itself and the
    restart warning could never fire (#1541 / #2027). Fail-soft — an
    unreachable API means nothing stale is serving, so the caller suppresses
    the warning.
    """
    try:
        body = api_get("/api/health")
    except Exception:
        return None
    if isinstance(body, dict):
        version = body.get("version")
        if version:
            return str(version)
    return None


def _render_rollback_result(body: dict) -> None:
    """Render the /api/updates/rollback response — every field, loudly (#2027/#2145).

    Rollback is the emergency path and it deliberately does not restart
    hal0-api (by-design: ``update-rollback-defers-hal0-api-restart``), so the
    one thing this banner must never do is imply the box is done. Names the
    version reverted to, surfaces the forward-only-migration
    ``schema_warning`` as its own callout, and — when the API's reported
    running version still disagrees with the rolled-back tree — says so and
    prints the exact remedy, mirroring the dashboard's own two-place notice
    and the ``[memory].enabled`` restart line in memory_commands.
    """
    channel = body.get("channel", "stable")
    rolled_back_to = str(body.get("rolled_back_to") or "")
    reverted = Path(rolled_back_to).name.removeprefix("hal0-") if rolled_back_to else ""
    headline = (
        f"[green]rolled back[/green] to [bold]{reverted}[/bold] ({channel})"
        if reverted
        else f"[green]rolled back[/green] ({channel})"
    )
    lines = [headline]
    previous_now = body.get("previous_now")
    if previous_now:
        lines.append(
            f"[dim]previous-version slot now holds {previous_now} "
            "(a second rollback returns to it)[/dim]"
        )
    console.print(Panel("\n".join(lines), border_style="green"))

    schema_warning = body.get("schema_warning")
    if schema_warning:
        console.print(
            Panel(
                f"[yellow]{schema_warning}[/yellow]",
                title="Schema warning",
                border_style="yellow",
            )
        )

    # The daemon that answered the rollback POST is by definition the
    # not-yet-restarted process, so its self-reported version is what is
    # still serving. Older daemons don't send it — fall back to /api/health.
    running = body.get("running_version") or _running_api_version()
    if running is None:
        # Unreachable — nothing stale is serving, so no restart nag.
        return
    running = str(running)
    if reverted and running == reverted:
        console.print(f"[dim]hal0-api is already serving {running}; no restart needed.[/dim]")
        return
    still = f"hal0-api is still serving [bold]{running}[/bold]"
    target = f" ([bold]{reverted}[/bold])" if reverted else ""
    console.print(
        Panel(
            f"[bold yellow]restart required[/bold yellow] — {still}; "
            f"the rolled-back tree{target} takes over only after:\n"
            "[bold]systemctl restart hal0-api[/bold]",
            title="hal0-api not restarted",
            border_style="yellow",
        )
    )


@update_app.callback(invoke_without_command=True)
def update(
    ctx: typer.Context,
    channel: UpdateChannel | None = typer.Option(
        None,
        "--channel",
        help="Persist the update channel (stable, preview, or nightly), then check.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Only check for updates; do not apply.",
    ),
    rollback: bool = typer.Option(
        False,
        "--rollback",
        help="Roll back to the previous version recorded at /var/lib/hal0/hal0.previous.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help="Require the authenticated channel manifest to exactly match this version.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt after reviewing release notes.",
    ),
    restart_slots: bool = typer.Option(
        False,
        "--restart-slots",
        help=(
            "Restart only the slots still running the pre-update launch command "
            "(post-update drift), including argv-only drift. Without this flag an "
            "update still restarts slots whose runner image changed."
        ),
    ),
    no_restart_slots: bool = typer.Option(
        False,
        "--no-restart-slots",
        help=(
            "Do not automatically restart slots whose runner image changed; "
            "leave them running the old image until restarted."
        ),
    ),
) -> None:
    """Check for, apply, or roll back a hal0 update.

    This is a thin client over /api/updates/*; the actual swap happens in
    the daemon. Real progress comes from polling /api/updates/status/<id>.
    """
    # `hal0 update component` (and any other sibling verb) still runs this
    # callback first — bail so the bare `hal0 update` self-update only fires
    # when no sub-command was given, mirroring the `hal0 doctor` pattern.
    if ctx.invoked_subcommand is not None:
        return

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    if channel is not None:
        try:
            api_put("/api/updates/channel", json={"channel": channel.value})
        except CliApiError as exc:
            die(str(exc))
            return
        console.print(f"[green]channel set to {channel.value}[/green]")

    # Standalone action: bounce drifted slots on demand, then stop. Kept
    # separate from the check/apply flow so an operator can clear post-update
    # drift at any later time (e.g. once in-flight requests have drained).
    if restart_slots and no_restart_slots:
        die("--restart-slots and --no-restart-slots are mutually exclusive")
        return
    if restart_slots:
        _restart_drifted_slots()
        return

    if rollback:
        # Same confirm gate as the apply path below: interactive TTY without
        # --yes prompts, headless/piped invocations proceed unattended. A
        # rollback reverts the whole install tree — at least as consequential
        # as an apply, so it shouldn't fire on zero confirmation.
        if (
            not yes
            and _interactive()
            and not typer.confirm(
                "Roll back to the previous hal0 install? This reverts the entire install tree.",
                default=False,
            )
        ):
            console.print("[dim]rollback cancelled.[/dim]")
            return
        try:
            body = api_post("/api/updates/rollback", timeout=_ROLLBACK_TIMEOUT_S)
        except CliApiTimeout as exc:
            # The server may still be reinstalling, or may have finished after
            # we stopped listening. Either way `hal0.previous` can already have
            # flipped, so a second --rollback would undo this one (#2661).
            console.print(f"[bold red]Error:[/bold red] {exc}")
            console.print(
                "[yellow]the server may still be rolling back, or may have finished "
                "after the CLI stopped waiting.[/yellow]"
            )
            console.print(
                "[dim]check with `hal0 update --check`, `cat current/VERSION` (under the "
                "install root, e.g. /usr/lib/hal0) or `journalctl -u hal0-api | grep "
                "updater.rollback`.[/dim]"
            )
            console.print(
                "[bold yellow]do not re-run `--rollback` until you have checked: "
                "it would flip back to the version you were leaving.[/bold yellow]"
            )
            raise typer.Exit(1) from exc
        except CliApiError as exc:
            die(str(exc))
            return
        _render_rollback_result(body if isinstance(body, dict) else {})
        return

    try:
        body = api_get("/api/updates/check")
    except CliApiError as exc:
        die(str(exc))
        return
    _print_check(body)

    if check:
        return

    target_version = (target or "").lstrip("v") or None
    if not body.get("update_available") and not target_version:
        console.print("[dim]nothing to apply.[/dim]")
        return

    # Refuse before staging: an editable/dev install has no FHS tree to swap,
    # so a prepare/commit would either 409 at the daemon or phantom-succeed
    # (audit 4.1). Fail fast with an actionable message.
    _refuse_if_editable()

    # ── Stage → review notes → confirm → activate (prepare/commit split) ────────
    # prepare downloads + cosign-verifies + extracts WITHOUT touching the running
    # system, so we can show verified release notes and confirm before activating.
    try:
        job = api_post(
            "/api/updates/prepare", json={"version": target_version} if target_version else {}
        )
    except CliApiError as exc:
        die(str(exc))
        return
    job_id = job.get("id")
    if not job_id:
        die("server returned no job id")
        return
    console.print(f"[cyan]staging update:[/cyan] {job_id}")
    prepared = _poll_job(job_id, terminal=("prepared", "failed"))
    if prepared.get("state") != "prepared":
        die(f"prepare failed: {prepared.get('error') or 'unknown error'}")
        return

    resolved = prepared.get("resolved_version") or target_version
    _render_notes(prepared.get("notes"))

    # Confirm before activating. Prompt only on an interactive TTY without --yes;
    # headless/piped invocations (cron, scripts) proceed as before so unattended
    # updates never block. Nothing is active yet — declining just leaves the
    # staged tree, which is harmless.
    if not yes and _interactive() and not typer.confirm(f"Apply hal0 {resolved}?", default=True):
        console.print(
            "[dim]staged but not applied — re-run `hal0 update` to apply, "
            "or `hal0 update --yes`.[/dim]"
        )
        return

    commit_body: dict[str, object] = {"version": resolved}
    try:
        cjob = api_post("/api/updates/commit", json=commit_body)
    except CliApiError as exc:
        die(str(exc))
        return
    cjob_id = cjob.get("id")
    if not cjob_id:
        die("server returned no commit job id")
        return
    console.print(f"[cyan]applying:[/cyan] {cjob_id}")

    final = _poll_job(cjob_id, terminal=("applied", "failed"))
    state = final.get("state")
    if state == "applied":
        if final.get("restarted") is False and final.get("restart_error"):
            console.print(
                f"[yellow]hal0-api restart did not complete:[/yellow] {final['restart_error']}"
            )
        # The unit files were re-rendered but slots were NOT bounced (a restart
        # could kill a mid-inference request). Surface which slots are still
        # running the pre-update command so the operator can opt into a restart.
        drift = _fetch_slot_drift()
        # hal0-api's own post-start pass may already have bounced the image
        # slots (``auto_restart``); report those and only restart what is
        # STILL image-drifted, so the two paths never double-restart.
        server_side = drift.get("auto_restart") or {}
        outcome: dict = {} if no_restart_slots else _auto_restart_image_drift(drift)
        if outcome:
            drift = _fetch_slot_drift()
        _print_drift_banner(
            drift,
            restarted=list(server_side.get("restarted") or [])
            + list(outcome.get("restarted") or []),
            skipped_busy=list(
                dict.fromkeys(
                    list(server_side.get("skipped_busy") or [])
                    + list(outcome.get("skipped_busy") or [])
                )
            ),
            failed=list(server_side.get("failed") or []) + list(outcome.get("failed") or []),
        )
        # An image diff still outstanding means a slot serves the build this
        # release replaced -- the update is not "fully successful" (#2096).
        # A slot whose launch command does not render is never restarted by
        # `--restart-slots` (#2660), so it gets its own line naming its own
        # remedy, never the restart advice (#2696).
        drifted = [x for x in drift.get("slots") or [] if isinstance(x, dict)]
        render_blocked = [x for x in drifted if _render_error(x)]
        image_pending = _slot_names(
            [x for x in drifted if _has_image_drift(x) and not _render_error(x)]
        )
        converged = _print_convergence(final.get("convergence"))
        # hal0-api is mid-self-restart right after commit applies (the unit
        # files were just re-rendered), so a strict _component_rows() fetch
        # here can 409/connection-refuse on a clean apply. Use the tolerant
        # variant and treat "couldn't recheck" as success rather than
        # fabricating an exit-2 convergence failure — `hal0 update status`
        # still does the strict check.
        component_rows = _component_rows_tolerant()
        if component_rows is None:
            console.print(
                "[dim]components not re-checked — hal0-api restarting; "
                "run `hal0 update status`[/dim]"
            )
            components_ok = True
        else:
            components_ok = _print_component_table(component_rows)
        _print_hermes_gateway_notice()
        if converged and components_ok and not image_pending and not render_blocked:
            console.print(Panel("[green]update applied.[/green]", border_style="green"))
            return
        if (image_pending or render_blocked) and converged and components_ok:
            lines: list[str] = []
            if image_pending:
                lines.append(
                    "[yellow]update applied, but restarts are pending:[/yellow] "
                    f"{', '.join(image_pending)} still run the old runner image.\n"
                    "[dim]Run `hal0 update --restart-slots` once the slot is idle.[/dim]"
                )
            for slot in render_blocked:
                lines.append(
                    "[yellow]update applied, but slot "
                    f"{escape(str(slot.get('slot')))} cannot restart until its config is "
                    f"fixed:[/yellow] {escape(str(_render_error(slot)))}\n"
                    "[dim]Fix the slot as described, then run "
                    "`hal0 update --restart-slots`.[/dim]"
                )
            lines.append("[dim](exit 2 = applied, convergence outstanding)[/dim]")
            console.print(Panel("\n".join(lines), border_style="yellow"))
            raise typer.Exit(2)
        # Refuse to report a clean success while the old on-disk shape survives:
        # the tree swapped, but the box is not the thing v1.0 promises. Exit 2 is
        # distinct from a failed update (die() → 1) so a wrapper can tell
        # "applied but not converged" from "did not apply".
        console.print(
            Panel(
                "[yellow]update applied, but this install is NOT fully converged.[/yellow]\n"
                "[dim]Run the commands above, then re-run `hal0 update` to re-check. "
                "(exit 2 = applied, convergence outstanding)[/dim]",
                border_style="yellow",
            )
        )
        raise typer.Exit(2)
    else:
        err = final.get("error") or "unknown error"
        die(f"update {state}: {err}")


# ── hal0 update status / component — per-component convergence (spec §3) ──────
#
# The daemon owns the component catalog (hal0.components.*): GET
# /api/updates/components reports each component's installed/pinned/status,
# and POST /api/updates/components/{id}/converge (re)runs its converge arm as
# a background job, polled the same way as the self-update prepare/commit
# jobs above. These verbs give an operator a component-scoped alternative to
# the all-or-nothing bare `hal0 update` for retrying just the piece that's
# stuck.

#: Component statuses that mean "not converged yet" — everything else
#: (currently just "converged", plus the openwebui-only "override" and
#: "not-installed") counts as settled for the purposes of the exit code.
_COMPONENT_PENDING = (
    "pending",
    "stale",
    "ahead",
    "build_failed",
    "snapshot_failed",
    "rolled_back",
    "activation_failed",
    "busy",
)


def _component_rows() -> list[dict]:
    """Fetch the /api/updates/components rows, or die on an API error."""
    try:
        body = api_get("/api/updates/components")
    except CliApiError as exc:
        die(str(exc))
        return []
    return body.get("components") or []


def _component_rows_tolerant() -> list[dict] | None:
    """Fetch the /api/updates/components rows, or None on an API error.

    Used right after a self-update commit, where hal0-api may be mid
    self-restart and briefly unreachable/erroring — that is not a
    convergence failure, just a fetch that couldn't happen yet. Callers
    that want strict "die on API error" behavior (e.g. `hal0 update
    status`) should keep using `_component_rows()` instead.
    """
    try:
        body = api_get("/api/updates/components")
    except CliApiError:
        return None
    return body.get("components") or []


def _print_hermes_gateway_notice() -> None:
    """Name `sudo hal0 agent install hermes` when the gateway unit is stale (#2615).

    `hal0 update` regenerates an old-shape gateway unit through `hal0-update
    hermes-gateway` (ADR-0034 decision 6); this is the fallback for when that
    could not run. A unit written before Hermes 0.22, or a box without linger
    for hal0, leaves Hermes's kanban workers unable to start, and nothing else
    says so outside the gateway journal. Read-only; never changes the exit code.
    """
    from hal0.system import linger

    reason = linger.gateway_unit_regen_reason()
    if reason is None:
        return
    console.print(
        f"[yellow]![/yellow] hermes gateway: {escape(reason)} — Hermes kanban workers cannot "
        "start and cron jobs run without their own scope. Fix: run "
        f"`{linger.GATEWAY_REGEN_COMMAND}` once.",
        soft_wrap=True,
    )


def _print_component_table(rows: list[dict]) -> bool:
    """Render the component table; True when everything is converged."""
    table = Table(title="Components", show_header=True, header_style="bold")
    for col in ("component", "installed", "pinned", "status"):
        table.add_column(col)
    all_ok = True
    for row in rows:
        status_txt = row.get("status", "?")
        if status_txt in _COMPONENT_PENDING:
            all_ok = False
            status_txt = f"[yellow]{status_txt}[/yellow]"
        elif status_txt == "converged":
            status_txt = "[green]converged[/green]"
        elif status_txt == "override":
            status_txt = "[cyan]override[/cyan]"
        pinned = row.get("pinned") or "—"
        if row.get("pin_label"):
            pinned = f"{pinned} ({row['pin_label']})"
        if row.get("override"):
            pinned = f"{pinned} [overridden: {_short_override(str(row['override']))}]"
        table.add_row(
            row.get("name", row.get("id", "?")),
            str(row.get("installed") or "—"),
            str(pinned),
            status_txt,
        )
    console.print(table)
    for row in rows:
        if row.get("override") and row.get("status") not in _COMPONENT_PENDING:
            console.print(
                f"[cyan]![/cyan] {row['id']}: held at override "
                f"{_short_override(str(row['override']))}, not the release pin — "
                f"[dim]hal0 update component {row['id']} --clear-override[/dim] to return"
            )
        if row.get("note"):
            console.print(f"[yellow]![/yellow] {row['id']}: {escape(str(row['note']))}")
        if row.get("status") in _COMPONENT_PENDING and (row.get("error") or row.get("remedy")):
            remedy = row.get("remedy") or f"retry with: hal0 update component {row['id']}"
            console.print(
                f"[yellow]![/yellow] {row['id']}: {row.get('error') or ''} [dim]{remedy}[/dim]"
            )
    return all_ok


@update_app.command("status")
def update_status_cmd() -> None:
    """Show hal0 + per-component version/convergence status (exit 2 if outstanding)."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        body = api_get("/api/updates/check")
    except CliApiError as exc:
        die(str(exc))
        return
    _print_check(body)
    components_ok = _print_component_table(_component_rows())
    _print_hermes_gateway_notice()
    if not components_ok:
        console.print(
            "[dim](exit 2 = components not converged — run `hal0 update` "
            "or `hal0 update component <id>`)[/dim]"
        )
        raise typer.Exit(2)


def _short_override(value: str) -> str:
    return value[:19] if value.startswith("sha256:") else value[:12]


#: Components whose converge arm keeps a persisted ``--target`` override.
_OVERRIDABLE_COMPONENTS = ("openwebui", "hermes")


def _component_body(component_id: str, target: str | None, clear_override: bool) -> dict:
    """The converge POST body for ``--target`` / ``--clear-override`` (dies on a bad target)."""
    body: dict[str, object] = {}
    if target is not None:
        if component_id == "openwebui":
            from hal0.openwebui.image_pin import normalize_digest

            normalized = normalize_digest(target)
            if normalized is None:
                die(f"--target {target!r} is not a sha256 digest")
            body["target"] = normalized
        elif component_id == "hermes":
            from hal0.agents import hermes_release as hr

            try:
                body["target"] = hr.require_vetted(target.strip())
            except hr.ReleaseError as exc:
                die(
                    f"--target: {exc}. Pass a full commit sha from VETTED_HERMES_REFS; "
                    "version strings and `==X.Y.Z` specs are not accepted."
                )
        else:
            die(f"--target is not supported for {component_id!r}")
    if clear_override:
        if component_id not in _OVERRIDABLE_COMPONENTS:
            die(f"--clear-override is not supported for {component_id!r}")
        body["clear_override"] = True
    return body


@update_app.command("component")
def update_component_cmd(
    component_id: str = typer.Argument(..., help="Component id (see `hal0 update status`)."),
    check: bool = typer.Option(False, "--check", help="Status row only; don't converge."),
    target: str | None = typer.Option(
        None,
        "--target",
        help=(
            "Pin a target other than the release pin; every later `hal0 update` keeps it "
            "until --clear-override. openwebui: a sha256 digest (sha256:… or bare 64-hex). "
            "hermes: a full 40-hex sha listed in VETTED_HERMES_REFS; the release pin's own "
            "commit counts as --clear-override."
        ),
    ),
    clear_override: bool = typer.Option(
        False,
        "--clear-override",
        help="Drop a --target override; converge back to the release pin.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Converge (or re-try) one component to its release pin, or to a --target override."""
    if check and (target is not None or clear_override):
        die("--check cannot be combined with --target or --clear-override")
        return
    body = _component_body(component_id, target, clear_override)
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    rows = [r for r in _component_rows() if r.get("id") == component_id]
    if not rows:
        die(f"unknown component {component_id!r} — run `hal0 update status` for the list")
        return
    _print_component_table(rows)
    if check:
        return
    if body:
        name = rows[0].get("name", component_id)
        action = (
            f"Pin {name} to {_short_override(str(body['target']))}"
            if "target" in body
            else f"Drop the {name} override"
        )
        if not yes and _interactive() and not typer.confirm(f"{action}?", default=True):
            console.print("[dim]aborted.[/dim]")
            return
    _converge_component(component_id, body)


def _converge_component(component_id: str, body: dict | None = None) -> None:
    try:
        job = api_post(f"/api/updates/components/{component_id}/converge", json=body or {})
    except CliApiError as exc:
        die(str(exc))
        return
    job_id = job.get("id")
    if not job_id:
        die("server returned no job id")
        return
    console.print(f"[cyan]converging {component_id}:[/cyan] {job_id}")
    final = _poll_job(job_id, terminal=("applied", "failed"))
    result = final.get("component_result") or {}
    if final.get("state") == "applied":
        console.print(
            Panel(
                f"[green]{component_id} {result.get('status', 'converged')}[/green]"
                + (
                    f" [dim]{result.get('from')} → {result.get('to')}[/dim]"
                    if result.get("to")
                    else ""
                ),
                border_style="green",
            )
        )
        return
    die(
        f"{component_id} converge failed: {final.get('error') or 'unknown error'}"
        + (f" — {result.get('remedy')}" if result.get("remedy") else "")
    )
