"""Unit tests for the two #1960 primitives:

* ``_run_post_activation_migrations_after_swap`` — the subprocess call
  ``Updater.commit()`` now makes AFTER ``seam.activate()`` so migrations run
  the INCOMING version's code, not the outgoing one still loaded in this
  process. See ``test_updater.py``'s
  ``test_commit_runs_post_activation_migrations_after_the_swap`` for the
  end-to-end ordering proof against ``commit()`` itself; this file covers
  the helper's own argument-passing, containment, and error-surfacing
  contract in isolation.
* ``check_outstanding_migrations`` — the boot-time safety net that re-runs
  the same passes on every ``hal0-api`` start so a box that missed its
  migrations (pre-fix, or a failed post-swap subprocess) self-heals.

Independent review of the first version of this PR (comment 5354502220)
found three blocking gaps, addressed here:

* B1 — ``subprocess.run`` itself can raise (``TimeoutExpired``, ``OSError``)
  rather than return a non-zero ``CompletedProcess``; the old code only
  handled the latter shape.
* B2 — the child's structured log (breadcrumbs #1935 relies on, plus any
  non-fatal per-pass warning) was captured and discarded, not relayed into
  the parent's own journal.
* N1 — the ``ceiling`` int must be derived from the INCOMING tree's
  ``PROFILE_CATALOG_SCHEMA_VERSION``, not pre-computed from the (still
  outgoing) parent process's constant.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

import pytest
from structlog.testing import capture_logs

from hal0.updater import updater as updater_module
from hal0.updater.updater import (
    UpdateError,
    _run_post_activation_migrations_after_swap,
    check_outstanding_migrations,
)


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _envelope(from_: int, to: int) -> str:
    """The child script's stdout contract: one JSON line, the result keyed
    under _MIGRATION_RESULT_KEY."""
    return json.dumps({updater_module._MIGRATION_RESULT_KEY: {"from": from_, "to": to}})


# ── _run_post_activation_migrations_after_swap: happy path ─────────────────────


def test_success_parses_the_result_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(cmd, *a, **k):
        return _Completed(stdout=_envelope(1, 2))

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    result = asyncio.run(
        _run_post_activation_migrations_after_swap(2, job_id="j1", reset_outstanding=False)
    )
    assert result == {"from": 1, "to": 2, "pass_warnings": []}


def test_result_envelope_survives_interleaved_log_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    """The envelope is scanned for from the END of stdout, exactly like
    hal0.updater.privileged._parse_result — an unexpected banner cannot
    displace the answer."""

    def _fake_run(cmd, *a, **k):
        stdout = "some pip warning that escaped to stdout\n" + _envelope(1, 2) + "\n"
        return _Completed(stdout=stdout)

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    result = asyncio.run(
        _run_post_activation_migrations_after_swap(1, job_id=None, reset_outstanding=False)
    )
    assert result == {"from": 1, "to": 2, "pass_warnings": []}


def test_args_are_passed_through_as_a_single_json_argv_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(cmd, *a, **k):
        captured["cmd"] = list(cmd)
        return _Completed(stdout=_envelope(1, 1))

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    asyncio.run(
        _run_post_activation_migrations_after_swap(3, job_id="job-xyz", reset_outstanding=True)
    )

    cmd = captured["cmd"]
    assert "run_post_activation_migrations" in cmd[2]
    payload = json.loads(cmd[3])
    assert payload == {"min_data_version": 3, "job_id": "job-xyz", "reset_outstanding": True}


def test_reset_outstanding_bool_not_a_precomputed_ceiling_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1960 N1: the wire payload carries the BOOL, never an int derived
    from this (outgoing) process's PROFILE_CATALOG_SCHEMA_VERSION — the
    child derives its own ceiling from its own import of that constant."""
    captured: dict[str, Any] = {}

    def _fake_run(cmd, *a, **k):
        captured["script"] = cmd[2]
        captured["payload"] = json.loads(cmd[3])
        return _Completed(stdout=_envelope(1, 1))

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    asyncio.run(_run_post_activation_migrations_after_swap(1, job_id=None, reset_outstanding=True))

    assert "ceiling" not in captured["payload"]
    assert captured["payload"]["reset_outstanding"] is True
    # The child script re-derives the ceiling from ITS OWN import.
    assert "PROFILE_CATALOG_SCHEMA_VERSION" in captured["script"]
    assert "from hal0.updater.updater import" in captured["script"]


# ── B2: relay + pass_warnings ────────────────────────────────────────────────


