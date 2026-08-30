"""uv install/verify coverage in installer/agents/hermes-prereqs.sh — #2124.

``ensure_uv`` installs uv via pipx to a fixed bin dir (production:
``/usr/local/bin``) and used to verify the install with `have_uv` (==
`command -v uv`), which asks ``$PATH`` rather than the location that was
actually written. On a non-login exec context — `pct exec`, `lxc-attach`,
`docker exec`, cloud-init/`runcmd`, a service manager — PATH commonly lacks
that bin dir even though the install landed there just fine (PAM's
`/etc/environment` PATH is never applied), so the script died with a
false "uv could not be installed" error on a box where it plainly was.

These tests exercise the real script end-to-end via subprocess (same
technique as ``test_hermes_prereqs_git.py``), with a minimal fake toolchain
that forces the Python-3.14-only / no-system-3.12 branch (so
``ensure_interpreter_path`` must go through ``ensure_uv``), and drive it
with a PATH that excludes the directory pipx writes `uv` into — reproducing
the reported box's PATH shape (`/sbin:/bin:/usr/sbin:/usr/bin`, no
`/usr/local/bin`) without needing to write into a real system directory:
the script honours an already-exported ``PIPX_BIN_DIR`` (falling back to
``/usr/local/bin`` when unset, per production default) so the test can
redirect the install target to a tmp dir instead.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREREQS = _REPO_ROOT / "installer" / "agents" / "hermes-prereqs.sh"


def _write_exe(path: Path, body: str) -> None:
    path.unlink(missing_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# Only `python3` on PATH (no `python3.12`) and it reports as fully capable
# (venv/pip import fine) — this is the Ubuntu-26.04-with-Python-3.14-only
# shape from the issue: `have_system_hermes_py` (`command -v python3.12`)
# is false, so `ensure_interpreter_path` must fall through to `ensure_uv`.
_FAKE_PYTHON3 = """#!/usr/bin/env bash
if [[ "$1" == "-c" ]]; then
    exit 0
fi
if [[ "$1" == "-m" && "$2" == "pip" ]]; then
    echo "pip 24.0"
    exit 0
fi
exit 0
"""

_FAKE_GIT_WORKING = "#!/usr/bin/env bash\necho 'git version 2.43.0'\nexit 0\n"


@pytest.fixture
def base_bin_dir(tmp_path: Path) -> Path:
    """Real bash + a fake, always-satisfied toolchain minus python3.12/uv —
    only uv/pipx/PATH vary per test.

    Neither python3.12 nor uv is present yet, so the script's "toolchain
    already complete" fast path (`have_system_hermes_py || have_uv`) is
    false and it falls through into the distro-detection + package-install
    section before ever reaching `ensure_interpreter_path` — exactly like a
    real fresh box. `distro_family`/`pkg_mgr` (installer/lib/distro.sh)
    resolve purely off `command -v <manager>` on PATH, so a no-op fake
    `apt-get` is enough to pick the debian branch without needing a real
    `/etc/os-release` (this test runs on any host, including macOS dev
    boxes) — nothing it "installs" is actually exercised since python3/git
    are already seeded below.
    """
    d = tmp_path / "bin"
    d.mkdir()
    for tool in ("bash", "dirname", "cat", "uname", "id", "chmod", "mkdir"):
        found = shutil.which(tool)
        if found:
            os.symlink(found, d / tool)
    _write_exe(d / "python3", _FAKE_PYTHON3)
    _write_exe(d / "git", _FAKE_GIT_WORKING)
    _write_exe(d / "apt-get", "#!/usr/bin/env bash\nexit 0\n")
    # Tests run as a non-root user, so hermes-prereqs.sh's `id -u` check
    # keeps the `sudo ` prefix pkg_install_cmd emits — a passthrough fake
    # sudo keeps the install commands runnable without real privileges.
    _write_exe(d / "sudo", '#!/usr/bin/env bash\nexec "$@"\n')
    return d


def _fake_pipx_that_writes_uv(marker: Path) -> str:
    """A `pipx install uv` fake that drops a working `uv` shim at
    `$PIPX_BIN_DIR/uv` — mirroring real pipx's `--bin-dir` behavior — and
    records that it ran."""
    return f"""#!/usr/bin/env bash
