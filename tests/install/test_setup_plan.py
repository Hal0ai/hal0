"""Tests for ``hal0 setup --plan`` / ``--dry-run`` (issue #1116).

``--plan`` must resolve the SAME ``Selections`` the real run would (via
``load_answers`` or ``build_auto_selections``), print a "will create" table,
and write NOTHING — no slot TOML, no first-run sentinel, no pulls, no
extension installs. It is the safe preview / CI-gate surface (spec §8).
"""

from __future__ import annotations

import socket
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hal0.cli.setup_command import app

runner = CliRunner()


def _write(tmp_path: Path, text: str) -> str:
    p = tmp_path / "hal0-setup.yaml"
    p.write_text(textwrap.dedent(text))
    return str(p)


_GOOD_ANSWERS = """
version: 1
model_store:
  path: /var/lib/hal0/models
slots:
  - capability: chat
    name: chat
    port: 8081
    model_id: auto
  - capability: coder
    name: coder
    port: 8082
    model_id: auto
npu:
  opt_in: auto
gen:
  mode: off
apps:
  openwebui: { enabled: true }
  hermes: { enabled: true }
"""

_BAD_ANSWERS = """
version: 2
model_store:
  path: /var/lib/hal0/models
slots: []
"""


def _fail_if_called(*_a, **_k):
    raise AssertionError("run_install must NOT be called under --plan")


@pytest.fixture(autouse=True)
def _hal0_home(tmp_path, monkeypatch):
    """Hermetic HAL0_HOME so slot/sentinel writes (if any leaked) land in a
    throwaway dir, and existing-slot detection starts empty."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _no_real_apply(monkeypatch):
    """--plan must never reach the apply path. Make that path explode."""
    monkeypatch.setattr("hal0.cli.setup_install.run_install", _fail_if_called)


def test_plan_auto_prints_table_and_writes_nothing(_hal0_home):
    result = runner.invoke(app, ["--plan", "--auto"])

    assert result.exit_code == 0, result.output
    assert "Slots to create" in result.output
    assert "Extensions to enable" in result.output
    assert "nothing was written" in result.output

    # zero writes: no slot TOML, no sentinel, anywhere under HAL0_HOME.
    written = list(_hal0_home.rglob("*"))
    assert written == [] or all(p.is_dir() for p in written), written


def test_plan_dry_run_alias_behaves_identically(_hal0_home):
    result = runner.invoke(app, ["--dry-run", "--auto"])

    assert result.exit_code == 0, result.output
    assert "Slots to create" in result.output


def test_plan_answers_good_file_prints_resolved_slots(tmp_path, _hal0_home):
    path = _write(tmp_path, _GOOD_ANSWERS)

    result = runner.invoke(app, ["--plan", "--answers", path])

    assert result.exit_code == 0, result.output
    assert "chat" in result.output
    assert "coder" in result.output
    assert "Extensions to enable" in result.output


def test_plan_answers_bad_file_exits_nonzero(tmp_path, _hal0_home):
    path = _write(tmp_path, _BAD_ANSWERS)

    result = runner.invoke(app, ["--plan", "--answers", path])

    assert result.exit_code != 0
    assert "version" in result.output


def _bind_free_port() -> socket.socket:
    """Bind an OS-assigned free port and hold it open; caller reads
    ``sock.getsockname()[1]`` and closes it when done."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


def test_plan_detects_port_in_use(tmp_path, _hal0_home):
    sock = _bind_free_port()
    try:
        port = sock.getsockname()[1]
        path = _write(
            tmp_path,
            f"""
            version: 1
            model_store: {{ path: /var/lib/hal0/models }}
            slots:
              - {{ capability: chat, name: chat, port: {port}, model_id: auto }}
            npu: {{ opt_in: false }}
            gen: {{ mode: off }}
            apps: {{ openwebui: {{ enabled: true }}, hermes: {{ enabled: false }} }}
            """,
        )
        result = runner.invoke(app, ["--plan", "--answers", path])
        assert result.exit_code == 0, result.output
        assert "port in use" in result.output
    finally:
        sock.close()


def test_plan_strict_answers_port_in_use_is_an_error(tmp_path, _hal0_home):
    sock = _bind_free_port()
    try:
        port = sock.getsockname()[1]
        path = _write(
            tmp_path,
            f"""
            version: 1
            strict: true
            model_store: {{ path: /var/lib/hal0/models }}
            slots:
              - {{ capability: chat, name: chat, port: {port}, model_id: auto }}
            npu: {{ opt_in: false }}
            gen: {{ mode: off }}
            apps: {{ openwebui: {{ enabled: true }}, hermes: {{ enabled: false }} }}
            """,
        )
        result = runner.invoke(app, ["--plan", "--answers", path])
        assert result.exit_code != 0, result.output
        assert "port in use" in result.output
    finally:
        sock.close()
