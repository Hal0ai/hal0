"""The gateway unit normaliser against both upstream unit shapes (ADR-0032 d12, ADR-0034 d5).

Fixtures:

* ``gateway-unit-3ef6bbd2.service`` — the shape ``hermes gateway install
  --system`` writes at the current pin: ``<venv>/bin/python -m
  hermes_cli.main … gateway run`` with ``VIRTUAL_ENV`` and an
  ``ExecStopPost``.
* ``gateway-unit-v0.22.2.service`` — generated at v0.22.2 (d923a5eb) in a
  scratch venv, operator paths replaced with the release layout:
  ``ExecStart``/``ExecStop``/``ExecStopPost`` all run ``"<venv>/bin/python"
  "-I" "-c" "…sys.path.insert(0, '<checkout>')…"``, no ``VIRTUAL_ENV``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.agents import hermes_gateway_unit as gu

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hermes"
ROOT = Path("/var/lib/hal0/hermes")


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "occurrences"),
    [("gateway-unit-3ef6bbd2.service", 4), ("gateway-unit-v0.22.2.service", 6)],
)
def test_every_release_prefix_becomes_current(name: str, occurrences: int) -> None:
    text = _fixture(name)
    assert text.count(f"{ROOT}/releases/") == occurrences
    out = gu.normalise_text(text)
    assert f"{ROOT}/releases/" not in out
    assert gu.remaining_release_paths(out) == []
    assert out.count(f"{ROOT}/current/") == occurrences
    # Nothing but the prefix changes: same line count, same directives.
    assert len(out.splitlines()) == len(text.splitlines())
    assert [ln.split("=", 1)[0] for ln in out.splitlines()] == [
        ln.split("=", 1)[0] for ln in text.splitlines()
    ]


def test_old_shape_keeps_its_module_invocation_and_virtual_env() -> None:
    out = gu.normalise_text(_fixture("gateway-unit-3ef6bbd2.service"))
    assert f"ExecStart={ROOT}/current/venv/bin/python -m hermes_cli.main gateway run" in out
    assert f'Environment="VIRTUAL_ENV={ROOT}/current/venv"' in out


def test_new_shape_rewrites_inside_the_quoted_c_strings() -> None:
    out = gu.normalise_text(_fixture("gateway-unit-v0.22.2.service"))
    for directive in ("ExecStart=", "ExecStop=-", "ExecStopPost=-"):
        line = next(ln for ln in out.splitlines() if ln.startswith(directive))
        assert line.startswith(f'{directive}"{ROOT}/current/venv/bin/python" "-I" "-c"')
        assert f"sys.path.insert(0, '{ROOT}/current/src')" in line


def test_idempotent() -> None:
    once = gu.normalise_text(_fixture("gateway-unit-v0.22.2.service"))
    assert gu.normalise_text(once) == once


def test_a_foreign_path_is_untouched() -> None:
    text = "ExecStart=/opt/other/releases/abc/venv/bin/python -m x\n"
    assert gu.normalise_text(text) == text


class _Run:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kw: Any) -> Any:
        self.calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_rewrites_the_file_then_reloads_and_try_restarts(tmp_path: Path) -> None:
    unit = tmp_path / "hermes-gateway.service"
    unit.write_text(_fixture("gateway-unit-v0.22.2.service"), encoding="utf-8")
    run = _Run()
    result = gu.normalise_gateway_unit(unit, run=run)
    assert result.ok and result.changed
    assert f"{ROOT}/releases/" not in unit.read_text(encoding="utf-8")
    # try-restart, never restart/start: a stopped gateway stays stopped.
    assert run.calls == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "try-restart", "hermes-gateway.service"],
    ]
    assert oct(unit.stat().st_mode & 0o777) == "0o644"


def test_an_already_stable_unit_is_a_no_op(tmp_path: Path) -> None:
    unit = tmp_path / "hermes-gateway.service"
    unit.write_text(gu.normalise_text(_fixture("gateway-unit-3ef6bbd2.service")), encoding="utf-8")
    run = _Run()
    result = gu.normalise_gateway_unit(unit, run=run)
    assert result.ok and not result.changed
    assert run.calls == []


def test_a_missing_unit_is_a_no_op(tmp_path: Path) -> None:
    result = gu.normalise_gateway_unit(tmp_path / "absent.service", run=_Run())
    assert result.ok and not result.changed


def test_main_exits_two_when_a_release_path_remains(tmp_path: Path, monkeypatch) -> None:
    unit = tmp_path / "hermes-gateway.service"
    # A release path without the trailing slash the prefix needs.
    unit.write_text(f"ExecStart={ROOT}/releases/abc\n", encoding="utf-8")
    monkeypatch.setattr(gu.subprocess, "run", _Run())
    assert gu.main([str(unit)]) == 2


def test_main_exits_zero_on_success(tmp_path: Path, monkeypatch) -> None:
    unit = tmp_path / "hermes-gateway.service"
    unit.write_text(_fixture("gateway-unit-3ef6bbd2.service"), encoding="utf-8")
    monkeypatch.setattr(gu.subprocess, "run", _Run())
    assert gu.main([str(unit)]) == 0


def test_module_form_is_runnable(tmp_path: Path) -> None:
    """install.sh runs it as ``python -m hal0.agents.hermes_gateway_unit``."""
    proc = subprocess.run(
        [
            __import__("sys").executable,
            "-m",
            "hal0.agents.hermes_gateway_unit",
            str(tmp_path / "x"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**__import__("os").environ, "PYTHONPATH": str(Path(gu.__file__).parents[2])},
    )
    assert proc.returncode == 0, proc.stderr
