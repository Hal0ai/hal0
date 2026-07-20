"""``hal0 doctor --verify`` — the post-setup report card (WS-K, issue #1114).

A single reusable verb that composes the *already-existing* health seams into
one visual pass/warn/fail report card, then prints the live URLs the operator
should open next plus the canonical help links.

Design: this module NEVER re-implements a check. Every row is sourced from a
running-hal0 API endpoint that already aggregates the underlying probe::

    check            source endpoint            underlying seam
    ────────────────────────────────────────────────────────────────────────
    Dashboard/API    GET /api/health            API liveness (installer hello)
    mDNS (.local)    GET /api/config/urls host  socket resolution of the host
    Runners          GET /api/health/system     SlotManager.list() errored dots
    Capability slots GET /api/capabilities      CapabilityOrchestrator.get_state
    Hindsight/banks  GET /api/memory/engine     :9177 /version + /v1/.../banks
    OpenWebUI        GET /api/services/health    loopback :3001 /health probe
    Hermes           GET /api/services/health   `systemctl is-active` hal0-agent

The report is deliberately NON-BLOCKING: a warn never fails an install. Only two
conditions are *critical* (rendered red): no reachable URL (the API/dashboard is
down) and zero healthy runner slots. The guided setup auto-runs :func:`run_verify`
at the very end; it is also runnable standalone anytime via ``hal0 doctor --verify``.
"""

from __future__ import annotations

import socket
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Pure classifiers (Check, check_*, build_checks, overall_status) live in
# hal0.health_report — the single owner shared with GET /api/doctor
# (api/routes/doctor.py can't import hal0.cli, same layering direction
# hal0.diagnostics already enforces). Re-exported here unchanged so every
# existing ``dv.check_api(...)`` / ``dv.build_checks(...)`` call site (this
# module's own render/orchestration code below, plus tests/cli/
# test_doctor_verify.py) keeps working without edits.
from hal0.health_report import (
    _FAIL,
    _PASS,
    _WARN,
    Check,
    _url_host,
    build_checks,
    check_api,
    check_capabilities,
    check_dns,
    check_hermes,
    check_memory,
    check_openwebui,
    check_runners,
    overall_status,
)

# ── canonical help links (static — the "what next" footer) ────────────────────
FIRST_RUN_GUIDE_URL = "https://hal0.dev/first-run-guide"
DOCS_URL = "https://hal0.dev/docs/"
DISCORD_URL = "https://discord.gg/7M4y6dcUyq"
WEBSITE_URL = "https://hal0.dev"


# ── rendering ──────────────────────────────────────────────────────────────────

_BADGE = {
    _PASS: "[green]✔ PASS[/green]",
    _WARN: "[yellow]▲ WARN[/yellow]",
    _FAIL: "[red]✖ FAIL[/red]",
}
_CRIT_BADGE = "[bold red]✖ FAIL[/bold red]"


