"""OpenWebUI presents the box client key to hal0's /v1.

With auth on, hal0 refuses OpenWebUI's placeholder ``sk-hal0-local``, so its
chat, voice and document features answered 401. When a client key exists,
``write_openwebui_env`` points every hal0-bound key at it and records those
keys as hal0-managed; when it goes away they fall back. Keys whose base URL
an operator re-pointed elsewhere never receive hal0's key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.openwebui.env_writer import dynamic_env_overrides, write_openwebui_env

CLIENT_KEY = "client-key-0123456789abcdefghijklmnopqrstuvw"

_HAL0_BOUND = ("OPENAI_API_KEYS", "AUDIO_STT_OPENAI_API_KEY", "AUDIO_TTS_OPENAI_API_KEY")


@pytest.fixture(autouse=True)
def _no_ambient_client_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_CLIENT_KEY", "unset-by-test")
    monkeypatch.delenv("HAL0_CLIENT_KEY")


def _parse(target: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        out[key] = value
    return out


def _managed(target: Path) -> set[str]:
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.startswith("# hal0-managed:"):
            return {k.strip() for k in line.split(":", 1)[1].split(",") if k.strip()}
    return set()


def _with_api_env(tmp_path: Path, content: str) -> Path:
    (tmp_path / "api.env").write_text(content, encoding="utf-8")
    return tmp_path / "openwebui.env"


def test_no_client_key_keeps_placeholder_and_no_chat_key(tmp_path: Path) -> None:
    target = write_openwebui_env(tmp_path / "openwebui.env")
    env = _parse(target)
    assert env["AUDIO_STT_OPENAI_API_KEY"] == "sk-hal0-local"
    assert env["AUDIO_TTS_OPENAI_API_KEY"] == "sk-hal0-local"
    assert "OPENAI_API_KEYS" not in env


def test_client_key_from_api_env_reaches_every_hal0_bound_key(tmp_path: Path) -> None:
    target = _with_api_env(tmp_path, f"HAL0_BIND_HOST=0.0.0.0\nHAL0_CLIENT_KEY={CLIENT_KEY}\n")
    write_openwebui_env(target, preserve_existing=True)
    env = _parse(target)
    for key in _HAL0_BOUND:
        assert env[key] == CLIENT_KEY, key
    assert set(_HAL0_BOUND) <= _managed(target)


def test_env_var_wins_over_api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The running API's os.environ carries a live rotation before any restart."""
    target = _with_api_env(tmp_path, "HAL0_CLIENT_KEY=stale-on-disk\n")
    monkeypatch.setenv("HAL0_CLIENT_KEY", CLIENT_KEY)
    write_openwebui_env(target, preserve_existing=True)
    assert _parse(target)["OPENAI_API_KEYS"] == CLIENT_KEY


def test_rag_key_follows_client_key_when_rag_block_active(tmp_path: Path) -> None:
    target = _with_api_env(tmp_path, f"HAL0_CLIENT_KEY={CLIENT_KEY}\n")
    overrides = dynamic_env_overrides(
        embed_model_id="nomic-embed",
        image_model_id=None,
        image_workflow_json=None,
        image_workflow_nodes_json=None,
        search_provider=None,
    )
    write_openwebui_env(target, overrides=overrides, preserve_existing=True)
    assert _parse(target)["RAG_OPENAI_API_KEY"] == CLIENT_KEY


def test_repointed_base_url_never_receives_hal0_key(tmp_path: Path) -> None:
    """An operator who sent STT to another service keeps their own key."""
    target = _with_api_env(tmp_path, f"HAL0_CLIENT_KEY={CLIENT_KEY}\n")
    target.write_text(
        "AUDIO_STT_OPENAI_API_BASE_URL=https://stt.example.com/v1\n"
        "AUDIO_STT_OPENAI_API_KEY=operator-stt-key\n",
        encoding="utf-8",
    )
    write_openwebui_env(target, preserve_existing=True)
    env = _parse(target)
    assert env["AUDIO_STT_OPENAI_API_KEY"] == "operator-stt-key"
    assert "AUDIO_STT_OPENAI_API_KEY" not in _managed(target)
    assert env["AUDIO_TTS_OPENAI_API_KEY"] == CLIENT_KEY


