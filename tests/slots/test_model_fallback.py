"""Load-time capability fallback for a non-servable slot ``model.default``.

A seed/default may pin a model id that never landed locally under that exact
id — e.g. the catalog id ``gemma-4-12b-it`` (``upstream=hal0``, no file) while
the operator's scanned gguf registered as ``gemma-4-12b-it-ud-q4-k-xl``. Pinned
to the ghost, the slot would crash-loop on a ``--model`` path that doesn't
exist. ``SlotManager._resolve_servable_model`` falls back to a locally
registered model matching the slot's capability instead.
"""

from __future__ import annotations

from pathlib import Path

from hal0.registry.curated import CURATED_MODELS
from hal0.registry.fallback import fallback_local_model
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.slots.manager import SlotManager


def _add_model(
    home: str,
    *,
    id: str,
    capability: str,
    size_bytes: int,
    exists: bool = True,
    suffix: str = ".gguf",
    tags: list[str] | None = None,
    default: bool = False,
) -> str:
    """Register a Model under HAL0_HOME, optionally materialising its file."""
    model_file = Path(home) / f"{id}{suffix}"
    if exists:
        model_file.write_bytes(b"\0")
    ModelRegistry().add(
        Model(
            id=id,
            name=id,
            path=str(model_file),
            size_bytes=size_bytes,
            capabilities=[capability],
            tags=tags or [],
            default=default,
        )
    )
    return id


def _mgr() -> SlotManager:
    # _resolve_servable_model only touches static helpers + the registry, so a
    # bare instance (no __init__) is enough to exercise it.
    return SlotManager.__new__(SlotManager)


def _llm_cfg(default: str, *, device: str = "gpu-vulkan") -> dict:
    return {
        "name": "utility",
        "type": "llm",
        "device": device,
        "model": {"default": default},
    }


def test_falls_back_to_local_when_configured_id_is_ghost(tmp_hal0_home):
    real = _add_model(
        tmp_hal0_home, id="gemma-4-12b-it-ud-q4-k-xl", capability="chat", size_bytes=6_900_000_000
    )
    cfg = _llm_cfg("gemma-4-12b-it")  # ghost: not registered, not curated
    assert _mgr()._resolve_servable_model("gemma-4-12b-it", cfg) == real


def test_no_fallback_when_configured_model_is_local(tmp_hal0_home):
    _add_model(tmp_hal0_home, id="gemma-4-12b-it", capability="chat", size_bytes=6_000_000_000)
    cfg = _llm_cfg("gemma-4-12b-it")
    assert _mgr()._resolve_servable_model("gemma-4-12b-it", cfg) == "gemma-4-12b-it"


def test_no_fallback_for_npu_device_flm_tag(tmp_hal0_home):
    # A local chat model exists, but an NPU/FLM slot is served by tag — never
    # repoint it at a gguf file.
    _add_model(tmp_hal0_home, id="some-local-chat", capability="chat", size_bytes=5_000_000_000)
    cfg = _llm_cfg("gemma4-it-e2b-FLM", device="npu")
    assert _mgr()._resolve_servable_model("gemma4-it-e2b-FLM", cfg) == "gemma4-it-e2b-FLM"


def test_no_fallback_when_configured_id_is_curated_pullable(tmp_hal0_home):
    # A curated id is still-to-be-pulled; don't pre-empt the download with a
    # fallback even though a different local chat model is present.
    curated_id = next(m.id for m in CURATED_MODELS if m.capability == "chat")
    _add_model(tmp_hal0_home, id="other-local-chat", capability="chat", size_bytes=4_000_000_000)
    cfg = _llm_cfg(curated_id)
    assert _mgr()._resolve_servable_model(curated_id, cfg) == curated_id


def test_no_fallback_when_no_capability_match(tmp_hal0_home):
    # Only an embed model is local; an llm (chat) slot finds no chat fallback.
    _add_model(tmp_hal0_home, id="some-embed", capability="embed", size_bytes=500_000_000)
    cfg = _llm_cfg("gemma-4-12b-it")
    assert _mgr()._resolve_servable_model("gemma-4-12b-it", cfg) == "gemma-4-12b-it"


def test_fallback_picks_largest_on_disk(tmp_hal0_home):
    _add_model(tmp_hal0_home, id="small-chat", capability="chat", size_bytes=1_000_000_000)
    big = _add_model(tmp_hal0_home, id="big-chat", capability="chat", size_bytes=20_000_000_000)
    cfg = _llm_cfg("ghost-id")
    assert _mgr()._resolve_servable_model("ghost-id", cfg) == big


