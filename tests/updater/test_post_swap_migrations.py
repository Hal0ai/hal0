"""Unit tests for the two #1960 primitives:

* ``_run_post_activation_migrations_after_swap`` — the subprocess call
  ``Updater.commit()`` now makes AFTER ``seam.activate()`` so migrations run
  the INCOMING version's code, not the outgoing one still loaded in this
  process. See ``test_updater.py``'s
  ``test_commit_runs_post_activation_migrations_after_the_swap`` for the
  end-to-end ordering proof against ``commit()`` itself; this file covers
  the helper's own argument-passing and error-surfacing contract in
  isolation.
* ``check_outstanding_migrations`` — the boot-time safety net that re-runs
  the same six passes on every ``hal0-api`` start so a box that missed its
  migrations (pre-fix, or a failed post-swap subprocess) self-heals.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

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


# ── _run_post_activation_migrations_after_swap ──────────────────────────────────


def test_success_parses_the_result_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(cmd, *a, **k):
        return _Completed(stdout="HAL0_MIGRATION_RESULT=" + json.dumps({"from": 1, "to": 2}))

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    result = asyncio.run(_run_post_activation_migrations_after_swap(2, job_id="j1", ceiling=None))
    assert result == (1, 2)


def test_result_marker_survives_interleaved_log_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    """structlog's default (unconfigured) logger prints straight to stdout —
    the same stdout ``capture_output=True`` captures — so the marker line
    must be found even with ordinary log lines around it."""

    def _fake_run(cmd, *a, **k):
        stdout = (
            "2026-08-20T00:00:00Z [info] updater.migrations_applied source=1 target=2\n"
            + "HAL0_MIGRATION_RESULT="
            + json.dumps({"from": 1, "to": 2})
            + "\n"
            "2026-08-20T00:00:01Z [info] updater.seed_profiles_prune_noop\n"
        )
        return _Completed(stdout=stdout)

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    result = asyncio.run(_run_post_activation_migrations_after_swap(1, job_id=None, ceiling=None))
    assert result == (1, 2)


def test_args_are_passed_through_as_a_single_json_argv_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(cmd, *a, **k):
        captured["cmd"] = list(cmd)
        return _Completed(stdout="HAL0_MIGRATION_RESULT=" + json.dumps({"from": 1, "to": 1}))

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    asyncio.run(_run_post_activation_migrations_after_swap(3, job_id="job-xyz", ceiling=1))

    cmd = captured["cmd"]
    assert "run_post_activation_migrations" in cmd[2]
    payload = json.loads(cmd[3])
    assert payload == {"min_data_version": 3, "job_id": "job-xyz", "ceiling": 1}


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
        asyncio.run(_run_post_activation_migrations_after_swap(1, job_id="j2", ceiling=None))

    assert "kaboom" in str(exc_info.value)
    assert exc_info.value.details["returncode"] == 1
    assert exc_info.value.details["job_id"] == "j2"


def test_zero_exit_with_no_marker_raises_update_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(cmd, *a, **k):
        return _Completed(stdout="some unrelated log line\n")

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    with pytest.raises(UpdateError) as exc_info:
        asyncio.run(_run_post_activation_migrations_after_swap(1, job_id=None, ceiling=None))

    assert "no result marker" in str(exc_info.value)


# ── check_outstanding_migrations ─────────────────────────────────────────────


def test_never_raises_on_internal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater_module, "profile_reset_status", lambda: {"due": False})

    def _boom(*, job_id=None, ceiling=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(updater_module, "run_post_activation_migrations", _boom)

    assert check_outstanding_migrations(job_id="boot") is None


def test_forwards_the_real_migration_result_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater_module, "profile_reset_status", lambda: {"due": False})
    monkeypatch.setattr(
        updater_module,
        "run_post_activation_migrations",
        lambda *, job_id=None, ceiling=None: (1, 2),
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

    def _fake_migrations(*, job_id=None, ceiling=None):
        seen["ceiling"] = ceiling
        return (1, 1)

    monkeypatch.setattr(updater_module, "run_post_activation_migrations", _fake_migrations)

    check_outstanding_migrations()
    assert seen["ceiling"] == updater_module.PROFILE_CATALOG_SCHEMA_VERSION - 1


def test_no_ceiling_when_the_reset_is_not_due(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater_module, "profile_reset_status", lambda: {"due": False})
    seen: dict[str, Any] = {}

    def _fake_migrations(*, job_id=None, ceiling=None):
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
        lambda *, job_id=None, ceiling=None: (1, 1),
    )

    def _unexpected(**kwargs: Any) -> None:
        raise AssertionError("check_outstanding_migrations must not call reset_profile_catalog")

    monkeypatch.setattr(updater_module, "reset_profile_catalog", _unexpected)

    assert check_outstanding_migrations() == (1, 1)
