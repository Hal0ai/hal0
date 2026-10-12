"""Parity: the converter's frozen launch resolution equals the live one.

``hal0.config.migrations.profile_centric_frozen`` answers "what does this slot
launch today?" after the release that ships ADR-0029 rewrites the real
resolver. This file pins it against that real resolver while both still
exist: every case runs the live path (``_resolve_profile_or_base`` ->
``_resolve_llama_scalars`` -> ``_llama_argv_segments`` -> ``resolve_argv``, the
same chain ``ContainerProvider.container_spec`` runs) and the frozen
``evaluate`` over the same slot, model and catalog, and asserts the identical
argv. The frozen side reads the model as a raw DB row (``model_to_row``), so
the row mapping is covered too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import tomli_w

from hal0.config import paths
from hal0.config.migrations import profile_centric_frozen as frozen
from hal0.db.repository import model_to_row
from hal0.errors import BadRequest
from hal0.providers import container as C
from hal0.registry.model import Model, ModelDefaults
from hal0.slots.argv import resolve_argv

CUSTOM_PROFILES: dict[str, Any] = {
    "legacy_seeds_migrated": True,
    "legacy_seeds_adopted": ["coding", "dense", "moe", "thinking", "chadrock-moe"],
    "profile": {
        "thinking": {
            "flags": (
                "--jinja -fa auto -b 2048 -ub 512 --reasoning on --reasoning-format deepseek "
                "--temp 1.0 --no-mmproj"
            ),
            "mtp": False,
        },
        "coding": {"flags": "--jinja -fa auto -b 2048 --temp 0.7 --top-k 40", "mtp": False},
        "hw-heavy": {"flags": "-ngl 999 --threads 4 -fa on -b 1024 -c 4096", "mtp": False},
        "fmt-none": {"flags": "-fa auto --reasoning-format none --temp 0.1", "mtp": False},
        "gpu-only": {"flags": "-fa on", "device_class": "gpu", "mtp": False},
        "cpu-only": {"flags": "-b 64", "device_class": "cpu", "mtp": False},
        "on-flm": {"flags": "", "runner": "flm", "mtp": False},
        "vk-hint": {"flags": "-b 77", "backend": "vulkan", "mtp": False},
        "fmt-none-joined": {"flags": "-fa auto --reasoning-format=none --temp 0.2", "mtp": False},
    },
}


def _write_profiles(raw: dict[str, Any] | None) -> None:
    target = paths.profiles_toml()
    target.parent.mkdir(parents=True, exist_ok=True)
    if raw is None:
        target.unlink(missing_ok=True)
        return
    target.write_bytes(tomli_w.dumps(raw).encode())


def _model(model_id: str = "m1", **kw: Any) -> Model:
    defaults = kw.pop("defaults", None)
    return Model(
        id=model_id,
        path=kw.pop("path", f"/models/{model_id}.gguf"),
        defaults=ModelDefaults(**defaults) if defaults else None,
        **kw,
    )


def _live(slot: dict[str, Any], model: Model) -> tuple[list[str], int]:
    info: dict[str, Any] = {"_model_key": model.id, **model.model_dump()}
    profile = C._resolve_profile_or_base(str(slot.get("profile") or ""), slot)
    sc = C._resolve_llama_scalars(slot, info, profile)
    segments = C._llama_argv_segments(
        port=int(slot.get("port") or 0),
        model_path=str(info.get("path") or ""),
        model_alias=sc["model_alias"],
        context_size=sc["context_size"],
        profile_flags=sc["flags_str"],
        model_defaults=sc["model_defaults"],
        chat_template_path=sc["chat_template_path"],
        mmproj=sc["mmproj"],
        slot_n_gpu_layers=sc["slot_n_gpu_layers"],
        slot_threads=sc["slot_threads"],
        slot_parallel=None,
        slot_metrics=sc["slot_metrics"],
        extra_args=sc["extra_args"],
        slot_profile_flags=sc["slot_profile_flags"],
        slot_profile_template_flags=sc["slot_profile_template_flags"],
    )
    return resolve_argv(segments).argv, int(sc["context_size"])


def _frozen(slot: dict[str, Any], model: Model, raw: dict[str, Any] | None) -> frozen.LaunchToday:
    row = model_to_row(model)
    info = frozen.model_info_from_row(row)
    return frozen.evaluate(
        frozen.hoist_slot(slot, str(slot.get("name") or "s")),
        info,
        frozen.Catalog(raw),
        chat_template_root=C.model_store_root(),
    )


def _slot(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "s",
        "type": "llm",
        "port": 8081,
        "device": "gpu-rocm",
        "model": {"default": "m1"},
    }
    model_tbl = kw.pop("model", None)
    base.update(kw)
    if model_tbl is not None:
        base["model"] = {**base["model"], **model_tbl}
    return base


THINK_TUNE = "--jinja -fa auto -b 2048 -ub 512 --reasoning off --temp 0.7 --no-mmproj --parallel 1"

CASES: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    ("seed_template_no_model_tune", _slot(profile="chat"), {}),
    (
        "aligned_stamp",
        _slot(profile="coding"),
        {"defaults": {"profile": "coding", "extra_args": "-fa auto --temp 0.7"}},
    ),
    (
        "divergent_overlay",
        _slot(profile="brain"),
        {"defaults": {"profile": "coding", "extra_args": THINK_TUNE}},
    ),
    (
        "hand_tune_no_provenance",
        _slot(profile="chat"),
        {"defaults": {"extra_args": "-b 512 --temp 0.2"}},
    ),
    ("custom_profile_template", _slot(profile="thinking"), {}),
    (
        "custom_hw_flags_overlay",
        _slot(profile="hw-heavy"),
        {"defaults": {"profile": "chat", "extra_args": "-b 4096"}},
    ),
    ("custom_hw_flags_template", _slot(profile="hw-heavy"), {}),
    ("reasoning_format_none", _slot(profile="fmt-none"), {}),
    (
        "missing_profile_falls_back",
        _slot(profile="vulkan-dense"),
        {"defaults": {"extra_args": "-b 128"}},
    ),
    ("empty_profile", _slot(type="embedding"), {}),
    ("embedding_template", _slot(type="embedding", profile="embedding"), {}),
    (
        "embedding_model_mode_no_jinja",
        _slot(type="embedding", profile="embedding"),
        {"defaults": {"extra_args": "--embedding -b 8192"}},
    ),
    (
        "ceiling_below_declared",
        _slot(profile="chat", model={"context_size": 4096}),
        {"defaults": {"context_size": 16384}},
    ),
    (
        "ceiling_above_declared",
        _slot(profile="chat", model={"context_size": 65536}),
        {"defaults": {"context_size": 16384}},
    ),
    (
        "ceiling_replaces_dense_cap",
        _slot(profile="thinking", model={"context_size": 96000}),
        {"metadata": {"context_length": 262144}},
    ),
    ("native_dense_capped", _slot(profile="chat"), {"metadata": {"context_length": 262144}}),
    ("no_native_ceiling_unhonoured", _slot(profile="chat", model={"context_size": 98304}), {}),
    ("implausible_ceiling_ignored", _slot(profile="chat", model={"context_size": 7}), {}),
    ("mtp_tag_rocm", _slot(profile="chat"), {"tags": ["mtp"]}),
    ("mtp_tag_vulkan", _slot(profile="chat", device="gpu-vulkan"), {"tags": ["mtp"]}),
    ("mtp_tag_cpu_runner_no_mtp", _slot(profile="cpu-chat", device="cpu"), {"tags": ["mtp"]}),
    ("mtp_explicit_true_cpu", _slot(profile="cpu-chat", device="cpu"), {"defaults": {"mtp": True}}),
    ("mtp_explicit_false", _slot(profile="chat"), {"tags": ["mtp"], "defaults": {"mtp": False}}),
    (
        "mtp_spec_override_in_tune",
        _slot(profile="chat"),
        {"tags": ["mtp"], "defaults": {"extra_args": "--spec-draft-n-max 6"}},
    ),
    ("mmproj_auto", _slot(profile="chat"), {"mmproj": "/models/mm.gguf"}),
    (
        "mmproj_vision_off",
        _slot(profile="chat"),
        {"mmproj": "/models/mm.gguf", "defaults": {"vision": False}},
    ),
    ("mmproj_and_no_mmproj", _slot(profile="thinking"), {"mmproj": "/models/mm.gguf"}),
    (
        "chat_template_and_jinja_off",
        _slot(profile="chat"),
        {"defaults": {"chat_template": "qwen-tool", "jinja": False}},
    ),
    ("chat_template_auto", _slot(profile="chat"), {"defaults": {"chat_template": "auto"}}),
    ("slot_hardware", _slot(profile="chat", n_gpu_layers=40, threads=12, metrics=False), {}),
    ("slot_binary_cpu", _slot(profile="chat", binary="cpu"), {"tags": ["mtp"]}),
    (
        "slot_backend_no_device",
        {**{k: v for k, v in _slot(profile="chat").items() if k != "device"}, "backend": "vulkan"},
        {},
    ),
    (
        "fit_device_class_mismatch",
        _slot(profile="cpu-only"),
        {"defaults": {"profile": "chat", "extra_args": "-b 32"}},
    ),
    (
        "fit_device_class_match",
        _slot(profile="gpu-only"),
        {"defaults": {"profile": "chat", "extra_args": "-b 32"}},
    ),
    ("fit_runner_wrong_family", _slot(profile="on-flm"), {}),
    (
        "fit_type_mismatch",
        _slot(type="llm", profile="embedding"),
        {"defaults": {"profile": "chat", "extra_args": "-b 32"}},
    ),
    ("demoted_seed_settled_missing", _slot(profile="moe"), {}),
    (
        "thinking_default_rides_along",
        _slot(profile="chat"),
        {"defaults": {"enable_thinking": True}},
    ),
    (
        "fit_backend_hint_without_runner",
        _slot(profile="vk-hint"),
        {"defaults": {"profile": "chat", "extra_args": "-b 32"}},
    ),
    (
        "profile_backend_drives_mtp_device",
        _slot(profile="vk-hint", device="gpu-vulkan"),
        {"tags": ["mtp"]},
    ),
    ("reasoning_format_none_joined", _slot(profile="fmt-none-joined"), {}),
    (
        "unparseable_ceiling",
        _slot(profile="chat", model={"context_size": "64k"}),
        {"metadata": {"context_length": 262144}},
    ),
    (
        "nested_slot_table",
        {
            "slot": {
                "name": "s",
                "type": "llm",
                "port": 8081,
                "device": "gpu-rocm",
                "profile": "chat",
            },
            "model": {"default": "m1"},
        },
        {"defaults": {"extra_args": "-b 7"}},
    ),
]


@pytest.mark.parametrize(("case", "slot", "model_kw"), CASES, ids=[c[0] for c in CASES])
def test_frozen_launch_matches_live(
    case: str, slot: dict[str, Any], model_kw: dict[str, Any]
) -> None:
    _write_profiles(CUSTOM_PROFILES)
    model = _model(**model_kw)
    hoisted = frozen.hoist_slot(slot, "s")
    live_argv, live_ctx = _live(hoisted, model)
    got = _frozen(slot, model, CUSTOM_PROFILES)
    assert list(got.argv) == live_argv, case
    assert got.ctx == live_ctx, case
    info: dict[str, Any] = {"_model_key": model.id, **model.model_dump()}
    profile = C._resolve_profile_or_base(str(hoisted.get("profile") or ""), hoisted)
    sc = C._resolve_llama_scalars(hoisted, info, profile)
    assert got.mmproj == sc["mmproj"], case
    defaults = model.defaults.model_dump() if model.defaults else {}
    assert got.enable_thinking == defaults.get("enable_thinking"), case


def test_case_matrix_exercises_every_branch() -> None:
    """Guard against a matrix that passes because it never reaches a branch."""
    _write_profiles(CUSTOM_PROFILES)
    results = {case: _frozen(slot, _model(**kw), CUSTOM_PROFILES) for case, slot, kw in CASES}
    assert results["divergent_overlay"].overlay_applied
    assert results["custom_profile_template"].template_applied
    assert not results["hand_tune_no_provenance"].template_applied
    assert results["missing_profile_falls_back"].profile_missing
    assert results["ceiling_replaces_dense_cap"].ctx == 96000
    assert results["ceiling_replaces_dense_cap"].ctx_source == "ceiling"
    assert results["native_dense_capped"].ctx == frozen.CTX_DENSE_CAP
    assert results["no_native_ceiling_unhonoured"].ctx == frozen.CTX_SAFE_FALLBACK
    assert results["mtp_tag_rocm"].mtp and not results["mtp_tag_cpu_runner_no_mtp"].mtp
    assert results["mtp_explicit_true_cpu"].mtp
    assert "--spec-draft-device" in results["mtp_tag_vulkan"].argv
    assert results["mmproj_auto"].mmproj and not results["mmproj_vision_off"].mmproj
    assert "--no-mmproj" in results["mmproj_and_no_mmproj"].argv
    assert not results["embedding_model_mode_no_jinja"].jinja
    assert "--reasoning-format" not in results["reasoning_format_none"].argv
    assert results["custom_hw_flags_overlay"].overlay_applied
    assert "999" not in results["custom_hw_flags_overlay"].argv  # profile -ngl stripped
    assert results["thinking_default_rides_along"].enable_thinking is True


def test_profiles_toml_without_ledger_or_marker_adopts_demoted() -> None:
    """A file with neither the ledger nor the marker has settled nothing."""
    raw: dict[str, Any] = {"profile": {"mine": {"flags": "-b 1", "mtp": False}}}
    _write_profiles(raw)
    slot = _slot(profile="moe")
    model = _model()
    got = _frozen(slot, model, raw)
    assert got.profile_launched == "moe" and not got.profile_missing
    live_argv, _ = _live(frozen.hoist_slot(slot, "s"), model)
    assert list(got.argv) == live_argv


@pytest.mark.parametrize(
    ("slot", "expected"),
    [
        ({"name": "f", "device": "npu", "model": {"default": "m", "context_size": 16384}}, "16384"),
        ({"name": "f", "device": "npu", "model": {"default": "m"}}, "8192"),
        ({"name": "f", "device": "npu", "ctx_size": 4096, "model": {"default": "m"}}, "4096"),
        ({"name": "f", "device": "npu", "defaults": {"context_size": 2048}, "model": {}}, "2048"),
    ],
)
def test_flm_ctx_matches_build_env(slot: dict[str, Any], expected: str) -> None:
    from hal0.providers.flm import FLMProvider

    env = FLMProvider().build_env(slot, {"_model_key": "m"})
    assert env["HAL0_FLM_CTX"] == frozen.flm_ctx(slot) == expected


def test_engine_flags_match_providers() -> None:
    from hal0.providers.comfyui import ComfyUIProvider
    from hal0.providers.kokoro import KokoroProvider
    from hal0.providers.qwen3tts import Qwen3TTSProvider

    raw = {"profile": {"my-tts": {"flags": "--model_path /x --speed 2", "mtp": False}}}
    _write_profiles(raw)
    catalog = frozen.Catalog(raw)
    for slot in (
        {"name": "i", "type": "image", "profile": "comfyui"},
        {"name": "i", "type": "image"},
    ):
        assert frozen.engine_flags(slot, catalog, "comfyui") == ComfyUIProvider()._profile_flags(
            slot
        )
    for provider, engine, slot in (
        (KokoroProvider(), "kokoro", {"name": "t", "type": "tts", "profile": "my-tts", "port": 9}),
        (KokoroProvider(), "kokoro", {"name": "t", "type": "tts", "profile": "kokoro", "port": 9}),
        (
            Qwen3TTSProvider(),
            "qwen3tts",
            {"name": "q", "type": "tts", "profile": "qwen3-tts", "port": 9},
        ),
    ):
        flags = frozen.engine_flags(slot, catalog, engine)
        assert flags is not None
        command = provider.container_spec(slot, {}).command
        tokens = flags.split()
        assert command[: len(tokens)] == tokens, (engine, command)
    # Kokoro's default profile name is not a seed: a profile-less slot cannot launch.
    assert frozen.engine_flags({"name": "t", "type": "tts"}, catalog, "kokoro") is None


def test_fresh_install_adopts_demoted_seed() -> None:
    """No profiles.toml: a demoted seed name resolves (and the live side adopts it)."""
    _write_profiles(None)
    slot = _slot(profile="dense")
    model = _model()
    got = _frozen(slot, model, None)  # before the live side writes profiles.toml
    assert got.profile_launched == "dense" and not got.profile_missing
    live_argv, _ = _live(frozen.hoist_slot(slot, "s"), model)
    assert list(got.argv) == live_argv


def test_managed_flag_in_model_tune_refuses_on_both_sides() -> None:
    _write_profiles(CUSTOM_PROFILES)
    model = _model(defaults={"extra_args": "--port 9999"})
    slot = _slot(profile="chat")
    with pytest.raises(BadRequest):
        _live(frozen.hoist_slot(slot, "s"), model)
    with pytest.raises(BadRequest):
        _frozen(slot, model, CUSTOM_PROFILES)


def test_engine_dispatch_matches_live() -> None:
    _write_profiles(CUSTOM_PROFILES)
    catalog = frozen.Catalog(CUSTOM_PROFILES)
    for slot in (
        {"name": "a", "device": "npu", "profile": "flm"},
        {"name": "b", "type": "tts", "profile": "qwen3-tts"},
        {"name": "c", "type": "tts"},
        {"name": "d", "type": "transcription", "profile": "moonshine"},
        {"name": "e", "type": "image", "profile": "comfyui"},
        {"name": "f", "type": "llm", "profile": "chat"},
        {"name": "g", "type": "llm", "profile": "on-flm"},
        {"name": "h", "type": "llm", "profile": "does-not-exist"},
    ):
        provider = C._spec_provider_for(slot)
        live = {
            None: frozen.LLAMA,
            "FLMProvider": "flm",
            "Qwen3TTSProvider": "qwen3tts",
            "KokoroProvider": "kokoro",
            "MoonshineProvider": "moonshine",
            "ComfyUIProvider": "comfyui",
        }[type(provider).__name__ if provider is not None else None]
        assert frozen.engine_for(slot, catalog) == live, slot


def test_frozen_seed_snapshot_matches_shipped_seeds() -> None:
    """Holds until ADR-0029 phase 2 reshapes the live seeds; then delete it."""
    from hal0.config.schema import FAMILY_DEFAULTS, LEGACY_SEED_PROFILES, SEED_PROFILES

    seeds, demoted = frozen.frozen_seeds()
    assert seeds == {k: dict(v) for k, v in SEED_PROFILES.items()}
    assert demoted == {k: dict(v) for k, v in LEGACY_SEED_PROFILES.items()}
    assert FAMILY_DEFAULTS == {} and frozen.FAMILY_FLAGS == {}


def test_frozen_constants_match_live() -> None:
    """Holds until ADR-0029 phase 2 changes the live screens; then delete it."""
    from hal0.config.schema import build_mtp_flag_bundle
    from hal0.slots import argv

    assert frozenset(argv.MANAGED_ARGS_DENYLIST) == frozen.MANAGED_FLAGS
    assert frozenset(argv.SLOT_HARDWARE_FLAGS) == frozen.HARDWARE_FLAGS
    assert frozenset(argv.UNTRUSTED_SEGMENT_LABELS) == frozen.UNTRUSTED_SEGMENTS
    assert frozenset(argv.PROFILE_SEGMENT_LABELS) == frozen.PROFILE_SEGMENTS
    assert C._CTX_DENSE_CAP == frozen.CTX_DENSE_CAP
    assert C._CTX_SAFE_FALLBACK == frozen.CTX_SAFE_FALLBACK
    for backend in (None, "rocm", "vulkan", "cuda", "bogus"):
        assert build_mtp_flag_bundle(backend) == frozen.mtp_bundle(backend)


def test_model_info_from_row_round_trips_defaults(tmp_path: Path) -> None:
    model = _model(
        tags=["mtp", "chat"],
        mmproj="/m/mm.gguf",
        architecture="qwen35",
        metadata={"context_length": 131072},
        defaults={
            "profile": "coding",
            "extra_args": "-b 1",
            "chat_template": "t",
            "context_size": 4096,
            "mtp": True,
            "jinja": False,
            "enable_thinking": False,
            "vision": False,
        },
    )
    info = frozen.model_info_from_row(model_to_row(model))
    dumped = model.model_dump()
    for key in (
        "profile",
        "extra_args",
        "chat_template",
        "context_size",
        "mtp",
        "jinja",
        "enable_thinking",
        "vision",
    ):
        assert info["defaults"][key] == dumped["defaults"][key], key
    assert info["metadata"]["context_length"] == 131072
    assert info["tags"] == ["mtp", "chat"]
    assert info["mmproj"] == "/m/mm.gguf"


@pytest.mark.parametrize("profile", ["chat", "thinking"])
def test_unregistered_model_plans_the_live_fallback(profile: str) -> None:
    """A slot naming a model with no registry row (never pulled, or deleted)
    launches ``--model <model id>`` today (``_best_effort_model_info``); the
    plan's view of today's launch, and of the launch after, must say so too."""
    from hal0.config.migrations import profile_centric as pc

    _write_profiles(CUSTOM_PROFILES)
    slot = _slot(profile=profile, model={"default": "gone-model"}, workers=1)
    path = paths.slots_config_dir() / "1.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tomli_w.dumps({**slot, "id": 1}).encode())

    resolved = C._resolve_slot_argv(frozen.hoist_slot(slot, "s"))
    assert resolved is not None
    live = list(resolved[1].argv)
    assert live[live.index("--model") + 1] == "gone-model"

    plan = pc.plan_conversion()
    (sp,) = plan.slots
    assert sp.status != "blocked", sp.error
    assert pc.effective_map(sp.argv_today) == pc.effective_map(live)
    after = list(sp.argv_after)
    assert after[after.index("--model") + 1] == "gone-model"
