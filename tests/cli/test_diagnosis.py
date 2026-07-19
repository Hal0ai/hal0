"""Tests for the §21.4 ``Diagnosis`` retrofit backbone.

Covers: dataclass immutability, the JSON round-trip shape (§4.1), the
Check -> Diagnosis adapter (§1.3), and a snapshot of the frozen ID
taxonomy (§1.2/§7 risk #1 — an ID rename here is a breaking change and
must be deliberate, never accidental).
"""

from __future__ import annotations

import dataclasses

import pytest

from hal0.cli.doctor_diagnosis import exit_code_for, render_json, to_diagnosis
from hal0.cli.doctor_verify import Check
from hal0.diagnostics import DIAGNOSIS_IDS, Diagnosis, Evidence, NextStep, overall_verdict

# ── dataclass shape ─────────────────────────────────────────────────────────


def test_diagnosis_is_frozen() -> None:
    d = Diagnosis(id="HAL0-DOCTOR-OK", severity="info", confidence="high", summary="clean")
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.summary = "mutated"  # type: ignore[misc]


def test_evidence_and_next_step_are_frozen() -> None:
    e = Evidence(kind="file", summary="present")
    n = NextStep(kind="command", label="run", target="hal0 doctor perms --fix")
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.summary = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        n.label = "x"  # type: ignore[misc]


def test_diagnosis_to_dict_round_trip() -> None:
    d = Diagnosis(
        id="HAL0-PERMS-TREE-NOT-SHARED",
        severity="fail",
        confidence="high",
        summary="tree not group-shared",
        detail="group is root, expected hal0",
        evidence=[Evidence(kind="file", summary="group is root", data={"path": "/opt/hal0"})],
        next_steps=[
            NextStep(
                kind="command",
                label="sudo hal0 doctor perms --fix",
                target="hal0 doctor perms --fix",
            )
        ],
        fixable=True,
    )
    payload = d.to_dict()
    assert payload == {
        "id": "HAL0-PERMS-TREE-NOT-SHARED",
        "severity": "fail",
        "confidence": "high",
        "summary": "tree not group-shared",
        "detail": "group is root, expected hal0",
        "fixable": True,
        "evidence": [{"kind": "file", "summary": "group is root", "data": {"path": "/opt/hal0"}}],
        "next_steps": [
            {
                "kind": "command",
                "label": "sudo hal0 doctor perms --fix",
                "target": "hal0 doctor perms --fix",
            }
        ],
    }


def test_render_json_is_a_list_of_to_dict() -> None:
    diagnoses = [
        Diagnosis(id="HAL0-DOCTOR-OK", severity="info", confidence="high", summary="clean"),
    ]
    import json as jsonlib

    parsed = jsonlib.loads(render_json(diagnoses))
    assert parsed == [diagnoses[0].to_dict()]


# ── overall_verdict / exit_code_for ─────────────────────────────────────────


def test_overall_verdict_ok_when_only_info() -> None:
    diagnoses = [Diagnosis(id="HAL0-DOCTOR-OK", severity="info", confidence="high", summary="ok")]
    assert overall_verdict(diagnoses) == "ok"
    assert exit_code_for(diagnoses) == 0


def test_overall_verdict_warn_on_fail_or_warn() -> None:
    diagnoses = [
        Diagnosis(id="HAL0-MODEL-UNREGISTERED", severity="warn", confidence="high", summary="x")
    ]
    assert overall_verdict(diagnoses) == "warn"
    assert exit_code_for(diagnoses) == 1


def test_overall_verdict_critical_wins() -> None:
    diagnoses = [
        Diagnosis(id="HAL0-MODEL-UNREGISTERED", severity="warn", confidence="high", summary="x"),
        Diagnosis(id="HAL0-API-UNREACHABLE", severity="critical", confidence="high", summary="y"),
    ]
    assert overall_verdict(diagnoses) == "critical"
    assert exit_code_for(diagnoses) == 2


# ── to_diagnosis adapter (§1.3) ─────────────────────────────────────────────


