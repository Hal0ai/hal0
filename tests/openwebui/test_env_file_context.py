"""The ``file_context`` capability pin survives the trip to the container.

Open WebUI 0.13 gated the files/RAG handler behind the model capability
``capabilities.file_context`` and flipped that capability's default to off
for an ordinary web chat (native function calling plus a session id). hal0's
Hindsight Filter injects the recalled memory through exactly that handler
(ADR-0049), so on 0.13 the injected context would silently stop reaching
the model. hal0 therefore pins the capability on through Open WebUI's own
``DEFAULT_MODEL_METADATA`` environment variable (ADR-0059).

The value is a JSON object, so it has to arrive at the container byte for
byte: the file is read by podman's ``--env-file`` and by systemd's
``EnvironmentFile=``, and both parsers have their own idea of what a value
is. These tests read the rendered file with a re-implementation of each
parser and assert the JSON survives both, and that it says what it says.
"""

from __future__ import annotations

import json
from pathlib import Path

from hal0.openwebui.env_writer import default_openwebui_env, write_openwebui_env
from tests.openwebui._podman_env import parse_podman_env
from tests.openwebui.test_env_bind_host_systemd import parse_systemd_env

KEY = "DEFAULT_MODEL_METADATA"


def test_the_shipped_default_pins_the_file_context_capability() -> None:
    """The default is Open WebUI's own metadata shape, compact, one line."""
    value = default_openwebui_env()[KEY]
    assert value == '{"capabilities":{"file_context":true}}'
    assert "\n" not in value
    assert json.loads(value) == {"capabilities": {"file_context": True}}


def test_the_pin_reaches_the_container_through_podman(tmp_path: Path) -> None:
    target = write_openwebui_env(tmp_path / "openwebui.env")
    env = parse_podman_env(target.read_text(encoding="utf-8"))
    assert env[KEY] == '{"capabilities":{"file_context":true}}'
    assert json.loads(env[KEY])["capabilities"]["file_context"] is True


def test_the_pin_reaches_the_container_through_systemd(tmp_path: Path) -> None:
    """systemd reads the same file, so its parser must not mangle the JSON."""
    target = write_openwebui_env(tmp_path / "openwebui.env")
    env = parse_systemd_env(target.read_text(encoding="utf-8"))
    assert env[KEY] == '{"capabilities":{"file_context":true}}'
    assert json.loads(env[KEY])["capabilities"]["file_context"] is True


def test_an_operator_hand_edit_wins_over_the_shipped_default(tmp_path: Path) -> None:
    """hal0 owns this key only as a shipped default, so a hand edit sticks."""
    target = tmp_path / "openwebui.env"
    target.write_text(f'{KEY}=' + '{"capabilities":{"file_context":false}}\n', encoding="utf-8")
    write_openwebui_env(target, preserve_existing=True)
    env = parse_podman_env(target.read_text(encoding="utf-8"))
    assert json.loads(env[KEY])["capabilities"]["file_context"] is False
