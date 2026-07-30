"""``hal0 doctor all`` — one read-only evidence pass over the box (§21.4).

The individual ``doctor`` subcommands each audit one surface (``perms``,
``models``, ``migrations``, ``profiles``) and ``doctor verify`` renders the
live-API report card. ``doctor all`` composes the *read-only* evidence into a
single roll-up so an operator (or a bug report) gets the whole picture in one
command, with ``--json`` for machine consumers.

It re-uses the tested :class:`hal0.cli.doctor_verify.Check` row type and the
verify report-card classifiers (API, runners, DNS, capabilities, memory,
OpenWebUI, Hermes), then adds the broader health rows the retrofit calls for:
auth posture, model-store integrity, pending migrations, bound slot ports,
and the ``hal0.target`` boot-enable anchor (r5-sync-assessment §6.1).

Strictly read-only — there is no ``--fix`` here; the per-surface subcommands
own repair. Exit codes:

* 0 — everything clean (advisory ``warn`` rows may still print)
* 1 — at least one non-critical ``fail`` (actionable finding)
* 2 — a critical row failed (API unreachable / zero healthy runners)
"""

from __future__ import annotations

import json as jsonlib
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli.doctor_verify import (
    _FAIL,
    _PASS,
    _WARN,
    Check,
    build_checks,
    gather_payloads,
)
from hal0.config import paths

console = Console()


# ── extra read-only classifiers (pure — take parsed JSON, return a Check) ──────


def check_auth_posture(auth: dict[str, Any] | None) -> Check:
    """Auth exposure posture from ``GET /api/auth/status`` (advisory).

    ``auth_required`` + ``has_admin_key`` describe whether the box gates
    access. The only misconfiguration we flag is "auth required but no admin
    key configured" (nobody can log in); an intentionally open dev install
    passes with a note.
    """
    if auth is None:
        return Check("auth", "Auth posture", _WARN, "auth status unreachable")
    required = bool(auth.get("auth_required"))
    has_key = bool(auth.get("has_admin_key"))
    if not required:
        return Check("auth", "Auth posture", _PASS, "open (auth not required — dev/loopback)")
    if not has_key:
        return Check(
            "auth",
            "Auth posture",
            _WARN,
            "auth required but no admin key set — set HAL0_ADMIN_KEY so an operator can log in",
        )
    return Check("auth", "Auth posture", _PASS, "auth required, admin key configured")


#: Secret-bearing files whose mode ``doctor`` refuses to let drift open.
_SECRET_FILES: tuple[Callable[[], Path], ...] = (
    lambda: paths.api_env(),
    lambda: paths.openwebui_env(),
)


def check_secret_file_modes() -> Check:
    """Secret-bearing config files must not be group- or world-readable.

    An independent backstop for #1466, deliberately NOT derived from
    ``install/perms.py``'s table: that table is what got it wrong — it pinned
    ``api.env`` at 0644 behind a ``FIXME(phase4)`` while the file held live
    provider tokens and, after a rotation, the box's admin key. A check
    generated from the same table would have agreed with the bug. This one
    asserts the property directly, so a fifth writer, a re-widened PermRow, or
    a hand ``chmod`` all surface here.

    Critical: the finding is "every local account can read your API keys".
    A missing file is clean — plenty of boxes have no ``openwebui.env``.
    """
    offenders: list[str] = []
    for resolve in _SECRET_FILES:
        path = resolve()
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            continue  # absent or unreadable — nothing to assert
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            offenders.append(f"{path.name} is {mode:o} ({path})")
    if offenders:
        return Check(
            "secret-modes",
            "Secret file modes",
            _FAIL,
            "world/group-readable secret file(s): "
            + "; ".join(offenders)
            + f" — expected {paths.API_ENV_MODE:o}; fix with `hal0 doctor perms --fix`",
            critical=True,
        )
    return Check("secret-modes", "Secret file modes", _PASS, "secret files are owner-only")


