"""Fresh installs persist a cosign for the updater — #2052.

``installer/bootstrap.sh`` verifies the release with a digest-pinned cosign
fetched into its throwaway work directory, but nothing used to leave a
cosign on the installed system — so the FIRST ``hal0 update`` of every
fresh install hard-failed its signature gate (``cosign is not installed``,
journal ``updater.privileged_failed verb=stage``). The updater's
requirement is correct; the dependency belongs to the platform.

The fix has two halves:

* ``ensure_cosign()`` (bootstrap.sh) exports ``HAL0_BOOTSTRAP_COSIGN``
  pointing at the binary it fetched and sha256-checked, so the verified
  bytes survive the ``exec`` into install.sh.
* ``persist_bootstrap_cosign`` (installer/lib/preflight.sh, called from
  install.sh's pre-flight step outside dev mode) installs that exact
  binary to ``/usr/local/bin/cosign`` — never overwriting a system cosign,
  and warning honestly (first ``hal0 update`` will fail) when there is
  nothing to persist, e.g. a tarball-direct install.

These tests exercise the shell function directly (subprocess, real bash —
the technique ``tests/installer/test_preflight_gpu_gate.py`` uses) plus
static-text assertions proving the bootstrap export and the install.sh
call site exist.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"
_BOOTSTRAP = _REPO_ROOT / "installer" / "bootstrap.sh"
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"

# ui.sh helpers the function calls; stubbed so output is capturable and the
# restricted environment needs no extra sourcing. Stub ONLY the reporters
# ui.sh actually defines (info/warn/err/die) — #2081 shipped because an
# earlier version of this stub set also defined an `ok()` that exists
# nowhere in the installer, masking the undefined-helper 127 that killed
# every real cosign-less fresh install at pre-flight.
_STUBS = """
info() { printf 'INFO:%s\\n' "$*"; }
warn() { printf 'WARN:%s\\n' "$*" >&2; }
err()  { printf 'ERR:%s\\n' "$*" >&2; }
die()  { err "$*"; exit 1; }
"""


def _run_persist(
    tmp_path: Path,
    *,
    with_system_cosign: bool,
    bootstrap_cosign: Path | None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Drive persist_bootstrap_cosign under a controlled PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir(exist_ok=True)
    if with_system_cosign:
        fake = bin_dir / "cosign"
        fake.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    env.pop("HAL0_BOOTSTRAP_COSIGN", None)
    if bootstrap_cosign is not None:
        env["HAL0_BOOTSTRAP_COSIGN"] = str(bootstrap_cosign)

    script = _STUBS + f'source "{_PREFLIGHT}"\n' + f'persist_bootstrap_cosign "{dest_dir}"\n'
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc, dest_dir


def _make_bootstrap_binary(tmp_path: Path) -> Path:
    src = tmp_path / "work" / "cosign"
    src.parent.mkdir(exist_ok=True)
    src.write_text("#!/usr/bin/env bash\necho fake-cosign\n", encoding="utf-8")
    src.chmod(0o755)
    return src


class TestPersistBootstrapCosign:
    def test_persists_bootstrap_binary_when_host_has_none(self, tmp_path: Path) -> None:
        src = _make_bootstrap_binary(tmp_path)
        proc, dest_dir = _run_persist(tmp_path, with_system_cosign=False, bootstrap_cosign=src)
        assert proc.returncode == 0, proc.stderr
        installed = dest_dir / "cosign"
        assert installed.is_file(), proc.stdout + proc.stderr
        assert installed.read_bytes() == src.read_bytes()
        mode = stat.S_IMODE(installed.stat().st_mode)
        assert mode & stat.S_IXUSR, f"not executable: {oct(mode)}"
        assert "INFO:" in proc.stdout

    def test_never_overwrites_a_system_cosign(self, tmp_path: Path) -> None:
        src = _make_bootstrap_binary(tmp_path)
        proc, dest_dir = _run_persist(tmp_path, with_system_cosign=True, bootstrap_cosign=src)
        assert proc.returncode == 0, proc.stderr
        assert not (dest_dir / "cosign").exists()

    def test_warns_and_survives_with_nothing_to_persist(self, tmp_path: Path) -> None:
        proc, dest_dir = _run_persist(tmp_path, with_system_cosign=False, bootstrap_cosign=None)
        # Soft path: the install must not die, but must say what will break.
        assert proc.returncode == 0, proc.stderr
        assert not (dest_dir / "cosign").exists()
        assert "WARN:" in proc.stderr
        assert "hal0 update" in proc.stderr

    def test_ignores_non_executable_bootstrap_path(self, tmp_path: Path) -> None:
        src = tmp_path / "work" / "cosign"
        src.parent.mkdir(exist_ok=True)
        src.write_text("not a binary", encoding="utf-8")  # no exec bit
        proc, dest_dir = _run_persist(tmp_path, with_system_cosign=False, bootstrap_cosign=src)
        assert proc.returncode == 0, proc.stderr
        assert not (dest_dir / "cosign").exists()
        assert "WARN:" in proc.stderr


class TestWiring:
    def test_bootstrap_exports_verified_binary_path(self) -> None:
        code = "\n".join(
            line
            for line in _BOOTSTRAP.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "export HAL0_BOOTSTRAP_COSIGN=" in code

    def test_install_sh_calls_persist_outside_dev_mode(self) -> None:
        text = _INSTALL_SH.read_text(encoding="utf-8")
        assert "persist_bootstrap_cosign" in text
        # The call must come before any filesystem mutation of the install
        # proper — i.e. in the pre-flight region, after the bootstrap-prereq
        # parity check.
        assert text.index("persist_bootstrap_cosign") < text.index("GPU / NPU device gate")
