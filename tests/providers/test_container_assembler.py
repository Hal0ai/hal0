"""Single-assembler tests: launch/preview parity, layer precedence, GPU
gating for cpu slots, and [server].env rendering.

These pin the refactor that makes the launch path
(``ContainerProvider.container_spec`` → ``_llama_launch_plan``) and the preview
path (``resolved_command_for_slot`` / ``_resolve_slot_argv``) share ONE labelled
segment builder (``_llama_argv_segments``), so an operator's previewed command
is byte-identical to what launches. ADR-0029: four layers, base → model →
profile → slot.
"""

from __future__ import annotations

import shlex
from typing import Any
from unittest.mock import patch

import pytest

from hal0.config.schema import MTP_DRAFT_BUNDLE, ProfileConfig
from hal0.errors import BadRequest, UnprocessableEntity
from hal0.providers.container import (
    ContainerProvider,
    _llama_argv_segments,
    _llama_launch_plan,
    _render_quadlet_from_plan,
    resolved_command_for_slot,
)
from hal0.slots.argv import resolve_argv


def _render_from_plan(token, plan, *, runtime_bin=None, publish_host="127.0.0.1"):
    """Shim so call sites barely change: the Quadlet renderer no longer takes a
    runtime binary (podman-only via its systemd generator), so accept+ignore it."""
    return _render_quadlet_from_plan(token, plan, publish_host=publish_host)


def _gpu_profile(**overrides: Any) -> ProfileConfig:
    base: dict[str, Any] = dict(flags="-fa on -ctk q8_0 -b 512 --parallel 1", mtp="off")
    base.update(overrides)
    return ProfileConfig(**base)


# ── 1. preview == launch parity (mtp + chat_template + mmproj + slot grid) ────


def test_preview_equals_launch_full_slot() -> None:
    """resolved_command_for_slot renders the exact argv container_spec launches
    for a slot exercising MTP drafting, a chat template, an mmproj sidecar, the
    profile's window and flags, and the slot HW grid (NGL + threads)."""
    cfg = {
        "name": "agent",
        "port": 8101,
        "profile": "rocm",
        "device": "gpu-rocm",
        # spec-hw-slot-ownership §2: NGL + threads are slot-top-level fields.
        "n_gpu_layers": 88,
        "threads": 8,
        "model": {"default": "my-model"},
    }
    model_info = {
        "_model_key": "my-model",
        "path": "/mnt/ai-models/my-model.gguf",
        "mmproj": "/mnt/ai-models/my-model/mmproj-f16.gguf",
        "defaults": {"chat_template": "chatml", "mtp": True},
        "metadata": {"context_length": 262144},
    }

    profile = _gpu_profile(mtp="auto", ctx_size=40000)

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
    assert preview[0] == launch_plan.image
    assert preview[1:] == launch_plan.command
    command = launch_plan.command
    assert command[command.index("--ctx-size") + 1] == "40000"
    assert command[command.index("--chat-template-file") + 1].endswith("chatml.jinja")
    assert "--mmproj" in command
    assert "--spec-type" in command
    assert command[command.index("--spec-draft-device") + 1] == "ROCm0"
    # The slot HW grid reached launch: -ngl 88 --threads 8.
    assert _ngl(command) == "88"
    assert command[command.index("--threads") + 1] == "8"


def test_preview_and_launch_both_refuse_an_unconverted_slot() -> None:
    cfg = {
        "name": "agent",
        "port": 8101,
        "profile": "rocm",
        "device": "gpu-rocm",
        "parallel": 4,
        "model": {"default": "my-model"},
    }
    model_info = {"_model_key": "my-model", "path": "/mnt/ai-models/my-model.gguf"}
    with (
        patch("hal0.providers.container._resolve_profile", return_value=_gpu_profile()),
        patch("hal0.providers.container._best_effort_model_info", return_value=model_info),
    ):
        assert resolved_command_for_slot(cfg) is None
        with pytest.raises(UnprocessableEntity) as excinfo:
            ContainerProvider().container_spec(cfg, model_info)
    assert excinfo.value.code == "slot.unconverted"


