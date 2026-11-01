"""A foreign ``binary`` on disk ends a load in ERROR, with the reason as its message.

The write seam refuses this state, so only a hand edit reaches it. The manager's
own load path has to surface it rather than leave a unit crash-looping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hal0.providers.container import ContainerProvider
from hal0.slots.manager import SlotManager
from hal0.slots.state import RunnerFamilyMismatch, SlotState
from tests.slots.conftest import FakeContainerProvider


class _RealLoadSync(FakeContainerProvider):
    """Everything fake except the one call under test."""

    def load_sync(self, cfg: dict[str, Any], model_info: dict[str, Any]) -> None:
        ContainerProvider().load_sync(cfg, model_info)


async def test_load_stamps_error_with_the_mismatch_message(
    slot_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (slot_root / "chat.toml").write_text(
        'name = "chat"\ntype = "llm"\ndevice = "gpu-rocm"\nport = 8081\n'
        'profile = "agent"\nbinary = "halogen"\n'
        '[model]\ndefault = "qwen3-4b-q4_k_m"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("hal0.providers.container.container_provider", _RealLoadSync)

    sm = SlotManager()
    with (
        patch("hal0.providers.container._SYSTEMCTL_SEAM") as seam,
        pytest.raises(RunnerFamilyMismatch),
    ):
        await sm.load("chat")

    assert sm.state("chat") is SlotState.ERROR
    record = sm._states[sm._key("chat")]
    assert record.message is not None
    assert "halogen" in record.message
    assert "'agent'" in record.message
    seam.write_quadlet.assert_not_called()
