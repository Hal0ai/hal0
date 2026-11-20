"""An orchestrator-auto-created capability slot carries its ``type`` (#1830).

``CapabilityOrchestrator._ensure_slot_exists`` is the deleted-slot fallback:
deleting the ``rerank`` slot flips its capability selection off, and
re-enabling it from the dashboard recreates the TOML from the selection. It
stamped a ``type`` for the two FLM-trio children only (embed/stt), so a
recreated ``rerank`` slot landed with NO ``type`` key — which meant
``SlotManager.create``'s type-implied profile inference had nothing to key on,
the TOML landed profile-less, ``_spec_provider_for`` fell through to
llama-server with no ``--reranking``, and the slot reported ``state=ready``
while 501ing ``/v1/rerank``: the reported bug verbatim, on a path #1830's fix
was advertised as covering. ``hal0 doctor profiles`` missed it too (its
profile-less capability check keys on the same absent ``type``).

The stamped types match the shipped seeds (``installer/etc-hal0/slots/
rerank.toml`` → ``type = "reranking"``, ``img.toml`` → ``type = "image"``).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.config import CapabilitySelection
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.ports.authority import PortAuthority
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.manager import SlotManager


def _manager(home: Path) -> SlotManager:
    db = home / "hal0.db"
    return SlotManager(
        identity_store=SlotIdentityStore(db_path=db),
        port_authority=PortAuthority(pool=(8081, 8200), db_path=db),
    )


def _read(home: Path, slot: str) -> dict[str, Any]:
    with open(home / "etc" / "hal0" / "slots" / f"{slot}.toml", "rb") as f:
        return tomllib.load(f)


@pytest.mark.parametrize(
    ("slot_name", "device", "expected_type", "expected_profile"),
    [
        ("rerank", "gpu-vulkan", "reranking", "reranking"),
        ("rerank", "cpu", "reranking", "reranking"),
        # The shipped img.toml seed carries ``profile = "comfyui"``; a recreated slot must too.
        # (It used to be None: the generic GPU rule answered the llama profile first, and
        # the slot-type veto dropped it.)
        ("img", "gpu-rocm", "image", "comfyui"),
    ],
)
async def test_autocreated_non_trio_slot_carries_its_type(
    tmp_hal0_home: str,
    slot_name: str,
    device: str,
    expected_type: str,
    expected_profile: str | None,
) -> None:
    home = Path(tmp_hal0_home)
    orch = CapabilityOrchestrator(
        _manager(home), config_path=home / "etc" / "hal0" / "capabilities.toml"
    )

    await orch._ensure_slot_exists(
        slot_name,
        CapabilitySelection(device=device, provider="llama-server", model="", enabled=True),
    )

    raw = _read(home, slot_name)
    assert raw.get("type") == expected_type
    assert raw.get("profile") == expected_profile


async def test_autocreated_embed_slot_keeps_its_trio_type(tmp_hal0_home: str) -> None:
    """HARD INVARIANT: the two FLM-trio children keep the types the NPU
    dispatch gate (``v1._is_npu_trio_request``) reads."""
    home = Path(tmp_hal0_home)
    orch = CapabilityOrchestrator(
        _manager(home), config_path=home / "etc" / "hal0" / "capabilities.toml"
    )

    await orch._ensure_slot_exists(
        "embed",
        CapabilitySelection(device="cpu", provider="llama-server", model="", enabled=True),
    )

    raw = _read(home, "embed")
    assert raw.get("type") == "embedding"
    assert raw.get("profile") == "embedding"


async def test_autocreated_stt_slot_carries_engine_provider_and_reseeds(
    tmp_hal0_home: str,
) -> None:
    """#3860: a first cpu stt enable writes ``provider = "moonshine"``, not the
    ``llama-server`` fallback, so a reseed from the TOML still applies."""
    home = Path(tmp_hal0_home)
    cfg_path = home / "etc" / "hal0" / "capabilities.toml"
    orch = CapabilityOrchestrator(_manager(home), config_path=cfg_path)

    await orch._ensure_slot_exists(
        "stt", CapabilitySelection(device="cpu", provider="", model="", enabled=True)
    )

    raw = _read(home, "stt")
    assert raw.get("provider") == "moonshine"
    assert raw.get("profile") == "moonshine"
    assert raw.get("type") == "transcription"

    # Reseed capabilities.toml from the slot TOMLs and re-apply stt.
    cfg_path.unlink(missing_ok=True)
    reseeded = CapabilityOrchestrator(_manager(home), config_path=cfg_path)
    await reseeded.initialize_if_missing()
    with open(cfg_path, "rb") as f:
        seeded = tomllib.load(f)
    assert seeded["selections"]["voice"]["stt"]["provider"] == "moonshine"