# ── 2. ngl / parallel / model-tune precedence ────────────────────────────────


def _ngl(command: list[str]) -> str | None:
    return command[command.index("-ngl") + 1] if "-ngl" in command else None


def _plan(**kw: Any):
    kw.setdefault("context_size", 8192)
    return _llama_launch_plan(
        image="i:1", port=8095, model_path="/m.gguf", devices=[], group_ids=[], **kw
    )


def test_ngl_in_profile_flags_is_denied() -> None:
    """A profile may not set ``-ngl``: the slot's typed NGL owns it, and the
    launch re-screens what the profile write path rejects."""
    with pytest.raises(BadRequest) as exc_info:
        _plan(profile_flags="-fa on -ngl 40", slot_n_gpu_layers=30)
    assert exc_info.value.code == "slot.hardware_flag_denied"


def test_slot_ngl_is_the_only_ngl() -> None:
    assert _ngl(_plan(slot_n_gpu_layers=30).command) == "30"
    assert _ngl(_plan(slot_n_gpu_layers=None).command) is None


def _parval(command: list[str]) -> str | None:
    for flag in ("--parallel", "-np"):
        if flag in command:
            return command[command.index(flag) + 1]
    return None


def test_parallel_from_profile_flags_emits_kv_unified() -> None:
    """Continuous batching is profile tuning: ``--parallel N`` (and its
    authored ``--kv-unified``) in the profile's flags reach launch."""
    plan = _plan(profile_flags="-fa on --parallel 8 --kv-unified")
    assert _parval(plan.command) == "8"
    assert "--kv-unified" in plan.command


def test_model_defaults_extra_args_never_reach_launch() -> None:
    """The model row holds facts only: a stray ``defaults.extra_args`` on a
    model_info dict contributes nothing to the argv."""
    cfg = {
        "name": "gpu-slot",
        "port": 8095,
        "profile": "rocm",
        "device": "gpu-rocm",
        "model": {"default": "m"},
    }
    model_info = {
        "_model_key": "m",
        "path": "/mnt/ai-models/m.gguf",
        "defaults": {"extra_args": "--rope-scaling linear --foo 1"},
    }
    with (
        patch("hal0.providers.container._resolve_profile", return_value=_gpu_profile()),
        patch("hal0.providers.container.resolve_gpu_device_paths", return_value=[]),
        patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[]),
    ):
        spec = ContainerProvider().container_spec(cfg, model_info)
    assert "--rope-scaling" not in spec.command
    assert "--foo" not in spec.command
    assert spec.command[spec.command.index("-ctk") + 1] == "q8_0"  # the profile's tune


# ── 3. cpu slot gets no GPU devices / GIDs ─────────────────────────────────


def test_cpu_slot_gets_no_gpu_plumbing() -> None:
    cfg = {
        "name": "cpu-slot",
        "port": 8095,
        "profile": "cpu-llm",
        "device": "cpu",
        "model": {"default": "m"},
    }
    cpu_profile = _gpu_profile()
    with (
        patch("hal0.providers.container._resolve_profile", return_value=cpu_profile),
        patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ),
        patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[993, 44]),
        patch("hal0.providers.container._dev_node_exists", return_value=True),
    ):
        spec = ContainerProvider().container_spec(
            cfg, {"_model_key": "m", "path": "/mnt/ai-models/m.gguf"}
        )
    assert spec.devices == []
    assert spec.group_add == []


def test_gpu_slot_existence_filters_devices() -> None:
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
        patch("hal0.providers.container._dev_node_exists", side_effect=_exists),
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
    assert _plan().env == {}


# ── 6. GOLDEN: the full ordered launch argv, pinned literally ────────────────
#
# Membership and index-relative lookups are invariant under a REORDER of
# `_llama_argv_segments`' return list. A reorder silently inverts the
# documented precedence chain (ADR-0029 decision 2)
#
#     base < model < profile < slot
#
# — e.g. hoisting `profile` above `slot` hands a profile file the power to
# override the slot's typed hardware grid. These tests pin the literal,
# fully-ordered argv. Any reorder, insertion, removal, or renaming of a
# segment fails them with a readable diff.

