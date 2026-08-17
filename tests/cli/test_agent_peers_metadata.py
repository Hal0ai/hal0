"""``hal0 agent peers`` survives JSON-string card metadata (#1897 spin-off).

Found while cross-checking the agents-bank replay defect on ct150: a card
whose ``hal0_state`` came back from the memory engine as a JSON *string*
instead of a dict crashed the whole listing with
``AttributeError: 'str' object has no attribute 'get'``.
"""

from __future__ import annotations

import io
import json
import urllib.request
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import agent_commands

runner = CliRunner()


def _fake_response(payload: dict[str, Any]) -> Any:
    class _Resp(io.BytesIO):
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

    return _Resp(json.dumps(payload).encode("utf-8"))


@pytest.fixture
def peers_response(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(payload: dict[str, Any]) -> None:
        monkeypatch.setattr(agent_commands, "_api_base", lambda: "http://127.0.0.1:8080")
        monkeypatch.setattr(agent_commands, "_api_unreachable", lambda url: False)
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda req, timeout=None: _fake_response(payload)
        )

    return _install


def test_peers_renders_json_string_metadata(peers_response: Any) -> None:
    peers_response(
        {
            "items": [
                {
                    "id": "card-1",
                    "metadata": {
                        "agent_id": "hermes",
                        "display_name": "Hermes",
                        "roles": ["orchestrator"],
                        "endpoint": json.dumps({"url": "http://10.0.1.142:8080"}),
                        "hal0_state": json.dumps({"registered_at": "2026-08-15T00:00:00Z"}),
                    },
                }
            ]
        }
    )

    result = runner.invoke(agent_commands.app, ["peers"])

    assert result.exit_code == 0, result.output
    assert "hermes" in result.output
    assert "2026-08-15" in result.output


def test_peers_tolerates_unparseable_metadata(peers_response: Any) -> None:
    peers_response(
        {
            "items": [
                {
                    "id": "card-2",
                    "metadata": {
                        "agent_id": "ghost",
                        "hal0_state": "not json at all",
                        "endpoint": None,
                        "roles": "solo",
                    },
                }
            ]
        }
    )

    result = runner.invoke(agent_commands.app, ["peers"])

    assert result.exit_code == 0, result.output
    assert "ghost" in result.output
    assert "solo" in result.output
