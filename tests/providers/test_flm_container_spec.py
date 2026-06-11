"""FLM container_spec: [npu] toggles + model-cache default (Phase A)."""

from typing import Any

from hal0.providers.flm import _DEFAULT_FLM_MODELS_DIR, FLMProvider


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "npu",
        "port": 8088,
        "device": "npu",
        "runtime": "container",
        "profile": "flm-npu",
        "model": {"default": "gemma3:4b", "context_size": 16384},
    }
    base.update(overrides)
    return base


def _model_info() -> dict[str, Any]:
    return {"_model_key": "gemma3:4b"}


def test_npu_table_drives_trio_flags() -> None:
    spec = FLMProvider().container_spec(_slot_cfg(npu={"asr": True, "embed": True}), _model_info())
    assert "--asr" in spec.command and "--embed" in spec.command


def test_npu_table_off_means_chat_only() -> None:
    spec = FLMProvider().container_spec(
        _slot_cfg(npu={"asr": False, "embed": False}), _model_info()
    )
    assert "--asr" not in spec.command and "--embed" not in spec.command


def test_legacy_defaults_load_asr_still_honoured() -> None:
    # Back-compat: old lemond-era shape, removed in Phase E.
    spec = FLMProvider().container_spec(_slot_cfg(defaults={"load_asr": "1"}), _model_info())
    assert "--asr" in spec.command


def test_npu_table_overrides_legacy_defaults() -> None:
    spec = FLMProvider().container_spec(
        _slot_cfg(npu={"asr": False, "embed": False}, defaults={"load_asr": "1"}),
        _model_info(),
    )
    assert "--asr" not in spec.command


def test_default_models_dir_is_flm_cache() -> None:
    assert _DEFAULT_FLM_MODELS_DIR == "/var/lib/hal0/.config/flm/models"
    spec = FLMProvider().container_spec(_slot_cfg(), _model_info())
    assert (
        "/var/lib/hal0/.config/flm/models",
        "/var/lib/hal0/.config/flm/models",
    ) in spec.mounts