def test_key_written_before_repoint_is_withdrawn(tmp_path: Path) -> None:
    """hal0 wrote its key, then the operator re-pointed the URL: the key must
    not be left behind to be sent to the new service."""
    target = _with_api_env(tmp_path, f"HAL0_CLIENT_KEY={CLIENT_KEY}\n")
    write_openwebui_env(target, preserve_existing=True)
    text = target.read_text(encoding="utf-8").replace(
        "AUDIO_STT_OPENAI_API_BASE_URL=http://host.docker.internal:8080/v1",
        "AUDIO_STT_OPENAI_API_BASE_URL=https://stt.example.com/v1",
    )
    target.write_text(text, encoding="utf-8")
    write_openwebui_env(target, preserve_existing=True)
    env = _parse(target)
    assert env["AUDIO_STT_OPENAI_API_BASE_URL"] == "https://stt.example.com/v1"
    assert "AUDIO_STT_OPENAI_API_KEY" not in env


def test_key_removed_from_box_falls_back_to_placeholder(tmp_path: Path) -> None:
    target = _with_api_env(tmp_path, f"HAL0_CLIENT_KEY={CLIENT_KEY}\n")
    write_openwebui_env(target, preserve_existing=True)
    (tmp_path / "api.env").write_text("HAL0_BIND_HOST=0.0.0.0\n", encoding="utf-8")
    write_openwebui_env(target, preserve_existing=True)
    env = _parse(target)
    assert env["AUDIO_STT_OPENAI_API_KEY"] == "sk-hal0-local"
    assert "OPENAI_API_KEYS" not in env
    assert not set(_HAL0_BOUND) & _managed(target)


def test_rotation_replaces_previous_key(tmp_path: Path) -> None:
    target = _with_api_env(tmp_path, "HAL0_CLIENT_KEY=old-key\n")
    write_openwebui_env(target, preserve_existing=True)
    (tmp_path / "api.env").write_text(f"HAL0_CLIENT_KEY={CLIENT_KEY}\n", encoding="utf-8")
    write_openwebui_env(target, preserve_existing=True)
    assert "old-key" not in target.read_text(encoding="utf-8")
    assert _parse(target)["OPENAI_API_KEYS"] == CLIENT_KEY


def _repoint(target: Path, old: str, new: str) -> None:
    text = target.read_text(encoding="utf-8")
    assert old in text, old
    target.write_text(text.replace(old, new), encoding="utf-8")


def test_operator_key_on_repointed_url_survives_render(tmp_path: Path) -> None:
    """hal0 wrote its key, then the operator re-pointed STT at another service
    *and* set that service's key: the next render must leave their key alone,
    because withdrawal is only for the value hal0 itself wrote."""
    target = _with_api_env(tmp_path, f"HAL0_CLIENT_KEY={CLIENT_KEY}\n")
    write_openwebui_env(target, preserve_existing=True)
    assert "AUDIO_STT_OPENAI_API_KEY" in _managed(target)
    _repoint(
        target,
        "AUDIO_STT_OPENAI_API_BASE_URL=http://host.docker.internal:8080/v1",
        "AUDIO_STT_OPENAI_API_BASE_URL=https://api.openai.com/v1",
    )
    _repoint(
        target,
        f"AUDIO_STT_OPENAI_API_KEY={CLIENT_KEY}",
        "AUDIO_STT_OPENAI_API_KEY=sk-operator-openai-key",
    )
    write_openwebui_env(target, preserve_existing=True)
    env = _parse(target)
    assert env["AUDIO_STT_OPENAI_API_BASE_URL"] == "https://api.openai.com/v1"
    assert env["AUDIO_STT_OPENAI_API_KEY"] == "sk-operator-openai-key"
    assert "AUDIO_STT_OPENAI_API_KEY" not in _managed(target)
    # A further render still leaves it alone, with or without a client key.
    write_openwebui_env(target, preserve_existing=True)
    (tmp_path / "api.env").write_text("HAL0_BIND_HOST=0.0.0.0\n", encoding="utf-8")
    write_openwebui_env(target, preserve_existing=True)
    assert _parse(target)["AUDIO_STT_OPENAI_API_KEY"] == "sk-operator-openai-key"