def check_model_store(
    models: Any,
    *,
    exists: Any = None,
) -> Check:
    """Registry entries whose file is gone → a non-critical fail.

    Mirrors ``doctor models`` step 1 (registry → file existence) but as a
    single roll-up row. ``exists`` is an injectable seam (defaults to
    ``Path.exists``) so the classifier is testable without touching disk.
    """
    _exists = exists if exists is not None else (lambda p: Path(p).exists())
    if models is None:
        return Check("models", "Model store", _WARN, "models endpoint unreachable")
    rows = models.get("models", models) if isinstance(models, dict) else models
    if not isinstance(rows, list):
        return Check("models", "Model store", _WARN, "unexpected models payload")
    local = [m for m in rows if isinstance(m, dict) and m.get("path")]
    dangling = [m for m in local if not _exists(str(m["path"]))]
    if dangling:
        names = ", ".join(str(m.get("id") or m.get("path")) for m in dangling[:3])
        more = f" (+{len(dangling) - 3} more)" if len(dangling) > 3 else ""
        return Check(
            "models",
            "Model store",
            _FAIL,
            f"{len(dangling)} registry entr(y/ies) point at missing files: {names}{more} "
            "— run `hal0 doctor models` to triage",
        )
    return Check("models", "Model store", _PASS, f"{len(local)} registered file(s) present")


def check_migrations(pending: tuple[int, int] | None) -> Check:
    """Pending v0.1→v0.2 model-layout migration (advisory warn).

    ``pending`` is the ``(create, overwrite)`` tuple from
    :func:`hal0.cli.doctor_commands.pending_layout_migration`, or ``None`` when
    the planner could not be consulted (degrades to a skipped/pass note).
    """
    if pending is None:
        return Check(
            "migrations", "Migrations", _PASS, "layout migration planner unavailable — skipped"
        )
    create, overwrite = pending
    if not create and not overwrite:
        return Check("migrations", "Migrations", _PASS, "model layout current")
    detail = f"{create} link(s) to create"
    if overwrite:
        detail += f", {overwrite} to overwrite"
    return Check(
        "migrations",
        "Migrations",
        _WARN,
        f"model-layout migration pending: {detail} — run `hal0 migrate model-layout --apply`",
    )


def _hal0_target_enabled_probe() -> bool | None:
    """Real ``systemctl is-enabled --quiet hal0.target`` probe.

    Returns ``True``/``False`` for a definitive answer, ``None`` when the
    question can't be asked at all (no ``systemctl`` on PATH, e.g. a
    container/CI box) — the caller degrades that to an advisory warn rather
    than a fail. Kept as a free function (not inlined) so tests can swap it
    out via the ``is_enabled`` seam without touching subprocess.
    """
    if shutil.which("systemctl") is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 B607
            ["systemctl", "is-enabled", "--quiet", "hal0.target"],
            check=False,
            timeout=5,
        )
    except OSError:
        return None
    return result.returncode == 0


def check_hal0_target(
    *,
    unit_dir: Path | None = None,
    exists: Callable[[Path], bool] | None = None,
    is_enabled: Callable[[], bool | None] | None = None,
) -> Check:
    """``hal0.target`` — the boot-enable anchor every slot Quadlet depends on.

    Every rendered per-slot Quadlet declares
    ``[Install] WantedBy=hal0.target`` (``providers/container.py``); if the
    target unit is missing, or installed but not enabled, slots that looked
    healthy before a reboot silently stay down after one
    (r5-sync-assessment §6.1, launch-blocker #1). ``exists``/``is_enabled``
    are injectable seams (mirrors ``check_model_store``'s ``exists`` param)
    so this is testable without a real systemd or filesystem.
    """
    _unit_dir = unit_dir if unit_dir is not None else Path("/etc/systemd/system")
    _exists = exists if exists is not None else (lambda p: p.exists())
    unit_path = _unit_dir / "hal0.target"
    if not _exists(unit_path):
        return Check(
            "hal0_target",
            "hal0.target",
            _FAIL,
            f"{unit_path} not installed — slots will not autostart after reboot; "
            "re-run the installer (sudo bash install.sh) to ship it",
        )
    _is_enabled = is_enabled if is_enabled is not None else _hal0_target_enabled_probe
    enabled = _is_enabled()
    if enabled is False:
        return Check(
            "hal0_target",
            "hal0.target",
            _FAIL,
            "hal0.target installed but not enabled — run `sudo systemctl enable --now hal0.target`",
        )
    if enabled is None:
        return Check(
            "hal0_target",
            "hal0.target",
            _WARN,
            "hal0.target installed — enabled state unknown (systemctl unavailable)",
        )
    return Check("hal0_target", "hal0.target", _PASS, "installed and enabled")


