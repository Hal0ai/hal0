"""Hindsight interpreter band (3.11-3.14) — installer/lib/preflight.sh and
hal0.memory.engine_upgrade (#1537).

litellm >=1.100 declares ``requires-python >=3.10,<3.15``, so a 3.14
interpreter installs hindsight-api on the plain path. These tests drive the
real shell resolver against FAKE interpreters, and the Python-side resolver
against a fake runner, then pin the removal of the ``--ignore-requires-python``
bypass.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hal0.memory import engine_upgrade

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"

_STUBS = (
    "info() { printf 'INFO: %s\\n' \"$*\"; }\n"
    "warn() { printf 'WARN: %s\\n' \"$*\"; }\n"
    "err()  { printf 'ERR: %s\\n' \"$*\"; }\n"
    "pkg_install_cmd() { :; }\n"
)


def _fake_python(path: Path, version: str) -> None:
    major, minor = version.split(".")
    path.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == "-c" ]]; then\n'
        '  case "$2" in\n'
        f"    *'sys.version_info[:2]'*) echo '{major}.{minor}' ;;\n"
        f"    *'sys.version_info[1]'*) echo '{minor}' ;;\n"
        "    *) exit 1 ;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    path.chmod(0o755)


def _resolve(bin_dir: Path, env_extra: dict[str, str] | None = None):
    script = (
        "set -uo pipefail\n"
        f"{_STUBS}"
        f"source {_PREFLIGHT!s}\n"
        "resolve_hindsight_python; rc=$?\n"
        'echo "RC:${rc}"\n'
        'echo "PY:${HINDSIGHT_PY}"\n'
    )
    env = {"PATH": str(bin_dir), "HOME": str(bin_dir)}
    env.update(env_extra or {})
    proc = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True, env=env)
    out = proc.stdout
    rc = int(out.split("RC:")[1].split()[0])
    py = out.split("PY:")[1].split("\n")[0]
    return rc, py, out


def test_band_is_3_11_to_3_14() -> None:
    text = _PREFLIGHT.read_text()
    assert "HINDSIGHT_PY_MIN_MINOR=11" in text
    assert "HINDSIGHT_PY_MAX_MINOR=14" in text
    assert (engine_upgrade.PY_MIN_MINOR, engine_upgrade.PY_MAX_MINOR) == (11, 14)


def test_default_python314_accepted_on_plain_path(tmp_path: Path) -> None:
    _fake_python(tmp_path / "python3", "3.14")
    rc, py, out = _resolve(tmp_path)
    assert rc == 0, out
    assert py == "python3"
    assert "bypass" not in out
    assert "FALLBACK" not in out


def test_python314_found_on_path_when_default_out_of_band(tmp_path: Path) -> None:
    _fake_python(tmp_path / "python3", "3.10")
    _fake_python(tmp_path / "python3.14", "3.14")
    rc, py, _ = _resolve(tmp_path)
    assert rc == 0
    assert py == "python3.14"


def test_override_in_band_honoured(tmp_path: Path) -> None:
    _fake_python(tmp_path / "python3.14", "3.14")
    rc, py, _ = _resolve(tmp_path, {"HAL0_HINDSIGHT_PYTHON": "python3.14"})
    assert rc == 0
    assert py == "python3.14"


def test_out_of_band_only_host_errors_clearly(tmp_path: Path) -> None:
    _fake_python(tmp_path / "python3", "3.15")
    _fake_python(tmp_path / "python3.10", "3.10")
    rc, py, out = _resolve(tmp_path)
    assert rc == 1
    assert py == ""
    assert "ERR:" in out
    assert "HAL0_HINDSIGHT_PYTHON" in out
    assert "3.11-3.14" in out
    assert "bypass" not in out


def test_out_of_band_override_errors(tmp_path: Path) -> None:
    _fake_python(tmp_path / "python3.10", "3.10")
    rc, _, out = _resolve(tmp_path, {"HAL0_HINDSIGHT_PYTHON": "python3.10"})
    assert rc == 1
    assert "HAL0_HINDSIGHT_PYTHON=python3.10" in out


def test_no_bypass_left_in_installer_or_updater() -> None:
    for path in (
        _PREFLIGHT,
        _INSTALL_SH,
        _REPO_ROOT / "src" / "hal0" / "memory" / "engine_upgrade.py",
    ):
        text = path.read_text()
        # the only allowed mention is none at all
        assert "--ignore-requires-python" not in text, path
        assert "HINDSIGHT_PY_FALLBACK" not in text, path


def test_install_sh_smoke_imports_hindsight_api() -> None:
    assert "-c 'import hindsight_api'" in _INSTALL_SH.read_text()


class _Runner:
    """subprocess.run stand-in: reports per-executable versions."""

    def __init__(self, versions: dict[str, str]) -> None:
        self.versions = versions

    def __call__(self, cmd, **_kw):
        ver = self.versions.get(cmd[0])
        if ver is None:
            raise FileNotFoundError(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, stdout=ver + "\n", stderr="")


@pytest.mark.parametrize(
    "ver,ok", [("3.10", False), ("3.11", True), ("3.14", True), ("3.15", False)]
)
def test_interpreter_in_band(ver: str, ok: bool) -> None:
    assert engine_upgrade._interpreter_in_band(_Runner({"py": ver}), "py") is ok


def test_resolve_interpreter_prefers_python314_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HAL0_HINDSIGHT_PYTHON", raising=False)
    monkeypatch.setattr(
        engine_upgrade.shutil,
        "which",
        lambda n: f"/x/{n}" if n in ("python3.14", "python3.12") else None,
    )
    runner = _Runner({"/x/python3.14": "3.14", "/x/python3.12": "3.12"})
    # venv with no usable base interpreter
    got = engine_upgrade._resolve_interpreter(runner, tmp_path / "nope")
    assert got == "/x/python3.14"


def test_resolve_interpreter_errors_when_nothing_in_band(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HAL0_HINDSIGHT_PYTHON", raising=False)
    monkeypatch.setattr(engine_upgrade.shutil, "which", lambda n: None)
    monkeypatch.setattr(engine_upgrade.os.path, "realpath", lambda p: str(p))
    runner = _Runner({})
    with pytest.raises(subprocess.SubprocessError, match="HAL0_HINDSIGHT_PYTHON"):
        engine_upgrade._resolve_interpreter(runner, tmp_path / "nope")


def test_resolve_interpreter_out_of_band_override_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_HINDSIGHT_PYTHON", "py310")
    with pytest.raises(subprocess.SubprocessError, match="HAL0_HINDSIGHT_PYTHON=py310"):
        engine_upgrade._resolve_interpreter(_Runner({"py310": "3.10"}), tmp_path)


def test_hindsight_failures_are_non_fatal_in_install_sh() -> None:
    text = _INSTALL_SH.read_text()
    assert "no usable Python for the Hindsight memory engine" not in text
    assert "HAL0_SKIP_HINDSIGHT=1\n    fi" in text  # resolver failure skips the block
    block = text[text.index('"${hs_pip}" install "hindsight-api==${HINDSIGHT_PIN}"') :][:1400]
    assert "die " not in block
    assert block.count('rm -rf "${HS_DIR}/.venv"') == 2
