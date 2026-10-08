"""Every effective-image seam agrees with what a fixed-family slot launches.

#2234 follow-up (PR #2364 review): a fixed-family slot (ComfyUI, Qwen3-TTS …)
with no ``binary`` launches ``provider.image_ref`` — ``image_pin`` →
``[slots].default_images[family]`` → the family's registry default. The
image pull, ``/api/slots`` status, the drift check and the load-time KFD
preflight all resolved through ``_resolve_image_ref``, which derived a llama
runner (rocmfpx / cpu) from the slot's device instead, so every seam
inspected a different image than the Quadlet launched — with or without a
family default set. These tests pin each seam to ``container_spec``'s ref
for every fixed-family runtime (the shipped ``img``, ``qwen3tts``, ``tts``
and ``flm`` seeds, plus a Moonshine STT slot).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.providers.container import _resolve_image_ref, _spec_provider_for

_OVERRIDE = "ghcr.io/hal0ai/operator-default:0999"


def _img_cfg() -> dict[str, Any]:
    """installer/etc-hal0/slots/img.toml, as loaded."""
    return {
        "name": "img",
        "type": "image",
        "provider": "comfyui",
        "device": "gpu-rocm",
        "runtime": "container",
        "profile": "comfyui",
        "port": 8188,
        "image": {"idle_restore_minutes": 60, "default_size": "1024x1024"},
    }


def _qwen3tts_cfg() -> dict[str, Any]:
    """installer/etc-hal0/slots/qwen3tts.toml, as loaded."""
    return {
        "name": "qwen3tts",
        "type": "tts",
        "device": "gpu-rocm",
        "runtime": "container",
        "profile": "qwen3-tts",
        "port": 8095,
    }


def _tts_cfg() -> dict[str, Any]:
    """installer/etc-hal0/slots/tts.toml (Kokoro), as loaded."""
    return {
        "name": "tts",
        "type": "tts",
        "device": "cpu",
        "runtime": "container",
        "profile": "kokoro",
        "port": 8085,
    }


def _flm_cfg() -> dict[str, Any]:
    """installer/etc-hal0/slots/flm.toml, as loaded."""
    return {
        "name": "flm",
        "type": "llm",
        "device": "npu",
        "runtime": "container",
        "profile": "flm",
        "port": 8088,
        "model": {"default": "qwen3:4b", "context_size": 16384},
    }


def _moonshine_cfg() -> dict[str, Any]:
    """A CPU speech-to-text slot (no shipped seed)."""
    return {
        "name": "stt",
        "type": "transcription",
        "device": "cpu",
        "runtime": "container",
        "profile": "moonshine",
        "port": 8096,
    }


_SLOTS = [
    pytest.param(_img_cfg, "comfyui", id="img-comfyui"),
    pytest.param(_qwen3tts_cfg, "qwen3tts", id="qwen3tts"),
    pytest.param(_tts_cfg, "kokoro", id="tts-kokoro"),
    pytest.param(_flm_cfg, "flm", id="flm"),
    pytest.param(_moonshine_cfg, "moonshine", id="moonshine"),
]
_DEFAULTS = [
    pytest.param(True, id="family-default-set"),
    pytest.param(False, id="no-family-default"),
]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_MODEL_STORE", "/mnt/ai-models")


def _set_family_default(monkeypatch: pytest.MonkeyPatch, family: str, enabled: bool) -> None:
    default_images = {family: _OVERRIDE} if enabled else {}
    fake = SimpleNamespace(slots=SimpleNamespace(default_images=default_images))
    monkeypatch.setattr("hal0.config.loader.load_hal0_config", lambda: fake)


def _launched_image(cfg: dict[str, Any], family: str, with_default: bool) -> str:
    provider = _spec_provider_for(cfg)
    assert provider is not None and provider.name == family
    # Every provider's container_spec renders ``image=self.image_ref(cfg)``;
    # image_ref is called directly so the FLM/Moonshine model-bundle preflights
    # (which need weights on disk) stay out of this test.
    image = provider.image_ref(cfg)
    if with_default:
        assert image == _OVERRIDE
    return image


@pytest.mark.parametrize("with_default", _DEFAULTS)
@pytest.mark.parametrize(("make_cfg", "family"), _SLOTS)
def test_resolve_image_ref_matches_launch(
    monkeypatch: pytest.MonkeyPatch, make_cfg: Any, family: str, with_default: bool
) -> None:
    """The shared entry point every seam calls (drift, runner-images, the
    `_gpu` lane check, brain/doctor) — with and without a profile object."""
    _set_family_default(monkeypatch, family, with_default)
    cfg = make_cfg()
    launched = _launched_image(cfg, family, with_default)
    assert _resolve_image_ref(cfg, None) == launched
    from hal0.profiles import ProfileCatalog

    assert _resolve_image_ref(cfg, ProfileCatalog().resolve(cfg["profile"])) == launched


@pytest.mark.parametrize("with_default", _DEFAULTS)
@pytest.mark.parametrize(("make_cfg", "family"), _SLOTS)
async def test_image_pull_resolves_launched_image(
    monkeypatch: pytest.MonkeyPatch, make_cfg: Any, family: str, with_default: bool
) -> None:
    from hal0.slots.image_pull import resolve_slot_image

    _set_family_default(monkeypatch, family, with_default)
    cfg = make_cfg()
    launched = _launched_image(cfg, family, with_default)
    sm = SimpleNamespace(iter_configs=AsyncMock(return_value=[cfg]))
    assert await resolve_slot_image(sm, cfg["name"]) == launched


@pytest.mark.parametrize("with_default", _DEFAULTS)
@pytest.mark.parametrize(("make_cfg", "family"), _SLOTS)
async def test_slot_view_reports_launched_image(
    monkeypatch: pytest.MonkeyPatch, make_cfg: Any, family: str, with_default: bool
) -> None:
    from hal0.slot_view import container_enrichment

    _set_family_default(monkeypatch, family, with_default)
    cfg = make_cfg()
    launched = _launched_image(cfg, family, with_default)
    provider = MagicMock()
    provider.is_active.return_value = True
    provider.health = AsyncMock(return_value={"ok": True})
    provider.running_image.return_value = launched
    provider.image_present.return_value = True
    out = await container_enrichment([cfg], pull_jobs={}, provider=provider)
    entry = out[cfg["name"]]
    assert entry["image"] == launched
    assert entry["image_mismatch"] is False


@pytest.mark.parametrize("with_default", _DEFAULTS)
@pytest.mark.parametrize(("make_cfg", "family"), _SLOTS)
async def test_drift_sees_no_image_drift_for_launched_image(
    monkeypatch: pytest.MonkeyPatch, make_cfg: Any, family: str, with_default: bool
) -> None:
    from hal0.slots.drift import compute_config_drift

    _set_family_default(monkeypatch, family, with_default)
    cfg = make_cfg()
    launched = _launched_image(cfg, family, with_default)
    provider = MagicMock()
    provider.running_argv.return_value = ["--port", str(cfg["port"])]
    provider.expected_argv.return_value = ["--port", str(cfg["port"])]
    provider.running_image.return_value = launched
    host = MagicMock()
    host._resolve_model_info = AsyncMock(return_value={})
    host._resolve_servable_model = MagicMock(side_effect=lambda m, _c: m)
    with patch("hal0.providers.container.container_provider", return_value=provider):
        drift = await compute_config_drift(host, cfg["name"], cfg=cfg, active=True)
    assert drift is not None
    assert [d for d in drift["diffs"] if d["key"] == "image"] == []


class _StopLoad(Exception):
    pass


@pytest.mark.parametrize("with_default", _DEFAULTS)
@pytest.mark.parametrize(("make_cfg", "family"), _SLOTS)
def test_load_preflight_inspects_launched_image(
    monkeypatch: pytest.MonkeyPatch, make_cfg: Any, family: str, with_default: bool
) -> None:
    from hal0.providers import container as container_mod

    _set_family_default(monkeypatch, family, with_default)
    cfg = make_cfg()
    launched = _launched_image(cfg, family, with_default)
    seen: dict[str, Any] = {}

    def _spy(_slot: str, *, image: str | None = None, **_kw: Any) -> None:
        seen["image"] = image
        raise _StopLoad  # the rest of load_sync writes units

    monkeypatch.setattr(container_mod, "require_kfd_for_gpu_slot", _spy)
    with pytest.raises(_StopLoad):
        container_mod.ContainerProvider().load_sync(cfg, {})
    assert seen["image"] == launched


@pytest.mark.parametrize("with_default", _DEFAULTS)
@pytest.mark.parametrize(
    ("make_cfg", "family"),
    [
        pytest.param(_img_cfg, "comfyui", id="img-comfyui"),
        pytest.param(_qwen3tts_cfg, "qwen3tts", id="qwen3tts"),
        pytest.param(_tts_cfg, "kokoro", id="tts-kokoro"),
    ],
)
def test_container_spec_launches_the_resolved_image(
    monkeypatch: pytest.MonkeyPatch, make_cfg: Any, family: str, with_default: bool
) -> None:
    """The Quadlet's image IS what the seams above compare against."""
    _set_family_default(monkeypatch, family, with_default)
    cfg = make_cfg()
    provider = _spec_provider_for(cfg)
    assert provider.container_spec(cfg, {}).image == _resolve_image_ref(cfg, None)


