"""#1464 — self-update must work on the shipped ``User=hal0`` posture.

On a v1.0 box ``hal0-api`` runs as the unprivileged ``hal0`` service account
while ``/usr/lib/hal0`` is ``root:root 0755`` and never service-writable. Every
update path wrote that tree in-process, so ``prepare``/``commit``/``rollback``
were structurally impossible — and failed only *after* a full download +
sha256 + cosign pass, as a raw ``Permission denied``.

These tests pin the fix: the three privileged phases route through the
``hal0-update`` sudo seam when (and only when) we run as the service account,
the grant surface is exactly ``check|stage|activate|discard``, only bounded
tokens cross it, and a missing grant fails fast with remediation.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.updater.privileged import SEAM_BIN, UpdateSeam, _parse_result, main
from hal0.updater.updater import (
    UpdateError,
    UpdatePrivilegeError,
    activate_release,
    assert_release_dir_name,
    assert_trusted_release_dir,
    discard_release,
    release_dir_name,
)


class FakeRun:
    """Recording stand-in for ``subprocess.run``."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


def _envelope(payload: dict[str, Any]) -> str:
    return json.dumps({"hal0_update_result": payload}) + "\n"


# ── release-directory tokens: the ONLY thing that crosses the boundary ─────────


def test_release_dir_name_builds_the_expected_basename() -> None:
    assert release_dir_name("1.0.0") == "hal0-1.0.0"
    assert release_dir_name("1.0.0-rc.1") == "hal0-1.0.0-rc.1"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "hal0",
        "hal0-",
        "hal0-..",
        "hal0-../../etc",
        "hal0-1.0.0/../root",
        "/usr/lib/hal0/hal0-1.0.0",
        "../hal0-1.0.0",
        "hal0-1.0.0 ; rm -rf /",
        "hal0-" + "a" * 200,
        "venv",
        "current",
    ],
)
def test_assert_release_dir_name_refuses_anything_that_could_escape(bad: str) -> None:
    with pytest.raises(ValueError):
        assert_release_dir_name(bad)


# ── routing: seam engaged only for the hal0 service account ────────────────────


def test_seam_is_passthrough_when_not_the_service_account(monkeypatch: Any) -> None:
    """Dev shells, CI and unit tests never have the grant — never route."""
    run = FakeRun()
    seam = UpdateSeam(run=run, is_hal0_user=lambda: False)
    assert seam.routed is False

    called: list[str] = []
    monkeypatch.setattr(
        "hal0.updater.privileged.discard_release",
        lambda name, job_id=None: called.append(name),
    )
    seam.discard("hal0-1.0.0")
    assert called == ["hal0-1.0.0"]
    assert run.calls == []


def test_stage_routes_through_sudo_with_channel_and_version() -> None:
    run = FakeRun(stdout=_envelope({"version": "1.0.0", "install_dir": "/usr/lib/hal0/hal0-1.0.0"}))
    seam = UpdateSeam(run=run, is_hal0_user=lambda: True)

    import asyncio

    result = asyncio.run(seam.stage("stable", "1.0.0"))

    assert run.calls == [["sudo", "-n", SEAM_BIN, "stage", "stable", "1.0.0"]]
    assert result["version"] == "1.0.0"


def test_stage_omits_the_version_when_unpinned() -> None:
    run = FakeRun(stdout=_envelope({"version": "1.0.0"}))
    seam = UpdateSeam(run=run, is_hal0_user=lambda: True)

    import asyncio

    asyncio.run(seam.stage("nightly"))
    assert run.calls == [["sudo", "-n", SEAM_BIN, "stage", "nightly"]]


def test_activate_and_discard_route_through_sudo() -> None:
    run = FakeRun(stdout=_envelope({"previous": "/usr/lib/hal0/hal0-0.9.9"}))
    seam = UpdateSeam(run=run, is_hal0_user=lambda: True)

    import asyncio

    out = asyncio.run(seam.activate("hal0-1.0.0"))
    seam.discard("hal0-1.0.0")

    assert run.calls == [
        ["sudo", "-n", SEAM_BIN, "activate", "hal0-1.0.0"],
        ["sudo", "-n", SEAM_BIN, "discard", "hal0-1.0.0"],
    ]
    assert out["previous"] == "/usr/lib/hal0/hal0-0.9.9"


