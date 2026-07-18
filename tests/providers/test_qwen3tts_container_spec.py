"""Qwen3-TTS (GPU) container spec + dispatch routing.

The GPU sibling of test_kokoro_container_spec.py: same OpenAI TTS contract,
but the spec must emit GPU device nodes + render/video GIDs, a writable
/cache mount, and the MIOpen env — and must NOT set HSA_OVERRIDE.
"""

from typing import Any
from unittest.mock import patch

import pytest

from hal0.providers.container import _render_quadlet_from_plan, _spec_provider_for
from hal0.providers.qwen3tts import Qwen3TTSProvider


def _render_from_spec(token, spec, *, runtime_bin=None, publish_host="127.0.0.1"):
    """Quadlet renderer shim (ignores the legacy runtime_bin kwarg)."""
    return _render_quadlet_from_plan(token, spec, publish_host=publish_host)


_FAKE_DEVICES = ["/dev/kfd", "/dev/dri/renderD128", "/dev/dri/amdgpu"]
_FAKE_GIDS = [993, 44]


@pytest.fixture(autouse=True)
def _pin_model_store(monkeypatch) -> None:
    """This module asserts on the literal /mnt/ai-models mount source —
    pin the resolver to it (ML-3's unified store.store_root() default is
    now paths.models_dir(), not /mnt/ai-models)."""
    monkeypatch.setenv("HAL0_MODEL_STORE", "/mnt/ai-models")


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "qwen3tts",
        "port": 8095,
        "device": "gpu-rocm",
        "type": "tts",
        "runtime": "container",
        "profile": "tts-qwen3",
        "model": {"default": "qwen3-tts"},
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _stub_gpu(tmp_hal0_home: str) -> Any:
    """Deterministic GPU passthrough regardless of the host (CI or real iGPU).

    Depends on ``tmp_hal0_home`` so the ``tts-qwen3`` profile resolves from
    SEED_PROFILES (isolated HAL0_HOME) rather than the host's real
    /etc/hal0/profiles.toml, which may not carry it yet (pre-deploy).
    """
    with (
        patch(
            "hal0.providers.qwen3tts.resolve_gpu_device_paths",
            return_value=list(_FAKE_DEVICES),
        ),
        patch(
            "hal0.providers.qwen3tts.resolve_gpu_group_ids",
            return_value=list(_FAKE_GIDS),
        ),
    ):
        yield


# ── Spec-level assertions ──────────────────────────────────────────────────────


def test_spec_emits_gpu_devices_and_groups() -> None:
    spec = Qwen3TTSProvider().container_spec(_slot_cfg(), {})
    assert spec.devices == _FAKE_DEVICES
    assert spec.group_add == ["993", "44"]


def test_spec_command_carries_port_host_model_path_and_voice() -> None:
    spec = Qwen3TTSProvider().container_spec(_slot_cfg(), {})
    c = spec.command
    assert c[c.index("--port") + 1] == "8095"
    assert c[c.index("--host") + 1] == "0.0.0.0"
    assert (
        c[c.index("--model_path") + 1]
        == "/mnt/ai-models/local/qwen3-tts/Qwen3-TTS-12Hz-1.7B-CustomVoice"
    )
    assert c[c.index("--default_voice") + 1] == "Ryan"
    assert c[c.index("--default_language") + 1] == "Auto"


def test_spec_miopen_env_set_no_hsa_override() -> None:
    spec = Qwen3TTSProvider().container_spec(_slot_cfg(), {})
    assert spec.env.get("MIOPEN_USER_DB_PATH") == "/cache/miopen"
    assert spec.env.get("MIOPEN_CUSTOM_CACHE_DIR") == "/cache/miopen"
    assert spec.env.get("MIOPEN_FIND_MODE") == "FAST"
    # Native gfx1151 perf gotcha: the override must never be baked in.
    assert "HSA_OVERRIDE_GFX_VERSION" not in spec.env


def test_spec_mounts_ro_model_store_and_rw_cache() -> None:
    spec = Qwen3TTSProvider().container_spec(_slot_cfg(), {})
    store = next(m for m in spec.mounts if m.source == "/mnt/ai-models")
    assert store.read_only is True
    assert store.target == "/mnt/ai-models"
    cache = next(m for m in spec.mounts if m.target == "/cache")
    assert cache.read_only is False
    assert cache.source == "/var/lib/hal0/qwen3tts-cache"
    assert cache.selinux == "z"


def test_spec_security_opts_and_loopback_publish() -> None:
    spec = Qwen3TTSProvider().container_spec(_slot_cfg(), {})
    assert "apparmor=unconfined" in spec.security_opt
    assert "seccomp=unconfined" in spec.security_opt
    assert spec.port == 8095
    assert spec.network_mode == ""


def test_cache_dir_env_override() -> None:
    with patch.dict("os.environ", {"HAL0_QWEN3TTS_CACHE": "/tmp/q3cache"}):
        spec = Qwen3TTSProvider().container_spec(_slot_cfg(), {})
    cache = next(m for m in spec.mounts if m.target == "/cache")
    assert cache.source == "/tmp/q3cache"


def test_no_registry_model_path_required() -> None:
    """qwen3-tts is not a GGUF; model_info with NO 'path' must not raise."""
    spec = Qwen3TTSProvider().container_spec(_slot_cfg(), {})  # {} = no 'path'
    assert spec.image == "ghcr.io/hal0ai/hal0-toolbox-qwen3tts:v1"


# ── Renderer integration ───────────────────────────────────────────────────────


def test_renderer_emits_gpu_args_cache_volume_and_miopen_env() -> None:
    spec = Qwen3TTSProvider().container_spec(_slot_cfg(), {})
    unit = _render_from_spec("qwen3tts", spec, runtime_bin="/usr/bin/podman")
    lines = unit.splitlines()

    assert "AddDevice=/dev/kfd" in lines
    assert "AddDevice=/dev/dri/renderD128" in lines
    assert "--group-add 993" in unit
    assert "--group-add 44" in unit
    assert "PublishPort=127.0.0.1:8095:8095" in lines
    assert "Volume=/mnt/ai-models:/mnt/ai-models:ro,z" in lines
    assert "Volume=/var/lib/hal0/qwen3tts-cache:/cache:z" in lines
    assert "Environment=MIOPEN_FIND_MODE=FAST" in lines
    assert "HSA_OVERRIDE_GFX_VERSION" not in unit


def test_slot_port_override_wins() -> None:
    spec = Qwen3TTSProvider().container_spec(_slot_cfg(port=8096), {})
    c = spec.command
    assert c[c.index("--port") + 1] == "8096"
    assert spec.port == 8096


# ── Dispatch routing ───────────────────────────────────────────────────────────


def test_spec_provider_qwen3tts_profile_returns_qwen3tts() -> None:
    """A type=tts slot whose profile resolves to the qwen3tts family gets the
    GPU provider, NOT the Kokoro fallback."""
    result = _spec_provider_for(_slot_cfg())
    assert isinstance(result, Qwen3TTSProvider)


def test_qwen3tts_family_wins_over_generic_tts_fallback() -> None:
    """Family discrimination beats the bare ``type == "tts"`` → Kokoro rule."""
    from hal0.providers.kokoro import KokoroProvider

    qwen = _spec_provider_for({"type": "tts", "profile": "tts-qwen3"})
    kok = _spec_provider_for({"type": "tts", "profile": "tts"})
    assert isinstance(qwen, Qwen3TTSProvider)
    assert isinstance(kok, KokoroProvider)
