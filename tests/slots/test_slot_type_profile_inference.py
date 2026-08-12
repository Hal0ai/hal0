"""A capability slot is created with the profile its TYPE requires (#1830).

For an ``embedding`` / ``reranking`` slot the profile is not a tuning choice:
it carries the llama-server MODE FLAG (``--embedding`` / ``--reranking``) that
makes the slot serve ``/v1/embeddings`` / ``/v1/rerank`` at all. Before this
fix, the only create-time profile source was the model's stamped
``defaults.profile`` (the Q1 adoption in ``SlotManager.create``) — and
auto-scan, ``model add``, pull and the curated catalog all register models
with ``defaults: null``. So every explicit creation path (``hal0 slot create``,
the dashboard New-slot modal, a raw ``POST /api/slots``, a stack apply, the
capability orchestrator's auto-create) wrote a slot TOML with NO ``profile``
key, the #1787 profile-template gate had nothing to apply, and the slot loaded
to ``state=ready`` while 501ing its own endpoint.

``SlotManager.create`` is the one chokepoint every path funnels through, so
the type-implied fallback lives there.
"""

from __future__ import annotations

import pytest

from hal0.registry.model import Model, ModelDefaults
from hal0.registry.store import ModelRegistry
from hal0.slots.manager import SlotManager
from hal0.slots.profile_adopt import type_implied_profile


def _register(model_id: str, *, profile: str | None = None) -> None:
    ModelRegistry().add(
        Model(
            id=model_id,
            path=f"/tmp/{model_id}.gguf",
            capabilities=["chat"],
            defaults=ModelDefaults(profile=profile) if profile else None,
        )
    )


def _cfg(name: str, slot_type: str, device: str, model: str = "unstamped") -> dict:
    # The shape the create modal / CLI / stack apply send: no ``profile`` key.
    return {
        "name": name,
        "port": 8097,
        "type": slot_type,
        "device": device,
        "provider": "llama-server",
        "group": "custom",
        "model": {"default": model},
    }


# ── the (type, device) → profile rule ────────────────────────────────────────


@pytest.mark.parametrize(
    ("slot_type", "device", "expected"),
    [
        # The rc.5 repro was a CPU-only box; the seed embedding/reranking
        # profiles are device-agnostic, so every llama-server device gets them.
        ("embedding", "cpu", "embedding"),
        ("embedding", "gpu-rocm", "embedding"),
        ("embedding", "gpu-vulkan", "embedding"),
        ("reranking", "cpu", "reranking"),
        ("reranking", "gpu-rocm", "reranking"),
        ("reranking", "gpu-vulkan", "reranking"),
        # NPU embeddings run on the FLM runtime, not llama-server.
        ("embedding", "npu", "flm"),
        # Engine-switched capability types.
        ("tts", "cpu", "kokoro"),
        ("tts", "gpu-rocm", "qwen3-tts"),
        # Qwen3-TTS ships a ROCm-only runner, so a Vulkan/CUDA box must NOT
        # be pinned to it — it infers nothing and keeps the profile-less
        # Kokoro fallback ``providers/container._spec_provider_for`` applies.
        ("tts", "gpu-vulkan", None),
        ("tts", "gpu-cuda", None),
        ("transcription", "cpu", "moonshine"),
        # ``image`` infers nothing today: the shared rule's generic GPU branch
        # answers ``chat`` before its image branch and the fit veto drops it.
        # Pinned as-is (the old ``("image", "img")`` row asserted an input no
        # slot can have — ``img`` is a device_class, not a device; the shipped
        # img.toml is device = "gpu-rocm").
        ("image", "gpu-rocm", None),
        # An llm slot has no mode flag at stake — the tune stays the
        # operator's (or the model's) choice, so nothing is inferred.
        ("llm", "cpu", None),
        ("llm", "gpu-rocm", None),
        ("", "cpu", None),
    ],
)
def test_type_implied_profile(
    tmp_hal0_home: str, slot_type: str, device: str, expected: str | None
) -> None:
    assert type_implied_profile({"type": slot_type, "device": device}) == expected


def test_type_implied_profile_vetoes_a_profile_that_does_not_fit(tmp_hal0_home: str) -> None:
    """A candidate that does not fit the slot is dropped, never written.

    ``transcription`` on a GPU device has no hal0 STT engine — the rule must
    answer ``None`` rather than hand the slot a llama chat profile that would
    never start the STT image.
    """
    assert type_implied_profile({"type": "transcription", "device": "gpu-rocm"}) is None


