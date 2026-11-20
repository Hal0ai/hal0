"""Unit tests for hal0.upstreams.presets and the shipped provider_presets.toml."""

from __future__ import annotations

import pytest

from hal0.config.schema import _VALID_AUTH_STYLES
from hal0.upstreams import presets
from hal0.upstreams.presets import PresetError, get_preset, load_presets, preset_to_api


def test_all_preset_ids_present() -> None:
    assert set(load_presets()) == {
        "minimax", "openai", "anthropic", "openrouter", "google_ai_studio",
        "deepseek", "groq", "mistral", "ollama", "hal0_peer", "custom",
    }  # fmt: skip


def test_every_preset_is_well_formed() -> None:
    for p in load_presets().values():
        assert p.category in {"cloud", "local", "peer", "custom"}
        assert p.auth in _VALID_AUTH_STYLES
        if p.category == "cloud":
            assert p.base_url.startswith("https://")
        for m in p.models:
            assert m.type in {"chat", "embed", "rerank", "tts", "stt", "image"}
            assert m.price_unit in {"Mtok", "Mchar", "image", "1k_search"}


def test_empty_base_url_only_for_peer_and_custom() -> None:
    for p in load_presets().values():
        assert bool(p.base_url) == (p.category not in {"peer", "custom"}), p.id


def test_anthropic_uses_openai_compatible_surface() -> None:
    p = get_preset("anthropic")
    assert p is not None
    assert p.auth == "bearer"  # not the native x-api-key style
    assert p.tools is True


def test_minimax_quirks() -> None:
    p = get_preset("minimax")
    assert p is not None
    assert p.quirks == ("no_nullable_union_tool_schema", "strip_llama_samplers")


def test_get_preset_unknown() -> None:
    assert get_preset("does-not-exist") is None


def test_malformed_file_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = (
        '[preset.broken]\nname = "Broken"\ncategory = "cloud"\n'
        'base_url = "https://x.example/v1"\nauth = "bearer"\n'
        '[[preset.broken.models]]\nid = "m"\ntype = "video"\n'
    )
    monkeypatch.setattr(presets, "_read_text", lambda: bad)
    presets.load_presets.cache_clear()
    try:
        with pytest.raises(PresetError, match="broken"):
            presets.load_presets()
    finally:
        presets.load_presets.cache_clear()


def test_duplicate_model_id_raises() -> None:
    dup = (
        '[preset.twice]\nname = "Twice"\ncategory = "cloud"\n'
        'base_url = "https://x.example/v1"\nauth = "bearer"\n'
        '[[preset.twice.models]]\nid = "m"\ntype = "chat"\n'
        '[[preset.twice.models]]\nid = "m"\ntype = "chat"\n'
    )
    with pytest.raises(PresetError, match=r"preset 'twice': model 'm' is listed twice"):
        presets._parse(dup)


def test_invalid_toml_raises() -> None:
    with pytest.raises(PresetError):
        presets._parse("not = [valid")


def test_api_shape_keeps_keys_the_ui_reads() -> None:
    p = get_preset("minimax")
    assert p is not None
    d = preset_to_api(p)
    assert {"id", "name", "base_url", "auth", "docs_url", "category", "models"} <= set(d)
    # CatalogEntry in ui/src/api/hooks/useUpstreams.ts
    assert {
        "auth_header_name", "models_path", "default_models", "default_model",
        "capabilities", "notes",
    } <= set(d)  # fmt: skip
    assert d["auth_header_name"] == "Authorization"
    assert d["default_model"] == "MiniMax-M3"
    assert "tools" in d["capabilities"]