def test_relays_the_childs_log_into_the_parent_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1960 B2: the child's breadcrumbs (#1935's contract, and the
    release-validation kit's post-upgrade grep) must not die in
    capture_output — they arrive on stderr (the child pins logs there) and
    must be relayed into the parent's own log."""
    child_stderr = (
        "2026-08-20T00:00:00Z [info] updater.migrations_applied source=1 target=2 job_id=j3\n"
        "2026-08-20T00:00:01Z [warning] updater.slot_vulkan_relabeled_rocm slot=a job_id=j3\n"
    )

    def _fake_run(cmd, *a, **k):
        return _Completed(stdout=_envelope(1, 2), stderr=child_stderr)

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    with capture_logs() as logs:
        asyncio.run(
            _run_post_activation_migrations_after_swap(1, job_id="j3", reset_outstanding=False)
        )

    relayed = [e for e in logs if e.get("event") == "updater.privileged_child_log"]
    assert len(relayed) == 2
    assert all(e["job_id"] == "j3" for e in relayed)
    assert all(e["verb"] == "post_swap_migrations" for e in relayed)
    assert any("migrations_applied" in e["line"] for e in relayed)
    assert any("slot_vulkan_relabeled_rocm" in e["line"] for e in relayed)


def test_pass_warnings_surfaced_when_a_nonfatal_pass_logs_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pass that swallows its own exception into a warning still exits
    the subprocess with rc=0 (by design) — that warning must not be
    silently discarded; it belongs in the returned pass_warnings list."""
    child_stderr = (
        "2026-08-20T00:00:00Z [warning] updater.mtp_migration_failed error=boom job_id=j4\n"
    )

    def _fake_run(cmd, *a, **k):
        return _Completed(stdout=_envelope(1, 1), stderr=child_stderr)

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    result = asyncio.run(
        _run_post_activation_migrations_after_swap(1, job_id="j4", reset_outstanding=False)
    )
    assert result["pass_warnings"] == ["updater.mtp_migration_failed"]


def test_no_pass_warnings_on_a_clean_run(monkeypatch: pytest.MonkeyPatch) -> None:
    child_stderr = "2026-08-20T00:00:00Z [info] updater.migrations_noop source=1 target=1\n"

    def _fake_run(cmd, *a, **k):
        return _Completed(stdout=_envelope(1, 1), stderr=child_stderr)

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    result = asyncio.run(
        _run_post_activation_migrations_after_swap(1, job_id=None, reset_outstanding=False)
    )
    assert result["pass_warnings"] == []


def test_relay_does_not_happen_on_the_failure_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors UpdateSeam._invoke exactly: on failure the stderr goes into
    the raised error's details (a diagnostic breadcrumb), not into a
    per-line relay — relaying only ever happens on the success path."""
    child_stderr = "hal0-migrate: schema migration exploded\n"

    def _fake_run(cmd, *a, **k):
        return _Completed(returncode=1, stderr=child_stderr)

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    with capture_logs() as logs, pytest.raises(UpdateError):
        asyncio.run(
            _run_post_activation_migrations_after_swap(1, job_id=None, reset_outstanding=False)
        )

    assert [e for e in logs if e.get("event") == "updater.privileged_child_log"] == []


# ── B1: containment of every subprocess failure shape ───────────────────────


def test_timeout_expired_is_converted_to_update_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """subprocess.run itself raises TimeoutExpired rather than returning a
    CompletedProcess — the old code did not catch this at all."""

    def _fake_run(cmd, *a, **k):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=300)

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    with pytest.raises(UpdateError) as exc_info:
        asyncio.run(
            _run_post_activation_migrations_after_swap(1, job_id="j5", reset_outstanding=False)
        )
    assert "timed out" in str(exc_info.value)
    assert exc_info.value.details["job_id"] == "j5"


def test_os_error_launching_the_subprocess_is_converted_to_update_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(cmd, *a, **k):
        raise OSError("Terminated")

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    with pytest.raises(UpdateError) as exc_info:
        asyncio.run(
            _run_post_activation_migrations_after_swap(1, job_id=None, reset_outstanding=False)
        )
    assert "could not be launched" in str(exc_info.value)


def test_nonzero_exit_raises_update_error_with_stderr_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(cmd, *a, **k):
        return _Completed(
            returncode=1,
            stderr="Traceback (most recent call last):\nhal0.errors.Hal0Error: kaboom\n",
        )

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    with pytest.raises(UpdateError) as exc_info:
        asyncio.run(
            _run_post_activation_migrations_after_swap(1, job_id="j2", reset_outstanding=False)
        )

    assert "kaboom" in str(exc_info.value)
    assert exc_info.value.details["returncode"] == 1
    assert exc_info.value.details["job_id"] == "j2"