# #2389: a slot with neither a profile nor an ``image_pin`` still launches a
# fixed-family image when its ``type`` routes it to one (bare ``type=tts`` →
# Kokoro, bare ``type=image`` → ComfyUI). Pull and status must report that
# image, while a bare llama slot keeps its "not-configured" answer (#1226).


def _bare_tts_cfg() -> dict[str, Any]:
    return {"name": "tts", "type": "tts", "device": "cpu", "runtime": "container", "port": 8085}


def _bare_img_cfg() -> dict[str, Any]:
    return {
        "name": "img",
        "type": "image",
        "device": "gpu-rocm",
        "runtime": "container",
        "port": 8188,
    }


def _bare_llama_cfg() -> dict[str, Any]:
    return {"name": "chat", "type": "llm", "device": "cpu", "runtime": "container", "port": 8081}


_BARE_SLOTS = [
    pytest.param(_bare_tts_cfg, "kokoro", id="bare-tts-kokoro"),
    pytest.param(_bare_img_cfg, "comfyui", id="bare-img-comfyui"),
]


def _status_provider(running: str | None) -> MagicMock:
    provider = MagicMock()
    provider.is_active.return_value = running is not None
    provider.health = AsyncMock(return_value={"ok": True})
    provider.running_image.return_value = running
    provider.image_present.return_value = True
    return provider


