"""Shared diagnosis return type — the §21.4 ``hal0 doctor`` retrofit backbone.

Every ``hal0 doctor`` check returns ``list[Diagnosis]``. Every renderer
(rich table, ``--json``, the support bundle) consumes the same shape. The
``id`` is the stable contract other lanes/consumers key off (operator
scripts, KB articles, a future ``hal0 doctor fix <id>`` dispatcher); the
human ``summary``/``detail`` are for humans only and may change freely.

Pure stdlib, **no imports from ``hal0.cli``** — this module sits below the
CLI layer so non-CLI code (e.g. a future runner-level probe under
``hal0.runners``/``hal0.slots``) can emit a :class:`Diagnosis` without
pulling in Typer/Rich or creating an import cycle back into ``cli/``.
See ``tests/diagnostics/test_layering.py`` for the enforced direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["info", "warn", "fail", "critical"]
Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Evidence:
    """One piece of proof backing a :class:`Diagnosis` — a probe result, a
    file stat, an API payload slice.

    ``kind`` lets renderers bucket (e.g. JSON grouping by kind). ``data`` is
    arbitrary JSON-serialisable; the rich path MUST NOT echo ``data``
    verbatim (use ``summary``) — the ``--json``/bundle paths emit both.
    """

    kind: str  # "file" | "command" | "endpoint" | "table_row" | "config"
    summary: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "summary": self.summary, "data": self.data}


@dataclass(frozen=True)
class NextStep:
    """One remediation affordance — a command, a doc link, or a manual action.

    ``kind="command"`` rows are (eventually) runnable by a ``hal0 doctor fix
    <id>`` dispatcher — not implemented by this spec, referenced only.
    ``kind="manual"`` rows are operator instruction that can't be a single
    command (e.g. "reboot after a modprobe change").
    """

    kind: Literal["command", "manual", "doc"]
    label: str  # "run: hal0 doctor models --fix"
    target: str  # the argv string, the doc URL, or the prose body

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "target": self.target}


@dataclass(frozen=True)
class Diagnosis:
    """One check's result. The stable ``id`` is the contract."""

    id: str  # "HAL0-GFX-TARGET-UNSUPPORTED" — stable across releases
    severity: Severity
    confidence: Confidence
    summary: str  # one-liner (used by the rich path)
    detail: str = ""  # extended description (JSON: full; rich: terse)
    evidence: list[Evidence] = field(default_factory=list)
    next_steps: list[NextStep] = field(default_factory=list)
    fixable: bool = False  # True iff a registered autofix matches a next_step

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "confidence": self.confidence,
            "summary": self.summary,
            "detail": self.detail,
            "fixable": self.fixable,
            "evidence": [e.to_dict() for e in self.evidence],
            "next_steps": [s.to_dict() for s in self.next_steps],
        }


def overall_verdict(diagnoses: list[Diagnosis]) -> str:
    """Roll a list of diagnoses up to ``"ok" | "warn" | "critical"``.

    Same vocabulary as ``doctor_verify.overall_status`` — kept identical so
    a caller comparing the two doesn't have to translate. ``critical`` iff
    any row is ``critical``; ``warn`` iff any row is ``fail``/``warn``
    (a non-critical ``fail`` is still surfaced as "needs attention" but does
    not escalate past warn at the roll-up level — per-command exit-code
    contracts apply their own critical/fail/warn → 0/1/2 mapping, this
    function only classifies the roll-up label).
    """
    if any(d.severity == "critical" for d in diagnoses):
        return "critical"
    if any(d.severity in ("fail", "warn") for d in diagnoses):
        return "warn"
    return "ok"


# ── §1.2 diagnosis-ID taxonomy — the stable, frozen contract ───────────────
#
# IDs are ``<SCOPE>-<DOMAIN>-<SHORT>``. Once published, renaming one is a
# breaking change (see spec-21-4-doctor.md §7 risk #1) — pinned by
# ``tests/cli/test_diagnosis.py``'s taxonomy snapshot. Grouped by the check
# family that emits them; a family not yet retrofitted still reserves its
# IDs here so other lanes (e.g. §21.2's gfx-guard) can depend on the string
# before the CLI-side emitter lands.
DIAGNOSIS_IDS: frozenset[str] = frozenset(
    {
        # host-tuning (§21.1 preflight WARN checks — lane-owned, ID reserved here)
        "HAL0-HOST-NOT-TUNED",
        # gfx-arch guard (§21.2 startup probe — lane-owned, ID reserved here)
        "HAL0-GFX-TARGET-UNSUPPORTED",
        "HAL0-ROCM-LIB-MISSING",
        # doctor models
        "HAL0-MODEL-FILE-MISSING",
        "HAL0-MODEL-UNREGISTERED",
        "HAL0-MODEL-STORE-MISSING",
        "HAL0-MODEL-FLM-STORE-DIVERGED",
        "HAL0-MODEL-FLM-STORE-UNMOUNTED",
        "HAL0-MODEL-FLM-STORE-NOT-WRITABLE",
        # doctor migrations
        "HAL0-MIGRATION-PENDING",
        # doctor profiles
        "HAL0-PROFILE-REF-DANGLES",
        "HAL0-PROFILE-IMAGE-MISSING",
        # doctor perms
        "HAL0-PERMS-HERMES-DRIFT",
        "HAL0-PERMS-TREE-NOT-SHARED",
        "HAL0-PERMS-PATH-OWNERSHIP-DRIFT",
        # doctor toolbox-pull
        "HAL0-TOOLBOX-IMAGE-UNREACHABLE",
        "HAL0-TOOLBOX-IMAGE-DIGEST-DRIFT",
        # doctor verify (live-API report card)
        "HAL0-API-UNREACHABLE",
        "HAL0-RUNNERS-NONE-HEALTHY",
        "HAL0-DNS-LOCAL-UNRESOLVED",
        "HAL0-CAPABILITIES-NONE",
        "HAL0-MEMORY-ENGINE-UNREACHABLE",
        "HAL0-OPENWEBUI-DOWN",
        "HAL0-HERMES-DOWN",
        # always-info
        "HAL0-DOCTOR-OK",
        "HAL0-DOCTOR-SKIPPED",
    }
)


__all__ = [
    "DIAGNOSIS_IDS",
    "Confidence",
    "Diagnosis",
    "Evidence",
    "NextStep",
    "Severity",
    "overall_verdict",
]