def test_operator_second_chat_connection_keeps_its_key(tmp_path: Path) -> None:
    """The operator added a second Open WebUI connection: the list-form key
    is withdrawn per entry, so the operator's entry is never deleted."""
    target = _with_api_env(tmp_path, f"HAL0_CLIENT_KEY={CLIENT_KEY}\n")
    write_openwebui_env(target, preserve_existing=True)
    assert "OPENAI_API_KEYS" in _managed(target)
    _repoint(
        target,
        "OPENAI_API_BASE_URLS=http://host.docker.internal:8080/v1",
        "OPENAI_API_BASE_URLS=http://host.docker.internal:8080/v1;https://api.openai.com/v1",
    )
    _repoint(
        target,
        f"OPENAI_API_KEYS={CLIENT_KEY}",
        f"OPENAI_API_KEYS={CLIENT_KEY};sk-operator-openai-key",
    )
    write_openwebui_env(target, preserve_existing=True)
    assert _parse(target)["OPENAI_API_KEYS"] == f"{CLIENT_KEY};sk-operator-openai-key"
    assert "OPENAI_API_KEYS" not in _managed(target)
    # The client key going away does not take the operator's line with it.
    (tmp_path / "api.env").write_text("HAL0_BIND_HOST=0.0.0.0\n", encoding="utf-8")
    write_openwebui_env(target, preserve_existing=True)
    assert _parse(target)["OPENAI_API_KEYS"] == f"{CLIENT_KEY};sk-operator-openai-key"


def test_hal0_entry_on_repointed_chat_connection_is_withdrawn(tmp_path: Path) -> None:
    """Per entry: hal0's key on a connection that no longer points at hal0 is
    replaced with the placeholder (keeping the list aligned with its URLs),
    while the operator's own entry stays."""
    target = _with_api_env(tmp_path, f"HAL0_CLIENT_KEY={CLIENT_KEY}\n")
    write_openwebui_env(target, preserve_existing=True)
    _repoint(
        target,
        "OPENAI_API_BASE_URLS=http://host.docker.internal:8080/v1",
        "OPENAI_API_BASE_URLS=https://other.example.com/v1;https://api.openai.com/v1",
    )
    _repoint(
        target,
        f"OPENAI_API_KEYS={CLIENT_KEY}",
        f"OPENAI_API_KEYS={CLIENT_KEY};sk-operator-openai-key",
    )
    write_openwebui_env(target, preserve_existing=True)
    value = _parse(target)["OPENAI_API_KEYS"]
    assert value == "sk-hal0-local;sk-operator-openai-key"
    assert "OPENAI_API_KEYS" not in _managed(target)


@pytest.mark.parametrize(
    ("key_var", "url_line", "new_url_line"),
    [
        (
            "AUDIO_TTS_OPENAI_API_KEY",
            "AUDIO_TTS_OPENAI_API_BASE_URL=http://host.docker.internal:8080/v1",
            "AUDIO_TTS_OPENAI_API_BASE_URL=https://tts.example.com/v1",
        ),
        (
            "OPENAI_API_KEYS",
            "OPENAI_API_BASE_URLS=http://host.docker.internal:8080/v1",
            "OPENAI_API_BASE_URLS=https://chat.example.com/v1",
        ),
    ],
)
def test_unchanged_hal0_key_still_withdrawn_on_repoint(
    tmp_path: Path, key_var: str, url_line: str, new_url_line: str
) -> None:
    """The value-equality guard does not stop the withdrawal it exists for:
    hal0's own, unchanged key never follows a URL away from hal0."""
    target = _with_api_env(tmp_path, f"HAL0_CLIENT_KEY={CLIENT_KEY}\n")
    write_openwebui_env(target, preserve_existing=True)
    assert _parse(target)[key_var] == CLIENT_KEY
    _repoint(target, url_line, new_url_line)
    write_openwebui_env(target, preserve_existing=True)
    assert key_var not in _parse(target)
    assert key_var not in _managed(target)
