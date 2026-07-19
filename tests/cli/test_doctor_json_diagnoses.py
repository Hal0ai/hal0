"""Tests for the §21.4 retrofit's ``_diagnose_*`` pure adapters.

Each ``doctor`` subcommand's ``--json`` flag renders these adapters'
output; the adapters are pure (row/tuple in, ``list[Diagnosis]`` out) so
they're tested directly rather than through the Typer command (which would
need a live API / root / a real filesystem for several of the surfaces).
"""

from __future__ import annotations

from hal0.cli.doctor_commands import (
    _diagnose_audit_rows,
    _diagnose_migration,
    _diagnose_models,
    _diagnose_profiles,
)
from hal0.diagnostics import overall_verdict

# ── _diagnose_audit_rows (doctor perms — Hermes / tree-share / path-ownership) ─


def test_diagnose_audit_rows_all_ok_emits_doctor_ok() -> None:
    rows = [{"path": "/a", "label": "a", "status": "ok", "detail": "fine"}]
    diagnoses = _diagnose_audit_rows(
        rows, diagnosis_id="HAL0-PERMS-HERMES-DRIFT", ok_summary="clean"
    )
    assert len(diagnoses) == 1
    assert diagnoses[0].id == "HAL0-DOCTOR-OK"
    assert diagnoses[0].severity == "info"


def test_diagnose_audit_rows_absent_only_still_ok() -> None:
    rows = [{"path": "/a", "label": "a", "status": "absent", "detail": "not present"}]
    diagnoses = _diagnose_audit_rows(
        rows, diagnosis_id="HAL0-PERMS-HERMES-DRIFT", ok_summary="clean"
    )
    assert diagnoses[0].id == "HAL0-DOCTOR-OK"


def test_diagnose_audit_rows_drift_becomes_fail_diagnosis() -> None:
    rows = [
        {"path": "/a", "label": "config.yaml", "status": "drift", "detail": "owned by root"},
        {"path": "/b", "label": "venv", "status": "ok", "detail": "fine"},
    ]
    diagnoses = _diagnose_audit_rows(
        rows, diagnosis_id="HAL0-PERMS-HERMES-DRIFT", ok_summary="clean"
    )
    assert len(diagnoses) == 1
    d = diagnoses[0]
    assert d.id == "HAL0-PERMS-HERMES-DRIFT"
    assert d.severity == "fail"
    assert d.confidence == "high"
    assert "config.yaml" in d.summary
    assert d.evidence[0].data == {"path": "/a", "label": "config.yaml"}


def test_diagnose_audit_rows_one_per_drift_row() -> None:
    rows = [
        {"path": "/a", "label": "a", "status": "drift", "detail": "d1"},
        {"path": "/b", "label": "b", "status": "drift", "detail": "d2"},
    ]
    diagnoses = _diagnose_audit_rows(
        rows, diagnosis_id="HAL0-PERMS-TREE-NOT-SHARED", ok_summary="x"
    )
    assert len(diagnoses) == 2
    assert {d.id for d in diagnoses} == {"HAL0-PERMS-TREE-NOT-SHARED"}


def test_diagnose_audit_rows_carries_next_steps() -> None:
    from hal0.diagnostics import NextStep

    rows = [{"path": "/a", "label": "a", "status": "drift", "detail": "d"}]
    step = NextStep(kind="command", label="fix it", target="hal0 doctor perms --fix")
    diagnoses = _diagnose_audit_rows(
        rows, diagnosis_id="HAL0-PERMS-TREE-NOT-SHARED", ok_summary="x", next_steps=[step]
    )
    assert diagnoses[0].next_steps == [step]


# ── _diagnose_models (doctor models) ────────────────────────────────────────


def test_diagnose_models_clean_emits_doctor_ok() -> None:
    diagnoses = _diagnose_models(
        dangling=[],
        unregistered=[],
        store_missing=False,
        effective="/store",
        divergence=None,
        mount_warn=None,
        flm_dir=__import__("pathlib").Path("/var/lib/hal0/.config/flm/models"),
        writ=None,
    )
    assert len(diagnoses) == 1
    assert diagnoses[0].id == "HAL0-DOCTOR-OK"


def test_diagnose_models_dangling_entry() -> None:
    diagnoses = _diagnose_models(
        dangling=[{"id": "m1", "path": "/models/m1.gguf"}],
        unregistered=[],
        store_missing=False,
        effective="/store",
        divergence=None,
        mount_warn=None,
        flm_dir=__import__("pathlib").Path("/flm"),
        writ=None,
    )
    ids = [d.id for d in diagnoses]
    assert ids == ["HAL0-MODEL-FILE-MISSING"]
    assert "m1" in diagnoses[0].next_steps[0].target


def test_diagnose_models_store_missing() -> None:
    diagnoses = _diagnose_models(
        dangling=[],
        unregistered=[],
        store_missing=True,
        effective="/store/gone",
        divergence=None,
        mount_warn=None,
        flm_dir=__import__("pathlib").Path("/flm"),
        writ=None,
    )
    assert diagnoses[0].id == "HAL0-MODEL-STORE-MISSING"
    assert diagnoses[0].severity == "fail"


def test_diagnose_models_unmounted_entry() -> None:
    """O25: a model outside every mounted root is a fail with the store-unmounted id."""
    diagnoses = _diagnose_models(
        dangling=[],
        unregistered=[],
        store_missing=False,
        unmounted=[{"id": "brain", "path": "/mnt/ai-models/brain.gguf"}],
        mount_roots=["/var/lib/hal0/models"],
        effective="/var/lib/hal0/models",
        divergence=None,
        mount_warn=None,
        flm_dir=__import__("pathlib").Path("/flm"),
        writ=None,
    )
    ids = [d.id for d in diagnoses]
    assert ids == ["HAL0-MODEL-STORE-UNMOUNTED"]
    assert diagnoses[0].severity == "fail"
    assert "/mnt/ai-models/brain.gguf" in diagnoses[0].detail


