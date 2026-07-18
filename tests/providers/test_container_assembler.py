"""Single-assembler tests: launch/preview parity, model-default precedence,
GPU gating for cpu profiles, and [server].env rendering.

These pin the refactor that makes the launch path
(``ContainerProvider.container_spec`` → ``_llama_launch_plan``) and the preview
path (``resolved_command_for_slot`` / ``_resolve_slot_argv``) share ONE labelled
segment builder (``_llama_argv_segments``), so an operator's previewed command
is byte-identical to what launches.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from hal0.config.schema import ProfileConfig
from hal0.errors import BadRequest
from hal0.providers.container import (
    ContainerProvider,
    _llama_launch_plan,
    _render_quadlet_from_plan,
    resolved_command_for_slot,
)


def _render_from_plan(token, plan, *, runtime_bin=None, publish_host="127.0.0.1"):
    """Shim so call sites barely change: the Quadlet renderer no longer takes a
    runtime binary (podman-only via its systemd generator), so accept+ignore it."""
    return _render_quadlet_from_plan(token, plan, publish_host=publish_host)


def _gpu_profile(**overrides: Any) -> ProfileConfig:
    base: dict[str, Any] = dict(
        image="ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        flags="-fa on -ctk q8_0 -b 512 --parallel 1 --threads 8",
        mtp=False,
        device_class="gpu",
        backend="rocm",
    )
    base.update(overrides)
    return ProfileConfig(**base)


# ── 1. preview == launch parity (mtp + chat_template + mmproj + extra_args) ────


def test_preview_equals_launch_full_slot() -> None:
    """resolved_command_for_slot renders the exact argv container_spec launches
    for a slot exercising the mtp override, model-registry defaults, a
    chat-template, an mmproj sidecar, a slot ngl override, and extra_args."""
    cfg = {
        "name": "agent",
        "port": 8101,
        "profile": "rocm",
        "device": "gpu-rocm",
        "mtp": True,
        "chat_template": "chatml",
        "vision": True,
        "model": {"default": "my-model", "context_size": 40000, "n_gpu_layers": 88},
        "server": {"extra_args": "-b 8192 --jinja"},
    }
    model_info = {
        "_model_key": "my-model",
        "path": "/mnt/ai-models/my-model.gguf",
        "mmproj": "/mnt/ai-models/my-model/mmproj-f16.gguf",
        "defaults": {"extra_args": "--rope-scaling linear", "n_gpu_layers": 40},
        "metadata": {"context_length": 262144},
    }

    profile = _gpu_profile(mtp=False)  # slot mtp=True must force the bundle on

    with (
        patch("hal0.providers.container._resolve_profile", return_value=profile),
        patch(
            "hal0.providers.container._best_effort_model_info",
            return_value=model_info,
        ),
    ):
        preview = resolved_command_for_slot(cfg)
        launch_plan = ContainerProvider().container_spec(cfg, model_info)

    assert preview is not None
    # preview = [image, *argv]; launch command excludes the image.
    assert preview[0] == profile.image
    assert preview[1:] == launch_plan.command


# ── 2. model-default / ngl precedence ─────────────────────────────────────────


def _ngl(command: list[str]) -> str | None:
    return command[command.index("-ngl") + 1] if "-ngl" in command else None


def test_ngl_in_extra_args_is_denied() -> None:
    """§21.7: ``-ngl`` is a managed flag (schema's ``[model].n_gpu_layers`` is
    the sanctioned channel) — ``[server].extra_args`` may not smuggle it, so
    this now raises instead of silently winning precedence."""
    with pytest.raises(BadRequest) as exc_info:
        _llama_launch_plan(
            image="i:1",
            port=8095,
            model_path="/m.gguf",
            flags_str="-ngl 10",
            devices=[],
            group_ids=[],
            model_defaults={"n_gpu_layers": 20},
            slot_n_gpu_layers=30,
            extra_args="-ngl 40",
        )
    assert exc_info.value.code == "slot.managed_arg_denied"


def test_ngl_precedence_slot_beats_model_default() -> None:
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="-ngl 10",
        devices=[],
        group_ids=[],
        model_defaults={"n_gpu_layers": 20},
        slot_n_gpu_layers=30,
    )
    assert _ngl(plan.command) == "30"


def test_ngl_precedence_model_default_beats_profile() -> None:
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="-ngl 10",
        devices=[],
        group_ids=[],
        model_defaults={"n_gpu_layers": 20},
        slot_n_gpu_layers=-1,  # unset sentinel → no slot override
    )
    assert _ngl(plan.command) == "20"


def _parval(command: list[str]) -> str | None:
    for flag in ("--parallel", "-np"):
        if flag in command:
            return command[command.index(flag) + 1]
    return None


def test_parallel_none_inherits_profile() -> None:
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="-fa on --parallel 1",
        devices=[],
        group_ids=[],
        slot_parallel=None,
    )
    assert _parval(plan.command) == "1"  # profile's value untouched
    assert "--kv-unified" not in plan.command


def test_parallel_slot_overrides_profile_and_adds_kv_unified() -> None:
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="-fa on --parallel 1",
        devices=[],
        group_ids=[],
        slot_parallel=8,
    )
    assert _parval(plan.command) == "8"
    # deduped to one occurrence across the -np/--parallel alias
    assert plan.command.count("--parallel") + plan.command.count("-np") == 1
    assert "--kv-unified" in plan.command  # N>1 → unified pool


def test_parallel_one_emits_no_kv_unified() -> None:
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="-fa on",
        devices=[],
        group_ids=[],
        slot_parallel=1,
    )
    assert _parval(plan.command) == "1"
    assert "--kv-unified" not in plan.command


def test_parallel_extra_args_wins_over_slot() -> None:
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="--parallel 1",
        devices=[],
        group_ids=[],
        slot_parallel=8,
        extra_args="-np 2",
    )
    assert _parval(plan.command) == "2"  # hand-authored extra_args is highest precedence
    assert plan.command.count("--parallel") + plan.command.count("-np") == 1


def test_model_default_extra_args_emitted_and_overridable() -> None:
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="",
        devices=[],
        group_ids=[],
        model_defaults={"extra_args": "--rope-scaling linear --foo 1"},
        extra_args="--foo 2",  # slot extra_args overrides a model default
    )
    assert "--rope-scaling" in plan.command
    assert plan.command[plan.command.index("--rope-scaling") + 1] == "linear"
    assert plan.command.count("--foo") == 1
    assert plan.command[plan.command.index("--foo") + 1] == "2"


# ── 3. cpu profile gets no GPU devices / GIDs ─────────────────────────────────


def test_cpu_profile_gets_no_gpu_plumbing() -> None:
    cfg = {
        "name": "cpu-slot",
        "port": 8095,
        "profile": "cpu-llm",
        "device": "cpu",
        "model": {"default": "m"},
    }
    cpu_profile = _gpu_profile(device_class="cpu", backend=None)
    with (
        patch("hal0.providers.container._resolve_profile", return_value=cpu_profile),
        patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ),
        patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[993, 44]),
        patch("hal0.providers.container.os.path.exists", return_value=True),
    ):
        spec = ContainerProvider().container_spec(
            cfg, {"_model_key": "m", "path": "/mnt/ai-models/m.gguf"}
        )
    assert spec.devices == []
    assert spec.group_add == []


def test_gpu_profile_existence_filters_devices() -> None:
    cfg = {
        "name": "gpu-slot",
        "port": 8095,
        "profile": "rocm",
        "device": "gpu-rocm",
        "model": {"default": "m"},
    }

    def _exists(path: str) -> bool:
        return path == "/dev/dri/renderD128"  # /dev/kfd absent on this host

    with (
        patch("hal0.providers.container._resolve_profile", return_value=_gpu_profile()),
        patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ),
        patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[993]),
        patch("hal0.providers.container.os.path.exists", side_effect=_exists),
    ):
        spec = ContainerProvider().container_spec(
            cfg, {"_model_key": "m", "path": "/mnt/ai-models/m.gguf"}
        )
    assert spec.devices == ["/dev/dri/renderD128"]  # non-existent /dev/kfd dropped
    assert spec.group_add == ["993"]


# ── 4. [server].env renders into the unit ─────────────────────────────────────


def test_server_env_threads_into_plan_and_unit() -> None:
    cfg = {
        "name": "gpu-slot",
        "port": 8095,
        "profile": "rocm",
        "device": "gpu-rocm",
        "model": {"default": "m"},
        "server": {"env": {"HSA_OVERRIDE_GFX_VERSION": "11.0.0"}},
    }
    with (
        patch("hal0.providers.container._resolve_profile", return_value=_gpu_profile()),
        patch("hal0.providers.container.resolve_gpu_device_paths", return_value=[]),
        patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[]),
    ):
        spec = ContainerProvider().container_spec(
            cfg, {"_model_key": "m", "path": "/mnt/ai-models/m.gguf"}
        )
    assert spec.env == {"HSA_OVERRIDE_GFX_VERSION": "11.0.0"}
    unit = _render_from_plan("gpu-slot", spec)
    assert "Environment=HSA_OVERRIDE_GFX_VERSION=11.0.0" in unit


def test_env_empty_when_no_server_env() -> None:
    plan = _llama_launch_plan(
        image="i:1", port=8095, model_path="/m.gguf", flags_str="", devices=[], group_ids=[]
    )
    assert plan.env == {}
