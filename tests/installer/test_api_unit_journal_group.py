"""hal0-api.service carries ``SupplementaryGroups=systemd-journal`` (#2435).

``/api/logs`` and the MCP ``logs_tail`` tool shell out to ``journalctl``
inside hal0-api, which runs ``User=hal0``. Without journal read the route
returned an empty 200 on every box. The grant is scoped to the hal0-api
unit's process tree: the installer must not add ``hal0`` to the group in
``/etc/group``, because ``hal0-agent@.service`` shares that uid (ADR-0002)
and would inherit it. No other shipped unit, drop-in or heredoc may carry
it either.

Render through ``hal0.install.api_unit`` with controlled host group lookup.
Also run the installer block to check its warning and template/renderer wiring.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from hal0.install import api_unit

INSTALLER = Path(__file__).resolve().parents[2] / "installer"
INSTALL_SH = INSTALLER / "install.sh"
AGENT_UNIT = INSTALLER / "systemd" / "hal0-agent@.service"
API_TEMPLATE = INSTALLER.parent / api_unit.TEMPLATE_RELPATH

_DIRECTIVE = "SupplementaryGroups=systemd-journal"


def _extract_block() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("# ── hal0-api journal read (#2435)")
    end = text.index('info "wrote ${API_UNIT}"', start)
    return text[start:end]


def _run_installer_block(tmp_path: Path, *, group_exists: bool, repo: Path) -> tuple[str, str]:
    """Run the extracted block; return (rendered unit text, combined output)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    getent = bindir / "getent"
    # Stub getent: succeeds only for `group systemd-journal` when asked to.
    rc = "0" if group_exists else "2"
    getent.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = group ] && [ "$2" = systemd-journal ]; then exit {rc}; fi\n'
        "exit 2\n",
        encoding="utf-8",
    )
    getent.chmod(0o755)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    script = (
        "set -euo pipefail\n"
        'info() { echo "INFO: $*"; }\n'
        'warn() { echo "WARN: $*"; }\n'
        'die() { echo "ERROR: $*" >&2; exit 1; }\n'
        f'PY="{sys.executable}" REPO_ROOT="{repo}"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        'PREFIX=/opt/hal0 CURRENT_LINK="" API_ENV=/etc/hal0/api.env ETC_DIR=/etc/hal0\n'
        "HF_SECRETS_ENV=/etc/hal0/hf.env HAL0_BIN=/opt/hal0/bin/hal0\n" + _extract_block()
    )
    env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '')}"}
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (unit_dir / "hal0-api.service").read_text(encoding="utf-8"), proc.stdout + proc.stderr


def _render_unit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *, group_exists: bool
) -> str:
    def getgrnam(name: str) -> object:
        assert name == "systemd-journal"
        if not group_exists:
            raise KeyError(name)
        return object()

    monkeypatch.setattr("grp.getgrnam", getgrnam)
    assert (
        api_unit.main(
            [
                "render",
                "--template",
                str(API_TEMPLATE),
                "--bin",
                "/opt/hal0/bin/hal0",
                "--workdir",
                "/opt/hal0",
                "--api-env",
                "/etc/hal0/api.env",
                "--etc-dir",
                "/etc/hal0",
                "--hf-secrets-env",
                "/etc/hal0/hf.env",
            ]
        )
        == 0
    )
    return capsys.readouterr().out


def test_installer_renders_through_template_and_renderer(tmp_path: Path) -> None:
    # Instrument copies so merely naming the files in install.sh cannot pass.
    repo = tmp_path / "repo"
    template = repo / api_unit.TEMPLATE_RELPATH
    template.parent.mkdir(parents=True)
    template.write_text(API_TEMPLATE.read_text() + "# template used\n", encoding="utf-8")
    renderer = repo / "src/hal0/install/api_unit.py"
    renderer.parent.mkdir(parents=True)
    renderer.write_text(
        (INSTALLER.parent / "src/hal0/install/api_unit.py")
        .read_text()
        .replace(
            "from __future__ import annotations",
            'from __future__ import annotations\nimport sys\nsys.stdout.write("# renderer used\\n")',
        ),
        encoding="utf-8",
    )
    unit, _out = _run_installer_block(tmp_path, group_exists=True, repo=repo)
    assert "# template used\n" in unit
    assert "# renderer used\n" in unit
    assert "EnvironmentFile=/etc/hal0/api.env\n" in unit
    assert "ExecStart=/opt/hal0/bin/hal0 serve --port ${HAL0_PORT}\n" in unit
    assert "@HAL0_" not in unit


