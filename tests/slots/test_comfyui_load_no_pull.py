"""A ComfyUI slot's model is a catalog image model, never a registry pull.

The img slot's ``[model].default`` names a ComfyUI catalog id (``sdxl-lightning``), which
the LLM model registry does not know, so the cache check says "not cached" and ``load()`` used
to flip through PULLING and call the registry pull for it. Weights for a ComfyUI slot land
through ``POST /api/comfyui/models/<id>/install``; the slot only ever serves what is there.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hal0.providers.comfyui import ComfyUIProvider
from hal0.slots._cfg_helpers import _cfg_provider
from hal0.slots.manager import SlotManager, _is_comfyui_cfg
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider

_IMAGE_MODEL = "sdxl-lightning"


@pytest.fixture(autouse=True)
def _comfyui_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """ComfyUI's readiness wait polls ``/system_stats`` on the slot port; no server is behind it."""

    async def ready(self: ComfyUIProvider, port: int) -> None:
        return None

    monkeypatch.setattr(ComfyUIProvider, "wait_ready", ready)


def _write_img_slot(slot_root: Path, *, provider: str) -> None:
    (slot_root / "img.toml").write_text(
        "\n".join(
            [
                'name = "img"',
                'type = "image"' if provider == "comfyui" else 'type = "llm"',
                f'provider = "{provider}"',
                'device = "gpu-rocm"',
                "port = 8188",
                "[model]",
                f'default = "{_IMAGE_MODEL}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


class _PullSpy:
    """A ``pull_runner`` that records the ids it was asked to pull."""

    def __init__(self) -> None:
        self.pulled: list[str] = []

    async def __call__(self, model_id: str) -> None:
        self.pulled.append(model_id)


async def _load_img(spy: _PullSpy) -> tuple[SlotState, list[str]]:
    """Load ``img`` with a cache check that always misses; return its state and what was seen."""
    sm = SlotManager(pull_runner=spy, model_cache_check=lambda _mid: False)
    seen: list[str] = []

    async def consume() -> None:
        async for rec in sm.state_stream():
            seen.append(rec.state.value)
            if rec.state in (SlotState.READY, SlotState.ERROR):
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    snap = await asyncio.wait_for(sm.load("img"), timeout=5.0)
    await asyncio.wait_for(task, timeout=2.0)
    return snap.state, seen


async def test_comfyui_slot_load_skips_registry_pull(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    _write_img_slot(slot_root, provider="comfyui")
    spy = _PullSpy()

    state, seen = await _load_img(spy)

    assert state == SlotState.READY
    assert spy.pulled == [], "a ComfyUI slot's model is not in the registry, so nothing to pull"
    assert "pulling" not in seen
    assert [model for _cfg, model in container_stub.load_calls] != [], "the container still starts"


async def test_llama_slot_still_pulls(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    _write_img_slot(slot_root, provider="llama-server")
    spy = _PullSpy()

    state, seen = await _load_img(spy)

    assert state == SlotState.READY
    assert spy.pulled == [_IMAGE_MODEL]
    assert "pulling" in seen


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({"provider": "comfyui"}, True),
        ({"slot": {"provider": "comfyui"}}, True),
        ({"provider": "llama-server"}, False),
        ({"slot": {"provider": "llama-server"}}, False),
        ({}, False),  # no provider reads as the default llama-server
    ],
)
def test_is_comfyui_cfg_reads_provider_where_the_slot_cfg_helper_does(
    cfg: dict[str, object], expected: bool
) -> None:
    assert _is_comfyui_cfg(cfg) is expected
    assert (_cfg_provider(cfg) == "comfyui") is expected