def test_fallback_skips_registered_models_with_missing_file(tmp_hal0_home):
    # Registered but the file isn't on disk → not a valid fallback target.
    _add_model(
        tmp_hal0_home, id="phantom-chat", capability="chat", size_bytes=3_000_000_000, exists=False
    )
    cfg = _llm_cfg("ghost-id")
    assert _mgr()._resolve_servable_model("ghost-id", cfg) == "ghost-id"


def test_fallback_local_model_returns_none_when_empty(tmp_hal0_home):
    assert SlotManager._fallback_local_model("chat") is None


# ── #940 hardening: diffusion guard + name-similarity ─────────────────────────


def test_ghost_chat_slot_never_picks_video_model(tmp_hal0_home):
    # The live failure: a 25GB video diffusion gguf got capabilities=['chat']
    # (default guess) and, being the largest, was selected for the chat slot.
    # It must be excluded — leaving a real (smaller) chat model as the pick.
    _add_model(tmp_hal0_home, id="ltx-2-19b-dev-fp8", capability="chat", size_bytes=25_000_000_000)
    real = _add_model(
        tmp_hal0_home, id="gemma-4-12b-it-ud-q4-k-xl", capability="chat", size_bytes=6_900_000_000
    )
    cfg = _llm_cfg("gemma-4-12b-it")  # ghost
    assert _mgr()._resolve_servable_model("gemma-4-12b-it", cfg) == real


def test_ghost_chat_slot_with_only_video_model_does_not_fall_back(tmp_hal0_home):
    # If the *only* chat-tagged candidate is a video model, there is no valid
    # fallback at all — keep the configured (ghost) id rather than serve video.
    _add_model(tmp_hal0_home, id="ltx-2-19b-dev-fp8", capability="chat", size_bytes=25_000_000_000)
    cfg = _llm_cfg("gemma-4-12b-it")
    assert _mgr()._resolve_servable_model("gemma-4-12b-it", cfg) == "gemma-4-12b-it"


def test_excludes_safetensors_diffusion_checkpoint(tmp_hal0_home):
    # A .safetensors checkpoint mislabelled chat must not be a fallback target.
    _add_model(
        tmp_hal0_home,
        id="v1-5-pruned-emaonly",
        capability="chat",
        size_bytes=4_000_000_000,
        suffix=".safetensors",
    )
    cfg = _llm_cfg("gemma-4-12b-it")
    assert _mgr()._resolve_servable_model("gemma-4-12b-it", cfg) == "gemma-4-12b-it"


def test_excludes_image_capability_model(tmp_hal0_home):
    # An explicit image-capability model is never a text-slot fallback even
    # when it is the only candidate carrying the requested 'chat' cap too.
    gguf = Path(tmp_hal0_home) / "flux-dev.gguf"
    gguf.write_bytes(b"\0")
    ModelRegistry().add(
        Model(
            id="flux-dev",
            name="flux-dev",
            path=str(gguf),
            size_bytes=12_000_000_000,
            capabilities=["chat", "image"],
        )
    )
    cfg = _llm_cfg("gemma-4-12b-it")
    assert _mgr()._resolve_servable_model("gemma-4-12b-it", cfg) == "gemma-4-12b-it"


def test_name_similarity_beats_larger_unrelated_chat_model(tmp_hal0_home):
    # A much larger but unrelated chat model must lose to the look-alike that
    # shares leading tokens with the configured (ghost) id.
    _add_model(tmp_hal0_home, id="qwen3-coder-30b", capability="chat", size_bytes=30_000_000_000)
    lookalike = _add_model(
        tmp_hal0_home, id="gemma-4-12b-it-ud-q4-k-xl", capability="chat", size_bytes=6_900_000_000
    )
    cfg = _llm_cfg("gemma-4-12b-it")
    assert _mgr()._resolve_servable_model("gemma-4-12b-it", cfg) == lookalike


def test_size_tiebreak_when_no_name_similarity(tmp_hal0_home):
    # No candidate shares a leading token with the ghost id → size wins
    # (legacy behaviour preserved).
    _add_model(tmp_hal0_home, id="alpha-chat", capability="chat", size_bytes=1_000_000_000)
    big = _add_model(tmp_hal0_home, id="beta-chat", capability="chat", size_bytes=9_000_000_000)
    cfg = _llm_cfg("zeta-ghost-id")
    assert _mgr()._resolve_servable_model("zeta-ghost-id", cfg) == big