def _service_section(unit: str) -> str:
    m = re.search(r"^\[Service\]\n(.*?)(?=^\[|\Z)", unit, re.MULTILINE | re.DOTALL)
    assert m is not None, "no [Service] section in rendered unit"
    return m.group(1)


def test_rendered_api_unit_carries_journal_group(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    unit = _render_unit(monkeypatch, capsys, group_exists=True)
    service = _service_section(unit)
    assert re.search(rf"^{re.escape(_DIRECTIVE)}$", service, re.MULTILINE), service
    # Sits with the identity block, after User=/Group=.
    assert service.index("User=hal0") < service.index(_DIRECTIVE)


def test_missing_journal_group_still_renders_a_startable_unit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A host without systemd-journal must not get a unit naming an unknown
    group: systemd refuses to start a unit whose SupplementaryGroups= does not
    resolve (status=216/GROUP)."""
    unit = _render_unit(monkeypatch, capsys, group_exists=False)
    warning = _extract_block().split("API_WORKDIR=", 1)[0]
    proc = subprocess.run(
        ["bash", "-c", 'getent() { return 2; }; warn() { echo "WARN: $*"; };\n' + warning],
        capture_output=True,
        text=True,
        check=True,
    )
    out = proc.stdout + proc.stderr
    assert not re.search(r"^SupplementaryGroups=", unit, re.MULTILINE), unit
    assert "User=hal0" in unit
    assert "ExecStart=" in unit
    assert "WARN:" in out and "systemd-journal" in out


def _installer_scripts() -> list[Path]:
    """install.sh plus every shell helper it sources or ships."""
    scripts = [INSTALL_SH, *sorted((INSTALLER / "lib").glob("*.sh"))]
    scripts += sorted(p for p in (INSTALLER / "wrappers").iterdir() if p.is_file())
    assert len(scripts) > 2, scripts
    return scripts


def _code_lines(path: Path) -> list[str]:
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_installer_never_adds_hal0_to_the_journal_group() -> None:
    """The grant is scoped to the hal0-api unit. ``usermod -aG systemd-journal
    hal0`` (or gpasswd) in install.sh or any installer helper would hand the
    group to every hal0-uid process, the bundled agent included (ADR-0002)."""
    for script in _installer_scripts():
        for line in _code_lines(script):
            if re.search(r"\b(usermod|gpasswd|adduser)\b", line):
                assert "journal" not in line, f"{script.name}: {line}"


def test_only_the_api_unit_gets_the_journal_group() -> None:
    """No other unit, drop-in, heredoc or installer helper gets the grant."""
    for path in sorted(INSTALLER.rglob("*")):
        if not path.is_file() or path == API_TEMPLATE:
            continue
        for line in _code_lines(path):
            if re.search(r"SupplementaryGroups\s*=", line):
                assert "journal" not in line.lower(), f"{path}: {line}"
            assert "HAL0_API_JOURNAL_GROUP_LINE" not in line, f"{path}: {line}"


def test_no_shipped_unit_or_dropin_gets_the_journal_group() -> None:
    """``installer/systemd/`` holds the agent template, its hermes drop-in,
    the bench units and hindsight-api. Only the API template may grant the group."""
    files = sorted(p for p in (INSTALLER / "systemd").rglob("*") if p.is_file())
    assert AGENT_UNIT in files
    assert any(p.parent.name == "hal0-agent@hermes.service.d" for p in files), files
    for unit in files:
        if unit == API_TEMPLATE:
            continue
        text = unit.read_text(encoding="utf-8")
        assert "systemd-journal" not in text, unit
        assert not re.search(r"^\s*SupplementaryGroups=", text, re.MULTILINE), unit