def test_seam_argv_is_the_whole_grant_surface() -> None:
    """The sudoers grant is pinned to the binary; the verb list is the allow-list."""
    run = FakeRun(stdout=_envelope({}))
    seam = UpdateSeam(run=run, is_hal0_user=lambda: True)

    import asyncio

    asyncio.run(seam.stage("stable"))
    asyncio.run(seam.activate("hal0-1.0.0"))
    seam.discard("hal0-1.0.0")
    seam.assert_privileges() if Path(SEAM_BIN).exists() else None

    verbs = {call[3] for call in run.calls}
    assert verbs <= {"check", "stage", "activate", "discard"}
    assert all(call[:3] == ["sudo", "-n", SEAM_BIN] for call in run.calls)


def test_seam_refuses_an_unknown_channel() -> None:
    import asyncio

    seam = UpdateSeam(run=FakeRun(), is_hal0_user=lambda: True)
    with pytest.raises(UpdateError):
        asyncio.run(seam.stage("evil"))


def test_seam_refuses_a_traversing_dir_name() -> None:
    seam = UpdateSeam(run=FakeRun(), is_hal0_user=lambda: True)
    with pytest.raises(UpdateError):
        seam.discard("hal0-1.0.0/../../etc")


def test_seam_surfaces_the_root_helpers_stderr_on_failure() -> None:
    run = FakeRun(returncode=1, stderr="hal0-update: cosign verify-blob failed")
    seam = UpdateSeam(run=run, is_hal0_user=lambda: True)
    with pytest.raises(UpdateError) as exc:
        seam.discard("hal0-1.0.0")
    assert "cosign verify-blob failed" in exc.value.details["stderr"]


# ── preflight: fail fast, with remediation, before any download ────────────────


def test_preflight_fails_when_the_seam_binary_is_absent(tmp_path: Path) -> None:
    seam = UpdateSeam(
        run=FakeRun(), is_hal0_user=lambda: True, seam_bin=str(tmp_path / "nope" / "hal0-update")
    )
    with pytest.raises(UpdatePrivilegeError) as exc:
        seam.assert_privileges()
    assert "install.sh" in exc.value.details["hint"]


def test_preflight_fails_when_the_grant_does_not_apply(tmp_path: Path) -> None:
    fake_bin = tmp_path / "hal0-update"
    fake_bin.write_text("#!/bin/sh\n")
    run = FakeRun(returncode=1, stderr="sudo: a password is required")
    seam = UpdateSeam(run=run, is_hal0_user=lambda: True, seam_bin=str(fake_bin))

    with pytest.raises(UpdatePrivilegeError) as exc:
        seam.assert_privileges()

    assert run.calls == [["sudo", "-n", str(fake_bin), "check"]]
    assert "/etc/sudoers.d/hal0-update" in exc.value.details["sudoers"]


def test_preflight_passes_when_the_grant_works(tmp_path: Path) -> None:
    fake_bin = tmp_path / "hal0-update"
    fake_bin.write_text("#!/bin/sh\n")
    seam = UpdateSeam(run=FakeRun(returncode=0), is_hal0_user=lambda: True, seam_bin=str(fake_bin))
    seam.assert_privileges()


