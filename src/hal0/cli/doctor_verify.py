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
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ── canonical help links (static — the "what next" footer) ────────────────────
FIRST_RUN_GUIDE_URL = "https://hal0.dev/first-run-guide"
DOCS_URL = "https://hal0.dev/docs/"
DISCORD_URL = "https://discord.gg/7M4y6dcUyq"
WEBSITE_URL = "https://hal0.dev"

# Status vocabulary. A "fail" row that is also ``critical`` renders red and
# drives the overall verdict; a plain "fail"/"warn" is amber and never blocks.
_PASS = "pass"
_WARN = "warn"
_FAIL = "fail"


@dataclass(frozen=True)
class Check:
    """One report-card row.

    ``critical`` marks the two install-defining conditions (no reachable URL,
    zero healthy runners). A critical ``fail`` is the only thing that flips the
    overall verdict to "critical"; every other ``fail``/``warn`` is advisory.
    """

    key: str
    label: str
    status: str  # _PASS | _WARN | _FAIL
    detail: str
    critical: bool = False


# ── per-check classifiers (pure — take parsed JSON, return a Check) ────────────


def check_api(health: dict[str, Any] | None) -> Check:
    """API/dashboard reachable — the anchor critical (no reachable URL)."""
    if health is None:
        return Check(
            "api",
            "Dashboard / API",
            _FAIL,
            "unreachable — start it with `hal0 serve`",
            critical=True,
        )
    version = health.get("version") if isinstance(health, dict) else None
    detail = f"serving (v{version})" if version else "serving"
    return Check("api", "Dashboard / API", _PASS, detail)


def check_dns(urls: dict[str, Any] | None) -> Check:
    """mDNS/.local resolvability of the advertised host (warn-only).

    A ``.local`` name that does not resolve is common on bridged LXC / VLANs
    where multicast is filtered — a heads-up (use the LAN IP), never a blocker.
    """
    host = _url_host(urls.get("api")) if isinstance(urls, dict) else None
    if not host:
        return Check("dns", "mDNS (.local)", _WARN, "no host advertised yet")
    if not host.endswith(".local"):
        # A plain IP / real DNS name — nothing mDNS-specific to verify.
        return Check("dns", "Hostname", _PASS, f"{host} (no mDNS needed)")
    try:
        socket.gethostbyname(host)
    except OSError:
        return Check(
            "dns",
            "mDNS (.local)",
            _WARN,
            f"{host} does not resolve here (multicast filtered? use the LAN IP)",
        )
    return Check("dns", "mDNS (.local)", _PASS, f"{host} resolves")


def check_runners(system: dict[str, Any] | None) -> Check:
    """Runner slots healthy — the second critical (zero healthy slots).

    Sourced from ``/api/health/system`` → ``checks.slot_manager`` which already
    walks ``SlotManager.list()`` and reports the total + the errored slot names.
    """
    if system is None:
        return Check("runners", "Runners", _FAIL, "health/system unreachable", critical=True)
    sm = (system.get("checks") or {}).get("slot_manager") if isinstance(system, dict) else None
    if not isinstance(sm, dict) or "slots" not in sm:
        return Check("runners", "Runners", _FAIL, "slot manager not wired", critical=True)
    total = int(sm.get("slots") or 0)
    errored = list(sm.get("errored") or [])
    healthy = total - len(errored)
    if healthy <= 0:
        detail = "no healthy runner slots" if total else "no runner slots configured yet"
        return Check("runners", "Runners", _FAIL, detail, critical=True)
    if errored:
        return Check(
            "runners",
            "Runners",
            _WARN,
            f"{healthy}/{total} healthy — errored: {', '.join(errored)}",
        )
    return Check("runners", "Runners", _PASS, f"{healthy}/{total} slot(s) healthy")


