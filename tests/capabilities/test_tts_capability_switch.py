"""voice.tts capability switch — Kokoro (CPU) vs Qwen3-TTS (GPU/ROCm).

The deferred follow-up to #972 (which landed the Qwen3TTSProvider
foundation): wire the picker so an operator can select between the two
TTS engines *within the single ``tts`` slot*. Three surfaces are exercised
here:

  - the catalog enumerates BOTH engines for voice.tts with their legal
    device/backends (``kokoro-v1`` on cpu, ``qwen3-tts`` on gpu-rocm),
  - ``_profile_for_fit`` resolves the right runtime profile per device:
    tts+gpu → ``tts-qwen3`` (Qwen3TTSProvider), tts+cpu → ``tts``
    (KokoroProvider) — NOT the generic llama rocm/vulkan profile,
  - the apply path (SlotConfigStore) rewrites the single ``tts`` slot's
    ``profile`` to match the picked engine, so the selection actually
    swaps which provider the one slot spawns.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from hal0.capabilities import catalog as catalog_mod
from hal0.capabilities.catalog import models_for_capability
from hal0.capabilities.orchestrator import CapabilityOrchestrator

# ── catalog enumeration ──────────────────────────────────────────────────────
#
# The catalog backends carry resolved profile/runtime_family, which come from
# ProfileCatalog reading the on-disk profiles.toml. ``tmp_hal0_home`` (no file
# → built-in SEED_PROFILES, which carry ``tts-qwen3``) isolates these from the
# host's live profiles.toml.


def test_voice_tts_catalog_enumerates_both_engines(tmp_hal0_home: str) -> None:
    rows = models_for_capability("tts", registry=None)
    ids = {row["id"] for row in rows}
    assert "kokoro-v1" in ids, "Kokoro CPU engine missing from voice.tts catalog"
    assert "qwen3-tts" in ids, "Qwen3-TTS GPU engine missing from voice.tts catalog"


def test_kokoro_row_offers_cpu_backend(tmp_hal0_home: str) -> None:
    rows = models_for_capability("tts", registry=None)
    kokoro = next((r for r in rows if r["id"] == "kokoro-v1"), None)
    assert kokoro is not None
    backends = {b["id"]: b for b in kokoro["backends"]}
    assert "cpu" in backends
    assert backends["cpu"]["provider"] == "kokoro"
    # The CPU engine resolves the Kokoro profile, not a llama profile.
    assert backends["cpu"]["runtime_family"] == "kokoro"
    assert backends["cpu"]["profile"] == "tts"


def test_qwen3_row_offers_gpu_rocm_backend(tmp_hal0_home: str) -> None:
    rows = models_for_capability("tts", registry=None)
    qwen = next((r for r in rows if r["id"] == "qwen3-tts"), None)
    assert qwen is not None
    backends = {b["id"]: b for b in qwen["backends"]}
    assert "gpu-rocm" in backends
    assert backends["gpu-rocm"]["provider"] == "qwen3tts"
    # The GPU engine resolves the Qwen3 TTS profile (→ Qwen3TTSProvider),
    # NOT the generic rocm llama profile.
    assert backends["gpu-rocm"]["runtime_family"] == "qwen3tts"
    assert backends["gpu-rocm"]["profile"] == "tts-qwen3"


# ── _profile_for_fit (catalog + orchestrator must agree) ─────────────────────
#
# These resolve a ResolvedProfile through ProfileCatalog (reads profiles.toml),
# so they take ``tmp_hal0_home`` for the same SEED_PROFILES isolation.


def test_catalog_profile_for_fit_tts_gpu_is_qwen3(tmp_hal0_home: str) -> None:
    profile = catalog_mod._profile_for_fit("tts", "gpu-rocm")
    assert profile is not None
    assert profile.name == "tts-qwen3"
    assert profile.runtime_family == "qwen3tts"


def test_catalog_profile_for_fit_tts_cpu_is_kokoro(tmp_hal0_home: str) -> None:
    profile = catalog_mod._profile_for_fit("tts", "cpu")
    assert profile is not None
    assert profile.name == "tts"
    assert profile.runtime_family == "kokoro"


def test_orchestrator_profile_for_fit_tts_gpu_is_qwen3(tmp_hal0_home: str) -> None:
    orch = CapabilityOrchestrator.__new__(CapabilityOrchestrator)
    profile = orch._profile_for_fit("tts", "gpu-rocm")
    assert profile is not None
    assert profile.name == "tts-qwen3"
    assert profile.runtime_family == "qwen3tts"


def test_orchestrator_profile_for_fit_tts_cpu_is_kokoro(tmp_hal0_home: str) -> None:
    orch = CapabilityOrchestrator.__new__(CapabilityOrchestrator)
    profile = orch._profile_for_fit("tts", "cpu")
    assert profile is not None
    assert profile.name == "tts"
    assert profile.runtime_family == "kokoro"


def test_orchestrator_profile_for_fit_non_tts_gpu_unchanged(tmp_hal0_home: str) -> None:
    # Regression guard: non-TTS gpu selections still resolve a llama-server
    # profile — the tts special-case must not leak. `chat` takes the plain
    # rocm base; `embed` takes its dedicated (still llama-server) embed lane.
    orch = CapabilityOrchestrator.__new__(CapabilityOrchestrator)
    assert orch._profile_for_fit("chat", "gpu-rocm").name == "rocm"
    assert orch._profile_for_fit("embed", "gpu-rocm").name == "embed"


# ── tts_profile_for_device (the shared device→profile mapping) ────────────────


def test_tts_profile_for_device_mapping() -> None:
    from hal0.capabilities.catalog import tts_profile_for_device

    assert tts_profile_for_device("cpu") == "tts"
    assert tts_profile_for_device("gpu-rocm") == "tts-qwen3"
    # Any GPU backend resolves the Qwen3 engine; unknown/empty → safe CPU default.
    assert tts_profile_for_device("gpu-vulkan") == "tts-qwen3"
    assert tts_profile_for_device("") == "tts"


# ── apply path: the engine swap lands in the single ``tts`` slot ──────────────


def _read_tts_slot(home: str) -> dict[str, Any]:
    with open(Path(home) / "etc" / "hal0" / "slots" / "tts.toml", "rb") as f:
        return tomllib.load(f)


def _write_tts_slot(home: str, *, device: str, profile: str) -> None:
    """Write the canonical single ``tts`` slot in a known engine state."""
    slots_dir = Path(home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "tts.toml").write_text(
        "\n".join(
            [
                'name = "tts"',
                'type = "tts"',
                f'device = "{device}"',
                'runtime = "container"',
                f'profile = "{profile}"',
                "enabled = true",
                "port = 8084",
                "[model]",
                'default = "kokoro-v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_apply_selecting_qwen3_gpu_swaps_tts_slot_to_qwen3_profile(
    tmp_hal0_home: str,
) -> None:
    """Picking qwen3 / gpu-rocm rewrites the ``tts`` slot's profile to tts-qwen3.

    The slot starts on Kokoro (profile=tts, device=cpu). After the apply the
    SAME slot TOML must carry profile=tts-qwen3, device=gpu-rocm, and
    provider=qwen3tts — so the next spawn routes through Qwen3TTSProvider.
    This is the selection-within-the-single-tts-slot contract.
    """
    from hal0.capabilities.config import CapabilitySelection
    from hal0.slot_config import SlotConfigStore, SlotSelection

    _write_tts_slot(tmp_hal0_home, device="cpu", profile="tts")
    store = SlotConfigStore()
    selection = CapabilitySelection.model_validate(
        {
            "device": "gpu-rocm",
            "provider": "qwen3tts",
            "model": "qwen3-tts",
            "enabled": True,
        }
    )
    cs = store.apply(SlotSelection(slot="voice", child="tts", slot_name="tts", selection=selection))
    store.commit(cs)

    on_disk = _read_tts_slot(tmp_hal0_home)
    assert on_disk["profile"] == "tts-qwen3", f"engine not swapped to qwen3: {on_disk!r}"
    assert on_disk["device"] == "gpu-rocm", on_disk
    assert on_disk["provider"] == "qwen3tts", on_disk
    assert on_disk["model"]["default"] == "qwen3-tts", on_disk


def test_apply_selecting_kokoro_cpu_swaps_tts_slot_back_to_kokoro_profile(
    tmp_hal0_home: str,
) -> None:
    """Picking kokoro / cpu reverts the ``tts`` slot to the Kokoro profile.

    Starting from the GPU engine state, selecting CPU Kokoro must rewrite the
    same slot to profile=tts, device=cpu, provider=kokoro.
    """
    from hal0.capabilities.config import CapabilitySelection
    from hal0.slot_config import SlotConfigStore, SlotSelection

    _write_tts_slot(tmp_hal0_home, device="gpu-rocm", profile="tts-qwen3")
    store = SlotConfigStore()
    selection = CapabilitySelection.model_validate(
        {"device": "cpu", "provider": "kokoro", "model": "kokoro-v1", "enabled": True}
    )
    cs = store.apply(SlotSelection(slot="voice", child="tts", slot_name="tts", selection=selection))
    store.commit(cs)

    on_disk = _read_tts_slot(tmp_hal0_home)
    assert on_disk["profile"] == "tts", f"engine not reverted to kokoro: {on_disk!r}"
    assert on_disk["device"] == "cpu", on_disk
    assert on_disk["provider"] == "kokoro", on_disk


def test_apply_disabled_tts_selection_does_not_rewrite_profile(tmp_hal0_home: str) -> None:
    """A disabled selection flips ``enabled`` but never rewrites the engine.

    Post-SC-1 the store owns ``enabled`` on both transitions, so a disable
    writes ``enabled = false`` — but the profile/device/provider engine fields
    (which only reconcile on an ENABLE) must survive untouched.
    """
    from hal0.capabilities.config import CapabilitySelection
    from hal0.slot_config import SlotConfigStore, SlotSelection

    _write_tts_slot(tmp_hal0_home, device="cpu", profile="tts")
    before = _read_tts_slot(tmp_hal0_home)
    store = SlotConfigStore()
    selection = CapabilitySelection.model_validate(
        {"device": "gpu-rocm", "provider": "qwen3tts", "model": "qwen3-tts", "enabled": False}
    )
    cs = store.apply(SlotSelection(slot="voice", child="tts", slot_name="tts", selection=selection))
    store.commit(cs)
    after = _read_tts_slot(tmp_hal0_home)
    assert after["enabled"] is False, "disable must flip enabled=false"
    # The engine fields never move on a disable — only ``enabled`` may differ.
    assert {k: v for k, v in after.items() if k != "enabled"} == {
        k: v for k, v in before.items() if k != "enabled"
    }, "disabled selection rewrote an engine field"


def test_apply_non_tts_child_does_not_write_profile(tmp_hal0_home: str) -> None:
    """Regression: a non-tts child's slot reconciliation never injects a profile.

    The profile derivation is gated on the tts child only — an embed apply
    must leave the slot TOML's profile untouched (it has none), so the engine
    switch can't leak into other capabilities.
    """
    from hal0.capabilities.config import CapabilitySelection
    from hal0.slot_config import SlotConfigStore, SlotSelection

    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "embed.toml").write_text(
        "\n".join(
            [
                'name = "embed"',
                "port = 8082",
                'device = "gpu-vulkan"',
                'provider = "llama-server"',
                "enabled = true",
                "[model]",
                'default = "nomic-embed-text-v1.5-q8_0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    store = SlotConfigStore()
    selection = CapabilitySelection.model_validate(
        {
            "device": "gpu-rocm",
            "provider": "llama-server",
            "model": "nomic-embed-text-v1.5-q8_0",
            "enabled": True,
        }
    )
    cs = store.apply(
        SlotSelection(slot="embed", child="embed", slot_name="embed", selection=selection)
    )
    store.commit(cs)

    with open(slots_dir / "embed.toml", "rb") as f:
        on_disk = tomllib.load(f)
    assert "profile" not in on_disk, f"embed slot must not gain a profile: {on_disk!r}"
