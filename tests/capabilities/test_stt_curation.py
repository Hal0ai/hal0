"""STT curation surfacing (#514).

whisper-large-v3-turbo is a visible STT default that loads via Lemonade's
built-in whisper.cpp recipe. It must show up in the ``stt`` capability
dropdown, while the lower-tier Tiny/Base bundle picks stay hidden
(bundle_only).
"""

from __future__ import annotations

from hal0.capabilities.catalog import models_for_capability
from hal0.registry.curated import CURATED_BY_ID


def test_whisper_large_v3_turbo_is_a_visible_stt_default() -> None:
    entry = CURATED_BY_ID["Whisper-Large-v3-Turbo"]
    assert entry.bundle_only is False
    assert entry.capability == "stt"
    assert entry.recommended_slot == "stt"


def test_stt_dropdown_surfaces_whisper_turbo_not_lower_tiers() -> None:
    rows = models_for_capability("stt", registry=None)
    ids = {row["id"] for row in rows}
    assert "Whisper-Large-v3-Turbo" in ids
    # Tiny/Base remain bundle_only — hidden from the standalone dropdown.
    assert "Whisper-Tiny" not in ids
    assert "Whisper-Base" not in ids


def test_whisper_turbo_offers_a_real_backend() -> None:
    rows = models_for_capability("stt", registry=None)
    whisper = [r for r in rows if r["id"] == "Whisper-Large-v3-Turbo"]
    assert whisper, "whisper-large-v3-turbo not surfaced for stt"
    backends = {b["id"] for b in whisper[0]["backends"]}
    providers = {b["provider"] for b in whisper[0]["backends"]}
    # whispercpp fans out to a real host backend (gpu-vulkan/cpu), not empty.
    assert backends & {"gpu-vulkan", "cpu"}
    assert "whispercpp" in providers
