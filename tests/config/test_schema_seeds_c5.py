"""Tests for Phase C5 — rerank + utility seed TOMLs and reranker defaults."""

import tomllib
from collections import Counter
from pathlib import Path

import pytest

from hal0.config.schema import MemoryEmbeddingConfig, SlotConfig
from hal0.install.static_seeds import STATIC_SEED_SLOTS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEEDED_SLOTS_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"


def test_seed_rerank_toml_validates() -> None:
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / "rerank.toml").read_text(encoding="utf-8"))
    slot = SlotConfig.model_validate(raw)
    assert slot.runtime == "container"
    assert slot.profile == "reranking"
    assert slot.device == "gpu-vulkan"
    assert slot.port == 8086  # drifted from 8083 per spec §5.4 (port fix for _SETUP_SLOTS[embed] conflict)
    # Per spec §4.3 / spec-hw-slot-ownership §10: --reranking is a
    # profile-owned capability toggle (profile.reranking.flags), NOT a
    # slot [server].extra_args (the extra_args slot field is
    # HAL0-SUNSET and inert at launch).  SlotConfig.server validates
    # that extra_args is None (clean seed — no stale slot overrides).
    assert slot.server.extra_args is None
    # Clean seed (WS-E, #1107): no model pin — boots grey, no surprise download.
    assert slot.model.default == ""
    assert slot.enabled is False


def test_seed_utility_toml_validates() -> None:
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / "utility.toml").read_text(encoding="utf-8"))
    slot = SlotConfig.model_validate(raw)
    assert slot.runtime == "container"
    assert slot.profile == "chat"
    assert slot.device == "gpu-vulkan"
    # 8090: 8081 was reclaimed as the `agent` seed's canonical primary port
    # (ADR-0023 LLM anchor); utility moved off it on new installs.
    assert slot.port == 8090
    # Clean seed (WS-E, #1107): the ghost id `gemma-4-12b-it` pin is gone — the
    # slot boots grey (no crash-loop, no surprise download). context_size is a
    # tuning default that applies once the operator assigns a model.
    assert slot.model.default == ""
    assert slot.model.context_size == 65536
    assert slot.enabled is False


def test_rerank_defaults_are_hindsight_era() -> None:
    cfg = MemoryEmbeddingConfig()
    # Gateway = hal0-api's own OpenAI surface; the dispatcher routes
    # /v1/rerankings to the rerank slot.
    assert cfg.rerank_gateway_url == "http://127.0.0.1:8080"
    assert cfg.rerank_model == "builtin.jina-reranker-v1-tiny-en-q8"


def test_cognee_era_embedding_keys_are_dropped_silently() -> None:
    # extra="ignore": a stale hal0.toml carrying the removed cognee-era keys
    # must load cleanly and shed them (gone on next save) instead of failing
    # validation — same migration path ADR-0023 used for [memory.graph].
    cfg = MemoryEmbeddingConfig.model_validate(
        {
            "model": "BAAI/bge-small-en-v1.5",
            "rerank_enabled": True,
            "rerank_url": "http://127.0.0.1:8083",
            "rerank_over_fetch_factor": 5,
            "rerank_max_candidates": 500,
        }
    )
    dumped = cfg.model_dump()
    for dead in ("model", "rerank_enabled", "rerank_url", "rerank_over_fetch_factor"):
        assert dead not in dumped


def _load_seed_slot(toml_path: Path) -> SlotConfig:
    """Validate a shipped seed TOML into a SlotConfig (top-level or [slot]-nested)."""
    raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    return SlotConfig.model_validate(raw.get("slot", raw))


def test_seed_slot_ports_are_mutually_unique() -> None:
    """No two shipped seed slot TOMLs may bind the same port.

    Deconfliction today is enforced only by hand-written comments in the TOMLs
    (agent 8081, rerank 8086, tts 8085, flm 8088, brain 8089, utility 8090,
    qwen3tts 8095, img 8188, coder 8082, embed 8083). A future seed addition (or an edit reusing a port)
    that collides two shipped slots would otherwise ship silently and crash-loop
    the second container on a fresh box. The glob includes the opt-in qwen3tts
    template (it is shipped, and its 8095 is distinct).
    """
    ports: dict[str, int] = {}
    for f in sorted(_SEEDED_SLOTS_DIR.glob("*.toml")):
        ports[f.stem] = _load_seed_slot(f).port
    dupes = {
        port: [name for name, p in ports.items() if p == port]
        for port, count in Counter(ports.values()).items()
        if count > 1
    }
    assert not dupes, f"seed slot port collisions: {dupes}"


#: Seeds the clean-seed invariant applies to — every operator-facing slot.
#: ``brain`` is the one deliberate exception (see test_brain_seed_ships_ready):
#: the platform steward must work out of the box, so #1258 ships it enabled
#: with a small 1B model pinned. Every other seed ships model-less/disabled.
_CLEAN_SEED_SLOTS = sorted((set(STATIC_SEED_SLOTS) | {"qwen3tts"}) - {"brain"})


@pytest.mark.parametrize("name", _CLEAN_SEED_SLOTS)
def test_seed_toml_ships_clean(name: str) -> None:
    """Clean-seed invariant (WS-E, #1107): every shipped seed ships DISABLED with
    no `[model].default` pin, so a fresh box boots a grey tile — no surprise
    multi-GB download, no crash-loop. Regressing any seed to enabled=true or a
    reintroduced model pin (the removed gemma-4-12b-it / sdxl-turbo ghosts) is
    the exact #1107 regression this guards. model is a default_factory
    ModelConfig with default=="" so the assertion holds for [model]-less TOMLs.

    ``brain`` is intentionally excluded (see test_brain_seed_ships_ready): it is
    the platform steward and ships ready, not clean, by design (#1258).
    """
    slot = _load_seed_slot(_SEEDED_SLOTS_DIR / f"{name}.toml")
    assert slot.enabled is False
    assert slot.model.default == ""


def test_brain_seed_ships_ready() -> None:
    """The brain steward is the deliberate exception to the clean-seed rule
    (#1258): unlike operator slots (which ship model-less so the operator picks),
    the brain drives the dashboard's sidebar steward chat and must work out of
    the box, so it ships ENABLED with a small 1B tool-use model pinned. Loading
    stays lazy (the chat falls back to the ``agent`` slot until the brain model
    is bound), so this does not reintroduce the #1107 eager-download regression.

    This pins the intent from both sides: if a future edit reverts the brain to
    clean/disabled, the steward silently stops working out of the box and this
    fails; test_seed_toml_ships_clean guards every *other* seed against the
    opposite regression.
    """
    slot = _load_seed_slot(_SEEDED_SLOTS_DIR / "brain.toml")
    assert slot.enabled is True
    assert slot.model.default != ""