def test_type_implied_profile_vetoes_a_runtime_family_the_device_cannot_run(
    tmp_hal0_home: str,
) -> None:
    """The RUNNER registry vetoes too, not just the profile's own fields.

    ``profile_fits_slot`` compares the profile's ``device_class`` / ``backend``,
    and the 1.0 seeds are device-agnostic logical tunes (both ``None``) — so it
    waves ``qwen3-tts`` through for a gpu-vulkan slot. But
    ``RUNNER_IMAGES["qwen3tts"]`` is ROCm-only (the provider wires /dev/kfd +
    MIOpen), so the slot would be pinned to a container its own device cannot
    run. Before the veto, ``hal0 slot create voice --type tts`` with no
    ``--hardware`` on a Vulkan-defaulting box turned a working Kokoro CPU slot
    into a dead Qwen3 one.
    """
    from hal0.profiles import ProfileCatalog
    from hal0.slots.profile_adopt import profile_fits_slot, runner_fits_slot

    cfg = {"type": "tts", "device": "gpu-vulkan"}
    # The premise: the profile itself "fits" — only the runner disagrees.
    assert profile_fits_slot("qwen3-tts", cfg) is True
    assert ProfileCatalog().resolve("qwen3-tts").runtime_family == "qwen3tts"
    assert runner_fits_slot("qwen3tts", cfg) is False

    assert type_implied_profile(cfg) is None
    # ROCm — the one backend that runner has an image for — still infers it.
    assert type_implied_profile({"type": "tts", "device": "gpu-rocm"}) == "qwen3-tts"


def test_type_implied_profile_ignores_the_cli_legacy_provider_default(
    tmp_hal0_home: str,
) -> None:
    """``hal0 slot create`` always sends the legacy ``provider=llama-server``.

    The STT rule rejects a provider it has no engine for (``whispercpp``), and
    that guard swallowed the CLI's legacy default too — so the CLI got no
    profile for a transcription slot while the dashboard modal (which sends no
    provider) got Moonshine. ``llama-server`` is not an STT engine; it means
    "unspecified" here.
    """
    assert type_implied_profile({"type": "transcription", "device": "cpu"}) == "moonshine"
    assert (
        type_implied_profile(
            {"type": "transcription", "device": "cpu", "provider": "llama-server"}
        )
        == "moonshine"
    )
    # A named engine hal0 has no runtime for is still rejected.
    assert (
        type_implied_profile({"type": "transcription", "device": "cpu", "provider": "whispercpp"})
        is None
    )


# ── the create chokepoint ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("slot_type", "device", "expected"),
    [
        ("reranking", "cpu", "reranking"),
        ("reranking", "gpu-rocm", "reranking"),
        ("embedding", "cpu", "embedding"),
        ("embedding", "gpu-vulkan", "embedding"),
    ],
)
async def test_create_infers_capability_profile_for_unstamped_model(
    tmp_hal0_home: str, slot_type: str, device: str, expected: str
) -> None:
    _register("unstamped")  # defaults: null — auto-scan / model add / pull
    sm = SlotManager()
    await sm.create("zz", _cfg("zz", slot_type, device))
    assert (await sm.get_config("zz")).get("profile") == expected


async def test_create_keeps_an_explicit_profile(tmp_hal0_home: str) -> None:
    _register("unstamped")
    sm = SlotManager()
    cfg = _cfg("zz", "embedding", "cpu")
    cfg["profile"] = "cpu-chat"
    await sm.create("zz", cfg)
    assert (await sm.get_config("zz")).get("profile") == "cpu-chat"


async def test_create_keeps_the_models_stamped_profile(tmp_hal0_home: str) -> None:
    """The Q1 model preference still wins over the type-implied fallback."""
    _register("stamped", profile="cpu-chat")
    sm = SlotManager()
    await sm.create("zz", _cfg("zz", "embedding", "cpu", model="stamped"))
    assert (await sm.get_config("zz")).get("profile") == "cpu-chat"


async def test_create_leaves_llm_slots_profileless(tmp_hal0_home: str) -> None:
    _register("unstamped")
    sm = SlotManager()
    await sm.create("zz", _cfg("zz", "llm", "cpu"))
    assert not (await sm.get_config("zz")).get("profile")


async def test_create_leaves_a_vulkan_tts_slot_on_the_kokoro_fallback(
    tmp_hal0_home: str,
) -> None:
    """`hal0 slot create voice --type tts` on a Vulkan box must not pin Qwen3.

    The ROCm-only qwen3tts runner cannot start on a Vulkan device; the
    profile-less TOML keeps ``_spec_provider_for``'s type=tts → Kokoro
    fallback, which is what this path did before #1830's inference landed.
    """
    _register("unstamped")
    sm = SlotManager()
    await sm.create("zz", _cfg("zz", "tts", "gpu-vulkan"))
    assert not (await sm.get_config("zz")).get("profile")


async def test_create_infers_without_a_model_bound(tmp_hal0_home: str) -> None:
    """A grey/unconfigured capability slot (no model yet) still gets a profile.

    The capability orchestrator's auto-create writes ``model.default = ""``;
    the type is what makes the mode flag necessary, not the binding.
    """
    sm = SlotManager()
    cfg = _cfg("zz", "reranking", "cpu")
    cfg["model"] = {"default": ""}
    await sm.create("zz", cfg)
    assert (await sm.get_config("zz")).get("profile") == "reranking"
