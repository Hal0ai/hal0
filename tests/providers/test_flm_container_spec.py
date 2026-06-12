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
    # Back-compat: legacy defaults-table shape still honoured.
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


def test_model_table_context_size_drives_ctx_len() -> None:
    """[model].context_size (SlotConfig shape) must reach --ctx-len.

    Regression: build_env read only the legacy ctx_size/defaults shapes
    and silently fell back to 8192 for container slots (live repro on CT105,
    Phase A deploy).
    """
    spec = FLMProvider().container_spec(_slot_cfg(), _model_info())
    idx = spec.command.index("--ctx-len")
    assert spec.command[idx + 1] == "16384"


def test_legacy_ctx_size_still_wins_when_model_table_absent() -> None:
    cfg = _slot_cfg(ctx_size=4096)
    cfg["model"] = {"default": "gemma3:4b"}  # no context_size
    spec = FLMProvider().container_spec(cfg, _model_info())
    idx = spec.command.index("--ctx-len")
    assert spec.command[idx + 1] == "4096"
