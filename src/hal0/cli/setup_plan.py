"""``hal0 setup --plan`` / ``--dry-run`` (issue #1116, spec §2/§8).

Resolves the same :class:`~hal0.install.orchestrate.Selections` the real run
would apply — via ``load_answers`` when ``--answers`` is given, else
``build_auto_selections`` — and prints a "will create" table. Writes NOTHING:
no slot TOML, no first-run sentinel, no pulls, no extension installs.  This is
the safe preview / CI-gate surface (spec §8): "``--plan`` runs all validation
and prints the will-create table ... with zero writes".

Kept in its own module (rather than growing ``setup_command.py`` in place) so
the sibling ``--emit-answers`` change (#1117) touching the same callback stays
a small, mergeable diff.
"""

from __future__ import annotations

import shutil
import socket
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from hal0.config.schema import HardwareInfo
from hal0.install.answers import AnswersError
from hal0.install.orchestrate import Selections

#: Below this many free GiB on the resolved model-store mount, flag a warning
#: (or an error, under a strict answer file — spec §8).
MIN_FREE_GIB = 10.0

console = Console()


def _answer_file_strict(path: str) -> bool:
    """Best-effort peek at the answer file's top-level ``strict`` flag.

    Mirrors ``hal0.install.answers._check_top_level_keys``'s ``strict``
    read, but only for gating the *plan's own* integration checks
    (free space / port-in-use) — it does not duplicate schema validation.
    """
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except OSError:
        return False
    if not isinstance(doc, dict):
        return False
    return bool(doc.get("strict", False))


def _free_space_gib(path: str) -> float | None:
    """Available GiB on the mount containing *path*.

    Walks up to the nearest existing ancestor so a not-yet-created
    ``storage_dir`` still reports the free space of the mount it will land
    on. Returns ``None`` if no ancestor can be stat'd (e.g. permission
    error).
    """
    p = Path(path)
    seen = set()
    while p not in seen:
        seen.add(p)
        if p.exists():
            try:
                usage = shutil.disk_usage(p)
            except OSError:
                return None
            return usage.free / (1024**3)
        if p.parent == p:
            return None
        p = p.parent
    return None


def _port_in_use(port: int) -> bool:
    """True if binding ``127.0.0.1:port`` fails (something is already there)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
        return False


def resolve_plan_selections(
    hw: HardwareInfo,
    *,
    answers: str | None,
    storage_dir: str,
    no_extensions: bool,
    no_slots: bool,
) -> Selections:
    """Resolve a :class:`Selections` the SAME way the real run would.

    Raises :class:`~hal0.install.answers.AnswersError` on a bad answer file —
    the caller turns that into a non-zero exit so ``--plan`` is a usable CI
    gate (spec §8).
    """
    if answers is not None:
        from hal0.install.answers import load_answers

        return load_answers(answers, hw)

    from hal0.cli.setup_command import _existing_slot_names, build_auto_selections

    return build_auto_selections(
        hw,
        storage_dir=storage_dir,
        with_extensions=not no_extensions,
        with_slots=not no_slots,
        existing_slots=_existing_slot_names(),
    )


def _render_plan_table(sel: Selections, *, strict: bool) -> bool:
    """Print the "will create" table. Returns True if a ``strict``-gated
    integration check failed (caller maps that to a non-zero exit)."""
    has_error = False

    console.print(f"[bold]Model store:[/bold] {sel.storage_dir}")
    free_gib = _free_space_gib(sel.storage_dir)
    if free_gib is None:
        console.print("  [yellow]WARN[/yellow] could not determine free space on this mount")
    else:
        low = free_gib < MIN_FREE_GIB
        if low:
            console.print(
                f"  [red]WARN[/red] only {free_gib:.1f} GiB free "
                f"(< {MIN_FREE_GIB:.0f} GiB threshold)"
            )
            if strict:
                has_error = True
        else:
            console.print(f"  {free_gib:.1f} GiB free")
    console.print(f"[bold]NPU opt-in:[/bold] {sel.npu_opt_in}")
    console.print()

    # Headers are printed as plain lines (not ``Table(title=...)``) — a
    # title longer than the table's content-driven width wraps mid-word,
    # which makes output brittle to assert on in tests and ugly in a
    # narrow terminal.
    console.print("[bold]Slots to create[/bold]")
    slot_table = Table()
    slot_table.add_column("Name")
    slot_table.add_column("Port", justify="right")
    slot_table.add_column("Model")
    slot_table.add_column("Device")
    slot_table.add_column("Profile")
    slot_table.add_column("Status")
    for s in sel.slots:
        device = s.device or "auto→derive"
        profile = s.profile or "auto→derive"
        model_id = s.model_id or "(none — scaffold only)"
        in_use = _port_in_use(s.port)
        if in_use:
            status = "[red]port in use[/red]"
            if strict:
                has_error = True
        else:
            status = "[green]free[/green]"
        slot_table.add_row(s.slot_name, str(s.port), model_id, device, profile, status)
    if not sel.slots:
        slot_table.add_row("[dim]-- none --[/dim]", "", "", "", "", "")
    console.print(slot_table)

    console.print("[bold]Extensions to enable[/bold]")
    ext_table = Table()
    ext_table.add_column("Extension")
    enabled_exts = [eid for eid, on in sel.extensions.items() if on]
    for eid in enabled_exts:
        ext_table.add_row(eid)
    if not enabled_exts:
        ext_table.add_row("[dim]-- none --[/dim]")
    console.print(ext_table)

    if sel.comfyui_defaults:
        console.print("[bold]ComfyUI defaults[/bold]")
        comfy_table = Table()
        comfy_table.add_column("Capability")
        comfy_table.add_column("Family")
        for cap_id, family in sel.comfyui_defaults:
            comfy_table.add_row(cap_id, family)
        console.print(comfy_table)

    console.print()
    console.print("[dim]--plan: nothing was written (no slots, no sentinel, no pulls).[/dim]")
    return has_error


def run_plan(
    hw: HardwareInfo,
    *,
    answers: str | None,
    storage_dir: str,
    no_extensions: bool,
    no_slots: bool,
) -> int:
    """Resolve + render the plan. Returns the process exit code."""
    strict = _answer_file_strict(answers) if answers is not None else False
    try:
        sel = resolve_plan_selections(
            hw,
            answers=answers,
            storage_dir=storage_dir,
            no_extensions=no_extensions,
            no_slots=no_slots,
        )
    except AnswersError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        return 1

    has_error = _render_plan_table(sel, strict=strict)
    return 1 if has_error else 0


__all__ = ["resolve_plan_selections", "run_plan"]
