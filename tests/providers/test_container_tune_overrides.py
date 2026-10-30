"""Slot ``[tune]`` overrides reach the launch argv through ``effective_tune`` (ADR-0045)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from hal0.config.schema import ProfileConfig
from hal0.providers.container import (
    ContainerProvider,
    _resolve_slot_argv,
    resolved_argv_detail_for_slot,
    slot_effective_context,
)

_SIDECAR = "/mnt/ai-models/vlm/mmproj-F32.mmproj"


def _profile(**kw: Any) -> ProfileConfig:
    kw.setdefault("flags", "-fa on --parallel 1")
    kw.setdefault("speculation", "off")
    return ProfileConfig(**kw)


def _slot(tune: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": "chat",
        "port": 8102,
        "profile": "p",
        "runtime": "container",
        "device": "gpu-rocm",
        "model": {"default": "chat-vlm"},
    }
    if tune is not None:
        cfg["tune"] = tune
    cfg.update(kw)
    return cfg


def _model(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"path": "/mnt/ai-models/m.gguf", "_model_key": "chat-vlm"}
    base.update(kw)
    return base


def _detail(
    cfg: dict[str, Any], profile: ProfileConfig, model: dict[str, Any], image: str | None = None
):
    patches = [
        patch("hal0.providers.container._resolve_profile", return_value=profile),
        patch("hal0.providers.container._best_effort_model_info", return_value=model),
    ]
    if image:
        patches.append(patch("hal0.providers.container._resolve_image_ref", return_value=image))
    for p in patches:
        p.start()
    try:
        return resolved_argv_detail_for_slot(cfg)
    finally:
        for p in patches:
            p.stop()


def _value(detail: dict[str, Any], flag: str) -> tuple[str | None, str | None]:
    for p in detail["provenance"]:
        if p["flag"] == flag:
            return p["value"], p["source"]
    return None, None


def test_slot_vision_off_drops_mmproj() -> None:
    cfg = _slot({"vision": "off"})
    d = _detail(cfg, _profile(vision="auto"), _model(mmproj=_SIDECAR))
    assert "--mmproj" not in d["argv"]
    d = _detail(_slot(), _profile(vision="auto"), _model(mmproj=_SIDECAR))
    assert "--mmproj" in d["argv"]


def test_slot_ctx_override_sets_ctx_size() -> None:
    d = _detail(_slot({"ctx_size": 8192}), _profile(ctx_size=32768), _model())
    assert _value(d, "--ctx-size") == ("8192", "base")


def test_profile_effort_emitted_in_profile_layer() -> None:
    d = _detail(_slot(), _profile(reasoning_effort="low"), _model())
    assert _value(d, "--reasoning-effort") == ("low", "profile")


def test_slot_effort_beats_profile_and_free_flag() -> None:
    prof = _profile(flags="-fa on --reasoning-effort low", reasoning_effort="medium")
    d = _detail(_slot({"reasoning_effort": "high"}), prof, _model())
    assert _value(d, "--reasoning-effort") == ("high", "slot")
    assert d["argv"].count("--reasoning-effort") == 1


def test_budget_minus_one_emits_nothing() -> None:
    d = _detail(_slot(), _profile(), _model())
    assert "--reasoning-budget" not in d["argv"]
    assert "--reasoning-effort" not in d["argv"]
    d = _detail(_slot({"reasoning_budget": 0}), _profile(), _model())
    assert _value(d, "--reasoning-budget") == ("0", "slot")


def test_slot_default_effort_suppresses_profile_effort() -> None:
    d = _detail(_slot({"reasoning_effort": "default"}), _profile(reasoning_effort="low"), _model())
    assert "--reasoning-effort" not in d["argv"]


def test_effort_gated_by_runner_build() -> None:
    img = "registry.example/hal0-runner-standard:b11700-rabc123"
    d = _detail(_slot({"reasoning_effort": "high"}), _profile(), _model(), image=img)
    assert "--reasoning-effort" not in d["argv"]
    reason = d["tune_effective"]["reasoning_effort_unavailable"]
    assert reason.startswith("runner build 11700")
    assert d["tune_effective"]["reasoning_effort"] == "high"


def test_effort_available_on_verified_build() -> None:
    img = "registry.example/hal0-runner-standard:b11840-rabc123"
    d = _detail(_slot({"reasoning_effort": "high"}), _profile(), _model(), image=img)
    assert _value(d, "--reasoning-effort") == ("high", "slot")
    assert d["tune_effective"]["reasoning_effort_unavailable"] is None


def test_slot_speculation_off_disables_drafter() -> None:
    model = _model(draft="/mnt/ai-models/d.gguf", draft_kind="simple")
    on = _detail(_slot(), _profile(speculation="auto"), model)
    assert "--spec-draft-model" in on["argv"]
    off = _detail(_slot({"speculation": "off"}), _profile(speculation="auto"), model)
    assert "--spec-draft-model" not in off["argv"]
    assert off["speculation"]["mode"] == "none"


def test_resolved_detail_carries_tune_effective() -> None:
    d = _detail(
        _slot({"vision": "off", "ctx_size": 4096}),
        _profile(reasoning_budget=256),
        _model(),
    )
    te = d["tune_effective"]
    assert set(te) == {
        "ctx_size",
        "enable_thinking",
        "reasoning_effort",
        "reasoning_budget",
        "vision",
        "speculation",
        "sources",
        "reasoning_effort_unavailable",
    }
    assert te["vision"] == "off" and te["sources"]["vision"] == "slot"
    assert te["reasoning_budget"] == 256 and te["sources"]["reasoning_budget"] == "profile"


def test_slot_effective_context_source_slot() -> None:
    with patch("hal0.providers.container._resolve_profile", return_value=_profile(ctx_size=32768)):
        assert slot_effective_context(_slot({"ctx_size": 8192})) == (8192, "slot")
        assert slot_effective_context(_slot()) == (32768, "profile")


def test_container_spec_and_preview_agree() -> None:
    cfg = _slot({"ctx_size": 8192, "reasoning_effort": "low"})
    prof = _profile()
    with (
        patch("hal0.providers.container._resolve_profile", return_value=prof),
        patch("hal0.providers.container._best_effort_model_info", return_value=_model()),
        patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ),
        patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[]),
    ):
        spec = ContainerProvider().container_spec(cfg, _model())
        _image, resolved, _plan = _resolve_slot_argv(cfg)  # type: ignore[misc]
    assert "--reasoning-effort" in spec.command
    assert spec.command[spec.command.index("--ctx-size") + 1] == "8192"
    assert resolved.argv[resolved.argv.index("--ctx-size") + 1] == "8192"
