"""TTS request-default injection + voices proxy (Settings → Voice, Phase 3).

``/v1/audio/speech`` seeds ``voice`` / ``speed`` / ``response_format`` from
the serving tts slot's persisted ``default_*`` fields when the request body
omits them — explicit request params always win. ``GET
/api/slots/{name}/voices`` proxies the engine's ``/v1/audio/voices`` and
fails soft to ``{"voices": [], "source": "offline"}`` on a cold slot.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes.v1 import _seed_tts_defaults, _tts_slot_config

# ── _seed_tts_defaults ───────────────────────────────────────────────────────


def test_seed_fills_omitted_params() -> None:
    body: dict = {"model": "kokoro-v1", "input": "hi"}
    cfg = {"default_voice": "bf_emma", "default_speed": 1.25, "default_response_format": "wav"}
    _seed_tts_defaults(cfg, body)
    assert body["voice"] == "bf_emma"
    assert body["speed"] == 1.25
    assert body["response_format"] == "wav"


def test_seed_never_overrides_explicit_params() -> None:
    body: dict = {"voice": "am_adam", "speed": 2.0, "response_format": "pcm"}
    cfg = {"default_voice": "bf_emma", "default_speed": 1.25, "default_response_format": "wav"}
    _seed_tts_defaults(cfg, body)
    assert body["voice"] == "am_adam"
    assert body["speed"] == 2.0
    assert body["response_format"] == "pcm"


def test_seed_skips_unset_defaults() -> None:
    body: dict = {"model": "kokoro-v1"}
    _seed_tts_defaults({"default_voice": None, "default_speed": None}, body)
    assert "voice" not in body
    assert "speed" not in body
    assert "response_format" not in body


def test_seed_ignores_bool_speed() -> None:
    # bool is an int subclass — a corrupted TOML must not inject speed=True.
    body: dict = {}
    _seed_tts_defaults({"default_speed": True}, body)
    assert "speed" not in body


# ── _tts_slot_config selection ───────────────────────────────────────────────


def _request_with_cfgs(cfgs: list[dict]) -> SimpleNamespace:
    async def iter_configs() -> list[dict]:
        return cfgs

    manager = SimpleNamespace(iter_configs=iter_configs)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(slot_manager=manager)))


@pytest.mark.asyncio
async def test_tts_slot_prefers_model_match() -> None:
    cfgs = [
        {"name": "tts", "type": "tts", "model": {"default": "kokoro-v1"}, "default_voice": "a"},
        {
            "name": "qwen3tts",
            "type": "tts",
            "model": {"default": "qwen3-tts"},
            "default_voice": "b",
        },
    ]
    cfg = await _tts_slot_config(_request_with_cfgs(cfgs), "qwen3-tts")
    assert cfg["default_voice"] == "b"


@pytest.mark.asyncio
async def test_tts_slot_falls_back_to_default_then_name() -> None:
    cfgs = [
        {"name": "other", "type": "tts", "default": True, "default_voice": "flagged"},
        {"name": "tts", "type": "tts", "default_voice": "named"},
    ]
    cfg = await _tts_slot_config(_request_with_cfgs(cfgs), "unknown-model")
    assert cfg["default_voice"] == "flagged"


@pytest.mark.asyncio
async def test_tts_slot_empty_when_no_tts_slots() -> None:
    cfgs = [{"name": "chat", "type": "llm"}]
    assert await _tts_slot_config(_request_with_cfgs(cfgs), "kokoro-v1") == {}


# ── GET /api/slots/{name}/voices ─────────────────────────────────────────────


@pytest.fixture
def isolated_app(tmp_hal0_home: str) -> FastAPI:
    return create_app()


@pytest.fixture
def isolated_client(isolated_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(isolated_app) as c:
        yield c


def _seed_tts_slot(home: str, port: int = 8084) -> None:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "tts.toml").write_text(
        "\n".join(
            [
                'name = "tts"',
                f"port = {port}",
                'type = "tts"',
                'provider = "kokoro"',
                "[model]",
                'default = "kokoro-v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_voices_offline_slot_fails_soft(tmp_hal0_home: str, isolated_client: TestClient) -> None:
    # Nothing listens on the seeded port in tests — the proxy must degrade
    # to an empty list, not surface a 502.
    _seed_tts_slot(tmp_hal0_home)
    r = isolated_client.get("/api/slots/tts/voices")
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "tts", "voices": [], "source": "offline"}


def test_voices_unknown_slot_404(tmp_hal0_home: str, isolated_client: TestClient) -> None:
    r = isolated_client.get("/api/slots/nope/voices")
    assert r.status_code == 404, r.text
