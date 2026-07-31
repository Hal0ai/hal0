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
    base: dict[str, Any] = dict(
        flags="-fa on -ctk q8_0 -b 512 --parallel 1",
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
    chat-template, an mmproj sidecar, the slot HW grid (NGL + threads), and
    extra_args."""
    cfg = {
        "name": "agent",
        "port": 8101,
        "profile": "rocm",
        "device": "gpu-rocm",
        # spec-hw-slot-ownership §2: NGL + threads are slot-top-level fields.
        "n_gpu_layers": 88,
        "threads": 8,
        # mtp/vision here are inert legacy keys (spec-hw-slot-ownership §1:
        # both are model-owned now, SlotConfig declares neither) — kept in
        # the fixture to prove preview/launch parity is unaffected by their
        # presence, not because they do anything.
        "mtp": True,
        "chat_template": "chatml",
        "vision": True,
        "model": {"default": "my-model", "context_size": 40000},
        "server": {"extra_args": "-b 8192 --jinja"},
    }
    model_info = {
        "_model_key": "my-model",
        "path": "/mnt/ai-models/my-model.gguf",
        "mmproj": "/mnt/ai-models/my-model/mmproj-f16.gguf",
        "defaults": {"extra_args": "--rope-scaling linear"},
        "metadata": {"context_length": 262144},
    }

    profile = _gpu_profile(mtp=False)

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
    # preview = [image, *argv]; launch command excludes the image. Image is the
    # HW-gated default now (profile.image is ignored — spec §3), so assert
    # parity against the launch plan's own image rather than profile.image.
    assert preview[0] == launch_plan.image
    assert preview[1:] == launch_plan.command
    # The slot HW grid reached launch: -ngl 88 --threads 8.
    assert _ngl(launch_plan.command) == "88"
    assert "--threads" in launch_plan.command
    assert launch_plan.command[launch_plan.command.index("--threads") + 1] == "8"


# ── 2. model-default / ngl precedence ─────────────────────────────────────────


def _ngl(command: list[str]) -> str | None:
    return command[command.index("-ngl") + 1] if "-ngl" in command else None


def test_ngl_in_model_extra_args_is_denied() -> None:
    """§21.7 (FLAGS-own): the managed-arg denylist is PRESERVED on the model's
    materialized tune. ``-ngl`` is managed (the typed ``defaults.n_gpu_layers``
    is the sanctioned channel) — a model's free-form ``defaults.extra_args`` may
    not smuggle it, so the screened ``model_extra_args`` segment raises."""
    with pytest.raises(BadRequest) as exc_info:
        _llama_launch_plan(
            image="i:1",
            port=8095,
            model_path="/m.gguf",
            flags_str="",
            devices=[],
            group_ids=[],
            model_defaults={"extra_args": "-ngl 40"},
        )
    assert exc_info.value.code == "slot.managed_arg_denied"


def test_slot_ngl_wins_and_model_ngl_key_ignored() -> None:
    """spec-hw-slot-ownership §2 (reverses the §5 fold): the slot owns NGL. The
    slot's ``-ngl`` reaches launch; a stray ``defaults.n_gpu_layers`` key on the
    model is ignored (that field was deleted) and never emits ``-ngl``."""
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="-ngl 10",  # inert profile flag (profile flags don't launch)
        devices=[],
        group_ids=[],
        model_defaults={"n_gpu_layers": 20},  # deleted field's key — ignored
        slot_n_gpu_layers=30,  # authoritative slot NGL
    )
    assert _ngl(plan.command) == "30"  # slot wins; not model 20, not profile 10


def test_model_defaults_ngl_key_never_emits_without_slot_ngl() -> None:
    """With no slot NGL supplied, a leftover ``defaults.n_gpu_layers`` key does
    NOT resurrect a model ``-ngl`` — model NGL ownership is gone entirely."""
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="-ngl 10",
        devices=[],
        group_ids=[],
        model_defaults={"n_gpu_layers": 20},
        slot_n_gpu_layers=None,  # no slot NGL → no -ngl at all
    )
    assert _ngl(plan.command) is None


def _parval(command: list[str]) -> str | None:
    for flag in ("--parallel", "-np"):
        if flag in command:
            return command[command.index(flag) + 1]
    return None


def test_slot_parallel_is_inert() -> None:
    """FLAGS-own: the slot ``parallel`` knob no longer reaches launch. A model
    that wants continuous batching carries ``--parallel N`` (+ ``--kv-unified``)
    in its own ``defaults.extra_args`` (the migrator folds it there)."""
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="-fa on --parallel 1",  # inert profile flag
        devices=[],
        group_ids=[],
        slot_parallel=8,  # inert slot override
    )
    assert _parval(plan.command) is None  # neither profile's 1 nor slot's 8
    assert "--kv-unified" not in plan.command


def test_parallel_from_model_extra_args_emits_kv_unified() -> None:
    """The model's OWN tune drives batching now: a folded ``--parallel N`` in
    ``defaults.extra_args`` reaches launch (and its authored ``--kv-unified``)."""
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="",
        devices=[],
        group_ids=[],
        model_defaults={"extra_args": "-fa on --parallel 8 --kv-unified"},
    )
    assert _parval(plan.command) == "8"
    assert "--kv-unified" in plan.command


def test_model_default_extra_args_emitted_slot_extra_args_inert() -> None:
    """The model's ``defaults.extra_args`` reaches launch (screened
    model_extra_args segment). FLAGS-own: a slot ``[server].extra_args`` is
    inert and can NOT override a model default anymore."""
    plan = _llama_launch_plan(
        image="i:1",
        port=8095,
        model_path="/m.gguf",
        flags_str="",
        devices=[],
        group_ids=[],
        model_defaults={"extra_args": "--rope-scaling linear --foo 1"},
        extra_args="--foo 2",  # inert slot extra_args — does NOT win
    )
    assert "--rope-scaling" in plan.command
    assert plan.command[plan.command.index("--rope-scaling") + 1] == "linear"
    assert plan.command.count("--foo") == 1
    assert plan.command[plan.command.index("--foo") + 1] == "1"  # model's, slot inert


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


# ── 6. GOLDEN: the full ordered launch argv, pinned literally ────────────────
#
# Everything above (and every other argv test in the tree) asserts SEGMENT
# MEMBERSHIP — `"-ngl" in command`, `command[command.index("--threads") + 1]`,
# `set(labels) == {...}`. Membership and index-relative lookups are invariant
# under a REORDER of `_llama_argv_segments`' return list, so today the entire
# suite passes if someone swaps `chat_template` and `mmproj`, or hoists
# `slot_hardware` above `model_extra_args`. That last one silently inverts the
# documented precedence chain
#
#     base < model_extra_args < slot_hardware < chat_template < mmproj
#
# and hands a model's `defaults.extra_args` the power to override the slot's
# typed hardware grid — the exact ownership inversion spec-hw-slot-ownership §2
# exists to prevent.
#
# These tests pin the literal, fully-ordered argv. Any reorder, insertion,
# removal, or renaming of a segment fails them with a readable diff.


#: Every segment populated, with a deliberate cross-segment `--threads`
#: collision (model says 3, slot says 16). Update this literal ONLY together
#: with a deliberate, documented change to the launch shape.
GOLDEN_MAXIMAL_ARGV: list[str] = [
    # base
    "--host", "0.0.0.0",
    "--port", "8081",
    "--model", "/var/lib/hal0/models/qwen3-4b/model.gguf",
    "--alias", "hal0/qwen3-4b",
    "--ctx-size", "32768",
    # model_extra_args (the model's logical tune; its --threads 3 lost)
    "-fa", "on",
    "-b", "2048",
    "-ub", "512",
    "--jinja",
    # slot_hardware (the slot's typed physical grid — wins the collision)
    "-ngl", "99",
    "--threads", "16",
    # chat_template
    "--chat-template-file", "/etc/hal0/templates/qwen3.jinja",
    # mmproj
    "--mmproj", "/var/lib/hal0/models/qwen3-4b/mmproj.gguf",
]  # fmt: skip


def _maximal_segments() -> list[tuple[str, list[str]]]:
    """Every segment populated. `profile_flags`, `slot_parallel` and
    `extra_args` are threaded in deliberately: they are inert (deleted at the
    top of the builder) and must contribute NOTHING to the golden argv."""
    return _llama_argv_segments(
        port=8081,
        model_path="/var/lib/hal0/models/qwen3-4b/model.gguf",
        model_alias="hal0/qwen3-4b",
        context_size=32768,
        model_defaults={"extra_args": "-fa on -b 2048 -ub 512 --threads 3 --jinja"},
        slot_n_gpu_layers=99,
        slot_threads=16,
        chat_template_path="/etc/hal0/templates/qwen3.jinja",
        mmproj="/var/lib/hal0/models/qwen3-4b/mmproj.gguf",
        # inert — must not appear anywhere in GOLDEN_MAXIMAL_ARGV
        profile_flags="-ctk q8_0 -ctv q8_0",
        slot_parallel=4,
        extra_args="--no-webui",
    )


def test_golden_full_ordered_launch_argv() -> None:
    """LITERAL equality on the whole argv — the reorder tripwire."""
    assert resolve_argv(_maximal_segments()).argv == GOLDEN_MAXIMAL_ARGV


def test_golden_segment_order_is_the_documented_precedence_chain() -> None:
    """The labels, in order. `set()` comparisons elsewhere cannot catch a swap;
    this is a list comparison."""
    labels = [label for label, _tokens in _maximal_segments()]
    assert labels == [
        "base",
        "model_extra_args",
        "slot_hardware",
        "chat_template",
        "mmproj",
    ]


def test_golden_provenance_pins_which_segment_won_each_flag() -> None:
    """The other half of the pin: not just the token order, but WHICH source
    won every surviving flag. A reorder that happened to preserve token order
    would still move a winner."""
    resolved = resolve_argv(_maximal_segments())
    assert [(p.flag, p.value, p.source) for p in resolved.provenance] == [
        ("--host", "0.0.0.0", "base"),
        ("--port", "8081", "base"),
        ("--model", "/var/lib/hal0/models/qwen3-4b/model.gguf", "base"),
        ("--alias", "hal0/qwen3-4b", "base"),
        ("--ctx-size", "32768", "base"),
        ("-fa", "on", "model_extra_args"),
        ("-b", "2048", "model_extra_args"),
        ("-ub", "512", "model_extra_args"),
        ("--jinja", None, "model_extra_args"),
        ("-ngl", "99", "slot_hardware"),
        ("--threads", "16", "slot_hardware"),
        ("--chat-template-file", "/etc/hal0/templates/qwen3.jinja", "chat_template"),
        ("--mmproj", "/var/lib/hal0/models/qwen3-4b/mmproj.gguf", "mmproj"),
    ]
    # exactly one token pair was dropped: the model's losing `--threads 3`
    assert resolved.removed == 1


def test_golden_launch_plan_command_equals_the_golden_argv() -> None:
    """The plan the load path actually renders — `_llama_launch_plan` collapses
    the same segments — carries the identical argv. Pins the plan level too, so
    a change to how the plan assembles the command cannot drift from the
    builder."""
    plan = _llama_launch_plan(
        image="ghcr.io/hal0ai/hal0-rocmfpx:test",
        port=8081,
        model_path="/var/lib/hal0/models/qwen3-4b/model.gguf",
        flags_str="-ctk q8_0 -ctv q8_0",
        devices=[],
        group_ids=[],
        context_size=32768,
        extra_args="--no-webui",
        model_alias="hal0/qwen3-4b",
        chat_template_path="/etc/hal0/templates/qwen3.jinja",
        mmproj="/var/lib/hal0/models/qwen3-4b/mmproj.gguf",
        model_defaults={"extra_args": "-fa on -b 2048 -ub 512 --threads 3 --jinja"},
        slot_n_gpu_layers=99,
        slot_threads=16,
        slot_parallel=4,
    )
    assert plan.command == GOLDEN_MAXIMAL_ARGV


def test_golden_minimal_argv() -> None:
    """The floor: nothing but a port and a model path. Pins that no segment
    leaks a default token in when its input is absent."""
    argv = resolve_argv(_llama_argv_segments(port=8081, model_path="/m.gguf")).argv
    assert argv == ["--host", "0.0.0.0", "--port", "8081", "--model", "/m.gguf"]


def test_golden_later_segments_beat_a_model_tune_claiming_their_flags() -> None:
    """Adversarial ordering proof. `--chat-template-file` and `--mmproj` are NOT
    in `MANAGED_ARGS_DENYLIST`, so a model's `defaults.extra_args` can smuggle
    them through the screen. They must still lose to the real `chat_template` /
    `mmproj` segments — which is true only because those segments come LAST.
    Hoist `model_extra_args` and hal0 loads the wrong projector."""
    argv = resolve_argv(
        _llama_argv_segments(
            port=8081,
            model_path="/m.gguf",
            context_size=4096,
            model_defaults={
                "extra_args": (
                    "--chat-template-file /tmp/evil.jinja --mmproj /tmp/evil.gguf --threads 1"
                )
            },
            slot_threads=16,
            slot_n_gpu_layers=0,
            chat_template_path="/etc/hal0/templates/real.jinja",
            mmproj="/models/real-mmproj.gguf",
        )
    ).argv
    assert argv == [
        "--host", "0.0.0.0",
        "--port", "8081",
        "--model", "/m.gguf",
        "--ctx-size", "4096",
        "-ngl", "0",
        "--threads", "16",
        "--chat-template-file", "/etc/hal0/templates/real.jinja",
        "--mmproj", "/models/real-mmproj.gguf",
    ]  # fmt: skip
    assert "/tmp/evil.jinja" not in argv
    assert "/tmp/evil.gguf" not in argv
