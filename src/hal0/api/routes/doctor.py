"""``GET /api/doctor`` — the doctor verdict feed (D6 diagnostics panel).

Composes the same live health seams ``hal0 doctor verify --json`` reports
(API, DNS, runners, capability slots, memory engine, OpenWebUI, Hermes)
into the ``Diagnosis[]`` shape the dashboard's DiagnosisPanel already
renders (``ui/src/api/hooks/useDiagnoses.ts``) — so once this route lands,
the panel's ``doctorFeedPending`` flips false and its rows drop straight
into the existing generic renderer, unchanged.

In-process callables (``request.app.state`` / sibling route handlers),
never HTTP self-calls — same convention as ``services_health.py``. The
classification itself (``build_checks`` / ``overall_status`` /
``to_diagnosis``) lives in :mod:`hal0.health_report`, the single owner
shared with ``hal0 doctor verify --json`` (this route cannot import
``hal0.cli`` — see that module's docstring for the layering direction).

Mounted at ``/api`` in ``create_app()`` (this file already declares the
full ``/doctor`` path, same pattern as ``power.py``/``throughput.py``).
Classified ADMIN in :mod:`hal0.security.exposure`: the feed surfaces
details from ADMIN-only subsystems (capability slots, memory engine,
services) even though the route itself is a GET.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel

from hal0.api.deps import get_capability_orchestrator
from hal0.api.routes.config import get_urls
from hal0.api.routes.health import health, health_system
from hal0.api.routes.memory_admin import engine_status
from hal0.api.routes.services_health import services_health
from hal0.diagnostics import Diagnosis
from hal0.health_report import build_checks, overall_status, to_diagnosis

log = structlog.get_logger(__name__)

router = APIRouter()


class EvidenceOut(BaseModel):
    kind: str
    summary: str
    data: dict[str, Any] = {}


class NextStepOut(BaseModel):
    kind: str
    label: str
    target: str


class DiagnosisOut(BaseModel):
    id: str
    severity: str
    confidence: str
    summary: str
    detail: str = ""
    fixable: bool = False
    evidence: list[EvidenceOut] = []
    next_steps: list[NextStepOut] = []

    @classmethod
    def from_diagnosis(cls, d: Diagnosis) -> DiagnosisOut:
        return cls(**d.to_dict())


class DoctorResponse(BaseModel):
    verdict: str
    diagnoses: list[DiagnosisOut]


async def _safe[T](awaitable: Awaitable[T]) -> T | None:
    """Await ``awaitable``, swallowing any exception to ``None``.

    Mirrors ``doctor_verify._safe_get``'s tolerance (a down subsystem
    yields ``None``, the classifier turns that into a warn/fail row) —
    but for in-process calls instead of HTTP.
    """
    try:
        return await awaitable
    except Exception:
        return None


@router.get("/doctor", response_model=DoctorResponse)
async def get_doctor(request: Request) -> DoctorResponse:
    """The same report card ``hal0 doctor verify --json`` prints, over HTTP."""
    health_payload = await _safe(health())
    urls_payload = await _safe(get_urls(request))
    system_payload = await _safe(health_system(request))
    memory_payload = await _safe(engine_status(request))
    services_payload = await _safe(services_health())

    capabilities_payload: dict[str, Any] | None
    try:
        orchestrator = get_capability_orchestrator(request)
    except RuntimeError:
        capabilities_payload = None
    else:
        capabilities_payload = await _safe(orchestrator.get_state())

    checks = build_checks(
        health=health_payload,
        urls=urls_payload,
        system=system_payload,
        capabilities=capabilities_payload,
        memory=memory_payload,
        services=services_payload,
    )
    verdict = overall_status(checks)
    diagnoses = [DiagnosisOut.from_diagnosis(to_diagnosis(c)) for c in checks]
    return DoctorResponse(verdict=verdict, diagnoses=diagnoses)


__all__ = ["router"]
