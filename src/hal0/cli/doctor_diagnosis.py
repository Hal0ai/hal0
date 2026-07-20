"""``hal0 doctor``'s CLI-side ``Diagnosis`` re-export + adapters (§21.4).

The dataclasses themselves live in :mod:`hal0.diagnostics` (layering-pure,
no ``hal0.cli`` import — see that module's docstring). ``to_diagnosis``
lives in :mod:`hal0.health_report` (also layering-pure — shared with
``GET /api/doctor``, which can't import ``hal0.cli``) and is re-exported
here unchanged for existing call sites. This module is the doctor-family's
single import point for them, plus the CLI-only helper every ``--json``
renderer shares: :func:`render_json` (the stable JSON emission every
subcommand's ``--json`` flag prints).
"""

from __future__ import annotations

import json as jsonlib

from hal0.diagnostics import (
    DIAGNOSIS_IDS,
    Confidence,
    Diagnosis,
    Evidence,
    NextStep,
    Severity,
    overall_verdict,
)
from hal0.health_report import to_diagnosis


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
