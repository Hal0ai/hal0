"""Tests for ``hal0 agent peers`` (#1905).

Observed on ct150 (upgraded box): the memory API sometimes returns a
card's nested ``metadata.hal0_state`` as a JSON-encoded *string* instead
of an already-parsed object. The old code did
``md.get("hal0_state") or {}`` — a non-empty string is truthy, so the
fallback never fires — and then called ``.get("registered_at")`` on that
string, raising ``AttributeError: 'str' object has no attribute 'get'``.

The HTTP layer is stubbed by monkey-patching ``urllib.request.urlopen``
inside the module — same pattern as ``test_agent_uninstall_memory.py``.
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import agent_commands

runner = CliRunner()


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._buf = BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


@pytest.fixture
def fake_urlopen(monkeypatch: pytest.MonkeyPatch):
    import urllib.request as _urllib

    def _install(payload: dict[str, Any]) -> None:
        def _fake_urlopen(req: Any, timeout: float = 5.0) -> _FakeResponse:
            return _FakeResponse(payload)

        monkeypatch.setattr(_urllib, "urlopen", _fake_urlopen)

    return _install


@pytest.fixture(autouse=True)
def _stub_reachability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_commands, "_api_unreachable", lambda _u: False)
    monkeypatch.setenv("HAL0_API_URL", "http://127.0.0.1:8080")


def test_peers_survives_hal0_state_as_json_string(fake_urlopen) -> None:
    """#1905 regression: a stringified ``hal0_state`` must not crash the
    command — it should render the row with "—" for that field."""
    fake_urlopen(
        {
            "items": [
                {
                    "metadata": {
                        "agent_id": "hermes-ct150",
                        "display_name": "Hermes",
                        "roles": ["chat"],
                        "endpoint": {"url": "http://10.0.1.150:9000"},
                        # The defect: a JSON string instead of a nested object.
                        "hal0_state": json.dumps({"registered_at": "2026-08-10T00:00:00Z"}),
                    }
                }
            ]
        }
    )

    result = runner.invoke(agent_commands.app, ["peers"])

    assert result.exit_code == 0, result.output
    assert "hermes-ct150" in result.output
    assert "2026-08-10" in result.output


def test_peers_malformed_hal0_state_string_degrades_to_dash(fake_urlopen) -> None:
    """A hal0_state string that isn't even valid JSON still must not crash."""
    fake_urlopen(
        {
            "items": [
                {
                    "metadata": {
                        "agent_id": "broken-card",
                        "hal0_state": "not-json-at-all",
                    }
                }
            ]
        }
    )

    result = runner.invoke(agent_commands.app, ["peers"])

    assert result.exit_code == 0, result.output
    assert "broken-card" in result.output


def test_peers_renders_normal_dict_shaped_cards(fake_urlopen) -> None:
    """Precondition: the ordinary (already-parsed dict) shape still works."""
    fake_urlopen(
        {
            "items": [
                {
                    "metadata": {
                        "agent_id": "hermes-ct105",
                        "display_name": "Hermes",
                        "roles": ["chat", "voice"],
                        "endpoint": {"url": "http://10.0.1.105:9000"},
                        "hal0_state": {"registered_at": "2026-08-01T00:00:00Z"},
                    }
                }
            ]
        }
    )

    result = runner.invoke(agent_commands.app, ["peers"])

    assert result.exit_code == 0, result.output
    assert "hermes-ct105" in result.output
    assert "2026-08-01" in result.output