def test_to_diagnosis_maps_critical_fail() -> None:
    c = Check("api", "Dashboard / API", "fail", "unreachable", critical=True)
    d = to_diagnosis(c)
    assert d.id == "HAL0-API-UNREACHABLE"
    assert d.severity == "critical"
    assert d.confidence == "high"
    assert d.summary == "Dashboard / API"
    assert d.detail == "unreachable"


def test_to_diagnosis_maps_noncritical_fail() -> None:
    c = Check("runners", "Runners", "fail", "no healthy runner slots", critical=False)
    d = to_diagnosis(c)
    assert d.id == "HAL0-RUNNERS-NONE-HEALTHY"
    assert d.severity == "fail"


def test_to_diagnosis_maps_warn_and_pass() -> None:
    warn = to_diagnosis(Check("dns", "mDNS (.local)", "warn", "does not resolve"))
    assert warn.severity == "warn"
    assert warn.id == "HAL0-DNS-LOCAL-UNRESOLVED"

    passed = to_diagnosis(Check("hermes", "Hermes", "pass", "up"))
    assert passed.severity == "info"
    assert passed.id == "HAL0-HERMES-DOWN"  # id is the check's identity, not its status


def test_to_diagnosis_covers_every_check_key() -> None:
    """Every key doctor_verify.build_checks() can emit has an id mapping."""
    for key in ("api", "dns", "runners", "capabilities", "memory", "openwebui", "hermes"):
        c = Check(key, key, "pass", "")
        d = to_diagnosis(c)
        assert d.id.startswith("HAL0-")


# ── ID taxonomy snapshot (§1.2, §7 risk #1) ─────────────────────────────────

_EXPECTED_TAXONOMY = frozenset(
    {
        "HAL0-HOST-NOT-TUNED",
        "HAL0-GFX-TARGET-UNSUPPORTED",
        "HAL0-ROCM-LIB-MISSING",
        "HAL0-MODEL-FILE-MISSING",
        "HAL0-MODEL-UNREGISTERED",
        "HAL0-MODEL-STORE-MISSING",
        "HAL0-MODEL-STORE-UNMOUNTED",
        "HAL0-MODEL-FLM-STORE-DIVERGED",
        "HAL0-MODEL-FLM-STORE-UNMOUNTED",
        "HAL0-MODEL-FLM-STORE-NOT-WRITABLE",
        "HAL0-MIGRATION-PENDING",
        "HAL0-PROFILE-REF-DANGLES",
        "HAL0-PROFILE-IMAGE-MISSING",
        "HAL0-PERMS-HERMES-DRIFT",
        "HAL0-PERMS-TREE-NOT-SHARED",
        "HAL0-PERMS-PATH-OWNERSHIP-DRIFT",
        "HAL0-TOOLBOX-IMAGE-UNREACHABLE",
        "HAL0-TOOLBOX-IMAGE-DIGEST-DRIFT",
        "HAL0-API-UNREACHABLE",
        "HAL0-RUNNERS-NONE-HEALTHY",
        "HAL0-DNS-LOCAL-UNRESOLVED",
        "HAL0-CAPABILITIES-NONE",
        "HAL0-MEMORY-ENGINE-UNREACHABLE",
        "HAL0-OPENWEBUI-DOWN",
        "HAL0-HERMES-DOWN",
        "HAL0-DOCTOR-OK",
        "HAL0-DOCTOR-SKIPPED",
    }
)


def test_diagnosis_id_taxonomy_snapshot() -> None:
    """Pin the §1.2 ID table exactly. Changing this set is a breaking change —
    see spec-21-4-doctor.md §7 risk #1: bump a schema version + CHANGELOG
    note, never a silent rename."""
    assert DIAGNOSIS_IDS == _EXPECTED_TAXONOMY


def test_every_taxonomy_id_follows_the_shape() -> None:
    for diag_id in DIAGNOSIS_IDS:
        assert diag_id.startswith("HAL0-"), diag_id
        assert diag_id == diag_id.upper(), diag_id
        assert "_" not in diag_id, diag_id
