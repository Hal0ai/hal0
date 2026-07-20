"""Heal-on-load: a type-less, llm-shaped slot defaults to ``type="llm"`` (O23).

``hal0_llm_slot_views`` hard-filters ``type != "llm"``, so a chat/llm slot TOML
that predates the seeded ``type`` key never enters the resolver views and
``hal0/<slot>`` aliases fail to resolve (live: ``brain.toml`` lacked ``type``).
"""

from __future__ import annotations

from pathlib import Path

from hal0.config.loader import load_slot_config
from hal0.slots._cfg_helpers import heal_missing_llm_type
from hal0.slots.manager import SlotManager


class TestHealHelper:
    def test_type_less_llm_shaped_slot_heals(self) -> None:
        data = {"name": "brain", "port": 8082, "model": {"default": "qwen"}}
        assert heal_missing_llm_type(data) is True
        assert data["type"] == "llm"

    def test_explicit_type_is_respected(self) -> None:
        data = {"name": "rerank", "type": "reranking", "model": {"default": "x"}}
        assert heal_missing_llm_type(data) is False
        assert data["type"] == "reranking"

    def test_no_model_table_is_not_healed(self) -> None:
        data = {"name": "tts", "port": 8090}  # kokoro tts has no [model] table
        assert heal_missing_llm_type(data) is False
        assert "type" not in data

    def test_image_table_is_not_healed(self) -> None:
        data = {"name": "img", "model": {"default": "sdxl"}, "image": {"steps": 20}}
        assert heal_missing_llm_type(data) is False
        assert "type" not in data

    def test_non_llama_provider_is_not_healed(self) -> None:
        data = {"name": "img", "provider": "comfyui", "model": {"default": "sdxl"}}
        assert heal_missing_llm_type(data) is False
        assert "type" not in data

    def test_empty_string_type_is_healed(self) -> None:
        data = {"name": "brain", "type": "", "model": {"default": "q"}}
        assert heal_missing_llm_type(data) is True
        assert data["type"] == "llm"


class TestHealOnLoad:
    def test_loader_heals_type_less_flat_slot(self, tmp_hal0_home: str) -> None:
        root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
        root.mkdir(parents=True, exist_ok=True)
        (root / "brain.toml").write_text(
            'name = "brain"\nport = 8082\nprovider = "llama-server"\n[model]\ndefault = "qwen"\n',
            encoding="utf-8",
        )
        cfg = load_slot_config("brain")
        assert getattr(cfg, "type", None) == "llm"

    async def test_manager_iter_configs_heals(self, tmp_hal0_home: str) -> None:
        root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
        root.mkdir(parents=True, exist_ok=True)
        (root / "brain.toml").write_text(
            'name = "brain"\nport = 8082\n[model]\ndefault = "qwen"\n',
            encoding="utf-8",
        )
        sm = SlotManager()
        cfgs = await sm.iter_configs()
        brain = next(c for c in cfgs if c.get("name") == "brain")
        assert brain.get("type") == "llm"
