"""hal0.toml ``[openwebui]`` schema and apply plan."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.api._settings_apply import REGISTRY
from hal0.config.schema import Hal0Config, OpenWebUIConfig


def test_openwebui_defaults() -> None:
    assert Hal0Config().openwebui.hermes_session_from_chat_id is True


def test_openwebui_forbids_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        OpenWebUIConfig(hermes_session_from_chat_idd=True)  # type: ignore[call-arg]


def test_openwebui_apply_class_is_immediate() -> None:
    assert REGISTRY["openwebui.hermes_session_from_chat_id"] == {
        "apply_class": "immediate",
        "services": [],
    }
