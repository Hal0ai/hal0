from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.manager import LoadedSlot, SlotManager


def _write_slot(
    root: Path,
    name: str,
    *,
    slot_type: str = "llm",
    model: str = "qwen3-4b",
    labels: tuple[str, ...] = (),
    default: bool = False,
    enabled: bool = True,
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
        f"enabled = {str(enabled).lower()}",
    ]
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
        enabled=True,
        labels=frozenset({"tool-calling", "vision"}),
        system_prompt="You are Chat.",
        profile="rocm",
        default=True,
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


@pytest.mark.asyncio
async def test_route_for_request_keeps_name_compatibility(slot_root: Path) -> None:
    _write_slot(slot_root, "chat", model="default-chat", default=True)

    name = await SlotManager().route_for_request("llm")

    assert name == "chat"
