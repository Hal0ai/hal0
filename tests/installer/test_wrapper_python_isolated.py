"""#3007/#3010 — root-run installer scripts must start Python in isolated mode.

sudo keeps the caller's working directory. ``python -c`` and ``python -``
put that directory first on ``sys.path``, so a root interpreter started from
a wrapper without ``-I`` imports whatever module the caller planted there.
Every wrapper under ``installer/wrappers/`` that the ``hal0`` account can run
through sudo is in scope, so the scan covers the whole directory.

#3010 extends the same rule to the scripts an operator runs as root from
whatever directory they happen to be in: ``installer/*.sh`` and
``installer/lib/*.sh``. ``-I`` implies ``-E`` and ``-s``, so none of those
Python snippets may rely on ``PYTHONPATH``/``PYTHONHOME`` or the user site
(they take everything through argv and stdin).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[2] / "installer"
WRAPPERS = INSTALLER / "wrappers"
BENCHCTL = WRAPPERS / "hal0-benchctl"

# An interpreter in command position: a bare or path-qualified python /
# python3 / python3.N, or a variable conventionally holding one ($py, ${PY}).
_INTERP = re.compile(
    r"""(?<![\w./-])
        (?P<interp>
            "?(?:\$\{?\w+\}?)?(?:/[\w./-]*/)?python(?:3(?:\.\d+)?)?"?
          | "?\$\{[0-9]\}"?
          | "?\$\{?(?:\w*py|\w*PY|\w*python|\w*PYTHON|def)\}?"?
        )
        (?=\s+(?P<arg>\S+))
    """,
    re.VERBOSE,
)
# What may precede a command: line start, a separator, `$(`, a keyword, or
# `NAME=value` environment assignments.
_COMMAND_POS = re.compile(
    r"(?:^|[;&|({]|\$\(|\b(?:exec|then|do|else|if)\s|!\s)\s*(?:\w+=(?:\"\$\{\w+\}\"|\"[^\"$]*\"|[^\s\"'$(\\]*)\s+)*$"
)


def _unisolated(text: str) -> list[tuple[int, str]]:
    """(line number, line) for each Python start whose first argument is not -I."""
    bad = []
    for lineno, line in enumerate(text.splitlines(), 1):
        code = line.lstrip()
        if not code or code.startswith("#"):
            continue
        for m in _INTERP.finditer(line):
            if not _COMMAND_POS.search(line[: m.start()]):
                continue  # an argument (`-x "$py"`, `command -v python3`), not a start
            if m.group("arg") != "-I":
                bad.append((lineno, line.strip()))
    return bad


def _starts(text: str) -> int:
    return sum(
        1
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
        for m in _INTERP.finditer(line)
        if _COMMAND_POS.search(line[: m.start()]) and m.group("arg") == "-I"
    )


def test_scanner_flags_unisolated_starts() -> None:
    sample = "\n".join(
        [
            "python3 -c 'import os'",
            'out="$("$py" -c "x")"',
            "/usr/lib/hal0/venv/bin/python - <<'PY'",
            'exec "${PY}" -m hal0.updater.privileged check',
            "# python3 -c 'a comment is not a start'",
            'for py in "$(command -v python3 2>/dev/null || true)"; do',
            "python3 -I -c 'fine'",
            '[[ -n "$py" && -x "$py" ]] || continue',
            "if out=$(python3 -c 'x'); then :; fi",
            "  for py in /usr/lib/hal0/venv/bin/python3 /usr/bin/python3; do",
            '"${VENV_DIR}/bin/python" -m hal0.install.x',
            'HF_TOKEN="${T}" "${VENV_DIR}/bin/python" -m hal0.install.x',
            'ver="$("${hs_py}" -c "x")"',
            '"${VENV_DIR}/bin/python" -I -m hal0.install.x',
            'if "${PY}" - "$a" <<PY; then',
            'printf x | "${PY}" -c "x"',
            'log "using python for the thing"',
            'if ! "${VENV_DIR}/bin/python" -m hal0.agents.x; then',
            'if ! "${VENV_DIR}/bin/python" -I -m hal0.agents.x; then',
            '_py_minor() { "${1}" -c "x"; }',
        ]
    )
    assert [n for n, _ in _unisolated(sample)] == [1, 2, 3, 4, 9, 11, 12, 13, 15, 16, 18, 20]


def test_every_wrapper_starts_python_isolated() -> None:
    offenders = []
    total = 0
    for path in sorted(WRAPPERS.iterdir()):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        total += _starts(text)
        offenders += [f"{path.name}:{n}: {line}" for n, line in _unisolated(text)]
    assert not offenders, "wrapper starts python without -I:\n" + "\n".join(offenders)
    # Guard against a scanner that silently matches nothing: agentenv (2),
    # benchctl (2) and hal0-update (5+) all start python today.
    assert total >= 9


def test_installer_scripts_start_python_isolated() -> None:
    """#3010: the root-run installer scripts, not just the sudo wrappers."""
    paths = sorted([*INSTALLER.glob("*.sh"), *(INSTALLER / "lib").glob("*.sh")])
    assert INSTALLER / "install.sh" in paths
    offenders = []
    total = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        total += _starts(text)
        offenders += [f"{path.relative_to(INSTALLER)}:{n}: {line}" for n, line in _unisolated(text)]
    assert not offenders, "installer starts python without -I:\n" + "\n".join(offenders)
    assert total >= 30  # guard against a scanner that matches nothing


@pytest.mark.parametrize("name", ["hal0-agentenv", "hal0-benchctl", "hal0-update"])
def test_wrapper_leaves_caller_cwd(name: str) -> None:
    text = (WRAPPERS / name).read_text(encoding="utf-8")
    assert re.search(r"^cd /$", text, re.MULTILINE), f"{name} must `cd /` before starting python"


def test_benchctl_ignores_hal0_package_in_caller_cwd(tmp_path: Path) -> None:
    """Behavioural: run the real benchctl resolver from a directory holding a
    planted ``hal0`` package and a real interpreter; the plant must not run."""
    plant = tmp_path / "plant"
    (plant / "hal0" / "config").mkdir(parents=True)
    marker = tmp_path / "HIJACKED"
    payload = f"open({str(marker)!r}, 'w').close()\n"
    (plant / "hal0" / "__init__.py").write_text(payload)
    (plant / "hal0" / "config" / "__init__.py").write_text(payload)
    (plant / "hal0" / "config" / "paths.py").write_text(
        payload + "def model_store_root():\n    return '/hijacked'\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HAL0_BENCH_PYTHON": sys.executable,
        "HAL0_MODEL_STORE": str(tmp_path / "models"),
        # A PYTHONPATH pointing at the plant must be ignored too (-I).
        "PYTHONPATH": str(plant),
    }
    proc = subprocess.run(
        ["bash", str(BENCHCTL), "help"],
        cwd=plant,
        env={**{k: v for k, v in os.environ.items() if k == "LANG"}, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert not marker.exists(), proc.stdout + proc.stderr
    assert "/hijacked" not in proc.stdout + proc.stderr
