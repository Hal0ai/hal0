"""RAM-gated primary recommendation + ctx resolution (#512, #513).

Covers:
  * ``recommend_primary_slot`` selects the largest-that-fits curated chat
    model by *unified RAM* (35B-A3B ≥48 GB, 9B 16-48 GB, 4B <16 GB), the
    same RAM signal the bundle picker gates on.
  * The primary slot ctx is resolved from the curated model's GGUF arch
    max (capped per tier), not a flat hard-coded 8192. The MoE/MTP
    primary defaults to a large ctx; small/CPU tiers stay conservative.
"""

from __future__ import annotations

from hal0.config.schema import GPUInfo, HardwareInfo
from hal0.hardware.recommend import (
    _PRIMARY_TIERS,
    _pick_chat_model,
    recommend_primary_slot,
)
from hal0.registry.curated import CURATED_BY_ID


def _amd_uma_host(unified_gb: int) -> HardwareInfo:
    """A Strix-Halo-class AMD UMA host with ``unified_gb`` of unified RAM."""
    mb = unified_gb * 1024
    return HardwareInfo(
        ram_mb=mb,
        ram_available_mb=mb,
        unified_memory_mb=mb,
        gpus=[
            GPUInfo(
                vendor="amd",
                name="Strix Halo",
                vram_mb=512,  # UMA: no discrete carve-out
                vulkan_capable=True,
            )
        ],
    )


def _cpu_only_host(ram_gb: int) -> HardwareInfo:
    mb = ram_gb * 1024
    return HardwareInfo(ram_mb=mb, ram_available_mb=mb, unified_memory_mb=mb, gpus=[])


# ── #512: every tier id is curated (mirrors the drift test) ──────────────────


def test_all_primary_tier_ids_are_curated() -> None:
    missing = [cid for cid, *_ in _PRIMARY_TIERS if cid not in CURATED_BY_ID]
    assert not missing, f"non-curated tier ids: {missing}"


def test_default_tier_set_matches_brief() -> None:
    ids = [cid for cid, *_ in _PRIMARY_TIERS]
    # The three RAM-gated GPU picks must be present and canonical.
    assert "Qwen3.6-35B-A3B-MTP-GGUF" in ids
    assert "qwen3.5-9b" in ids
    assert "qwen3-4b" in ids
    # CPU/MIT fallbacks retained.
    assert "phi3-mini" in ids
    assert "llama32-3b" in ids


# ── #512: RAM-gated selection ────────────────────────────────────────────────


def test_96gb_strix_halo_seeds_35b_a3b() -> None:
    hw = _amd_uma_host(96)
    rec = recommend_primary_slot(hw)
    assert rec["model"]["default"] == "Qwen3.6-35B-A3B-MTP-GGUF"


def test_48gb_seeds_35b_a3b() -> None:
    assert recommend_primary_slot(_amd_uma_host(48))["model"]["default"] == (
        "Qwen3.6-35B-A3B-MTP-GGUF"
    )


def test_32gb_seeds_9b() -> None:
    assert recommend_primary_slot(_amd_uma_host(32))["model"]["default"] == "qwen3.5-9b"


def test_16gb_seeds_9b() -> None:
    assert recommend_primary_slot(_amd_uma_host(16))["model"]["default"] == "qwen3.5-9b"


def test_8gb_seeds_4b() -> None:
    assert recommend_primary_slot(_amd_uma_host(8))["model"]["default"] == "qwen3-4b"


def test_pick_chat_model_thresholds() -> None:
    assert _pick_chat_model(48) == "Qwen3.6-35B-A3B-MTP-GGUF"
    assert _pick_chat_model(47) == "qwen3.5-9b"
    assert _pick_chat_model(16) == "qwen3.5-9b"
    assert _pick_chat_model(15) == "qwen3-4b"
    assert _pick_chat_model(8) == "qwen3-4b"


# ── #513: ctx resolved from curated arch max, not hard-coded 8192 ────────────


def test_moe_primary_gets_large_ctx() -> None:
    rec = recommend_primary_slot(_amd_uma_host(96))
    assert rec["model"]["default"] == "Qwen3.6-35B-A3B-MTP-GGUF"
    # Hybrid SSM+attn KV is tiny - default to the full 131072 window.
    assert rec["model"]["context_size"] == 131072


def test_9b_primary_gets_capped_ctx() -> None:
    rec = recommend_primary_slot(_amd_uma_host(32))
    assert rec["model"]["default"] == "qwen3.5-9b"
    # Dense 9B: cap below the arch max to keep the KV cache predictable.
    ctx = rec["model"]["context_size"]
    assert 0 < ctx <= 131072
    assert ctx <= 32768  # conservative cap for a dense mid-tier on a small box


def test_no_hardcoded_8192_for_moe() -> None:
    rec = recommend_primary_slot(_amd_uma_host(96))
    assert rec["model"]["context_size"] != 8192


def test_cpu_host_gets_small_conservative_model() -> None:
    # No GPU → fall back to a small CPU-friendly pick, never the 35B MoE.
    rec = recommend_primary_slot(_cpu_only_host(64))
    assert rec["model"]["default"] != "Qwen3.6-35B-A3B-MTP-GGUF"
    assert rec["backend"] == "cpu"
