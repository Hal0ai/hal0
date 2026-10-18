"""Golden path #5 (pull→assign→infer) — the ADR-0029 launch layering.

llama-server argv is composed from four layers, lowest precedence first, then
deduplicated last-wins by :func:`hal0.slots.argv.resolve_argv`:

1. ``base``    — ``--host --port --model --alias --ctx-size`` (hal0-managed;
   ``--ctx-size`` is the profile's ``ctx_size`` rule);
2. ``model``   — ``--jinja``, ``--chat-template-file``, ``--mmproj`` (facts
   about the file; the projector only when the profile's ``vision`` is
   ``auto``);
3. ``profile`` — the MTP draft bundle when the profile's MTP is in effect,
   then the profile's own ``flags`` (so its ``--spec-draft-*`` values win);
4. ``slot``    — ``-ngl``, ``--threads``, ``--metrics`` and the
   ``--spec-draft-device`` derived from the slot's device.

The profile is read live at every launch — there is no copy-on-stamp — so two
slots running one model under two profiles launch two different tunes.
"""

from __future__ import annotations

import shlex

import pytest

from hal0.config.schema import MTP_DRAFT_BUNDLE, SEED_PROFILES, ProfileConfig
from hal0.errors import UnprocessableEntity
from hal0.profiles import ResolvedProfile, resolve_loaded_profile
from hal0.providers.container import (
    _llama_argv_segments,
    _resolve_llama_scalars,
    _segments_from_scalars,
    resolve_slot_profile,
)
from hal0.slots.argv import resolve_argv

_MODEL_PATH = "/models/qwen-mtp.gguf"


def _seed(name: str) -> ResolvedProfile:
    return resolve_loaded_profile(name, ProfileConfig.model_validate(SEED_PROFILES[name]))


def _custom(name: str, **fields: object) -> ResolvedProfile:
    return resolve_loaded_profile(name, ProfileConfig(**fields))


def _slot(profile: str, *, device: str = "gpu-vulkan") -> dict:
    return {
        "name": "agent",
        "profile": profile,
        "port": 8081,
        "device": device,
        "n_gpu_layers": 99,
        "threads": 8,
        "model": {"default": "qwen-mtp"},
    }


def _model(**extra: object) -> dict:
    info: dict = {
        "_model_key": "qwen-mtp",
        "path": _MODEL_PATH,
        "mmproj": "/models/qwen-mmproj.gguf",
        "tags": ["mtp"],
        "metadata": {"context_length": 262144},
    }
    info.update(extra)
    return info


def _launch(slot: dict, model: dict, profile: ResolvedProfile) -> list[tuple[str, list[str]]]:
    scalars = _resolve_llama_scalars(slot, model, profile, for_launch=False)
    return _segments_from_scalars(8081, model["path"], scalars)


def test_argv_builder_emits_exactly_the_four_layers_in_order() -> None:
    segments = _llama_argv_segments(
        port=8081,
        model_path=_MODEL_PATH,
        model_alias="qwen-mtp",
        context_size=65536,
        jinja=True,
        chat_template_path="/models/chat-templates/qwen.jinja",
        mmproj="/models/qwen-mmproj.gguf",
        mtp=True,
        profile_flags="-fa auto -ctk q8_0 --parallel 2 --spec-draft-n-max 8",
        slot_n_gpu_layers=99,
        slot_threads=8,
        slot_metrics=True,
        draft_device="Vulkan0",
    )
    assert [label for label, _ in segments] == ["base", "model", "profile", "slot"]
    by_label = dict(segments)
    assert by_label["base"] == [
        "--host",
        "0.0.0.0",
        "--port",
        "8081",
        "--model",
        _MODEL_PATH,
        "--alias",
        "qwen-mtp",
        "--ctx-size",
        "65536",
    ]
    assert by_label["model"] == [
        "--jinja",
        "--chat-template-file",
        "/models/chat-templates/qwen.jinja",
        "--mmproj",
        "/models/qwen-mmproj.gguf",
    ]
    # The MTP bundle first, then the profile's own flags.
    bundle = ["--spec-type", "draft-mtp", *shlex.split(MTP_DRAFT_BUNDLE)]
    assert by_label["profile"][: len(bundle)] == bundle
    assert by_label["profile"][len(bundle) :] == [
        "-fa",
        "auto",
        "-ctk",
        "q8_0",
        "--parallel",
        "2",
        "--spec-draft-n-max",
        "8",
    ]
    assert by_label["slot"] == [
        "-ngl",
        "99",
        "--threads",
        "8",
        "--metrics",
        "--spec-draft-device",
        "Vulkan0",
    ]

    resolved = resolve_argv(segments)
    prov = {p.flag: p.source for p in resolved.provenance}
    assert prov["--ctx-size"] == "base"
    assert prov["--mmproj"] == "model"
    assert prov["--parallel"] == "profile"
    assert prov["-ngl"] == "slot"
    assert prov["--spec-draft-device"] == "slot"
    # The profile's --spec-draft-n-max follows the bundle's 4 and wins.
    argv = resolved.argv
    assert argv[argv.index("--spec-draft-n-max") + 1] == "8"
    assert argv.count("--spec-draft-n-max") == 1