_BUNDLE = shlex.split(MTP_DRAFT_BUNDLE)

#: Every layer populated, with a deliberate profile-over-bundle collision
#: (`--spec-draft-n-max`: bundle 4, profile 8). Update this literal ONLY
#: together with a deliberate, documented change to the launch shape.
GOLDEN_MAXIMAL_ARGV: list[str] = [
    # base (hal0-managed)
    "--host", "0.0.0.0",
    "--port", "8081",
    "--model", "/var/lib/hal0/models/qwen3-4b/model.gguf",
    "--alias", "hal0/qwen3-4b",
    "--ctx-size", "32768",
    # model (facts)
    "--jinja",
    "--chat-template-file", "/etc/hal0/templates/qwen3.jinja",
    "--mmproj", "/var/lib/hal0/models/qwen3-4b/mmproj.gguf",
    # profile: the MTP bundle (its --spec-draft-n-max 4 lost) ...
    "--spec-type", "draft-mtp",
    "--spec-draft-ngl", "all",
    "--spec-draft-n-min", "0",
    "--spec-draft-p-min", "0.0",
    "--spec-draft-p-split", "0.10",
    "--spec-draft-type-k", "q8_0",
    "--spec-draft-type-v", "q8_0",
    "--spec-draft-threads", "16",
    "--spec-draft-threads-batch", "32",
    "--spec-draft-poll", "1",
    "--spec-draft-poll-batch", "1",
    # ... then the profile's own flags
    "-fa", "on",
    "-b", "2048",
    "-ub", "512",
    "--spec-draft-n-max", "8",
    # slot (placement)
    "-ngl", "99",
    "--threads", "16",
    "--metrics",
    "--spec-draft-device", "Vulkan0",
]  # fmt: skip

_MAXIMAL_KW: dict[str, Any] = dict(
    port=8081,
    model_path="/var/lib/hal0/models/qwen3-4b/model.gguf",
    model_alias="hal0/qwen3-4b",
    context_size=32768,
    jinja=True,
    chat_template_path="/etc/hal0/templates/qwen3.jinja",
    mmproj="/var/lib/hal0/models/qwen3-4b/mmproj.gguf",
    mtp=True,
    profile_flags="-fa on -b 2048 -ub 512 --spec-draft-n-max 8",
    slot_n_gpu_layers=99,
    slot_threads=16,
    draft_device="Vulkan0",
)


def _maximal_segments() -> list[tuple[str, list[str]]]:
    return _llama_argv_segments(**_MAXIMAL_KW)


def test_golden_full_ordered_launch_argv() -> None:
    """LITERAL equality on the whole argv — the reorder tripwire."""
    assert resolve_argv(_maximal_segments()).argv == GOLDEN_MAXIMAL_ARGV


def test_golden_segment_order_is_the_documented_precedence_chain() -> None:
    """The labels, in order. `set()` comparisons elsewhere cannot catch a swap;
    this is a list comparison."""
    labels = [label for label, _tokens in _maximal_segments()]
    assert labels == ["base", "model", "profile", "slot"]


