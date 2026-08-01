from __future__ import annotations

from pathlib import Path

import pytest

from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.slots.manager import LoadedSlot, SlotManager


def _write_slot(
    root: Path,
    name: str,
    *,
    slot_type: str = "llm",
    model: str = "qwen3-4b",
    labels: tuple[str, ...] = (),
    default: bool = False,
    stale_enabled: bool | None = None,
    device: str = "gpu-rocm",
    profile: str | None = None,
    system_prompt: str | None = None,
) -> None:
    lines = [
        f'name = "{name}"',
        "port = 8081",
        f'type = "{slot_type}"',
        f'device = "{device}"',
        'provider = "llama-server"',
    ]
    # ``stale_enabled`` writes the removed pre-#1369 key so we can prove it is
    # inert; production TOMLs never carry it after the migration.
    if stale_enabled is not None:
        lines.append(f"enabled = {str(stale_enabled).lower()}")
    if default:
        lines.append("default = true")
    if profile is not None:
        lines.append(f'profile = "{profile}"')
    if system_prompt is not None:
        lines.append(f'system_prompt = "{system_prompt}"')
    lines.extend(
        [
            "[model]",
            f'default = "{model}"',
        ]
    )
    if labels:
        rendered = ", ".join(f'"{label}"' for label in labels)
        lines.append(f"labels = [{rendered}]")
    (root / f"{name}.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_loaded_slot_returns_typed_slot(slot_root: Path) -> None:
    _write_slot(
        slot_root,
        "chat",
        labels=("tool-calling", "vision"),
        default=True,
        profile="rocm",
        system_prompt="You are Chat.",
    )

    slot = await SlotManager().loaded_slot("chat")

    assert slot == LoadedSlot(
        name="chat",
        model_id="qwen3-4b",
        slot_type="llm",
        device="gpu-rocm",
        labels=frozenset({"tool-calling", "vision"}),
        system_prompt="You are Chat.",
        profile="rocm",
        default=True,
        # The model isn't in the registry in this test (no registry fixture
        # wired), so capability_flags.tool_calling can't be resolved and
        # loaded_slot_from_config falls back to the "tool-calling" label —
        # present here — per the §7.1d 🔴 fix's pre-migration fall-through.
        tool_calling=True,
    )


@pytest.mark.asyncio
async def test_resolve_for_request_returns_default_loaded_slot(slot_root: Path) -> None:
    _write_slot(slot_root, "chat", model="default-chat", default=True)
    _write_slot(slot_root, "coder", model="coder-chat")

    slot = await SlotManager().resolve_for_request("llm")

    assert slot is not None
    assert slot.name == "chat"
    assert slot.model_id == "default-chat"


@pytest.mark.asyncio
async def test_resolve_for_request_applies_label_overlay(slot_root: Path) -> None:
    _write_slot(slot_root, "chat", model="plain-chat", default=True)
    _write_slot(slot_root, "vision", model="vision-chat", labels=("vision",))

    slot = await SlotManager().resolve_for_request("llm", required_labels=("vision",))

    assert slot is not None
    assert slot.name == "vision"
    assert slot.model_id == "vision-chat"


# ── #1469: label overlay falls back to registry-derived modality ────────────
#
# 7 of the omni router's 8 tools gate on `required_model_labels`, which the
# overlay above satisfies from the slot TOML's hand-authored `[model].labels`
# — a field nothing in the installer, the registry import path, or the
# dashboard ever writes, so every live box carries zero labels and those
# tools are permanently inert even when a correctly-typed, correctly-capable
# slot exists. Mirrors the exact fallback shape §7.1d already gave
# `tool_calling` (registry `capability_flags` first, hand-authored label
# second): here the source is `hal0.model_meta.modality.derive_modalities_
# from_model_info`, which is already populated at registration time from
# real facts (mmproj presence, pooling_type, backend family) — NOT a new
# field nobody writes. Only labels that fold onto the closed `Modality` enum
# via `normalize_modality` (vision/tts/asr/embed/rerank/image, and their
# tool-taxonomy aliases transcription/embeddings/reranking) get this
# fallback; `edit` has no modality equivalent and is deliberately NOT
# covered here (see the boundary test below) — inventing a new registry
# field to distinguish "can edit" from "can generate" is a real design
# decision, out of scope for this fix.


@pytest.mark.asyncio
async def test_resolve_for_request_label_overlay_falls_back_to_derived_vision_modality(
    slot_root: Path, tmp_hal0_home: str
) -> None:
    ModelRegistry().add(
        Model(id="vlm-7b", path="/tmp/vlm-7b.gguf", mmproj="/tmp/vlm-7b-mmproj.gguf")
    )
    _write_slot(slot_root, "chat", model="plain-chat", default=True)
    # No `labels=("vision",)` here — the registry's mmproj-derived modality
    # is the ONLY signal that this slot can serve a vision requirement.
    _write_slot(slot_root, "vlm", model="vlm-7b")

    slot = await SlotManager().resolve_for_request("llm", required_labels=("vision",))

    assert slot is not None
    assert slot.name == "vlm"


@pytest.mark.asyncio
async def test_resolve_for_request_label_overlay_falls_back_to_derived_embed_modality(
    slot_root: Path, tmp_hal0_home: str
) -> None:
    # embed_text's tool label is "embeddings" (tool-taxonomy spelling);
    # MODALITY_ALIASES folds it to the canonical "embed" Modality, which
    # derives from pooling_type=1 — no TOML label written either.
    ModelRegistry().add(
        Model(
            id="embed-model",
            path="/tmp/embed-model.gguf",
            metadata={"pooling_type": 1},
        )
    )
    _write_slot(slot_root, "embedder", slot_type="embedding", model="embed-model")

    slot = await SlotManager().resolve_for_request("embedding", required_labels=("embeddings",))

    assert slot is not None
    assert slot.name == "embedder"


@pytest.mark.asyncio
async def test_resolve_for_request_label_overlay_has_no_fallback_for_edit(
    slot_root: Path, tmp_hal0_home: str
) -> None:
    """`edit` (edit_image's required label) has no Modality equivalent — a
    model that derives `image` (comfyui) must NOT be treated as satisfying
    `edit` just because it's the only image-capable slot. Documents the
    known, deliberately-unfixed gap rather than silently over-matching."""
    ModelRegistry().add(Model(id="image-model", path="/tmp/image-model.gguf", backends=["comfyui"]))
    _write_slot(slot_root, "img", slot_type="image", model="image-model", default=True)

    slot = await SlotManager().resolve_for_request("image", required_labels=("edit",))

    assert slot is None


@pytest.mark.asyncio
async def test_route_for_request_keeps_name_compatibility(slot_root: Path) -> None:
    _write_slot(slot_root, "chat", model="default-chat", default=True)

    name = await SlotManager().route_for_request("llm")

    assert name == "chat"


@pytest.mark.asyncio
async def test_model_less_slot_is_not_routable(slot_root: Path) -> None:
    """An empty ``[model].default`` is the sole "not activated" signal (#1369)."""
    _write_slot(slot_root, "grey", slot_type="embedding", model="")

    assert await SlotManager().loaded_slot("grey") is None
    assert await SlotManager().resolve_for_request("embedding") is None


@pytest.mark.asyncio
async def test_stale_enabled_false_no_longer_hides_a_configured_slot(
    slot_root: Path,
) -> None:
    """A pre-#1369 ``enabled = false`` must not suppress a slot that has a model.

    The key is inert leftover config, so routing sees a normal live slot.
    Before the migration sweeps it off disk this is the operator-visible
    behaviour change: bound model wins.
    """
    _write_slot(slot_root, "chat", model="qwen3-4b", stale_enabled=False)

    slot = await SlotManager().loaded_slot("chat")

    assert slot is not None
    assert slot.model_id == "qwen3-4b"
    assert not hasattr(slot, "enabled")
