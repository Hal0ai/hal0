"""Pure health-report classifiers -- the single owner shared by ``hal0 doctor
verify`` (CLI) and ``GET /api/doctor`` (API).

Sits below both ``hal0.cli`` and ``hal0.api`` on purpose, same layering
reason as :mod:`hal0.diagnostics` (that module's docstring / the
``hal0.cli`` layering it enforces via ``tests/diagnostics/test_layering.py``
applies here too: the API layer must never import ``hal0.cli``). Every
function below is a pure transform over already-fetched payload dicts --
no Rich/Typer, no FastAPI, no network I/O -- so both callers can compose it
with their own data-gathering seam (the CLI fetches over HTTP via
``hal0.cli._shared.api_get``; the API route calls the sibling route
handlers in-process, same "no HTTP self-calls" convention as
``services_health.py``).

Originally landed inline in ``hal0.cli.doctor_verify`` (WS-K, issue #1114)
+ ``hal0.cli.doctor_diagnosis`` (§21.4 retrofit); hoisted here so
``GET /api/doctor`` can reuse the identical classification instead of a
second, drifting copy. Both CLI modules re-export these names unchanged,
so existing ``hal0.cli.doctor_verify.check_api(...)`` etc. call sites (and
their tests) are unaffected.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

from hal0.diagnostics import Confidence, Diagnosis, Evidence, Severity

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


# ── per-check classifiers (pure -- take parsed JSON, return a Check) ───────────


def check_api(health: dict[str, Any] | None) -> Check:
    """API/dashboard reachable -- the anchor critical (no reachable URL)."""
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
    where multicast is filtered -- a heads-up (use the LAN IP), never a
    blocker.
    """
    host = _url_host(urls.get("api")) if isinstance(urls, dict) else None
    if not host:
        return Check("dns", "mDNS (.local)", _WARN, "no host advertised yet")
    if not host.endswith(".local"):
        # A plain IP / real DNS name -- nothing mDNS-specific to verify.
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
    """Runner slots healthy -- the second critical (zero healthy slots).

    Sourced from ``/api/health/system`` -> ``checks.slot_manager`` which
    already walks ``SlotManager.list()`` and reports the total + the
    errored slot names.
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
    """Capability slots (embed/voice/img) configured -- advisory."""
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
    """Hindsight memory engine + banks -- advisory (memory is optional)."""
    if memory is None:
        return Check("memory", "Hindsight / banks", _WARN, "memory admin unreachable")
    if not isinstance(memory, dict) or memory.get("engine") is None:
        # engine=None covers two very different states (#1543/#1613): memory
        # deliberately disabled (enabled=False → an honest PASS) vs. memory
        # ENABLED but running on the degraded in-memory fallback because
        # hindsight lost the boot race. The latter used to render as
        # "✔ PASS  disabled (no memory engine)" while hindsight-api was
        # active — the checkmark operators read as "done".
        if isinstance(memory, dict) and memory.get("enabled"):
            return Check(
                "memory",
                "Hindsight / banks",
                _WARN,
                "memory enabled but engine degraded (pgvector fallback) — "
                "waits for the self-heal re-probe, or restart hal0-api",
            )
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

    ``critical`` iff any critical row failed (the only blocking-*looking*
    state, though even this never raises during auto-run). ``warn`` if any
    non-critical fail/warn is present. ``ok`` when every row passed.
    """
    if any(c.status == _FAIL and c.critical for c in checks):
        return "critical"
    if any(c.status in (_FAIL, _WARN) for c in checks):
        return "warn"
    return "ok"


# ── Check -> Diagnosis adapter (§1.3 doctor retrofit) ───────────────────────

# id_map is the spec's §1.3 contract: one Check.key -> one stable Diagnosis id.
_CHECK_ID_MAP: dict[str, str] = {
    "api": "HAL0-API-UNREACHABLE",
    "dns": "HAL0-DNS-LOCAL-UNRESOLVED",
    "runners": "HAL0-RUNNERS-NONE-HEALTHY",
    "capabilities": "HAL0-CAPABILITIES-NONE",
    "memory": "HAL0-MEMORY-ENGINE-UNREACHABLE",
    "openwebui": "HAL0-OPENWEBUI-DOWN",
    "hermes": "HAL0-HERMES-DOWN",
}


def to_diagnosis(c: Check) -> Diagnosis:
    """Map one :class:`Check` row to a :class:`~hal0.diagnostics.Diagnosis` row.

    ``status`` -> ``severity``: a critical-flagged ``fail`` becomes
    ``critical`` (the two anchor conditions -- API unreachable, zero healthy
    runners); every other ``fail``/``warn``/``pass`` maps to
    ``fail``/``warn``/``info`` respectively. ``confidence`` is always
    ``"high"`` -- every :class:`Check` is sourced from an unconditional
    live-API probe, not a heuristic.
    """
    if c.status == _FAIL and c.critical:
        severity: Severity = "critical"
    elif c.status == _FAIL:
        severity = "fail"
    elif c.status == _WARN:
        severity = "warn"
    else:
        severity = "info"
    confidence: Confidence = "high"
    return Diagnosis(
        id=_CHECK_ID_MAP[c.key],
        severity=severity,
        confidence=confidence,
        summary=c.label,
        detail=c.detail,
        evidence=[Evidence(kind="endpoint", summary=c.detail)],
        next_steps=[],
    )


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
    "overall_status",
    "to_diagnosis",
]
