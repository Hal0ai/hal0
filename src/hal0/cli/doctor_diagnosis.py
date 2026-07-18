"""``hal0 doctor``'s CLI-side ``Diagnosis`` re-export + adapters (§21.4).

The dataclasses themselves live in :mod:`hal0.diagnostics` (layering-pure,
no ``hal0.cli`` import — see that module's docstring). This module is the
doctor-family's single import point for them, plus the two small CLI-only
helpers every ``--json`` renderer shares: :func:`to_diagnosis` (adapts the
older :class:`hal0.cli.doctor_verify.Check` row into a ``Diagnosis``) and
:func:`render_json` (the stable JSON emission every subcommand's ``--json``
flag prints).
"""

from __future__ import annotations

import json as jsonlib
from typing import TYPE_CHECKING

from hal0.diagnostics import (
    DIAGNOSIS_IDS,
    Confidence,
    Diagnosis,
    Evidence,
    NextStep,
    Severity,
    overall_verdict,
)

if TYPE_CHECKING:
    from hal0.cli.doctor_verify import Check

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
    """Map one ``doctor_verify.Check`` row to a ``Diagnosis`` row.

    ``status`` -> ``severity``: a critical-flagged ``fail`` becomes
    ``critical`` (the two anchor conditions — API unreachable, zero healthy
    runners); every other ``fail``/``warn``/``pass`` maps to
    ``fail``/``warn``/``info`` respectively. ``confidence`` is always
    ``"high"`` — every ``Check`` is sourced from an unconditional live-API
    probe, not a heuristic.
    """
    from hal0.cli.doctor_verify import _FAIL, _WARN

    if c.status == _FAIL and c.critical:
        severity: Severity = "critical"
    elif c.status == _FAIL:
        severity = "fail"
    elif c.status == _WARN:
        severity = "warn"
    else:
        severity = "info"
    return Diagnosis(
        id=_CHECK_ID_MAP[c.key],
        severity=severity,
        confidence="high",
        summary=c.label,
        detail=c.detail,
        evidence=[Evidence(kind="endpoint", summary=c.detail)],
        next_steps=[],
    )


def render_json(diagnoses: list[Diagnosis]) -> str:
    """The stable ``--json`` shape every doctor subcommand prints.

    ``json.dumps(..., indent=2, sort_keys=False)`` per §4.1/§4.2 — field
    order is the ``Diagnosis.to_dict()`` insertion order, not sorted, so
    ``id``/``severity`` stay first for a human skimming raw output.
    """
    return jsonlib.dumps([d.to_dict() for d in diagnoses], indent=2, sort_keys=False)


def exit_code_for(diagnoses: list[Diagnosis]) -> int:
    """The §4.2 generic ``--json`` exit-code translation: critical->2,
    fail/warn->1, ok->0. Individual subcommands may override this with
    their own preserved (§4.3) exit-code contract instead."""
    return {"ok": 0, "warn": 1, "critical": 2}[overall_verdict(diagnoses)]


__all__ = [
    "DIAGNOSIS_IDS",
    "Confidence",
    "Diagnosis",
    "Evidence",
    "NextStep",
    "Severity",
    "exit_code_for",
    "overall_verdict",
    "render_json",
    "to_diagnosis",
]
