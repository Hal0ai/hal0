"""hal0#1890: the FPX-on-wrong-runner guard (hal0#1790) through the REAL
launch/preview path, not a hand-built ``model_info`` dict.

``tests/providers/test_container.py::TestFPXQuantRunnerGuard`` proves
``_guard_fpx_quant_runner`` itself is correct — but every one of those tests
hand-builds ``model_info`` with ``quant="ROCmFP8"`` set directly, which is
exactly why the guard's suite kept passing while it was inert in production:
a pull-registered model (the installer-seeded FPX brain model included)
never got ``quant`` on the actual registry row, only lazily at API-serializer
time. This file drives the real chain instead: ``run_pull`` registers the
model the way hal0 installs it, ``SlotManager._resolve_model_info`` resolves
it the way ``load()``/preview do, and the guard is fed exactly that dict.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from hal0.providers.container import UnprocessableEntity, _guard_fpx_quant_runner
from hal0.registry.pull import make_job, run_pull
from hal0.registry.store import ModelRegistry
from hal0.slots.manager import SlotManager


def _mgr() -> SlotManager:
    # _resolve_model_info only touches the registry — a bare instance
    # (no __init__, no slot config, no provider) is enough to exercise it.
    return SlotManager.__new__(SlotManager)


async def _pull_registered_model_info(model_id: str, hf_file: str) -> dict:
    """Register ``model_id`` the way a real pull install does, then resolve
    it through the exact method the launch/preview paths call."""
    job = make_job(model_id)
    registry = ModelRegistry()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * 4096))
    )
    try:
        await run_pull(
            job,
            hf_repo="Hal0ai/hal0-brain-sft-ROCmFPX-GGUF",
            hf_file=hf_file,
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()
    assert job.state == "completed", f"got {job.state}: {job.error}"
    return await _mgr()._resolve_model_info(model_id)


async def test_resolve_model_info_surfaces_quant_for_pull_registered_model(
    tmp_hal0_home: str,
) -> None:
    """The dict _guard_fpx_quant_runner is actually handed must carry the
    detected quant — not just the API list serializer's lazy backfill."""
    info = await _pull_registered_model_info(
        "hal0-brain-sft-q8-rocmfpx", "hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf"
    )
    assert info.get("quant") == "ROCmFP8"


async def test_guard_refuses_pull_registered_fpx_model_on_cpu_runner(
    tmp_hal0_home: str,
) -> None:
    """End-to-end hal0#1790 shape via the real pull → resolve chain: loading
    a pull-installed FPX model on a non-FPX runner must 422, not silently
    pass the guard because ``quant`` was never stored."""
    info = await _pull_registered_model_info(
        "hal0-brain-sft-q8-rocmfpx", "hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf"
    )
    cpu_runner = SimpleNamespace(key="cpu")
    with pytest.raises(UnprocessableEntity) as exc_info:
        _guard_fpx_quant_runner({"name": "brain"}, info, cpu_runner)
    assert exc_info.value.code == "slot.unsupported_quant_for_runner"
    assert exc_info.value.details["quant"] == "ROCmFP8"


async def test_guard_allows_pull_registered_fpx_model_on_fpx_runner(
    tmp_hal0_home: str,
) -> None:
    """Sanity: the same pull-registered model on its own runner family
    still launches — the guard is quant/runner-pair specific, not a
    blanket refusal."""
    info = await _pull_registered_model_info(
        "hal0-brain-sft-q8-rocmfpx", "hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf"
    )
    rocmfpx_runner = SimpleNamespace(key="rocmfpx")
    _guard_fpx_quant_runner({"name": "brain"}, info, rocmfpx_runner)  # no raise