def test_models_outside_mount_roots_flags_only_unreachable() -> None:
    from hal0.cli.doctor_commands import _models_outside_mount_roots

    local = [
        {"id": "under", "path": "/mnt/ai-models/a.gguf"},
        {"id": "outside", "path": "/opt/models/b.gguf"},
        {"id": "nested", "path": "/mnt/ai-models/sub/c.gguf"},
    ]
    out = _models_outside_mount_roots(local, ["/mnt/ai-models"])
    assert [m["id"] for m in out] == ["outside"]


def test_models_outside_mount_roots_empty_roots_is_noop() -> None:
    from hal0.cli.doctor_commands import _models_outside_mount_roots

    local = [{"id": "x", "path": "/anywhere/x.gguf"}]
    assert _models_outside_mount_roots(local, []) == []


def test_diagnose_models_unregistered_files() -> None:
    diagnoses = _diagnose_models(
        dangling=[],
        unregistered=["/store/orphan.gguf"],
        store_missing=False,
        effective="/store",
        divergence=None,
        mount_warn=None,
        flm_dir=__import__("pathlib").Path("/flm"),
        writ=None,
    )
    assert diagnoses[0].id == "HAL0-MODEL-UNREGISTERED"
    assert diagnoses[0].severity == "warn"


def test_diagnose_models_flm_divergence_not_fixable() -> None:
    diagnoses = _diagnose_models(
        dangling=[],
        unregistered=[],
        store_missing=False,
        effective="/store",
        divergence={"status": "warn", "detail": "env overrides toml"},
        mount_warn=None,
        flm_dir=__import__("pathlib").Path("/flm"),
        writ=None,
    )
    assert diagnoses[0].id == "HAL0-MODEL-FLM-STORE-DIVERGED"
    assert diagnoses[0].fixable is False


def test_diagnose_models_flm_unmounted() -> None:
    diagnoses = _diagnose_models(
        dangling=[],
        unregistered=[],
        store_missing=False,
        effective="/store",
        divergence=None,
        mount_warn={"status": "warn", "detail": "not mounted"},
        flm_dir=__import__("pathlib").Path("/mnt/flm"),
        writ=None,
    )
    assert diagnoses[0].id == "HAL0-MODEL-FLM-STORE-UNMOUNTED"
    assert diagnoses[0].next_steps[0].kind == "manual"


def test_diagnose_models_flm_not_writable_is_fixable() -> None:
    diagnoses = _diagnose_models(
        dangling=[],
        unregistered=[],
        store_missing=False,
        effective="/store",
        divergence=None,
        mount_warn=None,
        flm_dir=__import__("pathlib").Path("/flm"),
        writ={"status": "fail", "uid": 0, "mode": 0o755, "detail": "not writable"},
    )
    assert diagnoses[0].id == "HAL0-MODEL-FLM-STORE-NOT-WRITABLE"
    assert diagnoses[0].fixable is True
    assert diagnoses[0].next_steps[0].target == "hal0 doctor models --fix"


# ── _diagnose_migration (doctor migrations) ─────────────────────────────────


def test_diagnose_migration_none_is_skipped() -> None:
    diagnoses = _diagnose_migration(None)
    assert diagnoses[0].id == "HAL0-DOCTOR-SKIPPED"


def test_diagnose_migration_nothing_pending_is_ok() -> None:
    diagnoses = _diagnose_migration((0, 0))
    assert diagnoses[0].id == "HAL0-DOCTOR-OK"


def test_diagnose_migration_pending_is_warn() -> None:
    diagnoses = _diagnose_migration((3, 1))
    assert len(diagnoses) == 1
    d = diagnoses[0]
    assert d.id == "HAL0-MIGRATION-PENDING"
    assert d.severity == "warn"
    assert d.evidence[0].data == {"create_count": 3, "overwrite_count": 1}
    assert d.next_steps[0].target == "hal0 migrate model-layout --apply"
    assert overall_verdict(diagnoses) == "warn"


# ── _diagnose_profiles (doctor profiles) ────────────────────────────────────


def test_diagnose_profiles_clean_is_ok() -> None:
    diagnoses = _diagnose_profiles([], [])
    assert diagnoses[0].id == "HAL0-DOCTOR-OK"


def test_diagnose_profiles_dangling_ref_is_fail() -> None:
    ref_rows = [{"label": "primary", "status": "drift", "detail": "references missing ghost"}]
    diagnoses = _diagnose_profiles(ref_rows, [])
    assert diagnoses[0].id == "HAL0-PROFILE-REF-DANGLES"
    assert diagnoses[0].severity == "fail"
    assert "primary" in diagnoses[0].next_steps[0].target


def test_diagnose_profiles_image_missing_is_warn() -> None:
    img_rows = [{"label": "rocm", "status": "warn", "detail": "image not pulled"}]
    diagnoses = _diagnose_profiles([], img_rows)
    assert diagnoses[0].id == "HAL0-PROFILE-IMAGE-MISSING"
    assert diagnoses[0].severity == "warn"


def test_diagnose_profiles_ok_rows_are_silent() -> None:
    ref_rows = [{"label": "primary", "status": "ok", "detail": "→ rocm"}]
    img_rows = [{"label": "rocm", "status": "ok", "detail": "present"}]
    diagnoses = _diagnose_profiles(ref_rows, img_rows)
    assert diagnoses[0].id == "HAL0-DOCTOR-OK"