@pytest.mark.parametrize("with_default", _DEFAULTS)
@pytest.mark.parametrize(("make_cfg", "family"), _BARE_SLOTS)
async def test_image_pull_resolves_profileless_fixed_family_slot(
    monkeypatch: pytest.MonkeyPatch, make_cfg: Any, family: str, with_default: bool
) -> None:
    from hal0.slots.image_pull import resolve_slot_image

    _set_family_default(monkeypatch, family, with_default)
    cfg = make_cfg()
    launched = _launched_image(cfg, family, with_default)
    sm = SimpleNamespace(iter_configs=AsyncMock(return_value=[cfg]))
    assert await resolve_slot_image(sm, cfg["name"]) == launched


@pytest.mark.parametrize("with_default", _DEFAULTS)
@pytest.mark.parametrize(("make_cfg", "family"), _BARE_SLOTS)
async def test_slot_view_reports_profileless_fixed_family_slot(
    monkeypatch: pytest.MonkeyPatch, make_cfg: Any, family: str, with_default: bool
) -> None:
    from hal0.slot_view import container_enrichment

    _set_family_default(monkeypatch, family, with_default)
    cfg = make_cfg()
    launched = _launched_image(cfg, family, with_default)
    out = await container_enrichment([cfg], pull_jobs={}, provider=_status_provider(launched))
    entry = out[cfg["name"]]
    assert entry["image"] == launched
    assert entry["image_mismatch"] is False
    assert entry["image_status"] == "present"


@pytest.mark.parametrize(("make_cfg", "family"), _BARE_SLOTS)
async def test_slot_view_timeout_profileless_fixed_family_slot_is_unknown(
    monkeypatch: pytest.MonkeyPatch, make_cfg: Any, family: str
) -> None:
    """A probe timeout learned nothing about the store; the slot still declares
    an image by its type, so the answer is ``unknown``, not ``not-configured``."""
    from hal0 import slot_view

    _set_family_default(monkeypatch, family, False)
    cfg = make_cfg()
    monkeypatch.setattr(slot_view, "_PROBE_TIMEOUT_S", 0.0)
    out = await slot_view.container_enrichment([cfg], pull_jobs={}, provider=_status_provider(None))
    assert out[cfg["name"]]["image_status"] == "unknown"


async def test_image_pull_bare_llama_slot_resolves_nothing() -> None:
    from hal0.slots.image_pull import resolve_slot_image

    cfg = _bare_llama_cfg()
    assert _spec_provider_for(cfg) is None
    sm = SimpleNamespace(iter_configs=AsyncMock(return_value=[cfg]))
    assert await resolve_slot_image(sm, cfg["name"]) is None


async def test_slot_view_bare_llama_slot_stays_not_configured() -> None:
    from hal0.slot_view import container_enrichment

    cfg = _bare_llama_cfg()
    out = await container_enrichment([cfg], pull_jobs={}, provider=_status_provider(None))
    entry = out[cfg["name"]]
    assert entry["image"] is None
    assert entry["resolved_command"] is None
    assert entry["image_status"] == "not-configured"
