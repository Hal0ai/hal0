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
        monkeypatch.setattr("hal0.service_identity.open_url", _fake_urlopen)

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
                        "endpoint": {"url": "http://192.0.2.150:9000"},
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


def test_peers_recovers_python_repr_shaped_card(fake_urlopen) -> None:
    """#1905 review: the REAL wire shape on ct150 is ``str(<dict>)`` — a
    Python repr with single quotes, which ``json.loads`` can never parse
    (the Hindsight retain path flattened nested metadata with ``str(v)``).
    The reader must recover these via ``ast.literal_eval`` so the peer
    registry is readable on exactly the box the issue was filed from, and
    ``roles`` shares the same storage path and failure mode — left
    uncoerced, ``", ".join(<str>)`` renders one cell entry per character.
    """
    fake_urlopen(
        {
            "items": [
                {
                    "metadata": {
                        "agent_id": "hermes-ct150",
                        # Every nested field in the repr shape str(v) produced.
                        "roles": str(["chat", "voice"]),
                        "endpoint": str({"url": "http://h:1"}),
                        "hal0_state": str(
                            {
                                "registered_at": "2026-08-10T00:00:00Z",
                                "bootstrap_version": 1,
                            }
                        ),
                    }
                }
            ]
        }
    )

    result = runner.invoke(agent_commands.app, ["peers"])

    assert result.exit_code == 0, result.output
    assert "hermes-ct150" in result.output
    # Registered and Endpoint recover their data — not "—".
    assert "2026-08-10" in result.output
    assert "http://h:1" in result.output
    # Roles render whole, not exploded per character.
    assert "chat" in result.output
    assert "voice" in result.output
    assert "c, h, a, t" not in result.output


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
                        "endpoint": {"url": "http://192.0.2.105:9000"},
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


def _fact(document_id: str, fact_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """One ``/api/memory/search`` hit: a fact inside an identity-card document."""
    return {
        "id": document_id,
        "document_id": document_id,
        "fact_id": fact_id,
        "metadata": metadata or {},
    }


def test_peers_renders_one_row_per_card_not_per_fact(fake_urlopen) -> None:
    """#3877: search returns one hit per extracted fact, and only some hits
    carry the card metadata. A card must render once, fully populated — not
    as a duplicated row plus rows of ``—``."""
    card = {
        "agent_id": "hermes",
        "display_name": "Hermes",
        "roles": ["chat"],
        "endpoint": {"url": "http://192.0.2.151:9000"},
        "hal0_state": {"registered_at": "2026-11-20T07:43:23Z"},
    }
    fake_urlopen(
        {
            "items": [
                _fact("f964e0c2", "f1", card),
                _fact("f964e0c2", "f2"),
                _fact("f964e0c2", "f3"),
                _fact("f964e0c2", "f4", {"agent_id": "hermes"}),
                _fact("f964e0c2", "f5"),
            ]
        }
    )

    result = runner.invoke(agent_commands.app, ["peers"])

    assert result.exit_code == 0, result.output
    assert "Agent peers (1)" in result.output
    assert result.output.count("hermes") == 1
    assert "—" not in result.output


def test_peers_takes_card_metadata_from_whichever_hit_carries_it(
    fake_urlopen, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The card fields are spread across different facts of the document; the
    row must merge them instead of taking only the first hit's metadata."""
    from rich.console import Console

    monkeypatch.setattr(agent_commands, "console", Console(width=200))
    fake_urlopen(
        {
            "items": [
                _fact("card-a", "f1", {"agent_id": "badger", "display_name": "Badger"}),
                _fact("card-a", "f2", {"endpoint": {"url": "http://192.0.2.7:9000"}}),
                _fact(
                    "card-a",
                    "f3",
                    {
                        "roles": ["chat", "voice"],
                        "hal0_state": {"registered_at": "2026-11-01T00:00:00Z"},
                    },
                ),
            ]
        }
    )

    result = runner.invoke(agent_commands.app, ["peers"])

    assert result.exit_code == 0, result.output
    assert "Agent peers (1)" in result.output
    assert "http://192.0.2.7:9000" in result.output
    assert "2026-11-01" in result.output
    assert "chat, voice" in result.output


def test_peers_header_counts_cards_not_facts(fake_urlopen) -> None:
    """Three card documents — even with six facts between them — are three peers."""
    fake_urlopen(
        {
            "items": [
                _fact("doc-a", "a1", {"agent_id": "alpha"}),
                _fact("doc-a", "a2"),
                _fact("doc-b", "b1", {"agent_id": "beta"}),
                _fact("doc-b", "b2"),
                _fact("doc-b", "b3"),
                _fact("doc-c", "c1", {"agent_id": "gamma"}),
            ]
        }
    )

    result = runner.invoke(agent_commands.app, ["peers"])

    assert result.exit_code == 0, result.output
    assert "Agent peers (3)" in result.output
    assert result.output.count("alpha") == 1
    assert result.output.count("beta") == 1
    assert result.output.count("gamma") == 1