def _lan_ip() -> str | None:
    """Best-effort primary LAN IPv4 (no traffic sent — UDP connect is a no-op)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def render_report(console: Console, checks: list[Check], urls: dict[str, Any] | None) -> None:
    """Print the report card, the live URLs, and the help-link footer."""
    verdict = overall_status(checks)
    border = {"ok": "green", "warn": "yellow", "critical": "red"}[verdict]
    title = {
        "ok": "[bold green]hal0 is ready ✔[/bold green]",
        "warn": "[bold yellow]hal0 is up — with notes ▲[/bold yellow]",
        "critical": "[bold red]hal0 needs attention ✖[/bold red]",
    }[verdict]

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left", width=8)
    table.add_column(style="bold", width=18)
    table.add_column()
    for c in checks:
        badge = _CRIT_BADGE if (c.status == _FAIL and c.critical) else _BADGE[c.status]
        table.add_row(badge, c.label, c.detail)

    console.print(Panel(table, title=title, border_style=border, title_align="left"))

    _render_urls(console, urls)
    _render_links(console)


def _render_urls(console: Console, urls: dict[str, Any] | None) -> None:
    """Live URLs block — computed dashboard/chat + mDNS + LAN IP."""
    lines: list[Text] = []
    api_url = urls.get("api") if isinstance(urls, dict) else None
    if api_url:
        lines.append(Text.from_markup(f"  Dashboard    [cyan]{api_url}[/cyan]"))
        # A LAN-IP alternative for when .local doesn't resolve on the client.
        host = _url_host(api_url)
        lan = _lan_ip()
        if lan and host and lan != host:
            lan_url = api_url.replace(host, lan, 1)
            lines.append(Text.from_markup(f"  Dashboard IP [cyan]{lan_url}[/cyan]"))
    if isinstance(urls, dict) and urls.get("openwebui_enabled") and urls.get("openwebui"):
        lines.append(Text.from_markup(f"  Chat (OWUI)  [cyan]{urls['openwebui']}[/cyan]"))
    if isinstance(urls, dict) and urls.get("hermes_enabled") and urls.get("hermes"):
        lines.append(Text.from_markup(f"  Hermes       [cyan]{urls['hermes']}[/cyan]"))
    if isinstance(urls, dict) and urls.get("comfyui"):
        lines.append(Text.from_markup(f"  ComfyUI      [cyan]{urls['comfyui']}[/cyan]"))

    if not lines:
        lines.append(Text("  (no URLs — the API is not reachable)", style="dim"))
    console.print("\n[bold]Open hal0[/bold]")
    for line in lines:
        console.print(line)


def _render_links(console: Console) -> None:
    """Static help-link footer."""
    console.print("\n[bold]Next steps & help[/bold]")
    console.print(f"  First-run guide  [cyan]{FIRST_RUN_GUIDE_URL}[/cyan]")
    console.print(f"  Docs             [cyan]{DOCS_URL}[/cyan]")
    console.print(f"  Discord          [cyan]{DISCORD_URL}[/cyan]")
    console.print(f"  Website          [cyan]{WEBSITE_URL}[/cyan]")


# ── orchestration (fetches + render; never raises on network failure) ──────────


def _safe_get(path: str, base: str | None) -> dict[str, Any] | None:
    """GET ``path`` and return the parsed dict, or ``None`` on any failure.

    Tolerant by design — a down subsystem yields ``None`` and the classifier
    turns that into a warn/fail row instead of crashing the report.
    """
    from hal0.cli._shared import CliApiError, api_get

    try:
        data = api_get(path, base=base)
    except CliApiError:
        return None
    return data if isinstance(data, dict) else None


def gather_payloads(base: str | None = None) -> dict[str, dict[str, Any] | None]:
    """Fetch every source endpoint the report card composes (tolerant)."""
    return {
        "health": _safe_get("/api/health", base),
        "urls": _safe_get("/api/config/urls", base),
        "system": _safe_get("/api/health/system", base),
        "capabilities": _safe_get("/api/capabilities", base),
        "memory": _safe_get("/api/memory/engine", base),
        "services": _safe_get("/api/services/health", base),
    }


def run_verify(
    *,
    console: Console | None = None,
    base: str | None = None,
    json_output: bool = False,
) -> int:
    """Fetch → classify → render. Returns an exit code; NEVER raises.

    Exit-code contract (for standalone scripting; the setup auto-run ignores it):
      0 — ok or warn (non-blocking)
      2 — critical (no reachable URL or zero healthy runners)

    ``json_output=True`` (§21.4 retrofit) skips the rich report card and
    prints ``{"verdict": ..., "diagnoses": [...]}`` instead — the
    ``doctor_verify.Check`` rows adapted to ``Diagnosis`` via
    :func:`hal0.cli.doctor_diagnosis.to_diagnosis` (§1.3). The exit-code
    contract above is unchanged either way (§4.3 — ``doctor verify`` keeps
    its 0/2 boundary under ``--json``).
    """
    con = console or Console()
    payloads = gather_payloads(base)
    checks = build_checks(
        health=payloads["health"],
        urls=payloads["urls"],
        system=payloads["system"],
        capabilities=payloads["capabilities"],
        memory=payloads["memory"],
        services=payloads["services"],
    )
    verdict = overall_status(checks)
    if json_output:
        import json as jsonlib

        from hal0.cli.doctor_diagnosis import to_diagnosis

        diagnoses = [to_diagnosis(c) for c in checks]
        con.print_json(
            jsonlib.dumps(
                {"verdict": verdict, "diagnoses": [d.to_dict() for d in diagnoses]},
                indent=2,
            )
        )
    else:
        render_report(con, checks, payloads["urls"])
    return 2 if verdict == "critical" else 0


__all__ = [
    "Check",
    "build_checks",
    "check_api",
    "check_capabilities",
    "check_dns",
    "check_hermes",
    "check_memory",
    "check_openwebui",
    "check_runners",
    "gather_payloads",
    "overall_status",
    "render_report",
    "run_verify",
]