def test_golden_provenance_pins_which_segment_won_each_flag() -> None:
    """The other half of the pin: not just the token order, but WHICH layer
    won every surviving flag."""
    resolved = resolve_argv(_maximal_segments())
    bundle_pairs = [
        (_BUNDLE[i], _BUNDLE[i + 1], "profile")
        for i in range(0, len(_BUNDLE), 2)
        if _BUNDLE[i] != "--spec-draft-n-max"
    ]
    assert [(p.flag, p.value, p.source) for p in resolved.provenance] == [
        ("--host", "0.0.0.0", "base"),
        ("--port", "8081", "base"),
        ("--model", "/var/lib/hal0/models/qwen3-4b/model.gguf", "base"),
        ("--alias", "hal0/qwen3-4b", "base"),
        ("--ctx-size", "32768", "base"),
        ("--jinja", None, "model"),
        ("--chat-template-file", "/etc/hal0/templates/qwen3.jinja", "model"),
        ("--mmproj", "/var/lib/hal0/models/qwen3-4b/mmproj.gguf", "model"),
        *bundle_pairs,
        ("-fa", "on", "profile"),
        ("-b", "2048", "profile"),
        ("-ub", "512", "profile"),
        ("--spec-draft-n-max", "8", "profile"),
        ("-ngl", "99", "slot"),
        ("--threads", "16", "slot"),
        ("--metrics", None, "slot"),
        ("--spec-draft-device", "Vulkan0", "slot"),
    ]
    # exactly one token pair was dropped: the bundle's losing --spec-draft-n-max 4
    assert resolved.removed == 1


def test_golden_launch_plan_command_equals_the_golden_argv() -> None:
    """The plan the load path actually renders — `_llama_launch_plan` collapses
    the same segments — carries the identical argv."""
    plan = _llama_launch_plan(
        image="ghcr.io/hal0ai/hal0-runner-standard:test",
        devices=[],
        group_ids=[],
        **_MAXIMAL_KW,
    )
    assert plan.command == GOLDEN_MAXIMAL_ARGV


def test_golden_minimal_argv() -> None:
    """The floor: a port, a model path and the window. Pins that no layer
    leaks a default token in when its input is absent. ``slot_metrics``
    defaults True (#1810), so it is explicitly disabled here; see
    ``test_golden_minimal_argv_defaults_metrics_on`` for the real default."""
    argv = resolve_argv(
        _llama_argv_segments(port=8081, model_path="/m.gguf", context_size=4096, slot_metrics=False)
    ).argv
    assert argv == [
        "--host",
        "0.0.0.0",
        "--port",
        "8081",
        "--model",
        "/m.gguf",
        "--ctx-size",
        "4096",
    ]


def test_golden_minimal_argv_defaults_metrics_on() -> None:
    """#1810: with no explicit ``slot_metrics``, the slot layer still emits
    ``--metrics`` — the default is ON, not "absent"."""
    argv = resolve_argv(
        _llama_argv_segments(port=8081, model_path="/m.gguf", context_size=4096)
    ).argv
    assert argv == [
        "--host", "0.0.0.0",
        "--port", "8081",
        "--model", "/m.gguf",
        "--ctx-size", "4096",
        "--metrics",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("flags", "code"),
    [
        ("--chat-template-file /tmp/evil.jinja", "profiles.model_fact_flag_denied"),
        ("--mmproj /tmp/evil.gguf", "profiles.model_fact_flag_denied"),
        ("--no-mmproj", "profiles.model_fact_flag_denied"),
        ("--jinja", "profiles.model_fact_flag_denied"),
        ("--spec-draft-device CUDA0", "profiles.model_fact_flag_denied"),
        ("--threads 1", "slot.hardware_flag_denied"),
        ("-ngl 0", "slot.hardware_flag_denied"),
        ("--ctx-size 4096", "slot.managed_arg_denied"),
        ("--model /tmp/evil.gguf", "slot.managed_arg_denied"),
    ],
)
def test_golden_profile_cannot_claim_another_layers_flags(flags: str, code: str) -> None:
    """Adversarial ordering proof. The profile layer sits between the model
    and slot layers, so a profile carrying a model-fact, managed or hardware
    flag could otherwise silently replace the real projector, template, window
    or placement. The launch refuses it instead (a profile file is editable on
    disk, so the save-time screen is repeated)."""
    with pytest.raises(BadRequest) as excinfo:
        resolve_argv(
            _llama_argv_segments(
                port=8081,
                model_path="/m.gguf",
                context_size=4096,
                profile_flags=f"-fa on {flags}",
                slot_threads=16,
                slot_n_gpu_layers=0,
                chat_template_path="/etc/hal0/templates/real.jinja",
                mmproj="/models/real-mmproj.gguf",
            )
        )
    assert excinfo.value.code == code