def check_ports(slots: Any) -> Check:
    """Bound slot ports (informational evidence, always advisory-clean).

    Surfaces the ports hal0's slots currently occupy so a port-collision
    diagnosis has the evidence in the same report. Never fails — a box with no
    slots yet is legal.
    """
    if not isinstance(slots, list):
        return Check("ports", "Slot ports", _WARN, "slots endpoint unreachable")
    bound = sorted({int(s["port"]) for s in slots if isinstance(s, dict) and s.get("port")})
    if not bound:
        return Check("ports", "Slot ports", _PASS, "no slot ports bound yet")
    return Check("ports", "Slot ports", _PASS, f"{len(bound)} bound: {', '.join(map(str, bound))}")


# ── orchestration ──────────────────────────────────────────────────────────────


def _get_any(path: str, base: str | None) -> Any:
    """Best-effort GET returning the raw parsed body (dict OR list), else None."""
    from hal0.cli._shared import CliApiError, api_get

    try:
        return api_get(path, base=base)
    except CliApiError:
        return None


def build_all_checks(base: str | None = None) -> list[Check]:
    """Fetch every read-only source and compose the full ordered check list."""
    payloads = gather_payloads(base)
    verify_rows = build_checks(
        health=payloads["health"],
        urls=payloads["urls"],
        system=payloads["system"],
        capabilities=payloads["capabilities"],
        memory=payloads["memory"],
        services=payloads["services"],
    )

    from hal0.cli.doctor_commands import pending_layout_migration

    extra_rows = [
        check_auth_posture(_get_any("/api/auth/status", base)),
        check_model_store(_get_any("/api/models", base)),
        check_migrations(pending_layout_migration()),
        check_ports(_get_any("/api/slots", base)),
        check_hal0_target(),
        check_secret_file_modes(),
    ]
    return verify_rows + extra_rows


def overall_verdict(checks: list[Check]) -> str:
    """Roll the rows up to ``ok`` | ``fail`` | ``critical``.

    ``critical`` iff any critical row failed; ``fail`` iff any non-critical
    ``fail`` is present; else ``ok`` (advisory ``warn`` rows do not block).
    """
    if any(c.status == _FAIL and c.critical for c in checks):
        return "critical"
    if any(c.status == _FAIL for c in checks):
        return "fail"
    return "ok"


_BADGE = {
    _PASS: "[green]✔ PASS[/green]",
    _WARN: "[yellow]▲ WARN[/yellow]",
    _FAIL: "[red]✖ FAIL[/red]",
}
_CRIT_BADGE = "[bold red]✖ FAIL[/bold red]"


def render_all(con: Console, checks: list[Check]) -> None:
    """Print the aggregate evidence table."""
    table = Table(title="hal0 doctor — evidence roll-up")
    table.add_column("Status", width=9)
    table.add_column("Check", style="bold", width=18)
    table.add_column("Detail")
    for c in checks:
        badge = _CRIT_BADGE if (c.status == _FAIL and c.critical) else _BADGE[c.status]
        table.add_row(badge, c.label, c.detail)
    con.print(table)


def _exit_code(checks: list[Check]) -> int:
    verdict = overall_verdict(checks)
    return {"ok": 0, "fail": 1, "critical": 2}[verdict]


def doctor_all_cmd(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the check rows as JSON instead of the human table.",
    ),
) -> None:
    """Run every read-only doctor check in one pass and roll up a verdict.

    Composes the ``doctor verify`` report card (API, runners, DNS, capability
    slots, memory, OpenWebUI, Hermes) with auth posture, model-store integrity,
    pending migrations, bound slot ports, and the ``hal0.target`` boot-enable
    anchor. Read-only — use the per-surface subcommands (``perms``/``models``)
    for ``--fix``.

    Exit codes: 0 clean, 1 an actionable fail, 2 a critical failure (API
    unreachable / zero healthy runners).
    """
    checks = build_all_checks()
    if json_output:
        rows = [
            {
                "key": c.key,
                "label": c.label,
                "status": c.status,
                "detail": c.detail,
                "critical": c.critical,
            }
            for c in checks
        ]
        console.print_json(jsonlib.dumps(rows))
    else:
        render_all(console, checks)
    raise typer.Exit(_exit_code(checks))


__all__ = [
    "build_all_checks",
    "check_auth_posture",
    "check_hal0_target",
    "check_migrations",
    "check_model_store",
    "check_ports",
    "doctor_all_cmd",
    "overall_verdict",
    "render_all",
]
