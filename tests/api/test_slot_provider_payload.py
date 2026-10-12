"""``/api/slots`` reports the provider the launch dispatch picks (ADR-0029).

The provider comes from the slot profile's runtime family, so the shipped
Kokoro / Qwen3-TTS / ComfyUI slots and a Moonshine STT slot never surface as
``llama-server``. That matters downstream: the hermes STATE.md capability
rollup drops any slot ``_slot_missing_required_model`` flags, and a
self-managed engine (Kokoro, Moonshine, Qwen3-TTS) legitimately has no model.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.agents.hermes_provision import _slot_missing_required_model
from hal0.api import create_app

_SHIPPED = Path(__file__).resolve().parents[2] / "installer" / "etc-hal0" / "slots"

_STT_TOML = """\
name = "stt"
port = 8087
type = "transcription"
device = "cpu"
profile = "moonshine"

[model]
default = ""
"""


@pytest.fixture
def slots_payload(tmp_hal0_home: str) -> Iterator[dict[str, dict[str, Any]]]:
    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    for name in ("tts", "qwen3tts", "img", "agent"):
        shutil.copy(_SHIPPED / f"{name}.toml", slots_dir / f"{name}.toml")
    (slots_dir / "stt.toml").write_text(_STT_TOML, encoding="utf-8")
    app: FastAPI = create_app()
    with TestClient(app) as client:
        r = client.get("/api/slots")
        assert r.status_code == 200, r.text
        yield {e["name"]: e for e in r.json()}


@pytest.mark.parametrize(
    ("slot", "provider"),
    [
        ("tts", "kokoro"),
        ("stt", "moonshine"),
        ("qwen3tts", "qwen3tts"),
        ("img", "comfyui"),
        ("agent", "llama-server"),
    ],
)
def test_api_slots_reports_the_profile_provider(
    slots_payload: dict[str, dict[str, Any]], slot: str, provider: str
) -> None:
    assert slots_payload[slot]["provider"] == provider


@pytest.mark.parametrize("slot", ["tts", "stt", "qwen3tts"])
def test_model_less_self_managed_slot_is_not_missing_a_model(
    slots_payload: dict[str, dict[str, Any]], slot: str
) -> None:
    """The shipped seeds are model-less (WS-E #1107); a self-managed engine
    bakes its model in, so the rollup must keep it."""
    entry = slots_payload[slot]
    assert not entry.get("model_id")
    assert _slot_missing_required_model(entry) is False


def test_model_less_llama_slot_is_missing_a_model(
    slots_payload: dict[str, dict[str, Any]],
) -> None:
    """The contrast: a model-less llama-server slot cannot serve."""
    entry = slots_payload["agent"]
    assert not entry.get("model_id")
    assert _slot_missing_required_model(entry) is True
