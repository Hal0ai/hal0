"""Successful installs must not leak bootstrap's work dir — #2065.

``installer/bootstrap.sh`` arms an EXIT trap to delete its
``/tmp/hal0-install-*`` work directory (release tarball + unpacked tree,
~150 MB), but that trap dies at the ``exec`` into install.sh — exec
replaces the process — so every successful install leaked the whole tree
(confirmed live on ct151: 152 MB left after a clean install).

The fix has two halves, mirroring the #2052 cosign hand-off:

* ``bootstrap.sh`` exports ``HAL0_BOOTSTRAP_WORK`` pointing at the work
  dir — but only when ``HAL0_BOOTSTRAP_KEEP_TMP`` is not set, so the
  debug knob keeps the tree exactly as before.
* ``cleanup_bootstrap_workdir`` (installer/lib/preflight.sh, called as
  install.sh's very last step) removes that tree. Running dead-last means
  it is strictly after ``persist_bootstrap_cosign`` (#2058 requires the
  cosign to be persisted out of the tree first) and that a failed install
  never reaches it — the tree stays for debugging.

These tests exercise the shell function directly (subprocess, real bash —
the technique ``tests/installer/test_install_cosign_persist.py`` uses),
drive bootstrap.sh end-to-end against the fixture harness from
``tests/installer/test_bootstrap_contract.py``, and pin the wiring with
static-text assertions.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

from test_bootstrap_contract import (
    _CANONICAL_ISSUER,
    _bootstrap_env,
    _exact_identity,
    _run_bootstrap,
    _write_executable,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"
_BOOTSTRAP = _REPO_ROOT / "installer" / "bootstrap.sh"
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"

# ui.sh helpers the function calls; stubbed so output is capturable and the
# restricted environment needs no extra sourcing.
_STUBS = """
info() { printf 'INFO:%s\\n' "$*"; }
ok()   { printf 'OK:%s\\n' "$*"; }
warn() { printf 'WARN:%s\\n' "$*" >&2; }
err()  { printf 'ERR:%s\\n' "$*" >&2; }
"""


def _run_cleanup(
    *,
    bootstrap_work: Path | str | None,
    keep_tmp: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Drive cleanup_bootstrap_workdir under a controlled environment."""
    env = dict(os.environ)
    env.pop("HAL0_BOOTSTRAP_WORK", None)
    env.pop("HAL0_BOOTSTRAP_KEEP_TMP", None)
    if bootstrap_work is not None:
        env["HAL0_BOOTSTRAP_WORK"] = str(bootstrap_work)
    if keep_tmp is not None:
        env["HAL0_BOOTSTRAP_KEEP_TMP"] = keep_tmp

    script = _STUBS + f'source "{_PREFLIGHT}"\n' + "cleanup_bootstrap_workdir\n"
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _make_workdir(tmp_path: Path) -> Path:
    work = tmp_path / "hal0-install-Fixt42"
    (work / "unpacked" / "installer").mkdir(parents=True)
    (work / "artifact.tar.gz").write_bytes(b"tarball bytes")
    (work / "unpacked" / "installer" / "install.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )
    return work


class TestCleanupBootstrapWorkdir:
    def test_removes_handed_off_work_dir(self, tmp_path: Path) -> None:
        work = _make_workdir(tmp_path)
        proc = _run_cleanup(bootstrap_work=work)
        assert proc.returncode == 0, proc.stderr
        assert not work.exists()
        assert "INFO:" in proc.stdout

    def test_noop_without_bootstrap_handoff(self, tmp_path: Path) -> None:
        # git-checkout / --dev / tarball-direct installs never see the var.
        proc = _run_cleanup(bootstrap_work=None)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == ""
        assert proc.stderr == ""

    def test_keep_tmp_keeps_the_tree(self, tmp_path: Path) -> None:
        # Defense in depth: bootstrap already leaves the var unset under
        # HAL0_BOOTSTRAP_KEEP_TMP=1, but the function must honor the knob
        # even if both are set.
        work = _make_workdir(tmp_path)
        proc = _run_cleanup(bootstrap_work=work, keep_tmp="1")
        assert proc.returncode == 0, proc.stderr
        assert work.exists()

    def test_refuses_paths_that_are_not_bootstrap_work_dirs(self, tmp_path: Path) -> None:
        # A stray or mangled value must never aim rm -rf anywhere else.
        precious = tmp_path / "precious"
        precious.mkdir()
        (precious / "data").write_text("keep me", encoding="utf-8")
        proc = _run_cleanup(bootstrap_work=precious)
        assert proc.returncode == 0, proc.stderr
        assert precious.exists()
        assert (precious / "data").read_text(encoding="utf-8") == "keep me"
        assert "WARN:" in proc.stderr

    def test_noop_when_work_dir_already_gone(self, tmp_path: Path) -> None:
        proc = _run_cleanup(bootstrap_work=tmp_path / "hal0-install-gone")
        assert proc.returncode == 0, proc.stderr


def _success_fixture_env(
    tmp_path: Path,
) -> tuple[dict[str, str], Path]:
    """Build the minimal verified-release fixture that reaches the exec.

    Same shape as test_bootstrap_contract's success test, with a stub
    install.sh that records the work-dir hand-off environment.
    """
    version = "1.2.3"
    artifact_url = f"https://fixtures.example/hal0-{version}.tar.gz"
    artifact_bundle_url = f"{artifact_url}.bundle"
    artifact = tmp_path / f"hal0-{version}.tar.gz"
    install_log = tmp_path / "install.log"

    install_script = tmp_path / "tree" / f"hal0-{version}" / "installer" / "install.sh"
    install_script.parent.mkdir(parents=True)
    _write_executable(
        install_script,
        """#!/usr/bin/env bash
set -euo pipefail
printf 'work=%s\\n' "${HAL0_BOOTSTRAP_WORK:-}" > "$INSTALL_LOG"
if [[ -n "${HAL0_BOOTSTRAP_WORK:-}" && -d "${HAL0_BOOTSTRAP_WORK}" ]]; then
    printf 'work_exists=1\\n' >> "$INSTALL_LOG"
fi
""",
    )
    with tarfile.open(artifact, "w:gz") as archive:
        archive.add(install_script.parents[1], arcname=f"hal0-{version}")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = json.dumps(
        {
            "_schema": "hal0.releases.v1",
            "version": version,
            "channel": "stable",
            "release_kind": "stable",
            "prerelease_stage": None,
            "url": artifact_url,
            "bundle_url": artifact_bundle_url,
            "digest_sha256": digest,
            "signer_identity": _exact_identity("stable", version),
            "signer_issuer": _CANONICAL_ISSUER,
        }
    ).encode()
    env, _, _ = _bootstrap_env(
        tmp_path,
        manifest,
        cosign_rc=0,
        artifact_fixture=artifact,
        artifact_url=artifact_url,
        artifact_bundle_url=artifact_bundle_url,
    )
    env["INSTALL_LOG"] = str(install_log)
    # The contract harness symlinks the host's real uname; bootstrap's
    # preflight requires Linux, so stub it for a host-independent test.
    uname = tmp_path / "bin" / "uname"
    uname.unlink()
    _write_executable(
        uname,
        """#!/usr/bin/env bash
case "${1:-}" in
    -m) printf 'x86_64\\n' ;;
    *)  printf 'Linux\\n' ;;
esac
""",
    )
    return env, install_log


class TestBootstrapHandsOffWorkdir:
    def test_export_reaches_install_sh_and_points_at_live_work_dir(self, tmp_path: Path) -> None:
        env, install_log = _success_fixture_env(tmp_path)

        proc = _run_bootstrap(env)

        assert proc.returncode == 0, proc.stderr
        lines = install_log.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("work=")
        work = lines[0].removeprefix("work=")
        assert Path(work).name.startswith("hal0-install-")
        assert "work_exists=1" in lines

    def test_keep_tmp_suppresses_the_hand_off(self, tmp_path: Path) -> None:
        env, install_log = _success_fixture_env(tmp_path)
        env["HAL0_BOOTSTRAP_KEEP_TMP"] = "1"

        proc = _run_bootstrap(env)

        assert proc.returncode == 0, proc.stderr
        lines = install_log.read_text(encoding="utf-8").splitlines()
        assert lines == ["work="]


class TestWiring:
    def test_bootstrap_exports_work_dir_path(self) -> None:
        code = "\n".join(
            line
            for line in _BOOTSTRAP.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "export HAL0_BOOTSTRAP_WORK=" in code

    def test_install_sh_cleans_up_after_cosign_persist_and_dead_last(self) -> None:
        text = _INSTALL_SH.read_text(encoding="utf-8")
        assert "cleanup_bootstrap_workdir" in text
        # Ordering constraint (#2058): bootstrap's cosign is persisted out
        # of the work dir early in pre-flight; cleanup must come after.
        assert text.index("persist_bootstrap_cosign") < text.index("cleanup_bootstrap_workdir")
        # And after the final summary box — a failed install must exit
        # before ever reaching it, leaving the tree for debugging.
        assert text.index('ui_box "hal0 is ready"') < text.index("cleanup_bootstrap_workdir")
