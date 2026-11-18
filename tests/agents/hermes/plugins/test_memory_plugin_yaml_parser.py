"""The hal0-memory plugin reads config.yaml with the parser the Hermes venv has (#3803).

The pinned Hermes venv ships ``ruamel.yaml`` and no PyYAML. hal0's own venv has
PyYAML, so every other test of these readers passes through a parser the
plugin never sees in production. These hide PyYAML and run the readers again.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hal0.agents.hermes.plugins.memory_hindsight import provider as prov

_KEY = "hal0ak_" + "k" * 43


@pytest.fixture
def no_pyyaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import yaml`` raise ImportError, as it does in the Hermes venv."""
    monkeypatch.setitem(sys.modules, "yaml", None)
    with pytest.raises(ImportError):
        import yaml  # noqa: F401


def _config(tmp_path: Path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.usefixtures("no_pyyaml")
def test_agent_key_is_read_from_the_profile_record(tmp_path: Path) -> None:
    path = _config(tmp_path, f"hal0:\n  agent_key:\n    agent: hermes\n    key: {_KEY}\n")
    assert prov._read_agent_key(path, "hermes") == _KEY
    assert prov._read_agent_key(path, "hermes__coder") is None


@pytest.mark.usefixtures("no_pyyaml")
def test_agent_key_is_read_from_an_mcp_entry(tmp_path: Path) -> None:
    path = _config(
        tmp_path,
        "mcp_servers:\n  hal0-memory:\n    headers:\n"
        f"      X-hal0-Agent: hermes\n      X-hal0-Agent-Key: {_KEY}\n",
    )
    assert prov._read_agent_key(path, "hermes") == _KEY


@pytest.mark.usefixtures("no_pyyaml")
@pytest.mark.parametrize(
    ("text", "private"),
    [
        ("model:\n  default: x\n", False),
        ("hal0:\n  memory_private: false\n", False),
        ("hal0:\n  memory_private: true\n", True),
        ("", False),
    ],
)
def test_memory_private_follows_the_file(tmp_path: Path, text: str, private: bool) -> None:
    assert prov._read_memory_private(_config(tmp_path, text)) is private


@pytest.mark.usefixtures("no_pyyaml")
def test_a_parse_error_still_fails_closed_without_quoting_the_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _config(tmp_path, "hal0: [1\nAuthorization: Bearer hunter2 }\n")
    with caplog.at_level("WARNING"):
        assert prov._read_memory_private(path) is True
    assert prov._read_agent_key(path, "hermes") is None
    assert "hunter2" not in caplog.text
    assert " at line " in caplog.text
