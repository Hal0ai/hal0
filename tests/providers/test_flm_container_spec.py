"""FLM container_spec: [npu] toggles + model-cache default (Phase A)."""

from typing import Any

from hal0.config import paths as cfg_paths
from hal0.providers.flm import FLMProvider


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "npu",
        "port": 8088,
        "device": "npu",
        "runtime": "container",
        "profile": "flm",
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


def test_chat_off_drops_positional_tag() -> None:
    """[npu].chat=false must stop the container serving chat.

    Regression: container_spec passed the positional chat tag
    unconditionally, so toggling Chat off in the drawer still served chat
    (only the native start_cmd honoured HAL0_FLM_LOAD_CHAT). With chat off
    the tag must be absent — 'serve' then flags, no model positional.
    """
    spec = FLMProvider().container_spec(
        _slot_cfg(npu={"chat": False, "embed": True}), _model_info()
    )
    assert "gemma3:4b" not in spec.command
    assert spec.command[0] == "serve"
    # First arg after 'serve' is a flag, not a positional model tag.
    assert spec.command[1].startswith("--")
    assert "--embed" in spec.command


def test_chat_on_keeps_positional_tag() -> None:
    spec = FLMProvider().container_spec(_slot_cfg(npu={"chat": True}), _model_info())
    assert spec.command[:2] == ["serve", "gemma3:4b"]


def test_no_per_role_model_flags() -> None:
    """FLM has no --asr-model / --embed-model — --asr / --embed are booleans
    that load FLM's single bundled whisper / embed-gemma. We must NEVER emit a
    per-role model flag (passing an unknown option crashes ``flm serve``)."""
    spec = FLMProvider().container_spec(
        _slot_cfg(npu={"chat": True, "asr": True, "embed": True}), _model_info()
    )
    assert "--asr-model" not in spec.command
    assert "--embed-model" not in spec.command
    assert "--asr" in spec.command and "--embed" in spec.command


def test_start_cmd_matches_container_role_args() -> None:
    """Native start_cmd and container_spec build the same --asr/--embed tail so
    the two paths can never drift (they share _flm_shadow_role_args)."""
    cfg = _slot_cfg(npu={"asr": True, "embed": True})
    provider = FLMProvider()
    env = provider.build_env(cfg, _model_info())
    argv = provider.start_cmd(env)
    spec = provider.container_spec(cfg, _model_info())
    for flag in ("--asr", "--embed"):
        assert flag in argv and flag in spec.command


def test_default_models_dir_is_flm_cache(tmp_hal0_home: str) -> None:
    """Isolated via ``tmp_hal0_home`` (tests/conftest.py): without it this
    reads the box's real /etc/hal0/hal0.toml through
    ``flm_models_dir()`` -> ``load_hal0_config()``, so a host with
    ``[models].flm_store`` set (e.g. relocated to /mnt/ai-models/FLM)
    fails here even though the DEFAULT-path assertion is correct.
    """
    spec = FLMProvider().container_spec(_slot_cfg(), _model_info())
    # Source follows the resolver default (HAL0_HOME-aware); the target is
    # FLM's hardcoded in-container HOME cache and never moves.
    assert (
        cfg_paths.default_flm_models_dir(),
        "/var/lib/hal0/.config/flm/models",
    ) in spec.mounts


def test_env_var_overrides_flm_models_dir(monkeypatch, tmp_path) -> None:
    custom = str(tmp_path / "flm-store")
    monkeypatch.setenv("HAL0_FLM_MODELS_DIR", custom)
    spec = FLMProvider().container_spec(_slot_cfg(), _model_info())
    assert (custom, "/var/lib/hal0/.config/flm/models") in spec.mounts


def test_models_flm_store_config_drives_mount(monkeypatch, tmp_path) -> None:
    """[models].flm_store must reach the container mount without the env var.

    Regression: the pre-config chain read only HAL0_FLM_MODELS_DIR, so a
    store relocated via hal0.toml was silently ignored and the slot kept
    bind-mounting the root-subvolume default (live repro on CT105 — dir gone
    after reboot, podman exit 125).
    """
    import hal0.config.loader as loader

    custom = str(tmp_path / "flm-relocated")
    monkeypatch.delenv("HAL0_FLM_MODELS_DIR", raising=False)

    class _Models:
        flm_store = custom

    class _Cfg:
        models = _Models()

    monkeypatch.setattr(loader, "load_hal0_config", lambda: _Cfg())
    spec = FLMProvider().container_spec(_slot_cfg(), _model_info())
    assert (custom, "/var/lib/hal0/.config/flm/models") in spec.mounts


def test_spec_build_creates_missing_store_dir(monkeypatch, tmp_path) -> None:
    """Spec build must mkdir the bind source so podman never sees ENOENT."""
    custom = tmp_path / "nested" / "flm-store"
    monkeypatch.setenv("HAL0_FLM_MODELS_DIR", str(custom))
    FLMProvider().container_spec(_slot_cfg(), _model_info())
    assert custom.is_dir()


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