# ── model-default-type consumer wiring: fallback_local_model prefers the
# per-type default when it's set AND servable ─────────────────────────────────
#
# NOTE: pytest derives ``tmp_hal0_home`` from the test's own name, and every
# registered model's path lives under that dir — so a test name containing
# "diffus"/"lora"/"unet"/"controlnet" would false-trip
# looks_diffusion_or_nontext's path-substring guard. Avoid those tokens in
# names below (say "video model" instead of the "d-word").


def test_fallback_prefers_type_default_over_name_similarity_and_size(tmp_hal0_home):
    # The default-marked model wins even though a *larger*, *closer-named*
    # peer is also on disk — an operator's explicit default beats every
    # heuristic signal.
    _add_model(
        tmp_hal0_home,
        id="gemma-4-12b-it-ud-q4-k-xl",
        capability="chat",
        size_bytes=30_000_000_000,
    )
    default_id = _add_model(
        tmp_hal0_home,
        id="unrelated-small-chat",
        capability="chat",
        size_bytes=1_000_000_000,
        default=True,
    )
    result = fallback_local_model("chat", "gemma-4-12b-it")
    assert result is not None
    assert result.id == default_id


def test_fallback_heuristic_unchanged_when_no_default_set(tmp_hal0_home):
    # No model carries the per-type default flag → falls through to the
    # existing name-similarity/size heuristic, verbatim.
    lookalike_id = _add_model(
        tmp_hal0_home, id="gemma-4-12b-it-ud-q4-k-xl", capability="chat", size_bytes=6_900_000_000
    )
    _add_model(tmp_hal0_home, id="qwen3-coder-30b", capability="chat", size_bytes=30_000_000_000)
    result = fallback_local_model("chat", "gemma-4-12b-it")
    assert result is not None
    assert result.id == lookalike_id


def test_fallback_skips_default_when_not_servable_falls_back_to_heuristic(tmp_hal0_home):
    # A default is set on a disk-missing model (registered, no file) — it's
    # not in the servable candidate pool, so the heuristic picks the real
    # on-disk look-alike instead.
    _add_model(
        tmp_hal0_home,
        id="ghost-default-chat",
        capability="chat",
        size_bytes=6_000_000_000,
        exists=False,
        default=True,
    )
    lookalike_id = _add_model(
        tmp_hal0_home, id="gemma-4-12b-it-ud-q4-k-xl", capability="chat", size_bytes=6_900_000_000
    )
    result = fallback_local_model("chat", "gemma-4-12b-it")
    assert result is not None
    assert result.id == lookalike_id


def test_fallback_skips_default_marked_video_model_falls_back_to_heuristic(tmp_hal0_home):
    # A default flag on a video artifact (mislabelled chat) must not be
    # honoured — it's filtered by the diffusion/non-text guard same as any
    # other candidate, so the heuristic runs as if no default existed.
    _add_model(
        tmp_hal0_home,
        id="ltx-2-19b-dev-fp8",
        capability="chat",
        size_bytes=25_000_000_000,
        default=True,
    )
    real_id = _add_model(
        tmp_hal0_home, id="gemma-4-12b-it-ud-q4-k-xl", capability="chat", size_bytes=6_900_000_000
    )
    result = fallback_local_model("chat", "gemma-4-12b-it")
    assert result is not None
    assert result.id == real_id


def test_fallback_default_of_other_capability_not_honoured(tmp_hal0_home):
    # A default marked on an embed-capability model must never leak into a
    # chat-capability fallback lookup, even though both share this registry.
    _add_model(
        tmp_hal0_home, id="embed-default", capability="embed", size_bytes=500_000_000, default=True
    )
    chat_id = _add_model(tmp_hal0_home, id="only-chat", capability="chat", size_bytes=2_000_000_000)
    result = fallback_local_model("chat", "ghost-id")
    assert result is not None
    assert result.id == chat_id


def test_fallback_local_model_returns_none_when_only_default_missing(tmp_hal0_home):
    # No candidates at all (nothing local) → still None, default-lookup path
    # doesn't change the empty case.
    assert fallback_local_model("chat", "ghost-id") is None