def test_zero_exit_with_no_envelope_raises_update_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(cmd, *a, **k):
        return _Completed(stdout="some unrelated log line\n")

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    with pytest.raises(UpdateError) as exc_info:
        asyncio.run(
            _run_post_activation_migrations_after_swap(1, job_id=None, reset_outstanding=False)
        )

    assert "no result marker" in str(exc_info.value)


# ── check_outstanding_migrations ─────────────────────────────────────────────


def test_never_raises_on_internal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater_module, "profile_reset_status", lambda: {"due": False})

    def _boom(
        *,
        job_id=None,
        ceiling=None,
        skip_image_retag=False,
        repair_hermes_venv=True,
        upgrade_memory_engine_venv=True,
    ):
        raise RuntimeError("disk full")

    monkeypatch.setattr(updater_module, "run_post_activation_migrations", _boom)

    assert check_outstanding_migrations(job_id="boot") is None


def test_forwards_the_real_migration_result_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater_module, "profile_reset_status", lambda: {"due": False})
    monkeypatch.setattr(
        updater_module,
        "run_post_activation_migrations",
        lambda *, job_id=None, ceiling=None, skip_image_retag=False, repair_hermes_venv=True, upgrade_memory_engine_venv=True: (
            1,
            2,
        ),
    )

    assert check_outstanding_migrations(job_id="boot") == (1, 2)


def test_caps_the_ceiling_while_the_profile_catalog_reset_is_outstanding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors Updater.commit()'s own ceiling derivation (#1960): this safety
    net must not let meta.schema_version race past the profile-catalog
    watermark on a box that has not gone through the one-shot reset — that
    would silently consume the gate outside the consent flow.
    """
    monkeypatch.setattr(updater_module, "profile_reset_status", lambda: {"due": True})
    seen: dict[str, Any] = {}

    def _fake_migrations(
        *,
        job_id=None,
        ceiling=None,
        skip_image_retag=False,
        repair_hermes_venv=True,
        upgrade_memory_engine_venv=True,
    ):
        seen["ceiling"] = ceiling
        return (1, 1)

    monkeypatch.setattr(updater_module, "run_post_activation_migrations", _fake_migrations)

    check_outstanding_migrations()
    assert seen["ceiling"] == updater_module.PROFILE_CATALOG_SCHEMA_VERSION - 1


def test_no_ceiling_when_the_reset_is_not_due(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater_module, "profile_reset_status", lambda: {"due": False})
    seen: dict[str, Any] = {}

    def _fake_migrations(
        *,
        job_id=None,
        ceiling=None,
        skip_image_retag=False,
        repair_hermes_venv=True,
        upgrade_memory_engine_venv=True,
    ):
        seen["ceiling"] = ceiling
        return (1, 1)

    monkeypatch.setattr(updater_module, "run_post_activation_migrations", _fake_migrations)

    check_outstanding_migrations()
    assert seen["ceiling"] is None


def test_never_calls_reset_profile_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """This is a read-only status check, not a reset attempt — it must never
    delete profiles.toml or perform the one-shot reset itself."""
    monkeypatch.setattr(updater_module, "profile_reset_status", lambda: {"due": True})
    monkeypatch.setattr(
        updater_module,
        "run_post_activation_migrations",
        lambda *, job_id=None, ceiling=None, skip_image_retag=False, repair_hermes_venv=True, upgrade_memory_engine_venv=True: (
            1,
            1,
        ),
    )

    def _unexpected(**kwargs: Any) -> None:
        raise AssertionError("check_outstanding_migrations must not call reset_profile_catalog")

    monkeypatch.setattr(updater_module, "reset_profile_catalog", _unexpected)

    assert check_outstanding_migrations() == (1, 1)


def test_skips_the_image_retag_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1960 N2: retag_stale_slot_images must not run on every boot — see
    run_post_activation_migrations's own docstring for the operator-state
    risk (#1867-class). check_outstanding_migrations must pass
    skip_image_retag=True."""
    monkeypatch.setattr(updater_module, "profile_reset_status", lambda: {"due": False})
    seen: dict[str, Any] = {}

    def _fake_migrations(
        *,
        job_id=None,
        ceiling=None,
        skip_image_retag=False,
        repair_hermes_venv=True,
        upgrade_memory_engine_venv=True,
    ):
        seen["skip_image_retag"] = skip_image_retag
        return (1, 1)

    monkeypatch.setattr(updater_module, "run_post_activation_migrations", _fake_migrations)

    check_outstanding_migrations()
    assert seen["skip_image_retag"] is True