def test_preflight_rejects_an_unwritable_install_root_when_unrouted(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """The pre-#1464 failure mode, now caught before the download."""
    root = tmp_path / "usr-lib" / "hal0"
    root.mkdir(parents=True)
    monkeypatch.setattr("hal0.updater.privileged._usr_lib_root", lambda: root)
    monkeypatch.setattr(os, "access", lambda *a, **k: False)

    seam = UpdateSeam(run=FakeRun(), is_hal0_user=lambda: False)
    with pytest.raises(UpdatePrivilegeError) as exc:
        seam.assert_privileges()
    assert "not writable" in exc.value.message


# ── the root-side helper's stdout contract ─────────────────────────────────────


def test_parse_result_ignores_noise_before_the_envelope() -> None:
    stdout = "WARNING: pip is being run as root\nnot json\n" + _envelope({"version": "1.0.0"})
    assert _parse_result(stdout, verb="stage") == {"version": "1.0.0"}


def test_parse_result_raises_when_no_envelope_was_emitted() -> None:
    with pytest.raises(UpdateError):
        _parse_result("only logs here\n", verb="stage")


def test_main_check_emits_an_envelope_and_exits_zero(capsys: Any) -> None:
    assert main(["check"]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["hal0_update_result"]["ok"] is True


def test_main_rejects_an_unknown_verb(capsys: Any) -> None:
    assert main(["rm-rf"]) == 64


def test_main_revalidates_arguments_as_root(capsys: Any) -> None:
    """The wrapper's regexes are a convenience; Python is the boundary."""
    assert main(["activate", "../../etc"]) == 1
    assert main(["stage", "evil-channel"]) == 1


# ── activate/discard against a real (HAL0_HOME) tree ───────────────────────────


def _install_root() -> Path:
    from hal0.updater.updater import _usr_lib_root

    root = _usr_lib_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_activate_release_swaps_the_symlink_and_reports_previous(monkeypatch: Any) -> None:
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: True)
    root = _install_root()
    old = root / "hal0-0.9.9"
    new = root / "hal0-1.0.0"
    old.mkdir()
    new.mkdir()
    os.symlink(old, root / "current")

    result = activate_release("hal0-1.0.0")

    assert (root / "current").resolve() == new.resolve()
    assert Path(result["previous"]).resolve() == old.resolve()


def test_activate_release_refuses_a_missing_tree() -> None:
    _install_root()
    with pytest.raises(UpdateError):
        activate_release("hal0-9.9.9")


def test_root_boundary_refuses_a_service_writable_tree() -> None:
    """`activate` ends in `pip install`, which runs the build backend as root.

    A tree the unprivileged service account could write must never reach it —
    that would turn the narrow grant into arbitrary root code execution. The
    assertion lives at the ROOT boundary (`main`), not in the shared primitive,
    and takes an injected euid so it needs neither root nor a global
    `os.geteuid` patch (which other suites monkeypatch).
    """
    root = _install_root()
    tree = root / "hal0-1.0.0"
    tree.mkdir()

    # Group-writable: the hal0 group could plant code here before activation.
    tree.chmod(0o775)
    with pytest.raises(UpdatePrivilegeError) as exc:
        assert_trusted_release_dir(tree, euid=0)
    assert "not root-owned" in exc.value.message
    assert "chown -R root:root" in exc.value.details["hint"]

    # Not root-owned (files here belong to the test user) — refused too.
    tree.chmod(0o755)
    with pytest.raises(UpdatePrivilegeError):
        assert_trusted_release_dir(tree, euid=0)

    # Below euid 0 there is no privilege boundary to protect: never enforced.
    assert_trusted_release_dir(tree, euid=1000)


def test_main_activate_runs_the_trust_check_before_touching_the_symlink(
    monkeypatch: Any,
) -> None:
    """The refusal must land at the root boundary, not inside the primitive."""
    root = _install_root()
    (root / "hal0-1.0.0").mkdir()
    called: list[str] = []
    monkeypatch.setattr(
        "hal0.updater.privileged.activate_release", lambda *a, **k: called.append("ran") or {}
    )
    monkeypatch.setattr(
        "hal0.updater.privileged.assert_trusted_release_dir",
        lambda path, **k: (_ for _ in ()).throw(UpdatePrivilegeError("not root-owned")),
    )

    assert main(["activate", "hal0-1.0.0"]) == 1
    assert called == []


def test_discard_release_is_idempotent_and_bounded() -> None:
    root = _install_root()
    tree = root / "hal0-1.0.0"
    tree.mkdir()
    (tree / "VERSION").write_text("1.0.0")

    discard_release("hal0-1.0.0")
    assert not tree.exists()
    discard_release("hal0-1.0.0")  # idempotent

    with pytest.raises(UpdateError):
        discard_release("../../etc")
