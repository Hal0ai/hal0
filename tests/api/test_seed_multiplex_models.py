"""``_seed_multiplex_models`` — dispatcher model-cache seeding for the FLM trio.

#733: the seeder still matched the pre-container schema (``provider = "flm"``
+ ``[defaults] load_embed/load_asr``) while live slot tomls moved to
``backend = "flm"`` + the ``[npu]`` table — so the canonical trio tags never
reached the model cache and canonical-tag requests fell to dispatch.no_route.
"""

from __future__ import annotations

from typing import Any

from hal0.api import _seed_multiplex_models

CANONICAL_EMBED = "embed-gemma:300m"
CANONICAL_ASR = "whisper-v3:turbo"


class FakeSlotManager:
    def __init__(self, configs: list[dict[str, Any]]) -> None:
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        return self._configs


async def _seed(
    configs: list[dict[str, Any]],
    cache: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    cache = cache if cache is not None else {}
    await _seed_multiplex_models(None, FakeSlotManager(configs), cache)
    return cache


def _npu_cfg(**over: Any) -> dict[str, Any]:
    """Live CT105 shape: backend key + [npu] table, no provider key."""
    cfg: dict[str, Any] = {
        "name": "npu",
        "type": "llm",
        "device": "npu",
        "backend": "flm",
        "npu": {"asr": True, "embed": True},
    }
    cfg.update(over)
    return cfg


class TestNpuTableSchema:
    async def test_backend_flm_with_npu_table_seeds_canonical_tags(self) -> None:
        cache = await _seed([_npu_cfg()])
        assert CANONICAL_EMBED in cache["hal0"]
        assert CANONICAL_ASR in cache["hal0"]

    async def test_embed_only_seeds_embed_tag(self) -> None:
        cache = await _seed([_npu_cfg(npu={"asr": False, "embed": True})])
        assert CANONICAL_EMBED in cache["hal0"]
        assert CANONICAL_ASR not in cache["hal0"]

    async def test_asr_only_seeds_asr_tag(self) -> None:
        cache = await _seed([_npu_cfg(npu={"asr": True, "embed": False})])
        assert CANONICAL_ASR in cache["hal0"]
        assert CANONICAL_EMBED not in cache["hal0"]


class TestLegacyDefaultsSchema:
    async def test_provider_flm_with_defaults_still_seeds(self) -> None:
        cache = await _seed(
            [
                {
                    "name": "npu",
                    "provider": "flm",
                    "defaults": {"load_embed": True, "load_asr": True},
                }
            ]
        )
        assert CANONICAL_EMBED in cache["hal0"]
        assert CANONICAL_ASR in cache["hal0"]


class TestNonMatching:
    async def test_non_flm_slot_seeds_nothing(self) -> None:
        cache = await _seed(
            [{"name": "chat", "backend": "vulkan", "npu": {"asr": True, "embed": True}}]
        )
        assert cache.get("hal0", []) == []

    async def test_flm_slot_without_flags_seeds_nothing(self) -> None:
        cache = await _seed([_npu_cfg(npu={"asr": False, "embed": False})])
        assert cache.get("hal0", []) == []

    async def test_idempotent_no_duplicate_tags(self) -> None:
        cache: dict[str, list[str]] = {"hal0": [CANONICAL_EMBED]}
        await _seed([_npu_cfg()], cache)
        assert cache["hal0"].count(CANONICAL_EMBED) == 1
        assert cache["hal0"].count(CANONICAL_ASR) == 1
