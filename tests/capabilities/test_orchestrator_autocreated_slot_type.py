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
from hal0.slots.config_write import _cfg_effective_provider
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


@pytest.mark.parametrize(
    ("slot_name", "device", "expected_provider"),
    [
        ("stt", "cpu", "moonshine"),
        ("stt", "npu", "flm"),
        ("tts", "cpu", "kokoro"),
        ("img", "gpu-rocm", "comfyui"),
        ("embed", "cpu", "llama-server"),
        ("rerank", "cpu", "llama-server"),
    ],
)
async def test_autocreated_slot_provider_is_the_engine_the_profile_runs(
    tmp_hal0_home: str, slot_name: str, device: str, expected_provider: str
) -> None:
    """#3860: the provider label written beside the profile is the engine the
    shared type-implied rule picks, never the ``llama-server`` fallback."""
    home = Path(tmp_hal0_home)
    orch = CapabilityOrchestrator(
        _manager(home), config_path=home / "etc" / "hal0" / "capabilities.toml"
    )

    await orch._ensure_slot_exists(
        slot_name, CapabilitySelection(device=device, provider="", model="", enabled=True)
    )

    # A slot copied from its seed (tts, embed, rerank, img) carries no ``provider`` line: the
    # engine is what the seed's profile runs, read through the same effective-provider rule
    # the launch dispatch uses. A synthesised slot (stt) stamps the label itself.
    raw = _read(home, slot_name)
    assert (raw.get("provider") or _cfg_effective_provider(raw)) == expected_provider


async def test_first_stt_enable_then_reseed_still_applies(tmp_hal0_home: str) -> None:
    """A fresh stt.toml reseeds a selection the next apply accepts."""
    home = Path(tmp_hal0_home)
    cfg_path = home / "etc" / "hal0" / "capabilities.toml"
    orch = CapabilityOrchestrator(_manager(home), config_path=cfg_path)
    await orch._ensure_slot_exists(
        "stt", CapabilitySelection(device="cpu", provider="", model="", enabled=True)
    )
    assert _read(home, "stt").get("profile") == "moonshine"

    cfg_path.unlink(missing_ok=True)
    reseeded = CapabilityOrchestrator(_manager(home), config_path=cfg_path)
    await reseeded.initialize_if_missing()
    seeded = tomllib.loads(cfg_path.read_text())
    assert seeded["selections"]["voice"]["stt"]["provider"] == "moonshine"
    result = await reseeded.apply("voice", "stt", {"model": "", "enabled": True})
    assert result["provider"] == "moonshine"


def _write_stale_stt_toml(home: Path) -> None:
    """The file a 1.7.1 / early-1.8.0 box already has: llama-server + moonshine."""
    path = home / "etc" / "hal0" / "slots" / "stt.toml"
    raw = path.read_text()
    raw = raw.replace('provider = "moonshine"', 'provider = "llama-server"')
    assert 'provider = "llama-server"' in raw
    path.write_text(raw)


async def test_stale_llama_server_stt_toml_is_healed_on_reseed(tmp_hal0_home: str) -> None:
    """Existing boxes: a ``llama-server`` stt.toml must not poison a reseed."""
    home = Path(tmp_hal0_home)
    cfg_path = home / "etc" / "hal0" / "capabilities.toml"
    orch = CapabilityOrchestrator(_manager(home), config_path=cfg_path)
    await orch._ensure_slot_exists(
        "stt", CapabilitySelection(device="cpu", provider="", model="", enabled=True)
    )
    _write_stale_stt_toml(home)

    cfg_path.unlink(missing_ok=True)
    reseeded = CapabilityOrchestrator(_manager(home), config_path=cfg_path)
    await reseeded.initialize_if_missing()
    seeded = tomllib.loads(cfg_path.read_text())
    assert seeded["selections"]["voice"]["stt"]["provider"] == "moonshine"
    result = await reseeded.apply("voice", "stt", {"model": "", "enabled": True})
    assert result["provider"] == "moonshine"


async def test_capabilities_toml_already_seeded_with_llama_server_is_healed(
    tmp_hal0_home: str,
) -> None:
    """A capabilities.toml seeded before this fix heals on the next apply, while
    a provider the caller sends explicitly is still refused (#2691)."""
    from hal0.errors import BadRequest

    home = Path(tmp_hal0_home)
    cfg_path = home / "etc" / "hal0" / "capabilities.toml"
    orch = CapabilityOrchestrator(_manager(home), config_path=cfg_path)
    await orch._ensure_slot_exists(
        "stt", CapabilitySelection(device="cpu", provider="", model="", enabled=True)
    )
    cfg_path.unlink(missing_ok=True)
    seeding = CapabilityOrchestrator(_manager(home), config_path=cfg_path)
    await seeding.initialize_if_missing()
    cfg_path.write_text(
        cfg_path.read_text().replace('provider = "moonshine"', 'provider = "llama-server"')
    )

    fresh = CapabilityOrchestrator(_manager(home), config_path=cfg_path)
    result = await fresh.apply("voice", "stt", {"model": "", "enabled": True})
    assert result["provider"] == "moonshine"

    with pytest.raises(BadRequest) as exc:
        await fresh.apply(
            "voice", "stt", {"model": "", "enabled": True, "provider": "llama-server"}
        )
    assert exc.value.code == "capability.no_engine_for_provider"
