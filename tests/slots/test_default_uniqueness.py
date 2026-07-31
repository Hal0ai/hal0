"""Write-time "one default per type" validation in SlotManager (SC-4).

ARCHITECTURE.md §"defaults" pins the contract: exactly one ``default = true``
slot may exist per ``type``. :meth:`SlotManager.default_slot_for` already
raises at routing time when two defaults slip onto disk, but nothing
stopped ``create()`` / ``update_config()`` from writing that second
default in the first place. These tests pin the write-time refusal
(the belt) while a companion test documents the routing-time backstop
(the suspenders).

Conventions mirror ``test_npu_exclusivity.py``:
  - Tests don't spawn containers (the validation runs before any I/O).
  - ``slot_root`` isolates the writer's TOML to a tmp directory. Note it
    pre-seeds a non-default ``chat`` llm slot, which is a legal peer and
    never an offender.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotConfigError


def _write_slot(
    root: Path,
    name: str,
    *,
    slot_type: str = "llm",
    model: str = "qwen3-4b",
    default: bool = False,
    enabled: bool = True,
    device: str = "gpu-rocm",
) -> None:
    """Seed a minimal slot TOML without going through SlotManager."""
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
    lines.extend(["[model]", f'default = "{model}"'])
    (root / f"{name}.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Negative paths: a second default of the same type is refused ────────────


@pytest.mark.asyncio
async def test_create_rejects_second_default_of_same_type(slot_root: Path) -> None:
    """Creating a second ``type=llm, default=true`` slot must be rejected."""
    _write_slot(slot_root, "a", slot_type="llm", default=True)
    sm = SlotManager()
    with pytest.raises(SlotConfigError) as exc:
        await sm.create(
            "b",
            {
                "name": "b",
                "port": 8083,
                "device": "gpu-rocm",
                "type": "llm",
                "default": True,
                "model": {"default": "qwen3-1b"},
            },
        )
    assert "a" in exc.value.details["conflicting_slots"]
    # The new slot must not have been written to disk.
    assert not (slot_root / "b.toml").exists()


@pytest.mark.asyncio
async def test_update_config_rejects_flipping_default_when_one_exists(slot_root: Path) -> None:
    """Flipping ``default=false → true`` when a peer default exists is blocked."""
    _write_slot(slot_root, "a", slot_type="llm", default=True)
    _write_slot(slot_root, "b", slot_type="llm", default=False)
    sm = SlotManager()
    with pytest.raises(SlotConfigError) as exc:
        await sm.update_config("b", {"default": True})
    assert "a" in exc.value.details["conflicting_slots"]


@pytest.mark.asyncio
async def test_default_slot_for_still_raises_on_two_disk_defaults(slot_root: Path) -> None:
    """Routing-time backstop: two on-disk defaults still raise at resolve."""
    _write_slot(slot_root, "a", slot_type="llm", default=True)
    _write_slot(slot_root, "b", slot_type="llm", default=True)
    sm = SlotManager()
    with pytest.raises(SlotConfigError):
        await sm.default_slot_for("llm")


# ── Positive paths: writes that DON'T create a second default ───────────────


@pytest.mark.asyncio
async def test_create_allows_first_default_of_type(slot_root: Path) -> None:
    """The first default of a type is legal even with a non-default peer."""
    _write_slot(slot_root, "b", slot_type="llm", default=False)
    sm = SlotManager()
    snap = await sm.create(
        "a",
        {
            "name": "a",
            "port": 8083,
            "device": "gpu-rocm",
            "type": "llm",
            "default": True,
            "model": {"default": "qwen3-1b"},
        },
    )
    assert snap is not None
    assert (slot_root / "a.toml").exists()


@pytest.mark.asyncio
async def test_create_allows_default_of_different_type(slot_root: Path) -> None:
    """A default of a DIFFERENT type does not conflict with an llm default."""
    _write_slot(slot_root, "a", slot_type="llm", default=True)
    sm = SlotManager()
    snap = await sm.create(
        "c",
        {
            "name": "c",
            "port": 8083,
            "device": "gpu-rocm",
            "type": "embedding",
            "default": True,
            "model": {"default": "embed-gemma"},
        },
    )
    assert snap is not None
    assert (slot_root / "c.toml").exists()


@pytest.mark.asyncio
async def test_create_allows_non_default_peer_of_same_type(slot_root: Path) -> None:
    """A non-default peer may coexist alongside an existing default."""
    _write_slot(slot_root, "a", slot_type="llm", default=True)
    sm = SlotManager()
    snap = await sm.create(
        "d",
        {
            "name": "d",
            "port": 8083,
            "device": "gpu-rocm",
            "type": "llm",
            "default": False,
            "model": {"default": "qwen3-1b"},
        },
    )
    assert snap is not None
    assert (slot_root / "d.toml").exists()


@pytest.mark.asyncio
async def test_update_config_sole_default_does_not_self_conflict(slot_root: Path) -> None:
    """Updating the sole default without touching the default flag is legal.

    The peer walk excludes the writer's own slot, so re-persisting the
    lone default must not trip on itself.
    """
    _write_slot(slot_root, "a", slot_type="llm", default=True)
    sm = SlotManager()
    await sm.update_config("a", {"model": {"context_size": 4096}})


@pytest.mark.asyncio
async def test_update_config_unrelated_field_not_blocked_by_stale_peer_default(
    slot_root: Path,
) -> None:
    """A pre-existing stale duplicate default must not brick unrelated edits.

    Two ``default=true`` slots of the same type can already be sitting on
    disk (e.g. seeded outside SlotManager, or left over from before this
    guard existed). Editing an unrelated field on either one must still
    succeed — cleanup of the stale duplicate is ``default_slot_for``'s job
    at routing time, not this write-time guard's (#1500).
    """
    _write_slot(slot_root, "a", slot_type="llm", default=True)
    _write_slot(slot_root, "b", slot_type="llm", default=True)
    sm = SlotManager()
    await sm.update_config("a", {"model": {"context_size": 4096}})