def test_agent_seed_on_a_vulkan_slot_golden_argv() -> None:
    """End to end through the shared scalar resolver: the profile owns the
    window (65536) and turns vision off; the model contributes --jinja and
    its MTP heads; the slot contributes placement and the Vulkan draft
    device."""
    argv = resolve_argv(_launch(_slot("agent"), _model(), _seed("agent"))).argv
    assert argv == [
        "--host",
        "0.0.0.0",
        "--port",
        "8081",
        "--model",
        _MODEL_PATH,
        "--alias",
        "qwen-mtp",
        "--ctx-size",
        "65536",
        "--jinja",
        "--spec-type",
        "draft-mtp",
        *shlex.split(MTP_DRAFT_BUNDLE),
        *shlex.split(str(SEED_PROFILES["agent"]["flags"])),
        "-ngl",
        "99",
        "--threads",
        "8",
        "--metrics",
        "--spec-draft-device",
        "Vulkan0",
    ]
    assert "--mmproj" not in argv  # the agent seed's vision = "off"


@pytest.mark.parametrize(
    ("device", "draft_device"),
    [("gpu-rocm", "ROCm0"), ("gpu-vulkan", "Vulkan0")],
)
def test_the_draft_device_follows_the_slots_device(device: str, draft_device: str) -> None:
    argv = resolve_argv(_launch(_slot("agent", device=device), _model(), _seed("agent"))).argv
    assert argv[argv.index("--spec-draft-device") + 1] == draft_device


def test_a_runner_without_mtp_support_never_drafts() -> None:
    """The CUDA runner has no MTP support: a model with heads under an
    ``speculation = "auto"`` profile launches without the bundle or a draft device."""
    argv = resolve_argv(_launch(_slot("agent", device="gpu-cuda"), _model(), _seed("agent"))).argv
    assert "--spec-type" not in argv
    assert "--spec-draft-device" not in argv


def test_the_profile_is_read_live_so_one_model_runs_two_tunes() -> None:
    """Two slots, one model, two profiles: two launches. Nothing is stamped
    onto the model, so neither slot's tune leaks into the other."""
    model = _model()
    fast = _custom("fast", flags="-fa auto -ctk q8_0 -ctv q8_0", ctx_size=16384)
    long = _custom("long", flags="-fa auto -b 4096", ctx_size=131072, speculation="off")

    fast_argv = resolve_argv(_launch(_slot("fast"), model, fast)).argv
    long_argv = resolve_argv(_launch(_slot("long"), model, long)).argv

    assert fast_argv[fast_argv.index("--ctx-size") + 1] == "16384"
    assert long_argv[long_argv.index("--ctx-size") + 1] == "131072"
    assert "-ctk" in fast_argv and "-ctk" not in long_argv
    assert "--spec-type" in fast_argv  # model heads + speculation auto
    assert "--spec-type" not in long_argv  # the profile's speculation = "off"
    assert "--spec-draft-device" not in long_argv
    assert "--mmproj" in fast_argv and "--mmproj" in long_argv  # vision auto


def test_unset_ctx_size_is_the_native_window_dense_capped() -> None:
    profile = _custom("plain", flags="-fa auto")
    argv = resolve_argv(_launch(_slot("plain"), _model(), profile)).argv
    assert argv[argv.index("--ctx-size") + 1] == "32768"
    small = resolve_argv(
        _launch(_slot("plain"), _model(metadata={"context_length": 4096}), profile)
    ).argv
    assert small[small.index("--ctx-size") + 1] == "4096"


def test_embedding_mode_drops_jinja_and_mtp() -> None:
    """``--jinja`` and MTP drafting are meaningless in embedding mode."""
    argv = resolve_argv(_launch(_slot("embedding"), _model(), _seed("embedding"))).argv
    assert "--embedding" in argv
    assert "--jinja" not in argv
    assert "--spec-type" not in argv
    assert argv[argv.index("--ctx-size") + 1] == "4096"


def test_a_model_without_mtp_heads_never_drafts() -> None:
    argv = resolve_argv(_launch(_slot("agent"), _model(tags=[]), _seed("agent"))).argv
    assert "--spec-type" not in argv
    assert "--spec-draft-device" not in argv


def test_a_profile_carrying_a_model_fact_flag_refuses_to_launch() -> None:
    """A hand-edited profile file is re-screened at launch."""
    from hal0.errors import BadRequest

    bad = _custom("bad", flags="-fa auto --no-mmproj")
    with pytest.raises(BadRequest) as exc:
        resolve_argv(_launch(_slot("bad"), _model(), bad))
    assert exc.value.code == "profiles.model_fact_flag_denied"


def test_a_slot_naming_no_profile_launches_its_types_seed(tmp_hal0_home: str) -> None:
    assert resolve_slot_profile({"name": "embed", "type": "embedding"}).name == "embedding"
    assert resolve_slot_profile({"name": "rerank", "type": "reranking"}).name == "reranking"
    assert resolve_slot_profile({"name": "chat", "type": "llm"}).name == "chat"


def test_a_slot_naming_a_missing_profile_refuses_to_launch(tmp_hal0_home: str) -> None:
    """No silent fallback to some other tune."""
    with pytest.raises(UnprocessableEntity) as exc:
        resolve_slot_profile({"name": "agent", "profile": "no-such-profile"})
    assert exc.value.code == "slot.profile_missing"
    assert exc.value.details == {"slot": "agent", "profile": "no-such-profile"}
