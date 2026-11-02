"""#3283 — env-only and argv-only unit changes both surface as slot drift.

The halogen runner is configured entirely through ``Environment=`` lines, so
its argv is the constant ``["all"]``. Drift must still show when a tune or
profile edit changes only the environment, and for every runner alike.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from hal0.providers import halogen as halogen_module
from hal0.providers.container import ContainerProvider, _resolve_image_ref
from hal0.slots.drift import compute_config_drift, load_would_restart

CKPT = "/mnt/ai-models/halogen-flash-next/qwen38-flash-next-ht43.hgn"
GGUF = "/mnt/ai-models/qwen.gguf"


class _Host:
    def __init__(self, cfg: dict[str, Any], info: dict[str, Any]) -> None:
        self.cfg, self.info = cfg, info

    async def _is_active(self, slot_name: str) -> bool:
        return True

    async def _maybe_load_config(self, slot_name: str) -> dict[str, Any] | None:
        return self.cfg

    async def _resolve_model_info(self, model_id: str | None) -> dict[str, Any]:
        return self.info

    def _resolve_servable_model(self, model_id: str, cfg: Any) -> str:
        return model_id


def _halogen(**tune: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    cfg: dict[str, Any] = {
        "name": "flash",
        "port": 8099,
        "device": "gpu-rocm",
        "type": "llm",
        "runtime": "container",
        "profile": "halogen",
        "model": {"default": "flash"},
    }
    if tune:
        cfg["tune"] = dict(tune)
    return cfg, {"_model_key": "flash", "path": CKPT, "backends": ["halogen"]}


def _llama(ctx: int) -> tuple[dict[str, Any], dict[str, Any]]:
    cfg: dict[str, Any] = {
        "name": "chat",
        "port": 8080,
        "device": "gpu-vulkan",
        "type": "llm",
        "runtime": "container",
        "model": {"default": "qwen"},
        "tune": {"ctx_size": ctx},
    }
    return cfg, {"_model_key": "qwen", "path": GGUF}


@pytest.fixture(autouse=True)
def _env(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("HAL0_MODEL_STORE", "/mnt/ai-models")
    monkeypatch.setattr(halogen_module, "ensure_shared_dir", lambda path: None)
    with (
        patch("hal0.providers.halogen.resolve_gpu_device_paths", return_value=["/dev/kfd"]),
        patch("hal0.providers.halogen.resolve_gpu_group_ids", return_value=[993]),
    ):
        yield


def _identity(cfg: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    ident = ContainerProvider().expected_identity(cfg, info)
    assert ident and ident["unit_sha"]
    return ident


async def _drift(
    running: tuple[dict[str, Any], dict[str, Any]],
    now: tuple[dict[str, Any], dict[str, Any]],
) -> dict[str, Any] | None:
    """Drift of a container launched from ``running`` against the config ``now``."""
    prov = ContainerProvider()
    run_argv = prov.expected_argv(*running)
    run_ident = _identity(*running)
    with (
        patch("hal0.providers.container.container_provider", return_value=prov),
        patch.object(prov, "running_argv", return_value=run_argv),
        patch.object(prov, "running_identity", return_value=run_ident),
        patch.object(prov, "running_image", return_value=_resolve_image_ref(now[0], None)),
    ):
        return await compute_config_drift(_Host(*now), now[0]["name"])


async def test_halogen_identical_render_is_clean() -> None:
    drift = await _drift(_halogen(ctx_size=32768), _halogen(ctx_size=32768))
    assert drift == {"drifted": False, "diffs": []}


async def test_halogen_env_only_change_is_drift() -> None:
    running, now = _halogen(ctx_size=32768), _halogen(ctx_size=65536)
    prov = ContainerProvider()
    assert prov.expected_argv(*running) == prov.expected_argv(*now) == ["all"]
    drift = await _drift(running, now)
    assert drift and drift["drifted"]
    assert [d["key"] for d in drift["diffs"]] == ["unit"]
    # A unit-only diff is the banner / --restart-slots case, not a load bounce.
    assert not load_would_restart(drift)


async def test_llama_argv_change_is_drift() -> None:
    drift = await _drift(_llama(4096), _llama(8192))
    assert drift and drift["drifted"]
    assert {"--ctx-size"} <= {d["key"] for d in drift["diffs"]}


async def test_llama_identical_render_is_clean() -> None:
    drift = await _drift(_llama(4096), _llama(4096))
    assert drift == {"drifted": False, "diffs": []}
