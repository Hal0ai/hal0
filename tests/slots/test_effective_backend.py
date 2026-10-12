"""The runtime ``backend`` token comes from the slot's ``device`` (ADR-0029).

A converted slot file carries no ``backend`` key (the profile-centric
conversion drops it) and a fresh slot never had one. Every runtime consumer
that tells an FLM/NPU slot apart by ``Slot.backend`` (NPU capacity, the
``/api/npu`` and hardware routes, the agent STATE.md rollup) relies on the
manager deriving that token from ``device`` alone.
"""

from __future__ import annotations

import pytest

from hal0.slots.config_write import _cfg_effective_backend
from hal0.slots.manager import SlotManager


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ("npu", "flm"),
        ("gpu-rocm", "rocm"),
        ("gpu-vulkan", "vulkan"),
        ("cpu", "cpu"),
    ],
)
def test_effective_backend_is_derived_from_device_alone(device: str, expected: str) -> None:
    assert _cfg_effective_backend({"name": "s", "device": device}) == expected


def test_device_outranks_a_stale_backend_key() -> None:
    assert _cfg_effective_backend({"device": "npu", "backend": "vulkan"}) == "flm"


def test_no_device_and_no_backend_is_unknown() -> None:
    assert _cfg_effective_backend({"name": "s"}) is None


async def test_an_npu_slot_without_a_backend_key_reports_flm(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    await sm.create(
        "npu",
        {
            "name": "npu",
            "port": 8088,
            "type": "llm",
            "device": "npu",
            "profile": "flm",
            "model": {"default": ""},
        },
    )
    cfg = await sm.get_config("npu")
    assert "backend" not in cfg
    slot = await sm.status("npu")
    assert slot.backend == "flm"