echo "$@" >> {marker}
if [[ "$1" == "install" && "$2" == "uv" ]]; then
    mkdir -p "$PIPX_BIN_DIR"
    cat > "$PIPX_BIN_DIR/uv" <<'UVEOF'
#!/usr/bin/env bash
echo "uv 0.12.7 (x86_64-unknown-linux-gnu)"
UVEOF
    chmod +x "$PIPX_BIN_DIR/uv"
fi
exit 0
"""


class TestUvInstallVerifiedByPathNotPATH:
    def test_uv_install_succeeds_even_when_bin_dir_not_on_path(
        self, base_bin_dir: Path, tmp_path: Path
    ) -> None:
        """The box has no python3.12, so uv is REQUIRED. pipx installs it to
        a dir this process's PATH deliberately excludes (standing in for a
        real box where /usr/local/bin is absent from a non-login PATH) — the
        script must still succeed, because it verifies the file it wrote,
        not `command -v uv`.
        """
        pipx_bin_dir = tmp_path / "pipx-bin"  # NOT added to PATH below
        marker = base_bin_dir / "pipx.called"
        _write_exe(base_bin_dir / "pipx", _fake_pipx_that_writes_uv(marker))

        env = {
            "PATH": str(base_bin_dir),  # excludes pipx_bin_dir entirely
            "PIPX_BIN_DIR": str(pipx_bin_dir),
        }
        proc = subprocess.run(
            ["bash", str(_PREREQS)], env=env, capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "toolchain ready" in proc.stdout
        assert marker.exists(), "pipx was never invoked to install uv"
        assert (pipx_bin_dir / "uv").is_file(), "uv was not written to the pipx bin dir"
        # The old bug: died here even though the file above exists.
        assert "could not be installed" not in proc.stdout + proc.stderr

    def test_uv_still_required_when_pipx_install_actually_fails(
        self, base_bin_dir: Path, tmp_path: Path
    ) -> None:
        """Guard against a fix that stops checking anything: if pipx's
        install genuinely doesn't produce a usable uv, the script must still
        die with the actionable remediation message."""
        pipx_bin_dir = tmp_path / "pipx-bin"
        _write_exe(base_bin_dir / "pipx", "#!/usr/bin/env bash\nexit 0\n")  # no-op, writes nothing

        env = {"PATH": str(base_bin_dir), "PIPX_BIN_DIR": str(pipx_bin_dir)}
        proc = subprocess.run(
            ["bash", str(_PREREQS)], env=env, capture_output=True, text=True, check=False
        )
        assert proc.returncode != 0
        assert "uv could not be installed" in (proc.stdout + proc.stderr)


class TestStaticWiring:
    _TEXT = _PREREQS.read_text(encoding="utf-8")

    def test_ensure_uv_verifies_the_written_path_not_command_dash_v(self) -> None:
        assert 'if [ -x "${UV_PIPX_BIN_DIR}/uv" ]; then' in self._TEXT

    def test_pipx_bin_dir_is_overridable_for_tests_defaults_to_usr_local_bin(self) -> None:
        assert 'UV_PIPX_BIN_DIR="${PIPX_BIN_DIR:-/usr/local/bin}"' in self._TEXT

    def test_have_uv_fast_path_still_path_based(self) -> None:
        # have_uv stays `command -v uv` — correct as the pre-install fast
        # path (a box that already has uv on PATH needs no reinstall); only
        # the post-install verification must not rely on it.
        assert "have_uv() { command -v uv >/dev/null 2>&1; }" in self._TEXT
