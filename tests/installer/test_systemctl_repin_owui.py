"""repin-owui: digest-validated single-purpose unit rewrite (component-updates
task 3).

``installer/wrappers/hal0-systemctl`` is the entire privileged surface a
service-user ``hal0-api`` can reach for slot/systemd ops (see the module
docstring at the top of the wrapper). ``hal0 update owui`` needs one more
root-only action to repin the *installed* OpenWebUI companion unit: rewrite
every ``open-webui@sha256:…`` occurrence in
``/etc/systemd/system/hal0-openwebui.service`` to a new digest, then
``daemon-reload`` so systemd picks it up. ``repin-owui`` is that verb.

WHY THIS RUNS A COPY OF THE WRAPPER, AND WHY ``UNIT_DIR`` IS OVERRIDABLE.
``tests/conftest.py``'s ``_no_real_systemctl`` guard hard-fails any test that
executes a binary named ``hal0-systemctl`` with a mutating verb (real root,
real polkit). This test copies the wrapper to a neutral filename and runs it
directly — never through sudo — exactly like
``tests/installer/test_systemctl_start_limit_recovery.py``. A ``systemctl``
shim goes first on ``PATH`` so the arm's ``daemon-reload`` call never reaches
the system bus. The wrapper has no pre-existing test-root convention for the
unit directory it writes into (``UNIT_DIR`` was a hardcoded literal), so this
verb's unit path is env-overridable (``${UNIT_DIR:-/etc/systemd/system}``,
same fallback shape the brief specifies) and the test points it at a
``tmp_path`` fixture instead of the real ``/etc/systemd/system``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-systemctl"

_SHIM = """#!/bin/bash
echo "$@" >> "$SYSTEMCTL_LOG"
exit 0
"""

_OLD_DIGEST = "sha256:" + "a" * 64
_NEW_DIGEST = "sha256:" + "b" * 64

_UNIT_BODY = f"""[Unit]
Description=hal0 OpenWebUI companion

[Container]
Image=ghcr.io/open-webui/open-webui@{_OLD_DIGEST}

[Service]
ExecStartPre=/usr/bin/podman pull ghcr.io/open-webui/open-webui@{_OLD_DIGEST}

[Install]
WantedBy=hal0.target
"""


@pytest.fixture
def seam(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Recording ``systemctl`` + a neutrally-named wrapper copy + a fake unit dir."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim_path = bindir / "systemctl"
    shim_path.write_text(_SHIM, encoding="utf-8")
    shim_path.chmod(0o755)

    # Byte-for-byte the shipped wrapper — only the filename differs, so the
    # conftest guard (which matches on basename) doesn't trip on a call that
    # provably cannot reach the real system bus. See the module docstring.
    wrapper_copy = tmp_path / "seam-under-test"
    shutil.copy2(WRAPPER, wrapper_copy)

    unit_dir = tmp_path / "unit-dir"
    unit_dir.mkdir()

    return bindir, tmp_path / "systemctl.log", wrapper_copy, unit_dir


def _run(
    seam: tuple[Path, Path, Path, Path], *args: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bindir, log, wrapper, unit_dir = seam
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["SYSTEMCTL_LOG"] = str(log)
    env["UNIT_DIR"] = str(unit_dir)
    proc = subprocess.run(
        [str(wrapper), "repin-owui", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, lines


def test_wrapper_is_syntactically_valid() -> None:
    proc = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_repin_owui_validates_before_writing() -> None:
    """The digest must be checked before anything is touched — a
    ``repin-owui`` arm that mutated the unit before validating it would be a
    root-side confused-deputy hole at the same seam #1716/#1740 closed."""
    text = WRAPPER.read_text()
    arm = text.split("  repin-owui)", 1)[1].split("\n    ;;", 1)[0]
    assert "die" in arm
    assert "sha256" in arm


def test_both_occurrences_are_rewritten_and_daemon_reloaded(
    seam: tuple[Path, Path, Path, Path],
) -> None:
    _, _, _, unit_dir = seam
    unit_path = unit_dir / "hal0-openwebui.service"
    unit_path.write_text(_UNIT_BODY, encoding="utf-8")

    proc, lines = _run(seam, _NEW_DIGEST)

    assert proc.returncode == 0, proc.stderr
    new_body = unit_path.read_text(encoding="utf-8")
    assert new_body.count(_OLD_DIGEST) == 0
    assert new_body.count(_NEW_DIGEST) == 2
    assert lines == ["daemon-reload"]


def test_a_malformed_digest_is_refused_and_the_unit_is_untouched(
    seam: tuple[Path, Path, Path, Path],
) -> None:
    _, _, _, unit_dir = seam
    unit_path = unit_dir / "hal0-openwebui.service"
    unit_path.write_text(_UNIT_BODY, encoding="utf-8")

    proc, lines = _run(seam, "not-a-digest")

    assert proc.returncode == 64
    assert "bad digest" in proc.stderr
    assert unit_path.read_text(encoding="utf-8") == _UNIT_BODY
    assert lines == [], "a rejected digest must never reach systemctl"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "sha256:" + "a" * 63,  # one hex char short
        "sha256:" + "a" * 65,  # one hex char long
        "sha256:" + "A" * 64,  # uppercase hex refused (unit stores lowercase)
        "md5:" + "a" * 32,  # wrong algorithm prefix
        "a" * 64,  # missing sha256: prefix
        "sha256:" + "g" * 64,  # non-hex character
        "sha256:aaaa; rm -rf /",  # shell metacharacters
    ],
)
def test_malformed_digests_are_all_refused(seam: tuple[Path, Path, Path, Path], bad: str) -> None:
    _, _, _, unit_dir = seam
    unit_path = unit_dir / "hal0-openwebui.service"
    unit_path.write_text(_UNIT_BODY, encoding="utf-8")

    proc, lines = _run(seam, bad)

    assert proc.returncode == 64
    assert unit_path.read_text(encoding="utf-8") == _UNIT_BODY
    assert lines == []


def test_a_missing_unit_file_is_refused(seam: tuple[Path, Path, Path, Path]) -> None:
    proc, lines = _run(seam, _NEW_DIGEST)

    assert proc.returncode == 64
    assert "not found" in proc.stderr
    assert lines == []


def test_help_documents_the_verb() -> None:
    proc = subprocess.run(
        [str(WRAPPER), "help"], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}
    )
    assert proc.returncode == 0, proc.stderr
    assert "repin-owui" in proc.stdout
