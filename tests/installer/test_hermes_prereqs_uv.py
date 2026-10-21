"""hermes-prereqs.sh no longer installs uv (ADR-0032 decision 9, #2615 PR 2).

uv is a pinned dependency of hal0 itself (``pyproject.toml``) and the Hermes
release builder runs the copy next to hal0's own interpreter
(``Path(sys.executable).parent / "uv"``, ADR-0034 decision 2), so every hal0
install and every ``hal0 update`` delivers it. The old ``ensure_uv`` pipx
install to ``/usr/local/bin`` (and the #2124 PATH-blind verification it
needed) is gone, along with the Python-side pipx lookup.

These tests run the real script end-to-end (same technique as
``test_hermes_prereqs_git.py``) on the box shape that used to need uv: only a
``python3`` (no ``python3.12``) and no uv anywhere. It must now complete
without trying to install uv.
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


def _fake_pipx(marker: Path) -> str:
    return f'#!/usr/bin/env bash\necho "$@" >> {marker}\nexit 0\n'


class TestNoUvPrerequisite:
    def test_a_box_without_python312_or_uv_needs_no_uv_install(self, base_bin_dir: Path) -> None:
        marker = base_bin_dir / "pipx.called"
        _write_exe(base_bin_dir / "pipx", _fake_pipx(marker))
        proc = subprocess.run(
            ["bash", str(_PREREQS)],
            env={"PATH": str(base_bin_dir)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "toolchain already present" in proc.stdout
        assert not marker.exists(), "pipx must not be asked to install uv"
        assert "uv" not in proc.stdout.replace("python venv", "")


class TestStaticWiring:
    _TEXT = _PREREQS.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "gone",
        ["ensure_uv", "UV_PIPX_BIN_DIR", "HAL0_UV_PIPX_BIN_DIR", "pipx install uv", "have_uv"],
    )
    def test_the_uv_install_path_is_gone(self, gone: str) -> None:
        assert gone not in self._TEXT

    def test_python_side_pipx_lookup_is_gone(self) -> None:
        from hal0.agents import hermes_provision as hp

        assert not hasattr(hp, "_UV_PIPX_FALLBACK_BIN")
        assert not hasattr(hp, "_uv_pipx_fallback_bin")
