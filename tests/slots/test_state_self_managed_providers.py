"""Tests for the SELF_MANAGED_PROVIDERS gate.

Some providers (kokoro, qwen3tts, moonshine, vibevoice) don't need an explicit
``model_id``. Qwen3-TTS and Moonshine additionally use operator-staged weights
whose configured IDs have no registry row. This gate keeps adoption +
transition guards from labelling a modelless llama-server / FLM slot as READY.
"""

from __future__ import annotations

from hal0.slots.state import (
    SELF_MANAGED_PROVIDERS,
    STAGED_WEIGHT_PROVIDERS,
    provider_registry_miss_is_expected,
    provider_requires_model,
)


def test_self_managed_providers_set_matches_ui() -> None:
    """The Python set must stay in sync with the UI's SELF_MANAGED_PROVIDERS."""
    assert frozenset({"kokoro", "qwen3tts", "moonshine", "vibevoice"}) == SELF_MANAGED_PROVIDERS


def test_qwen3tts_does_not_require_model() -> None:
    assert provider_requires_model("qwen3tts") is False


def test_kokoro_does_not_require_model() -> None:
    assert provider_requires_model("kokoro") is False


def test_moonshine_does_not_require_model() -> None:
    assert provider_requires_model("moonshine") is False


def test_vibevoice_does_not_require_model() -> None:
    assert provider_requires_model("vibevoice") is False


def test_llama_server_requires_model() -> None:
    assert provider_requires_model("llama-server") is True


def test_flm_requires_model() -> None:
    assert provider_requires_model("flm") is True


def test_provider_check_is_case_insensitive() -> None:
    assert provider_requires_model("Kokoro") is False
    assert provider_requires_model("MOONSHINE") is False
    assert provider_requires_model("Llama-Server") is True


def test_provider_check_is_none_safe() -> None:
    # No provider declared → treat as "requires model" (safer default).
    assert provider_requires_model(None) is True
    assert provider_requires_model("") is True


def test_expected_registry_misses_are_only_staged_self_managed_engines() -> None:
    assert frozenset({"qwen3tts", "moonshine"}) == STAGED_WEIGHT_PROVIDERS
    assert STAGED_WEIGHT_PROVIDERS <= SELF_MANAGED_PROVIDERS
    assert provider_registry_miss_is_expected("QWEN3TTS") is True
    assert provider_registry_miss_is_expected("moonshine") is True
    assert provider_registry_miss_is_expected("kokoro") is False
    assert provider_registry_miss_is_expected("vibevoice") is False
    assert provider_registry_miss_is_expected("llama-server") is False
