"""The slot routes refuse a ``[server].env`` that sets a llama-server option."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def chat_slot(tmp_hal0_home: str) -> Path:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "chat.toml"
    path.write_text(
        'name = "chat"\nport = 8081\ndevice = "gpu-rocm"\nprofile = "chat"\n'
        'type = "llm"\n\n[model]\ndefault = "m"\n',
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("name", ["LLAMA_ARG_MODEL", "LLAMA_ARG_AGENT", "LLAMA_API_KEY"])
def test_put_config_refuses_a_llama_option_in_env(
    chat_slot: Path, isolated_client: TestClient, name: str
) -> None:
    before = chat_slot.read_text(encoding="utf-8")
    r = isolated_client.put("/api/slots/chat/config", json={"server": {"env": {name: "x"}}})
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "slot.llama_env_denied"
    assert err["details"]["keys"] == [name]
    assert chat_slot.read_text(encoding="utf-8") == before


def test_put_config_accepts_ordinary_env(chat_slot: Path, isolated_client: TestClient) -> None:
    r = isolated_client.put(
        "/api/slots/chat/config", json={"server": {"env": {"HSA_OVERRIDE_GFX_VERSION": "11.5.1"}}}
    )
    assert r.status_code == 200, r.text


def test_create_refuses_a_llama_option_in_env(
    tmp_hal0_home: str, isolated_client: TestClient
) -> None:
    r = isolated_client.post(
        "/api/slots",
        json={
            "name": "evil",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "model": "m",
            "server": {"env": {"LLAMA_ARG_HF_REPO": "org/model"}},
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.llama_env_denied"
    assert not (Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "evil.toml").exists()