def check_capabilities(capabilities: dict[str, Any] | None) -> Check:
    """Capability slots (embed/voice/img) configured — advisory."""
    if capabilities is None:
        return Check("capabilities", "Capability slots", _WARN, "unreachable")
    selections = capabilities.get("selections") if isinstance(capabilities, dict) else None
    if not isinstance(selections, dict) or not selections:
        return Check("capabilities", "Capability slots", _WARN, "none configured")
    active = [k for k, v in selections.items() if v]
    if not active:
        return Check("capabilities", "Capability slots", _WARN, "none active")
    return Check(
        "capabilities",
        "Capability slots",
        _PASS,
        f"{len(active)} active: {', '.join(sorted(active))}",
    )


def check_memory(memory: dict[str, Any] | None) -> Check:
    """Hindsight memory engine + banks — advisory (memory is optional)."""
    if memory is None:
        return Check("memory", "Hindsight / banks", _WARN, "memory admin unreachable")
    if not isinstance(memory, dict) or memory.get("engine") is None:
        return Check("memory", "Hindsight / banks", _PASS, "disabled (no memory engine)")
    if not memory.get("reachable"):
        return Check("memory", "Hindsight / banks", _WARN, "engine enabled but :9177 unreachable")
    banks = memory.get("banks_total")
    detail = f"reachable — {banks} bank(s)" if banks is not None else "reachable"
    return Check("memory", "Hindsight / banks", _PASS, detail)


def _service_check(services: dict[str, Any] | None, sid: str, label: str) -> Check:
    """Shared classifier for the two companion services (OWUI, Hermes)."""
    if services is None:
        return Check(sid, label, _WARN, "services health unreachable")
    entries = services.get("services") if isinstance(services, dict) else None
    entry = (
        next((s for s in entries if isinstance(s, dict) and s.get("id") == sid), None)
        if isinstance(entries, list)
        else None
    )
    if entry is None:
        return Check(sid, label, _WARN, "not reported")
    if entry.get("up"):
        return Check(sid, label, _PASS, str(entry.get("detail") or "up"))
    return Check(sid, label, _WARN, str(entry.get("detail") or "down"))


def check_openwebui(services: dict[str, Any] | None) -> Check:
    return _service_check(services, "openwebui", "OpenWebUI")


def check_hermes(services: dict[str, Any] | None) -> Check:
    return _service_check(services, "hermes", "Hermes")


def build_checks(
    *,
    health: dict[str, Any] | None,
    urls: dict[str, Any] | None,
    system: dict[str, Any] | None,
    capabilities: dict[str, Any] | None,
    memory: dict[str, Any] | None,
    services: dict[str, Any] | None,
) -> list[Check]:
    """Compose the full ordered check suite from the fetched payloads (pure)."""
    return [
        check_api(health),
        check_dns(urls),
        check_runners(system),
        check_capabilities(capabilities),
        check_memory(memory),
        check_openwebui(services),
        check_hermes(services),
    ]


def overall_status(checks: list[Check]) -> str:
    """Roll the rows up to ``ok`` | ``warn`` | ``critical``.

    ``critical`` iff any critical row failed (the only blocking-*looking* state,
    though even this never raises during auto-run). ``warn`` if any non-critical
    fail/warn is present. ``ok`` when every row passed.
    """
    if any(c.status == _FAIL and c.critical for c in checks):
        return "critical"
    if any(c.status in (_FAIL, _WARN) for c in checks):
        return "warn"
    return "ok"


# ── rendering ──────────────────────────────────────────────────────────────────

_BADGE = {
    _PASS: "[green]✔ PASS[/green]",
    _WARN: "[yellow]▲ WARN[/yellow]",
    _FAIL: "[red]✖ FAIL[/red]",
}
_CRIT_BADGE = "[bold red]✖ FAIL[/bold red]"


def _url_host(url: Any) -> str | None:
    """Extract the bare hostname from an ``http(s)://host[:port]`` URL."""
    if not isinstance(url, str) or "://" not in url:
        return None
    rest = url.split("://", 1)[1]
    hostport = rest.split("/", 1)[0]
    if hostport.startswith("["):  # IPv6 literal
        end = hostport.find("]")
        return hostport[1:end] if end > 0 else hostport
    return hostport.rsplit(":", 1)[0] if ":" in hostport else hostport


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
